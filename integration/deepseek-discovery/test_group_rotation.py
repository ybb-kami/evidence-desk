from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import time
from unittest import TestCase
from unittest.mock import patch

from discovery_tickets import Denied, Tickets
from group_budget import Denied as BudgetDenied, ENDPOINT, GROUPS, GroupBudget, NANO
from group_dispatch import ApprovedGroupCaller


class GroupRotation(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.seat = 'c' * 64
        self.db = Path(temp.name) / 'budget.db'
        self.budget = GroupBudget(self.db, approval_sha='a' * 64, price_sha='b' * 64,
                                  verified_epoch=5, expiry_epoch=30, clock=lambda: 10,
                                  input_bound=lambda wire: 64)
        self.budget.provision()
        self.sent = []

        def send(endpoint, wire):
            self.sent.append((endpoint, json.loads(wire)))
            if endpoint == ENDPOINT['search']:
                return {'results': [{'url': 'https://arxiv.org/abs/1706.03762', 'title': 'Fixture'}]}
            return {'content': 'Offline fixture response.'}

        self.tickets = Tickets.grouped(ApprovedGroupCaller(self.budget, send))
        self.tickets.clock = lambda: 100

    def search(self, terms):
        preview = self.tickets.preview(self.seat, 'search', terms)
        self.tickets.confirm(self.seat, preview['token'], True)
        return preview

    def model(self, question):
        gid = self.tickets.group_for(self.seat, 'model')
        self.tickets.caller.call(self.seat, gid, 'model', sha256(question.encode()).hexdigest(), {
            'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': question}],
            'max_tokens': 512, 'stream': False, 'thinking': {'type': 'disabled'},
            'response_format': {'type': 'json_object'},
        })
        return gid

    def test_new_question_rotates_only_after_same_operation(self):
        first = self.search('first public question')
        old_group = first['group']
        self.assertEqual(self.tickets.group_for(self.seat), old_group)
        self.assertEqual(self.model('first document question'), old_group)
        new_group = self.model('second document question')
        self.assertNotEqual(new_group, old_group)
        self.assertEqual(self.tickets.group_for(self.seat), new_group)

        fetch = self.tickets.preview(self.seat, 'fetch', 0)
        self.assertEqual(fetch['group'], old_group)
        self.assertEqual(self.tickets.confirm(self.seat, fetch['token'], True)['kind'], 'web_text_not_pdf_evidence')
        with self.assertRaises(Denied):
            self.tickets.confirm(self.seat, fetch['token'], True)
        with self.assertRaises(Denied):
            self.tickets.confirm(self.seat, first['token'], True)

        second = self.search('second public question')
        self.assertEqual(second['group'], new_group)
        with sqlite3.connect(self.db) as conn:
            rows = set(conn.execute('SELECT gid,op,nano FROM spend'))
        self.assertEqual(rows, {(old_group, op, NANO[op]) for op in ENDPOINT}
                         | {(new_group, op, NANO[op]) for op in ('model', 'search')})
        self.assertEqual(len(self.sent), 5)

    def test_concurrent_confirm_of_same_group_does_not_rotate_or_retry(self):
        before_reserve = Event()
        release = Event()
        original = self.tickets.caller.call

        def hold_first(seat, gid, op, rid, body):
            if body['query'] == 'first public question':
                before_reserve.set()
                if not release.wait(5):
                    raise TimeoutError('test call never released')
            return original(seat, gid, op, rid, body)

        first = self.tickets.preview(self.seat, 'search', 'first public question')
        with patch.object(self.tickets.caller, 'call', side_effect=hold_first), ThreadPoolExecutor(2) as pool:
            pending = pool.submit(self.tickets.confirm, self.seat, first['token'], True)
            try:
                self.assertTrue(before_reserve.wait(5))
                with self.assertRaises(Denied):
                    self.tickets.confirm(self.seat, first['token'], True)
                second = self.search('second public question')
                self.assertEqual(second['group'], first['group'])
            finally:
                release.set()
            with self.assertRaisesRegex(BudgetDenied, 'duplicate action'):
                pending.result(timeout=5)

        self.assertEqual([body['query'] for _, body in self.sent], ['second public question'])
        next_preview = self.tickets.preview(self.seat, 'search', 'third public question')
        self.assertNotEqual(next_preview['group'], first['group'])
        self.assertEqual(len(self.sent), 1)

    def test_group_limit_applies_to_repeated_search_previews(self):
        groups = {self.search(f'public question {i}')['group'] for i in range(GROUPS)}
        self.assertEqual(len(groups), GROUPS)
        preview = self.tickets.preview('d' * 64, 'search', 'one more question from a new seat')
        with self.assertRaisesRegex(BudgetDenied, 'group cap'):
            self.tickets.confirm('d' * 64, preview['token'], True)
        self.assertEqual(len(self.sent), GROUPS)

    def test_reservation_lookup_checks_owner_and_keeps_ledger_unchanged(self):
        gid = self.tickets.group_for(self.seat, 'model')
        self.assertFalse(self.budget.has_reservation(seat=self.seat, gid=gid, op='model'))
        self.assertEqual(self.model('first document question'), gid)
        before = self.db.read_bytes()
        self.assertTrue(self.budget.has_reservation(seat=self.seat, gid=gid, op='model'))
        self.assertFalse(self.budget.has_reservation(seat=self.seat, gid=gid, op='search'))
        with self.assertRaisesRegex(BudgetDenied, 'wrong seat'):
            self.budget.has_reservation(seat='d' * 64, gid=gid, op='model')
        self.assertEqual(self.db.read_bytes(), before)


