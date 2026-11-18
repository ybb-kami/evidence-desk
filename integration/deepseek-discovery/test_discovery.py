import unittest
from discovery import run
class Contract(unittest.TestCase):
 def setUp(self):
  self.calls=[]; self.debits=[]
  self.url='https://arxiv.org/abs/1706.03762'
 def go(self,a,v,yes=True,urls=(),reserve=None):
  return run(a,v,confirmed=yes,allowed_results=urls,reserve=reserve or self.debits.append,send=lambda e,b:self.calls.append((e,b)) or {'results':[{'url':self.url},{'url':'https://arxiv.org/private'}],'content':'web'})
 def test_flow(self):
  rows=self.go('search','attention')
  self.assertEqual(len(rows),1);self.assertEqual(self.calls[0][1],{'query':'attention','count':5,'need_content':False})
  self.assertEqual(self.go('fetch',self.url,urls=[rows[0]['url']])['kind'],'web_text_not_pdf_evidence');self.assertEqual(self.debits,[750,750])
 def test_refuse(self):
  for a,v,y,u in [('search','x',False,()),('fetch',self.url,True,()),('fetch','https://arxiv.org/private',True,['https://arxiv.org/private'])]:
   with self.assertRaises(ValueError):self.go(a,v,y,u)
  self.assertEqual((self.calls,self.debits),([],[]))
 def test_budget(self):
  def deny(n):raise RuntimeError()
  with self.assertRaises(RuntimeError):self.go('search','x',reserve=deny)
  self.assertEqual(self.calls,[])
