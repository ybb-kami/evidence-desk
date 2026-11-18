"""Regression for offline default and failed-closed provider entry."""
from test_acceptance import EvidenceDeskAcceptance as _Fixture
from urllib.error import HTTPError
import unittest

class ConsentTests(unittest.TestCase):
    setUp = _Fixture.setUp
    start_server = _Fixture.start_server
    stop_server = _Fixture.stop_server
    request = _Fixture.request
    read_json = _Fixture.read_json
    import_pdf = _Fixture.import_pdf

    def test_provider_is_off_by_default_and_requires_consent(self):
        self.assertFalse(self.read_json('/api/library')['deepseek_enabled'])
        with self.assertRaises(HTTPError) as no:
            self.read_json('/api/verify',{'query':'verify page numbering','ids':[]})
        self.assertEqual(no.exception.code,403)
        no.exception.close()
        doc=self.import_pdf('sample.pdf')['doc']['id']
        for payload in ({'query':'private','consent':True},{'query':'private','consent':True,'ids':[]},{'query':'q','consent':True,'ids':[doc,doc]}):
            with self.assertRaises(HTTPError) as exc:self.read_json('/api/verify',payload)
            self.assertEqual(exc.exception.code,400)
            exc.exception.close()
        result=self.read_json('/api/verify',{'query':'verify page numbering','ids':[doc],'consent':True})
        self.assertEqual(result['status'],'not_configured')
        self.assertEqual(result['quotes'],[])

del _Fixture
