from group_budget import *
import concurrent.futures,itertools,tempfile,unittest
from hashlib import sha256
def id_(x):return sha256(str(x).encode()).hexdigest()
class Budget(unittest.TestCase):
 def setUp(self):
    t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'ledger.db';self.t=100
    self.kw=dict(approval_sha='a'*64,price_sha='b'*64,verified_epoch=90,expiry_epoch=200,input_bound=lambda w:1000,clock=lambda:self.t)
    self.b=GroupBudget(self.path,**self.kw);self.b.provision()
 def charge(self,group,op='search',seat=1,*,b=None,body=None,rid=None):
    wire={'search':{'query':'abc','count':5,'need_content':False},'fetch':{'url':'https://arxiv.org/abs/1706.03762'},'model':{'model':'deepseek-flash','messages':[{'role':'user','content':'public question'}],'max_tokens':512,'stream':False,'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}}}[op]
    return (b or self.b).reserve(seat=id_(seat),gid=id_(group),op=op,rid=id_((group,op,seat,rid)),endpoint=ENDPOINT[op],wire=json.dumps(body if body is not None else wire).encode())
 def test_full_budget(self):
    for g in range(100):self.charge(g,'search')
    with self.b._connect() as c:self.assertEqual(c.execute('SELECT COUNT(*),SUM(nano) FROM spend').fetchone(),(100,75000000))
    self.assertRaises(Denied,self.charge,101)
 def test_replay_across_restart(self):
    self.charge(1);b=GroupBudget(self.path,**self.kw);self.assertRaises(Denied,self.charge,1,b=b,rid='alt')
    self.assertRaises(Denied,self.charge,1,'model',2);self.assertRaises(FileExistsError,b.provision)
 def test_race(self):
    def attempt(i):
        try:self.charge(4,rid=i);return 1
        except Denied:return 0
    with concurrent.futures.ThreadPoolExecutor(8) as ex:self.assertEqual(sum(ex.map(attempt,range(16))),1)
 def test_bad_inputs(self):
    for count in (None,0,16385):
        self.b.input_bound=lambda w:count
        self.assertRaises(Denied,self.charge,1,'model')
    self.t=200;self.assertRaises(Denied,self.charge,2)
 def test_wrong_config(self):
    self.charge(1);b=GroupBudget(self.path,**dict(self.kw,approval_sha='c'*64))
    self.assertRaises(Denied,self.charge,2,b=b)
 def test_per_seat(self):
    b=GroupBudget(self.path.with_name('separate-test.sqlite'),seat_groups=1,**self.kw);b.provision()
    self.charge(1,b=b);self.assertRaises(Denied,self.charge,2,b=b);self.charge(2,seat=2,b=b)
 def test_extra_tool(self):self.assertRaises(Denied,self.charge,99,'model',body={'tools':['web_search']})
