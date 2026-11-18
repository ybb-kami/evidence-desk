"""Offline migration tests with the same GroupBudget entry point as production."""

from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from group_budget import CAP, ENDPOINT, LEGACY, NANO, Denied, GroupBudget
from candidate.gateway.model_budget import ApprovedLimits, BudgetGate, PRICE_SOURCE, VerifiedPrice
from discovery_tickets import Ledger as ToolLedger
from group_dispatch import ApprovedGroupCaller
from legacy_ledger import MigrationDenied, _group_config, adopt_legacy_snapshot, require_migrated_policy
from legacy_ledger import rollback_check, upgrade_policy_in_place


def fingerprint(data):
    return sha256(data).hexdigest()


def identity(label):
    return fingerprint(str(label).encode())


class LegacyLedgerTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="group-policy-migration-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / "ledger.sqlite"
        self.price = dict(approval_sha="a" * 64, price_sha="b" * 64, verified_epoch=90,
                          expiry_epoch=180, seat_groups=100, input_bound=lambda _: 1000, clock=lambda: 100)
        self.new_policy = dict(cny_per_usd_upper="8", fx_source_sha256="c" * 64,
                               fx_verified_epoch=92, fx_expiry_epoch=172)
        self.old = GroupBudget(self.path, **self.price)
        self.new = GroupBudget(self.path, **self.price, cap_policy=self.new_policy)
        self.old.provision()
        self.backup = self.folder / "static.sqlite"

    def reserve(self, budget, group, op="model", **options):
        body = {
            "model": {"model": "deepseek-flash", "messages": [{"role": "user", "content": "public question"}],
                      "max_tokens": 512, "stream": False, "thinking": {"type": "disabled"},
                      "response_format": {"type": "json_object"}},
            "search": {"query": "public question", "count": 1, "need_content": False},
        }[op]
        return budget.reserve(gid=identity(group), seat=identity("seat"), rid=identity((group, op)),
                              op=op, endpoint=ENDPOINT[op], wire=json.dumps(body).encode(), **options)

    def snapshot(self):
        self.backup.unlink(missing_ok=True)
        with closing(sqlite3.connect(self.path)) as live, closing(sqlite3.connect(self.backup)) as saved:
            live.backup(saved)
            saved.execute("PRAGMA journal_mode=DELETE")
        return fingerprint(self.backup.read_bytes())

    def upgrade(self, **overrides):
        options = dict(snapshot=self.backup, snapshot_sha=fingerprint(self.backup.read_bytes()), writers_stopped=True)
        return upgrade_policy_in_place(self.old, self.new, **{**options, **overrides})

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            return tuple(db.execute(query).fetchall() for query in
                         ("SELECT * FROM groups_ ORDER BY gid", "SELECT * FROM spend ORDER BY rid"))

    def check_error(self, reason, func):
        with self.assertRaises(MigrationDenied) as failure:
            func()
        self.assertEqual(failure.exception.code, reason)

    def test_in_place_upgrade_is_durable_and_keeps_exact_rows_and_caps(self):
        self.reserve(self.old, 1)
        self.reserve(self.old, 1, "search")
        historical = self.rows()
        inode = self.path.stat().st_ino
        original_sha = self.snapshot()
        receipt = self.upgrade()
        self.assertEqual(receipt["action"], "upgraded_in_place")
        self.assertEqual(receipt["historical"]["reserved_nano_usd"], NANO["model"] + NANO["search"])
        self.assertEqual(receipt["historical"]["reserved_by_kind"], {"model": 1, "search": 1, "fetch": 0})
        self.assertEqual((self.path.stat().st_ino, self.rows()), (inode, historical))
        self.assertEqual(fingerprint(self.backup.read_bytes()), original_sha)
        self.assertEqual(self.upgrade()["action"], "already_upgraded")
        self.assertEqual(rollback_check(self.path, migration_sha=receipt["migration_sha"])["new_admissions"], 0)

        with self.assertRaises(Denied):
            self.reserve(self.old, 3)
        old_sends = []
        old_sender = ApprovedGroupCaller(self.old, lambda *args: old_sends.append(args))
        with self.assertRaises(Denied):
            old_sender.call(identity("seat"), identity(3), "search", identity("old-sender-call"),
                            {"query": "public question", "count": 1, "need_content": False})
        self.assertEqual(old_sends, [])
        after_restart = GroupBudget(self.path, **self.price, cap_policy=self.new_policy)
        with self.assertRaises(Denied):
            self.reserve(after_restart, 1)
        for group in range(2, 8):
            self.reserve(after_restart, group)
        remaining = self.new.cap - (7 * NANO["model"] + NANO["search"])
        self.assertGreater(self.new.cap, CAP)
        self.assertGreaterEqual(remaining, 0)
        self.assertLess(remaining, NANO["model"])
        with self.assertRaises(Denied):
            self.reserve(after_restart, 8)
        self.assertEqual(self.upgrade()["new_admissions"], 6)
        self.check_error("rollback_requires_reconciliation",
                         lambda: rollback_check(self.path, migration_sha=receipt["migration_sha"]))

    def test_a_post_cutover_unknown_reservation_blocks_rollback_even_if_removed(self):
        self.reserve(self.old, 1)
        self.snapshot()
        receipt = self.upgrade()
        self.reserve(self.new, 2, "search")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM spend WHERE gid=?", (identity(2),))
            db.execute("DELETE FROM groups_ WHERE gid=?", (identity(2),))
        self.check_error("rollback_requires_reconciliation",
                         lambda: rollback_check(self.path, migration_sha=receipt["migration_sha"]))
        self.check_error("previous_migration_differs", self.upgrade)

    def test_wrong_digest_pending_and_moved_live_state_fail_closed(self):
        self.reserve(self.old, 1)
        self.snapshot()
        self.check_error("stop_all_paid_writers_first", lambda: self.upgrade(writers_stopped=False))
        self.check_error("unverified_snapshot", lambda: self.upgrade(snapshot_sha="d" * 64))
        self.reserve(self.old, 2, "search")
        self.check_error("snapshot_out_of_sync", self.upgrade)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE private_pending(rid TEXT PRIMARY KEY REFERENCES spend(rid))")
            db.execute("INSERT INTO private_pending VALUES(?)", (identity((1, "model")),))
        self.snapshot()
        self.check_error("pending_paid_requests_require_reconciliation", self.upgrade)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT hash FROM conf").fetchone(), (self.old.config,))
            db.execute("DELETE FROM private_pending")
        self.snapshot()
        self.assertEqual(self.upgrade()["action"], "upgraded_in_place")

    def test_old_undercounted_model_or_changed_policy_is_rejected(self):
        self.reserve(self.old, 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE spend SET nano=? WHERE op='model'", (LEGACY["model"],))
        self.snapshot()
        self.check_error("invalid_ledger_reservation", self.upgrade)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE spend SET nano=? WHERE op='model'", (NANO["model"],))
            db.execute("UPDATE conf SET hash=?", ("d" * 64,))
        self.snapshot()
        self.check_error("previous_configuration_mismatch", self.upgrade)

    def review(self, path, kind, binding, **overrides):
        target = GroupBudget(self.folder / "review.sqlite", **self.price, cap_policy=self.new_policy)
        args = dict(snapshot=path, snapshot_sha=fingerprint(path.read_bytes()), kind=kind,
                    currency="USD", source_binding=binding, writers_stopped=True)
        return target, adopt_legacy_snapshot(target, **{**args, **overrides})

    def test_standalone_tool_history_is_preserved_only_in_inactive_review(self):
        original = self.folder / "tools.sqlite"
        historical = ToolLedger(original, tool_limit_micro=3000, seat_limit_micro=1800, valid_until=150,
                                clock=lambda: 100)
        historical.initialize_for_test()
        for rid, kind in (("a" * 48, "search"), ("b" * 48, "fetch")):
            historical.reserve(identity("seat"), rid, kind, 750)
        before = original.read_bytes()
        binding = {"deployment_sha256": "c" * 64, "tool_limit_micro": 3000, "seat_limit_micro": 1800}
        target, result = self.review(original, "standalone-tools", binding)
        self.assertIs(result["paid_eligible"], False)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(result["historical"]["reserved_nano_usd"], 1_500_000)
        self.assertEqual(result["historical"]["groups"], 2)
        self.assertEqual(self.review(original, "standalone-tools", binding)[1]["action"], "already_staged_for_review")
        self.check_error("unverified_migration", lambda: rollback_check(target.path, migration_sha=result["migration_sha"]))
        with self.assertRaises(Denied):
            self.reserve(target, 1)
        with closing(sqlite3.connect(target.path)) as db:
            self.assertEqual(db.execute("SELECT rid,op,nano,wire_sha FROM spend ORDER BY rid").fetchall(),
                             [("a" * 48, "search", 750_000, ""), ("b" * 48, "fetch", 750_000, "")])

    def make_old_model(self, currency):
        path = self.folder / "old-model.sqlite"
        price = VerifiedPrice(provider="DeepSeek", model="deepseek-flash", currency=currency,
                              source_url=PRICE_SOURCE, source_sha256="e" * 64,
                              verified_epoch=80, expires_epoch=150, input_miss_per_million="0.3",
                              output_per_million="1.2", additional_per_request_upper="0",
                              billable_input_tokens_upper=2000, billable_output_tokens_limit=512,
                              output_cap_covers_all_billed_tokens=True, bounds_and_fees_verified=True)
        approval = ApprovedLimits(approval_id="synthetic-old-limits", user_approval_sha256="f" * 64,
                                  price_fingerprint=price.fingerprint, currency=currency,
                                  approved_epoch=90, expires_epoch=149, total_amount="0.3", period_amount="0.3",
                                  session_amount="0.3", per_request_amount="0.3", total_requests=3,
                                  period_requests=3, session_requests=3, period_seconds=60,
                                  max_input_bytes=2000, max_output_tokens=512)
        old = BudgetGate(path, approval, price, clock=lambda: 100)
        old.initialize()
        charge = old.reserve(request_id="exact-old-model-request", session_id="exact-old-model-session",
                             request_body=json.dumps({"model": "deepseek-flash",
                                                      "messages": [{"role":"user", "content":"public fixture"}],
                                                      "max_tokens": 512, "stream": False,
                                                      "thinking": {"type": "disabled"}}).encode())
        return path, {"approval": asdict(approval), "price": asdict(price)}, charge

    def test_standalone_model_preserves_exact_charge_and_cannot_enable_paid(self):
        original, binding, charge = self.make_old_model("USD")
        bad_binding = {**binding, "approval": {**binding["approval"], "total_amount": "0.2"}}
        self.check_error("previous_configuration_mismatch",
                         lambda: self.review(original, "standalone-model", bad_binding))
        target, result = self.review(original, "standalone-model", binding)
        self.assertIs(result["paid_eligible"], False)
        self.assertEqual(result["historical"]["reserved_nano_usd"], charge.precharged_nano)
        self.assertEqual(result["historical"]["reserved_by_kind"]["model"], 1)
        with closing(sqlite3.connect(target.path)) as db:
            self.assertEqual(db.execute("SELECT rid,nano,wire_sha FROM spend").fetchone(),
                             (charge.request_id, charge.precharged_nano, charge.body_sha256))
        with self.assertRaises(Denied):
            self.reserve(target, 1)

    def test_cny_source_is_refused_even_if_declared_usd(self):
        original, binding, _ = self.make_old_model("CNY")
        self.check_error("unsupported_legacy_currency", lambda: self.review(original, "standalone-model", binding))
        self.assertFalse((self.folder / "review.sqlite").exists())

    def test_early_group_history_keeps_charge_and_identifiers_only_for_review(self):
        self.reserve(self.old, 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE conf SET hash=?", (self.old.legacy,))
            db.execute("UPDATE spend SET nano=?", (LEGACY["model"],))
        historical = self.rows()
        self.snapshot()
        binding = {key: value for key, value in self.price.items() if key not in {"clock", "input_bound"}}
        target, result = self.review(self.backup, "group", binding)
        self.assertIs(result["paid_eligible"], False)
        self.assertEqual(result["historical"]["reserved_nano_usd"], LEGACY["model"])
        with closing(sqlite3.connect(target.path)) as db:
            retained = tuple(db.execute(query).fetchall() for query in
                             ("SELECT * FROM groups_ ORDER BY gid", "SELECT * FROM spend ORDER BY rid"))
        self.assertEqual(retained, historical)
        with self.assertRaises(Denied):
            self.reserve(target, 2)

    def test_approved_policy_requires_a_migrated_ledger_and_checks_live_spend(self):
        clean = GroupBudget(self.folder / "pristine.sqlite", **self.price, cap_policy=self.new_policy)
        clean.provision()
        with closing(clean._connect()) as db:
            self.check_error("policy_migration_required", lambda: require_migrated_policy(clean, db))

        self.reserve(self.old, 1)
        self.snapshot()
        self.assertNotIn("historical_rids", self.upgrade())
        self.reserve(self.new, 2, "search", private_dispatch=True)
        with closing(self.new._connect()) as db, db:
            self.assertEqual(require_migrated_policy(self.new, db)["new_admissions"], 1)
            db.execute("INSERT INTO private_pending VALUES(?)", (identity((1, "model")),))
            self.check_error("unverified_pending_policy_reservation", lambda: require_migrated_policy(self.new, db))
            db.execute("DELETE FROM private_pending WHERE rid=?", (identity((1, "model")),))
            db.execute("UPDATE spend SET wire_sha=? WHERE rid=?", ("d" * 64, identity((1, "model"))))
            self.check_error("changed_policy_history", lambda: require_migrated_policy(self.new, db))

    def test_operator_runtime_path_can_be_reused_only_for_matching_live_path(self):
        config = {key: value for key, value in self.price.items() if key not in {"clock", "input_bound"}}
        self.assertEqual(_group_config({**config, "path": str(self.path)}, self.path).config, self.old.config)
        self.check_error("invalid_configuration_path",
                         lambda: _group_config({**config, "path": str(self.path)}, self.backup))
        self.assertEqual(_group_config({**config, "path": str(self.path)}, self.backup, source_provenance=True).config,
                         self.old.config)

    def test_stamped_copy_with_same_configuration_cannot_admit_or_rollback(self):
        self.reserve(self.old, 1)
        backup_sha = self.snapshot()
        receipt = self.upgrade()
        copied = self.folder / "copied-ledger.sqlite"
        shutil.copyfile(self.path, copied)
        clone = GroupBudget(copied, **self.price, cap_policy=self.new_policy)
        self.assertEqual(clone.config, self.new.config)
        self.assertEqual(copied.read_bytes(), self.path.read_bytes())
        with closing(self.new._connect()) as original:
            self.assertEqual(require_migrated_policy(self.new, original)["migration_sha"], receipt["migration_sha"])
        with closing(clone._connect()) as db:
            self.check_error("ledger_instance_mismatch", lambda: require_migrated_policy(clone, db))
            self.check_error("ledger_instance_mismatch", lambda: require_migrated_policy(self.new, db))
        self.check_error("ledger_instance_mismatch",
                         lambda: rollback_check(clone.path, migration_sha=receipt["migration_sha"]))
        old_clone = GroupBudget(copied, **self.price)
        self.check_error("ledger_instance_mismatch",
                         lambda: upgrade_policy_in_place(old_clone, clone, snapshot=self.backup,
                                                         snapshot_sha=backup_sha, writers_stopped=True))

    def test_renamed_or_replaced_active_file_cannot_admit(self):
        self.reserve(self.old, 1)
        self.snapshot()
        receipt = self.upgrade()
        renamed = self.folder / "renamed.sqlite"
        original_inode = self.path.stat().st_ino
        self.path.rename(renamed)
        self.assertEqual(renamed.stat().st_ino, original_inode)
        moved = GroupBudget(renamed, **self.price, cap_policy=self.new_policy)
        with closing(moved._connect()) as db:
            self.check_error("ledger_instance_mismatch", lambda: require_migrated_policy(moved, db))

        renamed.rename(self.path)
        restore = self.folder / "restore.sqlite"
        shutil.copyfile(self.path, restore)
        self.reserve(self.new, 2, "search")
        restore.replace(self.path)
        self.assertNotEqual(self.path.stat().st_ino, original_inode)
        with closing(self.new._connect()) as db:
            self.check_error("ledger_instance_mismatch", lambda: require_migrated_policy(self.new, db))
        self.check_error("ledger_instance_mismatch",
                         lambda: rollback_check(self.path, migration_sha=receipt["migration_sha"]))


if __name__ == "__main__":
    unittest.main()
