import unittest
from pathlib import Path
from engine import parse_pdf
from local_review import collection_review
class TestCollectionReview(unittest.TestCase):
    def test_demo_source_and_empty(self):
        self.assertEqual(collection_review([])['edges'],[])
        docs=[parse_pdf(p.read_bytes(),p.name) for p in (Path(__file__).parent/'demo').glob('*.pdf')]
        r=collection_review(docs);self.assertEqual(len(r['edges']),1)
        self.assertEqual((r['edges'][0]['source_document_name'],r['edges'][0]['page']),('B-cloud.pdf',2))
        self.assertEqual(r['future_mentions'],[])
    def test_retains_speaker_and_limits(self):
        d=parse_pdf((Path(__file__).parent/'sample.pdf').read_bytes(),'sample.pdf')
        text='The authors noted future work may explain this variability.'
        d['chunks'][0]['text']=text
        r=collection_review([d]);self.assertEqual(r['future_mentions'][0]['text'],text)
        self.assertIn('未查证',r['limits'])
