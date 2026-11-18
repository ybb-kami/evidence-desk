from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from audit_group_ledger import audit
from group_budget import CAP, ENDPOINT, GROUPS, LEGACY, NANO, VERSION, Denied, GroupBudget


def sid(value):
    return sha256(str(value).encode()).hexdigest()


class CapPolicyTests(TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(TemporaryDirectory())) / 'ledger.sqlite3'
        self.now = 100
        self.common = dict(approval_sha='a' * 64, price_sha='b' * 64, verified_epoch=90, expiry_epoch=300,
                           clock=lambda: self.now, input_bound=lambda wire: 1000)
        self.policy = {'cny_per_usd_upper': '7.500000000', 'fx_source_sha256': 'c' * 64,
                       'fx_verified_epoch': 95, 'fx_expiry_epoch': 180}

    def budget(self, policy=None):
        return GroupBudget(self.path, **self.common, cap_policy=policy)

    def reserve(self, budget, group, *, seat=1, private=False):
        wire = b'{"query":"public terms","count":5,"need_content":false}'
        return budget.reserve(seat=sid(seat), gid=sid(group), op='search', rid=sid(('search', group)),
                              endpoint=ENDPOINT['search'], wire=wire, private_dispatch=private)

    def test_exact_floor_and_legacy_fingerprints(self):
        old = self.budget()
        for charge_table, recorded in ((NANO, old.config), (LEGACY, old.legacy)):
            format_v0 = ['a' * 64, 'b' * 64, 90, 300, 100, VERSION, charge_table, CAP, GROUPS]
            self.assertEqual(recorded, sha256(json.dumps(format_v0, sort_keys=True).encode()).hexdigest())
        with localcontext() as context:
            context.prec = 3
            new = self.budget(self.policy)
        self.assertEqual(new.cap, 2_666_666_666)
        self.assertLessEqual(Decimal(new.cap) * Decimal('7.5'), 20_000_000_000)
        self.assertGreater(Decimal(new.cap + 1) * Decimal('7.5'), 20_000_000_000)
        self.assertNotEqual(new.config, old.config)
        normalized = self.budget(dict(self.policy, cny_per_usd_upper='7.5'))
        self.assertEqual(normalized.config, new.config)
        for field, value in (('fx_source_sha256', 'd' * 64), ('fx_verified_epoch', 96), ('fx_expiry_epoch', 181)):
            other = self.budget(dict(self.policy, **{field: value}))
            self.assertEqual(other.cap, new.cap)
            self.assertNotEqual(other.config, new.config)

    def test_atomic_boundary_across_seats_and_restart(self):
        # Synthetic high rate makes the expected cap exactly two fixture Search charges.
        policy = dict(self.policy, cny_per_usd_upper='13333.333333333')
        budget = self.budget(policy)
        self.assertEqual(budget.cap, 2 * NANO['search'])
        budget.provision()

        def request(seat):
            try:
                self.reserve(self.budget(policy), seat, seat=seat)
            except Denied as error:
                self.assertEqual(str(error), 'hard cap')
                return False
            return True

        with ThreadPoolExecutor(max_workers=4) as pool:
            sent = list(pool.map(request, range(1, 5)))
        self.assertEqual(sum(sent), 2)
        restarted = self.budget(policy)
        with self.assertRaisesRegex(Denied, 'hard cap'):
            self.reserve(restarted, 9, seat=2)
        result = audit(self.path, budget=restarted)
        self.assertEqual(result['groups_with_reservations'], 2)
        self.assertEqual(result['reserved_nano_usd'], restarted.cap)
        self.assertEqual(result['limit_nano_usd'], restarted.cap)
        with self.assertRaisesRegex(Denied, 'operator configuration required'):
            audit(self.path)

    def test_group_cap_stays_100_under_fx_policy(self):
        budget = self.budget(self.policy)
        budget.provision()
        for group in range(100):
            self.reserve(budget, group, seat=1 + group % 4)
        with self.assertRaisesRegex(Denied, 'group cap'):
            self.reserve(budget, 100, seat=4)
        result = audit(self.path, budget=budget)
        self.assertEqual(result['groups_with_reservations'], 100)
        self.assertLess(result['reserved_nano_usd'], budget.cap)

    def test_unusable_rate_is_rejected_before_admission(self):
        for value in (0, True, -2, '-5', '0.00', 'nan', '1e2', ' 7.5', '6.0000000009', '0.000000001'):
            with self.subTest(rate=value), self.assertRaises(Denied):
                self.budget(dict(self.policy, cny_per_usd_upper=value))
        for fields in ({'fx_source_sha256': 'unverified'}, {'fx_verified_epoch': True}, {'fx_expiry_epoch': 86500},
                       {'cap_nano_usd': 500_000_000_000}):
            with self.subTest(fields=fields), self.assertRaises(Denied):
                self.budget(dict(self.policy, **fields))
        budget = self.budget(self.policy)
        budget.provision()
        self.now = 180
        with self.assertRaisesRegex(Denied, 'stale exchange rate'):
            self.reserve(budget, 0)
        with self.assertRaisesRegex(Denied, 'stale exchange rate'):
            audit(self.path, budget=budget)
        self.now = 94
        with self.assertRaisesRegex(Denied, 'stale exchange rate'):
            self.reserve(budget, 0)

    def test_no_policy_change_without_migration_and_earlier_reserves_still_count(self):
        old = self.budget()
        old.provision()
        self.reserve(old, 1)
        policy = dict(self.policy, cny_per_usd_upper='13333.333333333')
        candidate = self.budget(policy)
        with self.assertRaisesRegex(Denied, 'policy does not bind'):
            self.reserve(candidate, 2)
        # This fixture models only the final binding after an explicit migration.
        # The migration tests verify snapshot proof and a safe transition independently.
        with old._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE conf SET hash=?', (candidate.config,))
            conn.execute(f'PRAGMA user_version={candidate.policy_version}')
        self.reserve(candidate, 2, seat=2)
        with self.assertRaisesRegex(Denied, 'hard cap'):
            self.reserve(candidate, 3, seat=3)
        with self.assertRaisesRegex(Denied, 'policy does not bind'):
            self.reserve(old, 3)
        self.assertEqual(audit(self.path, budget=candidate)['reserved_nano_usd'], candidate.cap)
        wrong = self.budget(dict(policy, fx_source_sha256='d' * 64))
        with self.assertRaisesRegex(Denied, 'configuration does not bind'):
            audit(self.path, budget=wrong)

    def test_private_dispatch_only_for_new_explicit_reservations_and_atomic_on_failure(self):
        budget = self.budget(self.policy)
        budget.provision()
        self.reserve(budget, 1)
        self.reserve(budget, 2, private=True)
        with budget._connect() as conn:
            self.assertEqual(conn.execute('SELECT rid FROM private_pending').fetchall(), [(sid(('search', 2)),)])
            conn.execute("CREATE TRIGGER reject_pending BEFORE INSERT ON private_pending BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Denied):
            self.reserve(budget, 3, private=True)
        result = audit(self.path, budget=budget)
        self.assertEqual(result['groups_with_reservations'], 2)
        self.assertEqual(result['reserved_operations'], {'search': 2})
