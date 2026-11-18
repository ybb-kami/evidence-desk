import os, unittest
from pathlib import Path
from frontmatter import first_page
from engine import parse_pdf
from test_acceptance import LocalHTTPFixture
class FrontmatterHTTP(LocalHTTPFixture,unittest.TestCase):
    def test_synthetic_sample_has_no_invented_names(self):
        doc=self.import_pdf('sample.pdf')['doc']
        r=self.read_json('/api/frontmatter/'+doc['id'])
        self.assertFalse(r['authors']);self.assertFalse(r['authors_without_local_affiliation'])
    @unittest.skipUnless(os.getenv('EVIDENCE_DESK_PUBLIC_ATTENTION_V7') and os.getenv('EVIDENCE_DESK_PUBLIC_BERT'),'public samples not supplied')
    def test_public_anchors_without_email_or_name_guessing(self):
        for var in ('EVIDENCE_DESK_PUBLIC_ATTENTION_V7','EVIDENCE_DESK_PUBLIC_BERT'):
            data=Path(os.environ[var]).read_bytes();doc=self.import_pdf('renamed.pdf',data)['doc'];r=self.read_json('/api/frontmatter/'+doc['id'])
            self.assertTrue(r['abstract_candidates']);self.assertTrue(all('@' not in v['name']+v['institution'] for v in r['authors']))
            if 'ATTENTION' in var:
                self.assertTrue(any(v['name']=='Ashish Vaswani' and v['institution']=='Google Brain' and v['block']==3 for v in r['authors']))
                self.assertTrue(any(v['name']=='Illia Polosukhin' for v in r['authors_without_local_affiliation']))
            else:
                self.assertEqual(len(r['authors']),4)
                self.assertTrue(all(v['institution']=='Google AI Language' for v in r['authors']))
