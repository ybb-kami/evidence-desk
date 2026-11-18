import unittest
from urllib.error import HTTPError
from test_acceptance import LocalHTTPFixture
class Walkthrough(LocalHTTPFixture,unittest.TestCase):
    def test_explicit_sample_import_and_real_trace(self):
        with self.assertRaises(HTTPError) as exc:self.read_json('/api/demo-import',{})
        self.assertEqual(exc.exception.code,400);exc.exception.close()
        self.assertEqual(len(self.read_json('/api/library')['docs']),0)
        docs=self.read_json('/api/demo-import',{'confirm':True})['docs']
        ids=[x['id'] for x in docs]
        self.assertEqual(len(set(ids)),2)
        t=self.read_json('/api/workflow',{'ids':ids,'query':'amber lantern'})
        self.assertEqual((t['coverage'],t['papers_with_evidence'],t['claim_support']),('partial',1,'not_assessed'))
        self.assertTrue(t['papers'][0]['evidence']);self.assertFalse(t['papers'][1]['evidence'])
        self.assertEqual(t['papers'][0]['evidence'][0]['page'],1)
        self.read_json('/api/demo-import',{'confirm':True})
        self.assertEqual(len(self.read_json('/api/library')['docs']),2)
