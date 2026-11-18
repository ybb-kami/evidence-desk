"""Offline GroupBudget migration. The operator must stop all paid writers first.

Back up the old ledger to a separate private, standalone SQLite file. Finish
checkpointing the backup, switch the copy to DELETE journal mode and close
it before recording its SHA-256. Keep both the original and backup. This
program never starts a provider, resets a ledger or restores a backup.
An active migration receipt belongs to one canonical path and file on Linux.
"""

import argparse
from collections import Counter
from contextlib import closing, contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory

from group_budget import CAP, GROUPS, LEGACY, NANO, Denied, GroupBudget
from candidate.gateway.model_budget import ApprovedLimits, BudgetDenied, BudgetGate, VerifiedPrice
from candidate.gateway.model_budget import _fingerprint as model_fingerprint


MAX_SNAPSHOT = 16 * 1024 * 1024
_SHA = re.compile(r"[a-f0-9]{64}")
_GROUP_COLUMNS = {"conf": ("hash",), "groups_": ("gid", "seat"),
                  "spend": ("rid", "gid", "op", "nano", "wire_sha")}
_MODEL_COLUMNS = {"budget_config": ("singleton", "fingerprint", "last_seen"),
                  "budget_ledger": ("request_id", "session_id", "epoch", "precharged_nano", "body_sha")}
_TOOL_COLUMNS = {"spend": ("id", "seat", "action", "amount")}
_HISTORIC_ID = re.compile(r"[-A-Za-z0-9_.:]{16,128}")
_TOOL_ID = re.compile(r"[a-f0-9]{48}")
_STAMP = "legacy_migration"
_TRIGGER = """CREATE TRIGGER legacy_migration_new_spend AFTER INSERT ON spend
BEGIN UPDATE legacy_migration SET new_admissions = new_admissions + 1 WHERE singleton = 1; END"""
_GROUP_CONFIG = {"approval_sha", "price_sha", "verified_epoch", "expiry_epoch", "seat_groups", "cap_policy"}


