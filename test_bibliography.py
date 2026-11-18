import unittest
from bibliography import export_refs
class BibliographyTest(unittest.TestCase):
    def test_realistic_fields_and_refusal(self):
        d={'id':'1'*64,'name':'renamed.pdf','arxiv':{'id':'1706.03762','version':7},'bibliographic_identity':{'title':'Attention Is All You Need'}}
        z=export_refs([d,{**d,'id':'2'*64,'arxiv':None}]);e=z['entries'][0]['csl']
        self.assertEqual(e['URL'],'https://arxiv.org/abs/1706.03762v7');self.assertNotIn('author',e);self.assertNotIn('issued',e)
        self.assertEqual(len(z['skipped']),1)
    def test_no_untrusted_links(self):
        d={'id':'3'*64,'name':'fake','arxiv':{'id':'https://example.com'},'bibliographic_identity':{'title':'A','doi':'https://example.com'}}
        self.assertEqual(export_refs([d])['entries'],[])
