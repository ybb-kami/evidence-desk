"""Commissioning tests. Every financial statement here is an invented fixture for this suite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from group_budget import Denied, ENDPOINT, GroupBudget, VERSION
from group_dispatch import ApprovedGroupCaller
from opening_reserve import canonical_bytes, inspect_evidence, provision_opening, validate_evidence, witness_path


class FirstOpening(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root/'active.sqlite'
        self.proofs = self.root/'private-proofs'
        self.proofs.mkdir()
        self.record = dict(kind='request_cost_upper',runs=['direct-test','app-smoke'],
                           source_sha256=self.source(b'TEST-ONLY: two attempts, 1200 billable output tokens each, no other calls.'),
                           attempts_upper=2, includes_all_attempts_and_pending=True, model_release=VERSION,
                           input_tokens_per_attempt_upper=2**20, output_tokens_per_attempt_upper=1200,
                           input_nano_usd_per_token_upper=300, output_nano_usd_per_token_upper=1200,
                           other_nano_usd_per_attempt_upper=0, pricing_source_sha256=self.source(b'TEST-ONLY: tariff fixture.'))
        self.evidence = dict(schema='evidence-desk-opening-v1', runs=['direct-test','app-smoke'],
                             ledger_path_sha256=sha256(os.fsencode(self.path)).hexdigest(),
                             opening_fx_cny_per_usd_upper='8', records=[self.record])
        self.options = dict(approval_sha='a'*64,price_sha='b'*64,verified_epoch=10,expiry_epoch=100,
                            seat_groups=4,clock=lambda:22, cap_policy=dict(cny_per_usd_upper='8.00',
                                fx_verified_epoch=10,fx_expiry_epoch=100,fx_source_sha256='c'*64))

    def source(self, data):
        digest = sha256(data).hexdigest()
        (self.proofs/(digest+'.proof')).write_bytes(data)
        return digest

    def budget(self):
        return GroupBudget(self.path, **self.options, opening_evidence_sha256=sha256(canonical_bytes(self.evidence)).hexdigest())

    def open(self, **options):
        flags = dict(no_existing_project_ledger=True, writers_stopped=True, proof_dir=self.proofs)
        return provision_opening(self.budget(), self.evidence, **{**flags, **options})

    def test_conditional_bound_and_unknown_attempts(self):
        verified = inspect_evidence(self.evidence, self.proofs)
        self.assertEqual(verified['reserved_nano_usd'], 632025600)
        self.assertEqual(verified['historical_requests'], 2)
        for changes in ({'attempts_upper':1}, {'attempts_upper':None}, {'output_tokens_per_attempt_upper':None},
                        {'includes_all_attempts_and_pending':False}, {'input_tokens_per_attempt_upper':8192},
                        {'model_release':'unverified'}, {'runs':['direct-test']},
                        {'input_nano_usd_per_token_upper':299}):
            evidence = deepcopy(self.evidence)
            evidence['records'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(Denied):
                validate_evidence(evidence)
        more = deepcopy(self.evidence)
        more['records'][0]['attempts_upper'] = 3
        self.assertEqual(validate_evidence(more)['reserved_nano_usd'], 948038400)

    def test_preflight_and_bad_evidence_never_create_a_ledger(self):
        for flags in ({'writers_stopped':False},{'no_existing_project_ledger':False}):
            with self.assertRaises(Denied):
                self.open(**flags)
        (self.proofs/(self.record['source_sha256']+'.proof')).write_bytes(b'changed')
        with self.assertRaisesRegex(Denied, 'digest mismatch'):
            self.open()
        self.assertFalse(self.path.exists())
        self.assertFalse(witness_path(self.path).exists())

    def test_concurrent_initialize_replay_and_no_private_send_authority_for_history(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            outcomes = list(pool.map(lambda _:self.open()['action'], range(5)))
        self.assertEqual(outcomes.count('opened_with_historical_upper_bound'),1)
        self.assertEqual(outcomes.count('already_opened'),4)
        budget = self.budget()
        with self.assertRaisesRegex(Denied,'private broker'):
            ApprovedGroupCaller(budget, lambda *_:self.fail('unexpected sender'))
        budget.reserve(seat='a'*64,gid='b'*64,op='search',rid='c'*64,endpoint=ENDPOINT['search'],
                       wire=b'{"query":"fixture","count":1,"need_content":false}')
        self.assertEqual(self.open()['action'],'already_opened')
        with closing(budget._connect()) as db:
            self.assertEqual(db.execute('SELECT count(*),sum(nano) FROM spend').fetchone(),(1,750000))
            self.assertEqual(db.execute('SELECT count(*) FROM opening_reserve').fetchone(),(1,))
            self.assertEqual(budget.opening_totals(db,verify_live=True),(632025600,2))
        self.path.unlink()
        with self.assertRaises(Denied):
            self.open()
        self.assertFalse(self.path.exists())

    def test_existing_database_wal_and_wrong_target_never_adopted(self):
        self.path.write_bytes(b'previous accounting')
        with self.assertRaises(Denied):
            self.open()
        self.assertEqual(self.path.read_bytes(),b'previous accounting')
        self.path.unlink()
        journal = Path(str(self.path)+'-wal')
        journal.write_bytes(b'prior')
        with self.assertRaises(Denied):
            self.open()
        journal.unlink()
        self.evidence['ledger_path_sha256'] = 'e'*64
        with self.assertRaises(Denied):
            self.open()
        self.assertFalse(self.path.exists())

    def test_interrupted_initialization_stays_closed(self):
        with patch('legacy_ledger.stamp_opening_policy', side_effect=Denied('fixture interruption')):
            with self.assertRaises(Denied):
                self.open()
        self.assertTrue(witness_path(self.path).is_file())
        with self.assertRaises(Denied):
            self.open()

    def test_cli_prepares_redacted_manifest_without_touching_the_target(self):
        records = self.root/'input.json'
        records.write_text(json.dumps([self.record]))
        manifest = self.root/'opening.json'
        script = str(Path(__file__).with_name('opening_reserve.py'))
        args = [sys.executable,script,'prepare','--ledger',str(self.path),'--records',str(records),
                '--upper-rate','8','--proof-dir',str(self.proofs),'--out',str(manifest)]
        result = subprocess.run(args, check=True, capture_output=True)
        self.assertEqual(json.loads(result.stdout)['evidence_sha256'],sha256(manifest.read_bytes()).hexdigest())
        self.assertEqual(manifest.stat().st_mode & 0o777,0o600)
        self.assertFalse(self.path.exists())
        self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)

    def test_approved_socket_contract_with_fake_sender_retains_opening_bound(self):
        import threading
        import time
        from operator_runtime import private_runtime
        from private_transport import UnixProviderBroker, fake_response
        from audit_group_ledger import audit

        now = int(time.time())
        self.options.pop('clock')
        self.options.update(verified_epoch=now, expiry_epoch=now+120)
        self.options['cap_policy'].update(fx_verified_epoch=now, fx_expiry_epoch=now+120)
        self.open()
        ledger = self.budget()
        config = dict(self.options, path=str(self.path), opening_evidence_sha256=ledger.opening_evidence_sha256)
        endpoint = self.root/'fixture.sock'
        sends = []
        def fixture(destination, wire):
            sends.append(destination)
            return fake_response(destination, wire)
        with UnixProviderBroker(endpoint, allow_uids=[os.getuid()], ledger=ledger,
                                provider_mode='approved_unix', approved_sender=fixture) as broker:
            server = threading.Thread(target=broker.serve_forever,daemon=True)
            server.start()
            try:
                bridges = private_runtime(config,socket_path=endpoint,broker_uid=os.getuid(),provider_mode='approved_unix')
                caller = bridges['discovery_bridge'].caller
                caller.call('a'*64,'b'*64,'search','c'*64,dict(query='public fixture',count=1,need_content=False))
                self.assertEqual(sends,[ENDPOINT['search']])
                self.assertIs(bridges['synthesis_bridge'].discovery,bridges['discovery_bridge'])
            finally:
                broker.close()
                server.join(5)
        status = audit(self.path,budget=ledger)
        self.assertEqual(status['total_reserved_nano_usd'],632775600)
        self.assertEqual(status['provider_invoiced_amount'],'not_observed')

    def test_native_usage_reconciles_provider_response_separately_for_both_runs(self):
        rows = []
        for name in self.evidence['runs']:
            native = dict(id='fixture-'+name,model='deepseek-flash',usage=dict(prompt_tokens=400,completion_tokens=80,total_tokens=480))
            rows.append(dict(kind='provider_usage_upper',runs=[name],source_sha256=self.source(canonical_bytes(native)),
                             attempts_upper=1,includes_all_attempts_and_pending=True,usage_covers_all_billable_tokens=True,
                             model_release=VERSION,prompt_tokens=400,completion_tokens=80,
                             input_nano_usd_per_token_upper=300,output_nano_usd_per_token_upper=1200,
                             other_nano_usd_per_attempt_upper=0,pricing_source_sha256=self.record['pricing_source_sha256']))
        self.evidence['records'] = rows
        self.assertEqual(inspect_evidence(self.evidence,self.proofs)['reserved_nano_usd'],432000)
        rows[1]['prompt_tokens'] = 399
        with self.assertRaisesRegex(Denied,'usage differs'):
            self.open()
        rows[1]['prompt_tokens'] = 400
        rows[1]['source_sha256'] = self.source(canonical_bytes(dict(id='fixture-direct-test',model=VERSION,
                                                      usage=dict(prompt_tokens=400,completion_tokens=80,total_tokens=480))))
        with self.assertRaisesRegex(Denied,'duplicate provider request'):
            self.open()
        self.assertFalse(self.path.exists())