class MigrationDenied(Denied):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _digest(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _require(condition, code):
    if not condition:
        raise MigrationDenied(code)


def _integ(db):
    _require(db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
             and db.execute("PRAGMA foreign_key_check").fetchone() is None, "invalid_ledger")


def _schema(db, expected, *, adopted=False, quiescent=True):
    entries = db.execute("SELECT name, type FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
    tables = {name for name, kind in entries if kind == "table"}
    other = {name for name, kind in entries if kind not in {"index", "table"}}
    extra = {_STAMP} if adopted else set()
    trigger = {"legacy_migration_new_spend"} if adopted else set()
    if expected == _GROUP_COLUMNS and "private_pending" in tables:
        expected = {**expected, "private_pending": ("rid",)}
    _require(tables == expected.keys() | extra and other == trigger, "unexpected_ledger_schema")
    for table, columns in expected.items():
        actual = tuple(row[1] for row in db.execute(f"PRAGMA table_xinfo({table})"))
        _require(actual == columns, "unexpected_ledger_schema")
    if quiescent and "private_pending" in expected:
        _require(db.execute("SELECT 1 FROM private_pending LIMIT 1").fetchone() is None,
                 "pending_paid_requests_require_reconciliation")


def _sidecars(path):
    return any(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))


@contextmanager
def _snapshot(path, expected_sha):
    path = Path(path).absolute()
    _require(_digest(expected_sha) and not _sidecars(path), "unverified_snapshot")
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
            before = os.fstat(source.fileno())
            _require(stat.S_ISREG(before.st_mode) and 100 <= before.st_size <= MAX_SNAPSHOT, "unverified_snapshot")
            raw = source.read(MAX_SNAPSHOT + 1)
            after = os.fstat(source.fileno())
        _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                 == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                 and len(raw) == before.st_size and not _sidecars(path)
                 and raw.startswith(b"SQLite format 3\x00") and raw[18:20] == b"\x01\x01"
                 and sha256(raw).hexdigest() == expected_sha, "unverified_snapshot")
        with TemporaryDirectory(prefix="group-ledger-snapshot-") as private:
            copy = Path(private) / "standalone.sqlite"
            copy.write_bytes(raw)
            with closing(sqlite3.connect(copy.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                _integ(db)
                yield db
    except (OSError, sqlite3.Error):
        raise MigrationDenied("unreadable_snapshot") from None


def _group_config(config, path, *, source_provenance=False):
    _require(isinstance(config, dict)
             and _GROUP_CONFIG - {"cap_policy"} <= config.keys() <= _GROUP_CONFIG | {"path"}, "invalid_configuration")
    options = dict(config)
    if "path" in options:
        configured = options.pop("path")
        _require(isinstance(configured, str) and Path(configured).is_absolute(), "invalid_configuration_path")
        if not source_provenance:
            _require(Path(configured) == Path(path).absolute(), "invalid_configuration_path")
    return GroupBudget(path, **options)


def _config_hash(db):
    conf = db.execute("SELECT hash FROM conf").fetchall()
    _require(len(conf) == 1 and _digest(conf[0][0]), "invalid_ledger_configuration")
    return conf[0][0]


def _active_instance(path, db=None):
    _require(sys.platform == "linux", "ledger_instance_binding_requires_linux")
    path = Path(path).absolute()
    canonical = path.resolve(strict=True)
    _require(path == canonical, "ledger_instance_mismatch")
    info = canonical.stat(follow_symlinks=False)
    _require(stat.S_ISREG(info.st_mode), "ledger_instance_mismatch")
    if db is not None:
        main = [filename for _, name, filename in db.execute("PRAGMA database_list") if name == "main"]
        _require(main == [str(canonical)], "ledger_instance_mismatch")
    return {"path_sha256": sha256(os.fsencode(canonical)).hexdigest(), "device": info.st_dev, "inode": info.st_ino}


def _history(db, *, rates=None, review=False):
    groups = db.execute("SELECT gid,seat FROM groups_ ORDER BY gid").fetchall()
    spend = db.execute("SELECT rid,gid,op,nano,wire_sha FROM spend ORDER BY rid").fetchall()
    scoped = dict(groups)
    _require(len(scoped) == len(groups) and all(_digest(gid) and _digest(seat) for gid, seat in groups),
             "invalid_ledger_groups")
    keys = set()
    operations = set()
    for rid, gid, kind, nano, digest in spend:
        valid_id = isinstance(rid, str) and (bool(_HISTORIC_ID.fullmatch(rid)) if review else _digest(rid))
        valid_digest = _digest(digest) or (review and digest == "")
        _require(valid_id and rid not in keys and gid in scoped and kind in NANO
                 and (gid, kind) not in operations and type(nano) is int and nano > 0
                 and valid_digest and (rates is None or nano == rates[kind]), "invalid_ledger_reservation")
        keys.add(rid)
        operations.add((gid, kind))
    _require({row[1] for row in spend} == set(scoped), "invalid_ledger_groups")
    return groups, spend


def _totals(history):
    counts = {op: 0 for op in NANO}
    for row in history[1]:
        counts[row[2]] += 1
    return {"groups": len(history[0]), "reservations": len(history[1]),
            "reserved_by_kind": counts, "reserved_nano_usd": sum(row[3] for row in history[1])}


def _contains(current, original):
    return all(set(before) <= set(after) for after, before in zip(current, original))


def _stamped(db):
    return db.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (_STAMP,)).fetchone() is not None


def _receipt(db):
    layout = db.execute("SELECT sql FROM sqlite_schema WHERE type='trigger' AND name='legacy_migration_new_spend'").fetchall()
    _require(layout == [(_TRIGGER,)], "migration_receipt_changed")
    rows = db.execute("SELECT singleton,metadata,new_admissions FROM legacy_migration").fetchall()
    _require(len(rows) == 1 and rows[0][0] == 1 and type(rows[0][2]) is int and rows[0][2] >= 0,
             "migration_receipt_changed")
    receipt = json.loads(rows[0][1])
    _require(isinstance(receipt, dict)
             and receipt.get("migration_sha") == _fingerprint({
                 key: value for key, value in receipt.items() if key != "migration_sha"
             }), "migration_receipt_changed")
    return receipt, rows[0][2]


def _stamp(db, proof):
    receipt = {**proof, "migration_sha": _fingerprint(proof)}
    db.execute("""CREATE TABLE legacy_migration(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), metadata TEXT NOT NULL,
        new_admissions INTEGER NOT NULL CHECK(new_admissions>=0))""")
    db.execute("INSERT INTO legacy_migration VALUES(1,?,0)", (json.dumps(receipt, sort_keys=True),))
    db.execute(_TRIGGER)
    return receipt


def _result(receipt, new_admissions, action):
    public = {key: value for key, value in receipt.items() if key != "historical_rids"}
    return {**public, "new_admissions": new_admissions, "action": action, "paid_requests_made": 0}


def upgrade_policy_in_place(previous, target, *, snapshot, snapshot_sha, writers_stopped=False):
    """Bind the same ledger to the new policy, preserving exact IDs and charges."""
    _require(writers_stopped is True, "stop_all_paid_writers_first")
    _require(isinstance(previous, GroupBudget) and isinstance(target, GroupBudget)
             and previous.path == target.path and target.path.is_file() and not target.path.is_symlink()
             and previous.config != target.config and getattr(target, "cap_policy", None) is not None
             and previous.seat_groups == target.seat_groups
             and target.cap >= getattr(previous, "cap", CAP), "invalid_policy_transition")
    try:
        _require(not target.path.samefile(snapshot), "separate_static_snapshot_required")
        target._time()
        instance = _active_instance(target.path)
        with _snapshot(snapshot, snapshot_sha) as backup:
            _schema(backup, _GROUP_COLUMNS)
            _require(_config_hash(backup) == previous.config, "previous_configuration_mismatch")
            _require(backup.execute("PRAGMA user_version").fetchone() == (previous.policy_version,),
                     "previous_policy_version_mismatch")
            original = _history(backup, rates=NANO)
        totals = _totals(original)
        _require(totals["groups"] <= GROUPS and totals["reserved_nano_usd"] <= getattr(previous, "cap", CAP)
                 and totals["reserved_nano_usd"] <= target.cap, "history_exceeds_policy")
        proof = {"kind": "policy_in_place", "currency": "USD", "snapshot_sha256": snapshot_sha,
                 "previous_config": previous.config, "target_config": target.config,
                 "applied_config": target.config, "target_cap_nano_usd": target.cap,
                 "policy_version": target.policy_version,
                 "history_sha256": _fingerprint(original), "historical": totals,
                 "historical_rids": [row[0] for row in original[1]], "instance": instance}
        with closing(target._connect()) as db, db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN IMMEDIATE")
            target._time()
            _require(_active_instance(target.path, db) == instance, "ledger_instance_mismatch")
            _integ(db)
            adopted = _stamped(db)
            _schema(db, _GROUP_COLUMNS, adopted=adopted)
            current = _history(db, rates=NANO)
            if adopted:
                receipt, n = _receipt(db)
                _require(receipt.get("instance") == instance, "ledger_instance_mismatch")
                _require(_config_hash(db) == target.config
                         and db.execute("PRAGMA user_version").fetchone() == (target.policy_version,)
                         and receipt["migration_sha"] == _fingerprint(proof)
                         and _contains(current, original) and len(current[1]) == len(original[1]) + n,
                         "previous_migration_differs")
                return _result(receipt, n, "already_upgraded")
            _require(_config_hash(db) == previous.config, "previous_configuration_mismatch")
            _require(db.execute("PRAGMA user_version").fetchone() == (previous.policy_version,),
                     "previous_policy_version_mismatch")
            _require(current == original, "snapshot_out_of_sync")
            receipt = _stamp(db, proof)
            updated = db.execute("UPDATE conf SET hash=? WHERE hash=?", (target.config, previous.config))
            db.execute(f"PRAGMA user_version={target.policy_version}")
            _require(updated.rowcount == 1 and _active_instance(target.path, db) == instance, "ledger_instance_mismatch")
            return _result(receipt, 0, "upgraded_in_place")
    except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
        raise MigrationDenied("migration_storage_unavailable") from None


def rollback_check(path, *, migration_sha):
    """Read-only check. Once new reservations exist the old budget stays offline."""
    path = Path(path).absolute()
    _require(_digest(migration_sha) and path.is_file() and not path.is_symlink(), "unverified_migration")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=8)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            _integ(db)
            _schema(db, _GROUP_COLUMNS, adopted=True)
            receipt, new_admissions = _receipt(db)
            _require(receipt.get("kind") == "policy_in_place" and receipt["migration_sha"] == migration_sha
                     and _config_hash(db) == receipt["applied_config"]
                     and db.execute("PRAGMA user_version").fetchone() == (receipt["policy_version"],),
                     "unverified_migration")
            _require(receipt.get("instance") == _active_instance(path, db), "ledger_instance_mismatch")
            _require(not new_admissions and _fingerprint(_history(db)) == receipt["history_sha256"],
                     "rollback_requires_reconciliation")
            return _result(receipt, 0, "no_spend_after_migration")
    except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
        raise MigrationDenied("migration_storage_unavailable") from None


