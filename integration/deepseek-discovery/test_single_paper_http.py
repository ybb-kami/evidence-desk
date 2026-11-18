"""Test the active gateway with original backend workers and offline dispatch."""

from contextlib import contextmanager
from hashlib import sha256
import http.client
import json
from pathlib import Path
from queue import Queue
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

import pymupdf


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'candidate' / 'gateway'))
import gateway
from launcher import demo_slots
import synthesis
from synthesis_bridge import SynthesisBridge
from discovery_tickets import Tickets
from group_budget import ENDPOINT, GroupBudget, NANO
from group_dispatch import ApprovedGroupCaller
from official_input_bound import submitted_count


@contextmanager
def running_gateway(app, folder, bridge):
    with socket.socket() as pending:
        pending.bind(('127.0.0.1', 0))
        port = pending.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    ready = Queue(maxsize=1)

    class TestPool(gateway.Pool):
        def serve_forever(self, *args, **kwargs):
            ready.put(self)
            return super().serve_forever(*args, **kwargs)

    with demo_slots(app, folder, 2, origin) as slots, patch.object(gateway, 'Pool', TestPool):
        worker = Thread(target=gateway.serve, args=(slots, port, origin), kwargs={
            'local_test': True, 'anonymous': True, 'synthesis_bridge': bridge, 'asset_root': app.parent,
        }, daemon=True)
        worker.start()
        server = ready.get(timeout=10)
        try:
            yield port, origin
        finally:
            server.shutdown()
            worker.join(timeout=10)


class Client:
    def __init__(self, port, origin):
        self.port, self.origin, self.cookie = port, origin, ''
        status, headers, _ = self.request('/')
        if status != 303:
            raise RuntimeError('anonymous gateway seat unavailable')
        self.cookie = headers['Set-Cookie'].partition(';')[0]

    def request(self, path, data=None, *, mime='application/json'):
        headers = {'Origin': self.origin, 'Cookie': self.cookie, 'Content-Type': mime}
        if isinstance(data, dict):
            data = json.dumps(data).encode()
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        try:
            conn.request('POST' if data is not None else 'GET', path, body=data, headers=headers)
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()

    def json(self, path, data):
        status, headers, raw = self.request(path, data)
        return status, json.loads(raw) if headers.get_content_type() == 'application/json' else None


def pdf(*pages):
    with pymupdf.open() as original:
        for lines in pages:
            page = original.new_page()
            for i, line in enumerate(lines):
                page.insert_text((50, 70 + 32 * i), line, fontsize=12)
        return original.tobytes()


