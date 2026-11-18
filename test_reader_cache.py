import unittest
from reader_cache import ReaderCache
class Store:
    def __init__(self):self.calls=[]
    def records(self,ids,archived=None):
        self.calls.append(ids[0])
        if ids[0]=='missing':return []
        return [{'pages':90,'id':ids[0],'name':'synthetic.pdf','chunks':[{'page':90,'block':1,'text':'Visible final page.'}]}]
class ReaderCacheTest(unittest.TestCase):
    def test_outline_single_page_and_eviction(self):
        s=Store();c=ReaderCache();r=c.get(s,'a','outline')
        self.assertTrue(r['paged']);self.assertNotIn('blocks',r['pages'][-1])
        self.assertEqual(c.get(s,'a','page',90)['blocks'][0]['text'],'Visible final page.')
        self.assertEqual(s.calls,['a'])
        with self.assertRaises(ValueError):c.get(s,'a','page',91)
        c.get(s,'b','outline');c.get(s,'a','page',1)
        self.assertEqual(s.calls,['a','b','a'])
        self.assertIsNone(c.get(s,'missing','outline'))