def _bound_history(ledger, db, receipt, n, instance):
    _require(receipt.get("instance") == instance, "ledger_instance_mismatch")
    _require(receipt.get("kind") == "policy_in_place" and receipt.get("currency") == "USD"
             and _digest(receipt.get("snapshot_sha256")) and _digest(receipt.get("previous_config"))
             and receipt["previous_config"] != ledger.config
             and receipt.get("target_config") == receipt.get("applied_config") == _config_hash(db) == ledger.config
             and receipt.get("target_cap_nano_usd") == ledger.cap
             and receipt.get("policy_version") == ledger.policy_version
             and db.execute("PRAGMA user_version").fetchone() == (ledger.policy_version,),
             "unverified_policy_migration")
    offset = receipt.get("admissions_at_cutover", 0)
    if "predecessor_migration_sha" in receipt:
        _require(_digest(receipt["predecessor_migration_sha"]) and _digest(receipt.get("first_migration_sha"))
                 and type(offset) is int and 0 <= offset <= n and isinstance(receipt.get("predecessor_historical"), dict),
                 "unverified_policy_migration")
    else:
        _require(offset == 0, "unverified_policy_migration")
    historical_keys = receipt.get("historical_rids")
    _require(isinstance(historical_keys, list) and all(map(_digest, historical_keys))
             and historical_keys == sorted(set(historical_keys)), "unverified_policy_migration")
    prior_rids = set(historical_keys)
    groups, entries = _history(db, rates=NANO)
    imported = [row for row in entries if row[0] in prior_rids]
    prior_gids = {row[1] for row in imported}
    historical = ([row for row in groups if row[0] in prior_gids], imported)
    _require(len(imported) == len(prior_rids) and len(entries) == len(imported) + n - offset
             and _fingerprint(historical) == receipt.get("history_sha256")
             and _totals(historical) == receipt.get("historical"), "changed_policy_history")
    totals = _totals((groups, entries))
    seats = Counter(seat for _, seat in groups)
    _require(totals["groups"] <= GROUPS and totals["reserved_nano_usd"] <= ledger.cap
             and all(count <= ledger.seat_groups for count in seats.values()), "history_exceeds_policy")
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='private_pending'").fetchone():
        pending = [row[0] for row in db.execute("SELECT rid FROM private_pending")]
        new_rids = {row[0] for row in entries} - prior_rids
        _require(len(set(pending)) == len(pending) and set(pending) <= new_rids,
                 "unverified_pending_policy_reservation")
    return groups, entries


