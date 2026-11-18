from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase

from audit_group_ledger import audit
from group_budget import ENDPOINT, Denied, GroupBudget, read_opening_reserve
from opening_reserve import canonical_bytes, provision_opening, validate_evidence, witness_path


def identifier(value):
    return sha256(str(value).encode()).hexdigest()


class OpeningBudgetTest(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.proofs = self.root / 'proofs'
        self.proofs.mkdir(mode=0o700)
        source = b'Test-only external attestation. No invoice, payment or live account.'
        self.source_sha = sha256(source).hexdigest()
        (self.proofs / f'{self.source_sha}.proof').write_bytes(source)
        self.options = dict(approval_sha='a'*64, price_sha='b'*64, verified_epoch=5, expiry_epoch=30,
                            clock=lambda: 10, cap_policy={'cny_per_usd_upper':'8',
                            'fx_source_sha256':'c'*64, 'fx_verified_epoch':5, 'fx_expiry_epoch':30})

    def evidence(self, path, amount, attempts, currency):
        return {'schema':'evidence-desk-opening-v1', 'ledger_path_sha256':identifier(path),
                'opening_fx_cny_per_usd_upper':'8', 'runs':['direct-test','app-smoke'],
                'records':[{'kind':'provider_billing_upper', 'runs':['direct-test','app-smoke'],
                    'source_sha256':self.source_sha, 'currency':currency, 'amount_upper':amount,
                    'attempts_upper':attempts, 'includes_all_attempts_and_pending':True}]}

    def provision(self, *, name='active', amount='2.49925', attempts=2, currency='USD'):
        room = self.root / name
        room.mkdir(mode=0o700)
        path = room / 'opening.sqlite'
        proof = self.evidence(path, amount, attempts, currency)
        budget = GroupBudget(path, **self.options, opening_evidence_sha256=sha256(canonical_bytes(proof)).hexdigest())
        provision_opening(budget, proof, proof_dir=self.proofs, no_existing_project_ledger=True, writers_stopped=True)
        return budget

    def reserve(self, budget, number, *, seat=None, op='search', private=True):
        body = {'query':'public title','count':5,'need_content':False} if op=='search' else {'url':'https://arxiv.org/abs/1706.03762'}
        return budget.reserve(seat=identifier(number if seat is None else seat), gid=identifier(('group', number)),
                              op=op, rid=identifier((number,op)), endpoint=ENDPOINT[op],
                              wire=json.dumps(body).encode(), private_dispatch=private)

    def test_nonrefundable_upper_bound_atomic_across_four_seats_and_restart(self):
        budget = self.provision()

        def admission(seat):
            restarted = GroupBudget(budget.path, **self.options, opening_evidence_sha256=budget.opening_evidence_sha256)
            try:
                self.reserve(restarted, seat)
                return True
            except Denied as error:
                self.assertIn('hard cap', str(error))
                return False

        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(admission, range(4)))
        self.assertEqual(sum(results), 1)
        self.assertFalse(admission(5))
        with closing(sqlite3.connect(budget.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM private_pending').fetchone(), (1,))
        summary = audit(budget.path, budget=budget)
        self.assertEqual(summary['reserved_operations'], {'search':1})
        self.assertEqual(summary['reserved_nano_usd'], 750_000)
        self.assertEqual(summary['historical_opening_reserve']['effective_upper_bound_nano_usd'], 2_499_250_000)
        self.assertEqual(summary['historical_opening_reserve']['historical_requests'], 2)
        self.assertEqual(summary['groups_counting_toward_limit'], 3)
        self.assertEqual(summary['total_reserved_nano_usd'], budget.cap)
        self.assertEqual(summary['remaining_nano_usd'], 0)
        self.assertEqual(summary['provider_invoiced_amount'], 'not_observed')

    def test_historical_requests_share_global_groups_with_new_seats(self):
        budget = self.provision(amount='1.00', attempts=99)
        self.reserve(budget, 1)
        with self.assertRaisesRegex(Denied, 'group cap'):
            self.reserve(budget, 2)
        self.reserve(budget, 1, op='fetch')
        summary = audit(budget.path, budget=budget)
        self.assertEqual(summary['groups_with_reservations'], 1)
        self.assertEqual(summary['groups_counting_toward_limit'], 100)
        self.assertEqual(summary['remaining_group_capacity'], 0)
        self.assertEqual(summary['reserved_operations'], {'search':1,'fetch':1})

    def test_repricing_uses_ceiling_and_cannot_refund_the_booked_bound(self):
        budget = self.provision(amount='1.000000001', currency='CNY')
        with closing(sqlite3.connect(budget.path)) as db:
            self.assertEqual(budget.opening_totals(db), (125_000_001, 2))
            for rate, expected in [('6', 166_666_667), ('10', 125_000_001)]:
                policy = dict(self.options['cap_policy'], cny_per_usd_upper=rate)
                candidate = GroupBudget(budget.path, **dict(self.options, cap_policy=policy),
                                        opening_evidence_sha256=budget.opening_evidence_sha256)
                self.assertEqual(candidate.opening_totals(db), (expected, 2))

    def test_missing_malformed_or_recomputed_opening_denies_live_paths(self):
        attacks = {
            'drop': 'DROP TABLE opening_reserve',
            'delete': 'DELETE FROM opening_reserve',
            'amount': 'UPDATE opening_reserve SET reserved_nano_usd=0',
            'count': 'UPDATE opening_reserve SET historical_requests=0',
            'schema': 'ALTER TABLE opening_reserve ADD COLUMN extra TEXT',
            'marker': 'PRAGMA user_version=1',
        }
        for name in [*attacks, 'recompute', 'witness']:
            with self.subTest(name=name):
                budget = self.provision(name=name)
                if name=='witness':
                    witness_path(budget.path).unlink()
                else:
                    with closing(sqlite3.connect(budget.path)) as db, db:
                        if name=='recompute':
                            changed=self.evidence(budget.path,'0.01',2,'USD')
                            encoded=canonical_bytes(changed)
                            db.execute('UPDATE opening_reserve SET evidence_json=?, evidence_sha256=?, reserved_nano_usd=?',
                                       (encoded.decode(),sha256(encoded).hexdigest(),validate_evidence(changed)['reserved_nano_usd']))
                            self.assertEqual(read_opening_reserve(db, required=True)['reserved_nano_usd'], 10_000_000)
                        else:
                            db.execute(attacks[name])
                with self.assertRaises(Denied):
                    self.reserve(budget, 1)
                with self.assertRaises(Denied):
                    budget.has_reservation(seat=identifier(1), gid=identifier(('group',1)), op='search')
                with self.assertRaises(Denied):
                    audit(budget.path, budget=budget)
                with closing(sqlite3.connect(budget.path)) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM spend').fetchone(), (0,))

    def test_unbound_or_replaced_opening_ledger_denied(self):
        budget = self.provision()
        with self.assertRaises(Denied):
            audit(budget.path)
        legacy = GroupBudget(budget.path, **self.options)
        with self.assertRaises(Denied):
            self.reserve(legacy, 1)
        replacement = budget.path.with_name('replaced.sqlite')
        replacement.write_bytes(budget.path.read_bytes())
        replacement.replace(budget.path)
        with self.assertRaises(Denied):
            self.reserve(budget, 1)

    def test_ordinary_provision_cannot_write_paid_opening_ledger(self):
        path = self.root / 'missing.sqlite'
        budget = GroupBudget(path, **self.options, opening_evidence_sha256='e'*64)
        with self.assertRaisesRegex(Denied, 'explicit opening'):
            budget.provision()
        self.assertFalse(path.exists())
