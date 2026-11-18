"""Regression checks for the live runner. All provider responses are simulated or disabled."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import deepseek_live as live
from deepseek_stub_http import worker, counts


class LiveRunnerTests(unittest.TestCase):
    def test_real_child_configuration_and_protocol_isolation(self):
        with TemporaryDirectory() as tmp:
            billing = dict(EVIDENCE_BUDGET_DB=str(Path(tmp) / 'shared.sqlite'),
                           DEEPSEEK_INPUT_USD_PER_MILLION='0.1', DEEPSEEK_OUTPUT_USD_PER_MILLION='0.2',
                           DEEPSEEK_SEARCH_USD_PER_CALL='0.01', DEEPSEEK_FETCH_USD_PER_CALL='0.01')
            with patch.dict(os.environ, {**billing, 'DEEPSEEK_API_KEY': ''}):
                for offline in (False, True):
                    with self.subTest(protocol_only=offline), patch.object(subprocess, 'Popen', wraps=subprocess.Popen) as spawn:
                        with live.workbench(live.ROOT, sys.executable, protocol_only=offline) as port:
                            env = spawn.call_args.kwargs['env']
                            for name, value in billing.items():
                                self.assertEqual(env.get(name), None if offline else value)
                            status = live.call(port, '/api/model-status')
                            self.assertEqual(status['model_price_configured'], not offline)
                            self.assertEqual(status['search_price_configured'], not offline)
                            self.assertFalse(status['configured'])

    def test_provider_failure_does_not_count_as_abstaining(self):
        for status in ('unavailable', 'not_configured', 'validation_failed'):
            with self.subTest(status=status), patch.object(live, 'followup', return_value={
                'answer': None, 'gap': 'no answer', 'generation': {'model': live.MODEL, 'status': status}}):
                self.assertFalse(live.missing_case(0, 'document', live.MODEL)['passed'])
        for reply in (None, 'rejected', 'declined'):
            with self.subTest(reply=reply), patch.object(live, 'followup', return_value={
                'answer': None, 'gap': 'no answer', 'generation': {
                    'model': live.MODEL, 'status': 'insufficient_evidence', 'model_reply': reply}}):
                self.assertEqual(live.missing_case(0, 'document', live.MODEL)['passed'], reply == 'declined')

    def test_public_search_uses_http_and_no_model_or_fetch(self):
        with TemporaryDirectory() as tmp, worker(Path(tmp)) as (port, _, log):
            result = live.search_case(port)
            self.assertTrue(result['passed'])
            self.assertEqual(counts(log), {'search': 1})

    def test_an_unrelated_search_result_fails_the_paper_check(self):
        web = dict(provider='DeepSeek Search API', state='ok', items=[{'url': 'https://arxiv.org/abs/9999.99999'}])
        with patch.object(live, 'call', return_value=[{'event': 'final', 'web': web}]):
            self.assertFalse(live.search_case(0)['passed'])


if __name__ == '__main__':
    unittest.main()