def renew_policy_in_place(previous, target, *, snapshot, snapshot_sha, writers_stopped=False):
    """Refresh the verified policy of an existing migrated ledger in one transaction."""
    _require(writers_stopped is True, "stop_all_paid_writers_first")
    _require(isinstance(previous, GroupBudget) and isinstance(target, GroupBudget)
             and previous.path == target.path and target.path.is_file() and not target.path.is_symlink()
             and previous.cap_policy is not None and target.cap_policy is not None
             and previous.policy_version == target.policy_version
             and previous.config != target.config and previous.seat_groups == target.seat_groups,
             "invalid_policy_transition")
    old_dates = (previous.checked, previous.cap_policy["fx_verified_epoch"])
    new_dates = (target.checked, target.cap_policy["fx_verified_epoch"])
    _require(all(new >= old for new, old in zip(new_dates, old_dates)) and new_dates != old_dates,
             "renewal_requires_fresh_evidence")
    try:
        _require(not target.path.samefile(snapshot), "separate_static_snapshot_required")
        target._time()
        instance = _active_instance(target.path)
        with _snapshot(snapshot, snapshot_sha) as backup:
            _schema(backup, _GROUP_COLUMNS, adopted=True)
            predecessor, before_n = _receipt(backup)
            original = _bound_history(previous, backup, predecessor, before_n, instance)
        totals = _totals(original)
        _require(totals["reserved_nano_usd"] <= target.cap, "history_exceeds_policy")
        proof = {"kind": "policy_in_place", "currency": "USD", "snapshot_sha256": snapshot_sha,
                 "previous_config": previous.config, "target_config": target.config, "applied_config": target.config,
                 "target_cap_nano_usd": target.cap, "policy_version": target.policy_version, "instance": instance,
                 "history_sha256": _fingerprint(original), "historical": totals,
                 "historical_rids": [row[0] for row in original[1]], "admissions_at_cutover": before_n,
                 "predecessor_migration_sha": predecessor["migration_sha"],
                 "predecessor_historical": predecessor["historical"],
                 "first_migration_sha": predecessor.get("first_migration_sha", predecessor["migration_sha"])}
        expected = _fingerprint(proof)
        with closing(target._connect()) as db, db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN IMMEDIATE")
            target._time()
            _require(_active_instance(target.path, db) == instance, "ledger_instance_mismatch")
            _integ(db)
            _schema(db, _GROUP_COLUMNS, adopted=True)
            current, n = _receipt(db)
            if current["migration_sha"] == expected:
                _bound_history(target, db, current, n, instance)
                return _result(current, n, "already_renewed")
            _require(current == predecessor and n == before_n, "snapshot_out_of_sync")
            _require(_bound_history(previous, db, current, n, instance) == original, "snapshot_out_of_sync")
            receipt = {**proof, "migration_sha": expected}
            db.execute("UPDATE legacy_migration SET metadata=? WHERE singleton=1", (json.dumps(receipt, sort_keys=True),))
            updated = db.execute("UPDATE conf SET hash=? WHERE hash=?", (target.config, previous.config))
            _require(updated.rowcount == 1 and _active_instance(target.path, db) == instance, "ledger_instance_mismatch")
            return _result(receipt, n, "renewed_in_place")
    except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
        raise MigrationDenied("migration_storage_unavailable") from None


