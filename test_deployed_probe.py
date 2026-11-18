import unittest
from tools.check_deployed_instance import origin,probe
from test_acceptance import LocalHTTPFixture
class ProbeTest(LocalHTTPFixture,unittest.TestCase):
    def test_probe_only_reads_and_returns_false_for_missing(self):
        f=self.import_pdf('sample.pdf')['doc']
        r=probe(self.base,{'present':f['id'],'missing':'e'*64},self.opener)
        self.assertFalse(r['complete_official_validation'])
        self.assertTrue(r['expected_files'][0]['source_sha256_matches'])
        self.assertTrue(r['expected_files'][0]['last_page_read'])
        self.assertFalse(r['expected_files'][1]['present_in_active_library'])
        self.assertEqual(len(self.read_json('/api/library')['docs']),1)
        for bad in ('http://example.org','https://user:token@example.org','https://example.org/?token=private'):
            with self.assertRaises(ValueError):origin(bad)
