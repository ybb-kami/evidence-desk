import unittest,tempfile,concurrent.futures
from pathlib import Path
from discovery_tickets import Tickets,Ledger,Denied
class TicketTests(unittest.TestCase):
 def setUp(self):
    d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.path=Path(d.name)/'budget.db';self.t=[0];self.calls=[]
    self.l=Ledger(self.path,tool_limit_micro=1500,seat_limit_micro=1500,valid_until=20,clock=lambda:self.t[0]);self.l.initialize_for_test()
    def mock(endpoint,body):
        self.calls.append((endpoint,body))
        return {'results':[{'url':'https://arxiv.org/abs/1706.03762','title':'Attention'}],'content':'Mock page'}
    self.b=Tickets(self.l,mock,lambda:self.t[0])
 def test_scope_consent_replay(self):
    p=self.b.preview('A','search','encoders')
    for seat,consent in [('B',True),('A',False)]:self.assertRaises(Denied,self.b.confirm,seat,p['token'],consent)
    self.assertFalse(self.calls);self.b.confirm('A',p['token'],True)
    self.assertRaises(Denied,self.b.confirm,'A',p['token'],True)
    self.assertRaises(Denied,self.b.preview,'B','fetch',0);self.assertRaises(Denied,self.b.preview,'A','fetch','https://arxiv.org/abs/1706.03762')
    p=self.b.preview('A','fetch',0);self.assertEqual(p['body'],{'url':'https://arxiv.org/abs/1706.03762'})
    self.assertEqual(self.b.confirm('A',p['token'],True)['kind'],'web_text_not_pdf_evidence')
 def test_durable_and_concurrent(self):
    keys=[self.b.preview(s,'search','encoders')['token'] for s in ['A','B','C']]
    def trial(sk):
        try:self.b.confirm(sk[0],sk[1],True);return 1
        except Denied:return 0
    with concurrent.futures.ThreadPoolExecutor(3) as pool:self.assertEqual(sum(pool.map(trial,zip('ABC',keys))),2)
    new=Tickets(self.l,self.b.transport,lambda:self.t[0]);p=new.preview('D','search','new')
    self.assertRaises(Denied,new.confirm,'D',p['token'],True);self.assertEqual(len(self.calls),2)
 def test_offline_expiry(self):
    b=Tickets();p=b.preview('A','search','abc');self.assertFalse(p['online']);self.assertRaises(Denied,b.confirm,'A',p['token'],True)
    p=self.b.preview('A','search','abc');self.t[0]=121;self.assertRaises(Denied,self.b.confirm,'A',p['token'],True);self.assertFalse(self.calls)
 def test_timeout_no_refund(self):
    def bad(*_):raise TimeoutError()
    self.b.transport=bad
    for i in range(2):
        p=self.b.preview('A','search','abc');self.assertRaises(TimeoutError,self.b.confirm,'A',p['token'],True)
    p=self.b.preview('A','search','abc');self.assertRaises(Denied,self.b.confirm,'A',p['token'],True)