def require_migrated_policy(ledger, db):
    """Check cutover proof in the caller's approved broker/gateway transaction."""
    _require(isinstance(ledger, GroupBudget) and ledger.cap_policy is not None, "policy_migration_required")
    ledger._time()
    try:
        _integ(db)
        _require(_stamped(db), "policy_migration_required")
        _schema(db, _GROUP_COLUMNS, adopted=True, quiescent=False)
        receipt, n = _receipt(db)
        _bound_history(ledger, db, receipt, n, _active_instance(ledger.path, db))
        return _result(receipt, n, "verified_migrated_policy")
    except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
        raise MigrationDenied("migration_storage_unavailable") from None


def _archived_groups(db, binding, snapshot):
    _schema(db, _GROUP_COLUMNS)
    before = _group_config(binding, snapshot, source_provenance=True)
    configured = _config_hash(db)
    _require(db.execute("PRAGMA user_version").fetchone() == (before.policy_version,)
             and configured in {before.config, before.legacy}, "previous_configuration_mismatch")
    return _history(db, rates=NANO if configured == before.config else LEGACY), configured


def _archived_models(db, binding):
    _schema(db, _MODEL_COLUMNS)
    _require(isinstance(binding, dict) and binding.keys() == {"approval", "price"}, "unverified_old_model_policy")
    try:
        approval = ApprovedLimits(**binding["approval"])
        price = VerifiedPrice(**binding["price"])
        _require(approval.currency == price.currency == "USD", "unsupported_legacy_currency")
        expected = model_fingerprint({"approval": binding["approval"], "price": binding["price"]})
        config = db.execute("SELECT singleton,fingerprint,last_seen FROM budget_config").fetchall()
        _require(len(config) == 1 and config[0][:2] == (1, expected) and type(config[0][2]) is int,
                 "previous_configuration_mismatch")
        last_seen = config[0][2]
        total, _, _, per_request, *_ = BudgetGate(".", approval, price)._rules(last_seen)
    except MigrationDenied:
        raise
    except (BudgetDenied, TypeError, ValueError):
        raise MigrationDenied("unverified_old_model_policy") from None
    result = []
    identities = set()
    for rid, seat, epoch, nano, body in db.execute("SELECT * FROM budget_ledger"):
        _require(isinstance(rid, str) and _HISTORIC_ID.fullmatch(rid) and rid not in identities
                 and isinstance(seat, str) and _HISTORIC_ID.fullmatch(seat)
                 and type(epoch) is int and approval.approved_epoch <= epoch <= last_seen
                 and type(nano) is int and 0 < nano <= per_request and _digest(body), "invalid_ledger_reservation")
        identities.add(rid)
        result.append((rid, seat, "model", nano, body))
    _require(len(result) <= approval.total_requests and sum(row[3] for row in result) <= total,
             "history_exceeds_policy")
    return _unpaired_groups(result, "standalone-model"), expected