class SinglePaperHTTP(unittest.TestCase):
    def test_single_and_multi_consent_share_the_original_ledger(self):
        originals = [
            pdf(['Bench observation notes.'], ['The model was trained with four Cobalt A90 GPUs on one machine.']),
            pdf(['The bronze encoder measured the calibration waveform.'], ['References', '[1] Bibliography sample.']),
            pdf(['The quartz encoder measured the calibration waveform.'], ['References', '[1] Bibliography sample.']),
            pdf(['General description.'], ['References', '[1] The indigo encoder measured the calibration waveform.']),
        ]
        ids = [sha256(data).hexdigest() for data in originals]
        with TemporaryDirectory(prefix='single-paper-') as temp:
            path = Path(temp)
            now = int(time.time())
            ledger = GroupBudget(path / 'ledger.sqlite', approval_sha='a'*64, price_sha='b'*64,
                                 verified_epoch=now, expiry_epoch=now+90, input_bound=submitted_count)
            ledger.provision()
            sent = []

            def provider(endpoint, wire):
                self.assertEqual(endpoint, ENDPOINT['model'])
                with ledger._connect() as conn:
                    self.assertIn((sha256(wire).hexdigest(),), conn.execute('SELECT wire_sha FROM spend').fetchall())
                payload = json.loads(wire)
                user = json.loads(payload['messages'][1]['content'])
                self.assertEqual(set(user), {'question', 'documents'})
                self.assertFalse(any(ident.encode() in wire for ident in ids))
                sent.append((wire, user))
                refs = [entries[0]['ref'] for entries in user['documents']]
                claims = [{'text': 'Review the selected calibration waveform.', 'refs': refs}]
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'claims': claims})}}]}

            discovery = Tickets.grouped(ApprovedGroupCaller(ledger, provider))
            bridge = SynthesisBridge.grouped(discovery)
            bridge.provider_mode = 'offline_fixture'
            app = HERE.parents[1] / 'app.py'
            with running_gateway(app, path / 'workers', bridge) as address:
                a, b = Client(*address), Client(*address)
                for index, client in enumerate((a, b, b, a)):
                    status, _, raw = client.request('/api/import?name=' + quote(f'field-{index}.pdf'), originals[index], mime='application/pdf')
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(raw)['doc']['id'], ids[index])
                preview_path, confirm_path = '/api/synthesis/preview', '/api/synthesis/confirm'
                query = 'calibration waveform'
                self.assertEqual(b.json(preview_path, {'ids': [ids[0]], 'query': query})[0], 400)
                status, missing = a.json(preview_path, {'ids': [ids[3]], 'query': query})
                self.assertEqual((status, missing['status'], missing['online']), (200, 'insufficient_evidence', False))
                self.assertNotIn('token', missing)
                self.assertEqual(sent, [])
                for client, other, selected, expected_pages in ((a, b, ids[:1], {2}), (b, a, ids[1:3], {1})):
                    question = 'What hardware was used for training?' if len(selected) == 1 else query
                    status, draft = client.json(preview_path, {'ids': selected, 'query': question})
                    self.assertEqual((status, draft['status'], draft['online']), (200, 'ready', True))
                    self.assertEqual(draft['provider_mode'], 'offline_fixture')
                    self.assertEqual(draft.get('mode'), 'single' if len(selected) == 1 else None)
                    self.assertEqual({source['doc_id'] for source in draft['sources'].values()}, set(selected))
                    self.assertEqual({source['page'] for source in draft['sources'].values()}, expected_pages)
                    consent = {'token': draft['token'], 'consent': True}
                    before = len(sent)
                    self.assertEqual(other.json(confirm_path, consent)[0], 409)
                    self.assertEqual(client.json(confirm_path, {**consent, 'consent': False})[0], 400)
                    self.assertEqual(client.json(confirm_path, {**consent, 'ids': selected})[0], 400)
                    self.assertEqual(len(sent), before)
                    status, result = client.json(confirm_path, consent)
                    self.assertEqual((status, result['status'], result['claim_support']), (200, 'draft_for_review', 'not_assessed'))
                    self.assertEqual(result.get('provider_mode'), 'offline_fixture')
                    self.assertEqual(result.get('mode'), draft.get('mode'))
                    self.assertEqual(sent[-1][0], draft['wire_text'].encode())
                    self.assertEqual(sent[-1][1]['question'], question)
                    self.assertEqual(len(sent[-1][1]['documents']), len(selected))
                    evidence = result['claims'][0]['evidence']
                    self.assertEqual({item['doc_id'] for item in evidence}, set(selected))
                    self.assertTrue(all(item in draft['sources'].values() for item in evidence))
                    for item in evidence:
                        status, _, data = client.request('/pdf/' + item['doc_id'])
                        self.assertEqual((status, sha256(data).hexdigest()), (200, item['doc_id']))
                        with pymupdf.open(stream=data, filetype='pdf') as original:
                            self.assertIn(item['text'], original[item['page']-1].get_text())
                    self.assertEqual(client.json(confirm_path, consent)[0], 409)
                    self.assertEqual(len(sent), before+1)
                with ledger._connect() as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*),COUNT(DISTINCT gid),SUM(nano) FROM spend').fetchone(),
                                     (2, 2, 2*NANO['model']))

    def test_single_rejects_missing_or_unbound_model_references(self):
        item = {'mode': 'single', 'wire': b'{}', 'sources': {
            'E1': {'doc_id': 'a'*64, 'page': 2, 'block': 3, 'part': 1, 'offset': 0,
                   'text': 'Synthetic source sentences are useful for testing.'},
        }}
        for claims in ([], [{'text': 'An unbound assertion.', 'refs': ['E9']}]):
            contents = json.dumps({'claims': claims})
            reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': contents}}]}
            self.assertEqual(synthesis.generate(item, lambda _: json.dumps(reply).encode()),
                             {'status': 'abstained', 'mode': 'single', 'claim_support': 'not_assessed', 'claims': []})

    def test_training_retry_is_single_and_does_not_substitute_other_conditions(self):
        ids = ['a'*64, 'b'*64]
        queries = []
        fake = 'The model was trained with no GPUs. Inference ran on four GPUs.'

        def backend(path, opts=None):
            if path == '/api/library':
                return {'docs': [{'id': pdf_id} for pdf_id in ids]}
            if path.startswith('/api/reader/'):
                return {'doc': {'id': path.rsplit('/', 1)[-1]}, 'pages': [
                    {'page': 1, 'blocks': [{'id': 'p1-b1', 'text': fake}]},
                ]}
            self.assertEqual(path, '/api/workflow')
            query, selected = opts['query'], opts['ids']
            queries.append(query)
            matched = query == 'training hardware'
            return {'schema': 'evidence-desk-workflow-v1', 'query': query, 'papers': [
                {'id': paper, 'status': 'verified_evidence' if matched else 'no_match', 'source_status': 'hash_matched',
                 'evidence': [{'page': 1, 'block': 1, 'part': 1, 'offset': 0, 'text': fake}] if matched else []}
                for paper in selected
            ]}

        bridge = SynthesisBridge()
        question = 'What hardware was used for training?'
        for selected, ask, expected in ((ids[:1], question, [question, 'training hardware']),
                                        (ids, question, [question]),
                                        (ids[:1], 'What hardware was used for inference?', ['What hardware was used for inference?']),
                                        (ids[:1], 'What hardware was used for training on Alpaca?', ['What hardware was used for training on Alpaca?'])):
            with self.subTest(ask=ask, count=len(selected)):
                queries.clear()
                code, preview = bridge.handle('preview', 'c'*64, {'ids': selected, 'query': ask}, backend)
                self.assertEqual((code, preview['status']), (200, 'insufficient_evidence'))
                self.assertNotIn('token', preview)
                self.assertEqual(queries, expected)
