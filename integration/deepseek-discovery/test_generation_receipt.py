"""Exercise confirm with injected response bodies, without a provider or network."""

from hashlib import sha256
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from candidate.gateway.synthesis_bridge import SynthesisBridge


DOC = 'a' * 64
SEAT = 'b' * 64
QUESTION = 'What did the researchers observe in the chamber?'
EXCERPT = 'The pressure remained constant throughout the trial.'
NO_METADATA = {'status': 'unavailable', 'model': None, 'usage': None, 'provider_request_id_sha256': None}


def backend(path, body=None):
    if path == '/api/library':
        return {'docs': [{'id': DOC}]}
    if path == '/api/reader/' + DOC:
        return {'doc': {'id': DOC}, 'pages': [{'page': 2, 'blocks': [{'id': 'p2-b1', 'text': EXCERPT}]}]}
    if path == '/api/workflow':
        return {'query': body['query'], 'schema': 'evidence-desk-workflow-v1', 'papers': [
            {'id': DOC, 'status': 'verified_evidence', 'source_status': 'hash_matched', 'evidence': [
                {'page': 2, 'block': 1, 'part': 1, 'offset': 0, 'text': EXCERPT},
            ]},
        ]}
    raise AssertionError(path)


def reply(*, finish='stop', **metadata):
    claims = [{'text': EXCERPT, 'refs': ['E1']}]
    return {'choices': [{'finish_reason': finish, 'message': {'content': json.dumps({'claims': claims})}}], **metadata}


class GenerationReceipt(unittest.TestCase):
    def run_confirm(self, mode, response):
        caller = Mock()
        if isinstance(response, Exception):
            caller.call.side_effect = response
        else:
            caller.call.return_value = response
        bridge = SynthesisBridge.grouped(SimpleNamespace(caller=caller, group_for=lambda seat, op: 'bound-group'))
        bridge.provider_mode = mode
        code, preview = bridge.handle('preview', SEAT, {'query': QUESTION, 'ids': [DOC]}, backend)
        self.assertEqual(code, 200)
        self.assertNotIn('generation_receipt', preview)
        caller.call.assert_not_called()
        consent = {'token': preview['token'], 'consent': True}
        code, result = bridge.handle('confirm', SEAT, consent, backend)
        caller.call.assert_called_once()
        sent = caller.call.call_args.args
        self.assertEqual(sent, (SEAT, 'bound-group', 'model', sha256(consent['token'].encode()).hexdigest(),
                                json.loads(preview['wire_text'])))
        self.assertEqual(bridge.handle('confirm', SEAT, consent, backend)[0], 409)
        caller.call.assert_called_once()
        return code, result, sent[3]

    def test_approved_attaches_only_redacted_observed_metadata(self):
        native_id = 'synthetic-provider-id-attempt-one'
        usage = {'prompt_tokens': 400, 'completion_tokens': 36, 'total_tokens': 436}
        native = reply(model='DeepSeek-V4.1-Flash-1017', id=native_id, authorization='fixture-private-value',
                       usage={**usage, 'reasoning_tokens': 0}, provider_cost=99, query=QUESTION)
        code, result, local_dispatch_id = self.run_confirm('approved_unix', native)
        self.assertEqual((code, result['status'], result['claim_support']), (200, 'draft_for_review', 'not_assessed'))
        self.assertEqual(result['claims'][0]['citations'][0]['page'], 2)
        self.assertEqual(result['generation_receipt'], {
            'status': 'available', 'model': 'DeepSeek-V4.1-Flash-1017', 'usage': usage,
            'provider_request_id_sha256': sha256(native_id.encode()).hexdigest(),
        })
        wire = json.dumps(result)
        for private in (native_id, QUESTION, 'fixture-private-value', 'provider_cost', 'reasoning_tokens', local_dispatch_id):
            self.assertNotIn(private, wire)

    def test_unconfigured_and_fixture_modes_never_attach_receipts(self):
        native = reply(model='deepseek-flash', id='provider-id', usage={
            'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11,
        })
        for mode in (None, 'offline_fixture'):
            with self.subTest(mode=mode):
                code, result, _ = self.run_confirm(mode, native)
                self.assertEqual((code, result['status']), (200, 'draft_for_review'))
                self.assertNotIn('generation_receipt', result)

    def test_partial_and_unavailable_fields_do_not_infer_from_mode_or_request(self):
        invalid_usages = (
            (10, 1, 111), (True, 1, 2), (10.0, 1, 11), (-1, 2, 1), (10, '1', 11), (1 << 53, 0, 1 << 53),
        )
        for prompt, completion, total in invalid_usages:
            with self.subTest(usage=(prompt, completion, total)):
                native = reply(model='deepseek-flash', id='   ', usage={
                    'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': total,
                })
                code, result, _ = self.run_confirm('approved_unix', native)
                self.assertEqual(code, 200)
                self.assertEqual(result['generation_receipt'], {**NO_METADATA, 'status': 'partial', 'model': 'deepseek-flash'})
        for native in (reply(), reply(model='fixture-private-value'), reply(model='deepseek-flash', offline_fixture=True)):
            code, result, _ = self.run_confirm('approved_unix', native)
            self.assertEqual(code, 200)
            self.assertEqual(result['generation_receipt'], NO_METADATA)

    def test_abstention_keeps_observed_usage_and_transport_error_has_no_receipt(self):
        usage = {'prompt_tokens': 10, 'completion_tokens': 0, 'total_tokens': 10}
        code, result, _ = self.run_confirm('approved_unix', reply(finish='length', usage=usage))
        self.assertEqual((code, result['status'], result['claim_support']), (200, 'abstained', 'not_assessed'))
        self.assertEqual(result['generation_receipt'], {**NO_METADATA, 'status': 'partial', 'usage': usage})
        code, result, _ = self.run_confirm('approved_unix', RuntimeError('fixture-private-value'))
        self.assertEqual(code, 503)
        self.assertNotIn('generation_receipt', result)
        self.assertNotIn('fixture-private-value', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