def _archived_tools(db, binding):
    _schema(db, _TOOL_COLUMNS)
    _require(isinstance(binding, dict) and binding.keys() == {"deployment_sha256", "tool_limit_micro", "seat_limit_micro"}
             and _digest(binding["deployment_sha256"]) and type(binding["tool_limit_micro"]) is int
             and type(binding["seat_limit_micro"]) is int
             and 0 < binding["seat_limit_micro"] <= binding["tool_limit_micro"] <= 100_000_000,
             "unverified_old_tool_policy")
    result = []
    identities = set()
    seats = {}
    for rid, seat, kind, micro in db.execute("SELECT * FROM spend"):
        _require(isinstance(rid, str) and _TOOL_ID.fullmatch(rid) and rid not in identities and _digest(seat)
                 and kind in ("search", "fetch") and type(micro) is int and micro == 750,
                 "invalid_ledger_reservation")
        identities.add(rid)
        seats[seat] = seats.get(seat, 0) + micro
        result.append((rid, seat, kind, micro * 1000, ""))
    _require(sum(seats.values()) <= binding["tool_limit_micro"]
             and all(amount <= binding["seat_limit_micro"] for amount in seats.values()), "history_exceeds_policy")
    return _unpaired_groups(result, "standalone-tools"), _fingerprint(binding)


def _unpaired_groups(rows, kind):
    groups, spend = [], []
    for rid, seat, op, nano, wire in rows:
        gid = _fingerprint([kind, rid])
        groups.append((gid, seat if _digest(seat) else _fingerprint([kind, seat])))
        spend.append((rid, gid, op, nano, wire))
    return sorted(groups), sorted(spend)