class GroupRotationHTTP(TestCase):
    def test_two_new_model_questions_for_one_session(self):
        from test_single_paper_http import Client, HERE, SynthesisBridge, pdf, running_gateway, submitted_count

        data = pdf(['The bronze encoder measured the calibration waveform.'])
        doc = sha256(data).hexdigest()
        questions = ['calibration waveform', 'bronze encoder']
        with TemporaryDirectory(prefix='repeat-model-question-') as temp:
            path = Path(temp)
            now = int(time.time())
            budget = GroupBudget(path / 'budget.db', approval_sha='a' * 64, price_sha='b' * 64,
                                 verified_epoch=now, expiry_epoch=now + 90, input_bound=submitted_count)
            budget.provision()
            sent = []

            def provider(endpoint, wire):
                self.assertEqual(endpoint, ENDPOINT['model'])
                request = json.loads(json.loads(wire)['messages'][1]['content'])
                ref = request['documents'][0][0]['ref']
                sent.append(request['question'])
                draft = {'claims': [{'text': 'Review the calibration observation.', 'refs': [ref]}]}
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(draft)}}]}

            bridge = SynthesisBridge.grouped(Tickets.grouped(ApprovedGroupCaller(budget, provider)))
            bridge.provider_mode = 'offline_fixture'
            with running_gateway(HERE.parents[1] / 'app.py', path / 'workers', bridge) as address:
                client = Client(*address)
                status, _, _ = client.request('/api/import?name=instrument.pdf', data, mime='application/pdf')
                self.assertEqual(status, 200)
                for index, question in enumerate(questions):
                    status, preview = client.json('/api/synthesis/preview', {'query': question, 'ids': [doc]})
                    self.assertEqual((status, preview['status']), (200, 'ready'))
                    self.assertEqual(sent, questions[:index])
                    consent = {'token': preview['token'], 'consent': True}
                    status, response = client.json('/api/synthesis/confirm', consent)
                    self.assertEqual((status, response.get('status')), (200, 'draft_for_review'))
                    self.assertEqual(client.json('/api/synthesis/confirm', consent)[0], 409)
            self.assertEqual(sent, questions)
            with sqlite3.connect(budget.path) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*), COUNT(DISTINCT seat) FROM groups_').fetchone(), (2, 1))
                self.assertEqual(conn.execute('SELECT COUNT(*), SUM(nano) FROM spend').fetchone(), (2, 2 * NANO['model']))
