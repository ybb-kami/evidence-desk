"""Offline migration tests with the same GroupBudget entry point as production."""

from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json
import os
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
from legacy_ledger import renew_policy_in_place, rollback_check, upgrade_policy_in_place
from opening_reserve import SCHEMA, canonical_bytes, provision_opening, witness_path


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
        self.now = 100
        self.price = dict(approval_sha="a" * 64, price_sha="b" * 64, verified_epoch=90,
                          expiry_epoch=180, seat_groups=100, input_bound=lambda _: 1000, clock=lambda: self.now)
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

    def refreshed(self, *, rate="10", path=None, day=181, **options):
        price = {**self.price, "verified_epoch": day, "expiry_epoch": day + 80, "price_sha": identity(("price", day))}
        fx = {"cny_per_usd_upper": rate, "fx_source_sha256": identity(("FX", day)),
              "fx_verified_epoch": day, "fx_expiry_epoch": day + 70}
        return GroupBudget(path or self.path, **price, cap_policy=fx, **options)

    def renew(self, previous, target, **overrides):
        options = dict(snapshot=self.backup, snapshot_sha=fingerprint(self.backup.read_bytes()), writers_stopped=True)
        return renew_policy_in_place(previous, target, **{**options, **overrides})

    def test_expired_policy_renews_under_lower_cap_without_resetting_history(self):
        self.reserve(self.old, 1)
        self.snapshot()
        first = self.upgrade()
        self.reserve(self.new, 2, "search")
        self.reserve(self.new, 3, private_dispatch=True)
        with closing(self.new._connect()) as db, db:
            # The prior sender consumed the permission. Delivery of the reserved request is unknown.
            db.execute("DELETE FROM private_pending WHERE rid=?", (identity((3, "model")),))
        historical = self.rows()
        self.snapshot()
        self.now = 182
        with self.assertRaises(Denied):
            self.reserve(self.new, 4, "search")
        target = self.refreshed()
        renewed = self.renew(self.new, target)
        self.assertLess(target.cap, self.new.cap)
        self.assertEqual(renewed["action"], "renewed_in_place")
        self.assertEqual(renewed["predecessor_migration_sha"], first["migration_sha"])
        self.assertEqual(renewed["first_migration_sha"], first["migration_sha"])
        self.assertEqual(renewed["predecessor_historical"], first["historical"])
        self.assertEqual(renewed["historical"]["reserved_nano_usd"], 2 * NANO["model"] + NANO["search"])
        self.assertEqual(renewed["new_admissions"], 2)
        self.assertEqual(self.rows(), historical)
        self.check_error("unverified_migration", lambda: rollback_check(self.path, migration_sha=first["migration_sha"]))
        self.check_error("rollback_requires_reconciliation",
                         lambda: rollback_check(self.path, migration_sha=renewed["migration_sha"]))
        self.assertEqual(self.renew(self.new, target)["action"], "already_renewed")
        self.reserve(target, 4, "search", private_dispatch=True)
        with closing(target._connect()) as db, db:
            self.assertEqual(require_migrated_policy(target, db)["new_admissions"], 3)
            db.execute("DELETE FROM private_pending WHERE rid=?", (identity((4, "search")),))
        self.snapshot()
        self.now = 267
        third = self.renew(target, self.refreshed(day=266))
        self.assertEqual(third["new_admissions"], 3)
        self.assertEqual(third["predecessor_migration_sha"], renewed["migration_sha"])
        self.assertEqual(third["first_migration_sha"], first["migration_sha"])
        self.check_error("rollback_requires_reconciliation",
                         lambda: rollback_check(self.path, migration_sha=third["migration_sha"]))

    def test_renewal_refuses_insufficient_cap_clone_stale_snapshot_and_pending(self):
        self.reserve(self.old, 1)
        self.snapshot()
        initial = self.upgrade()
        self.snapshot()
        self.reserve(self.new, 2, "search", private_dispatch=True)
        self.now = 182
        target = self.refreshed()
        self.check_error("pending_paid_requests_require_reconciliation", lambda: self.renew(self.new, target))
        with closing(self.new._connect()) as db, db:
            db.execute("DELETE FROM private_pending")
        self.check_error("snapshot_out_of_sync", lambda: self.renew(self.new, target))
        self.snapshot()
        self.check_error("history_exceeds_policy", lambda: self.renew(self.new, self.refreshed(rate="100")))
        clone_path = self.folder / "renewal-clone.sqlite"
        shutil.copyfile(self.path, clone_path)
        clone = GroupBudget(clone_path, **self.price, cap_policy=self.new_policy)
        self.check_error("ledger_instance_mismatch", lambda: self.renew(clone, self.refreshed(path=clone_path)))
        with closing(self.new._connect()) as db:
            self.assertEqual(db.execute("SELECT hash FROM conf").fetchone(), (self.new.config,))
            self.assertEqual(json.loads(db.execute("SELECT metadata FROM legacy_migration").fetchone()[0])["migration_sha"],
                             initial["migration_sha"])

    def opening_ledger(self):
        project = self.folder / "opening-project"
        project.mkdir(mode=0o700)
        proof_dir = project / "proofs"
        proof_dir.mkdir(mode=0o700)
        opening_path = project / "opening.sqlite"
        records = []
        for run, currency, upper in (("direct-test", "USD", "0.1"), ("app-smoke", "CNY", "2.4")):
            raw = f"Synthetic evidence fixture for {run}.\n".encode()
            source = fingerprint(raw)
            (proof_dir / (source + ".proof")).write_bytes(raw)
            records.append({"kind": "provider_billing_upper", "runs": [run], "source_sha256": source,
                            "attempts_upper": 1, "includes_all_attempts_and_pending": True,
                            "currency": currency, "amount_upper": upper})
        evidence = {"schema": SCHEMA, "ledger_path_sha256": fingerprint(os.fsencode(opening_path)),
                    "opening_fx_cny_per_usd_upper": "8", "runs": ["direct-test", "app-smoke"], "records": records}
        digest = fingerprint(canonical_bytes(evidence))
        ledger = GroupBudget(opening_path, **self.price, cap_policy=self.new_policy, opening_evidence_sha256=digest)
        opened = provision_opening(ledger, evidence, proof_dir=proof_dir, no_existing_project_ledger=True, writers_stopped=True)
        self.assertEqual(opened["action"], "opened_with_historical_upper_bound")
        with closing(ledger._connect()) as db:
            receipt = require_migrated_policy(ledger, db)
        return ledger, digest, receipt

    def test_opening_is_bound_and_kept_out_of_dispatch_and_rollback(self):
        ledger, digest, receipt = self.opening_ledger()
        self.assertEqual(receipt["opening_reserve"]["evidence_sha256"], digest)
        self.assertEqual(receipt["opening_effective_nano_usd"], 400_000_000)
        self.assertEqual(receipt["historical"]["reservations"], 0)
        self.check_error("opening_reserve_nonrefundable",
                         lambda: rollback_check(ledger.path, migration_sha=receipt["migration_sha"]))
        with closing(ledger._connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM spend").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM groups_").fetchone(), (0,))
            self.assertEqual(require_migrated_policy(ledger, db)["migration_sha"], receipt["migration_sha"])
            witness = witness_path(ledger.path)
            held = witness.with_name("held-by-test")
            witness.rename(held)
            try:
                with self.assertRaisesRegex(Denied, "opening authority missing or changed"):
                    require_migrated_policy(ledger, db)
                with self.assertRaisesRegex(Denied, "opening authority missing or changed"):
                    rollback_check(ledger.path, migration_sha=receipt["migration_sha"])
            finally:
                held.rename(witness)
        copy = self.folder / "opening-copy.sqlite"
        shutil.copyfile(ledger.path, copy)
        copied = GroupBudget(copy, **self.price, cap_policy=self.new_policy, opening_evidence_sha256=digest)
        with closing(copied._connect()) as db:
            self.check_error("ledger_instance_mismatch", lambda: require_migrated_policy(copied, db))
            self.check_error("ledger_instance_mismatch",
                             lambda: rollback_check(copied.path, migration_sha=receipt["migration_sha"]))

        with closing(ledger._connect()) as db, db:
            db.execute("UPDATE opening_reserve SET reserved_nano_usd=0")
            with self.assertRaises(Denied):
                require_migrated_policy(ledger, db)

    def test_opening_renewal_requires_same_evidence_and_reprices_without_releasing_reserve(self):
        ledger, digest, first = self.opening_ledger()
        self.reserve(ledger, 2, "search")
        self.path = ledger.path
        self.snapshot()
        self.now = 182
        without_opening = self.refreshed(rate="6")
        wrong_digest = self.refreshed(rate="6", opening_evidence_sha256=identity("other evidence"))
        self.check_error("invalid_policy_transition", lambda: self.renew(ledger, without_opening))
        self.check_error("invalid_policy_transition", lambda: self.renew(ledger, wrong_digest))
        too_small = self.refreshed(rate="70", opening_evidence_sha256=digest)
        with self.assertRaises(Denied):
            self.renew(ledger, too_small)
        target = self.refreshed(rate="6", opening_evidence_sha256=digest)
        witness = witness_path(ledger.path)
        original_witness = witness.read_bytes()
        changed_witness = json.loads(original_witness)
        changed_witness["opening_evidence_sha256"] = identity("tampered witness")
        witness.write_bytes(canonical_bytes(changed_witness))
        try:
            with self.assertRaisesRegex(Denied, "opening authority missing or changed"):
                self.renew(ledger, target)
        finally:
            witness.write_bytes(original_witness)
        second = self.renew(ledger, target)
        self.assertEqual(second["opening_reserve"], first["opening_reserve"])
        self.assertEqual(second["opening_effective_nano_usd"], 500_000_000)
        self.assertEqual(second["historical"]["reserved_nano_usd"], NANO["search"])
        self.assertEqual(second["new_admissions"], 1)
        self.assertEqual(second["kind"], "opening_in_place")
        self.assertEqual(second["predecessor_migration_sha"], first["migration_sha"])
        self.assertEqual(self.renew(ledger, target)["action"], "already_renewed")
        self.check_error("unverified_migration", lambda: rollback_check(target.path, migration_sha=first["migration_sha"]))
        self.check_error("opening_reserve_nonrefundable",
                         lambda: rollback_check(target.path, migration_sha=second["migration_sha"]))
        with closing(target._connect()) as db:
            self.assertEqual(require_migrated_policy(target, db)["opening_effective_nano_usd"], 500_000_000)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM spend").fetchone(), (1,))


if __name__ == "__main__":
    unittest.main()