def adopt_legacy_snapshot(target, *, snapshot, snapshot_sha, kind, currency, source_binding, writers_stopped=False):
    """Stage exactly one old snapshot for manual review in an inactive copy.

    Old standalone ledgers do not establish shared group identities. Each of
    their reservations is represented as a separate history group. This copy
    cannot admit requests under target.config and is never a cutover candidate.
    """
    _require(writers_stopped is True, "stop_all_paid_writers_first")
    _require(isinstance(target, GroupBudget) and currency == "USD", "unsupported_legacy_currency")
    _require(kind in {"group", "standalone-model", "standalone-tools"}, "unknown_legacy_source")
    try:
        with _snapshot(snapshot, snapshot_sha) as backup:
            if kind == "group":
                historical, binding = _archived_groups(backup, source_binding, snapshot)
            elif kind == "standalone-model":
                historical, binding = _archived_models(backup, source_binding)
            else:
                historical, binding = _archived_tools(backup, source_binding)
        totals = _totals(historical)
        _require(totals["reserved_nano_usd"] < 2**63, "history_out_of_range")
        blocked_config = _fingerprint(["legacy_review_only", target.config, snapshot_sha])
        proof = {"kind": "legacy_review_only", "source_kind": kind, "currency": "USD",
                 "snapshot_sha256": snapshot_sha, "source_binding": binding, "target_config": target.config,
                 "applied_config": blocked_config, "policy_version": target.policy_version, "paid_eligible": False,
                 "history_sha256": _fingerprint(historical), "historical": totals}
        if os.path.lexists(target.path):
            _require(not target.path.is_symlink() and not target.path.samefile(snapshot), "review_path_already_exists")
            with closing(sqlite3.connect(target.path.as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("BEGIN")
                _integ(db)
                _schema(db, _GROUP_COLUMNS, adopted=True)
                receipt, new = _receipt(db)
                _require(receipt["migration_sha"] == _fingerprint(proof) and new == 0
                         and _config_hash(db) == blocked_config
                         and db.execute("PRAGMA user_version").fetchone() == (target.policy_version,)
                         and _history(db, review=True) == historical,
                         "review_path_already_exists")
                return _result(receipt, new, "already_staged_for_review")
        with TemporaryDirectory(prefix="legacy-ledger-review-", dir=target.path.parent) as private:
            staged = Path(private) / "history.sqlite"
            # The normal schema cannot be published until its configuration is blocked.
            fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(staged)) as db, db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript("""CREATE TABLE conf(hash TEXT NOT NULL);
                    CREATE TABLE groups_(gid TEXT PRIMARY KEY NOT NULL,seat TEXT NOT NULL);
                    CREATE TABLE spend(rid TEXT PRIMARY KEY NOT NULL,gid TEXT NOT NULL,op TEXT NOT NULL,
                         nano INTEGER NOT NULL,wire_sha TEXT NOT NULL, UNIQUE(gid,op),
                         FOREIGN KEY(gid) REFERENCES groups_(gid));""")
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO conf VALUES(?)", (blocked_config,))
                db.execute(f"PRAGMA user_version={target.policy_version}")
                db.executemany("INSERT INTO groups_ VALUES(?,?)", historical[0])
                db.executemany("INSERT INTO spend VALUES(?,?,?,?,?)", historical[1])
                _require(_history(db, review=True) == historical, "history_verification_failed")
                receipt = _stamp(db, proof)
            os.link(staged, target.path)
            parent = os.open(target.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        return _result(receipt, 0, "staged_for_review_only")
    except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
        raise MigrationDenied("migration_storage_unavailable") from None


def _json_file(path):
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source, object_pairs_hook=_unique)


def _unique(pairs):
    values = {}
    for key, value in pairs:
        _require(key not in values, "duplicate_configuration_field")
        values[key] = value
    return values


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    upgrade = commands.add_parser("upgrade-in-place", help="Upgrade an exact, quiescent GroupBudget ledger",
                                  description="Previous and target runtime JSON may include path. When present it must equal the absolute ledger path.")
    renew = commands.add_parser("renew-policy-in-place", help="Refresh verified policy on the same migrated ledger",
                                description="Keep the entire preceding static snapshot and migration receipt. Refreshing policy preserves prior reservations and does not start a paid service.")
    for command in (upgrade, renew):
        for flag in ("ledger", "snapshot", "previous-config", "target-config"):
            command.add_argument("--" + flag, required=True, type=Path)
        command.add_argument("--snapshot-sha256", required=True)
        command.add_argument("--writers-stopped", action="store_true",
                             help="Attest every paid writer and in-flight send stopped")
    rollback = commands.add_parser("rollback-check", help="Read-only exact check, never restores historical paid service")
    rollback.add_argument("--ledger", required=True, type=Path)
    rollback.add_argument("--migration-sha", required=True)
    review = commands.add_parser("stage-legacy-review", help="Create an inactive manual-review copy of one legacy ledger",
                                 description="A target-config path, if present, must match the output. A group source-binding path identifies its original deployment and need not match the copy.")
    for flag in ("output", "snapshot", "target-config", "source-binding"):
        review.add_argument("--" + flag, required=True, type=Path)
    review.add_argument("--snapshot-sha256", required=True)
    review.add_argument("--currency", required=True, choices=("USD",))
    review.add_argument("--kind", required=True, choices=("group", "standalone-model", "standalone-tools"))
    review.add_argument("--writers-stopped", action="store_true")
    args = cli.parse_args()
    try:
        if args.command == "rollback-check":
            result = rollback_check(args.ledger, migration_sha=args.migration_sha)
        elif args.command == "stage-legacy-review":
            target = _group_config(_json_file(args.target_config), args.output)
            result = adopt_legacy_snapshot(target, snapshot=args.snapshot, snapshot_sha=args.snapshot_sha256,
                                           kind=args.kind, currency=args.currency,
                                           source_binding=_json_file(args.source_binding),
                                           writers_stopped=args.writers_stopped)
        else:
            previous = _group_config(_json_file(args.previous_config), args.ledger)
            target = _group_config(_json_file(args.target_config), args.ledger)
            change = renew_policy_in_place if args.command == "renew-policy-in-place" else upgrade_policy_in_place
            result = change(previous, target, snapshot=args.snapshot, snapshot_sha=args.snapshot_sha256,
                            writers_stopped=args.writers_stopped)
    except MigrationDenied as exc:
        cli.exit(2, f"Migration denied: {exc.code}\n")
    except (Denied, OSError, ValueError, TypeError, RecursionError):
        cli.exit(2, "Migration denied: unverifiable_policy_or_storage\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
