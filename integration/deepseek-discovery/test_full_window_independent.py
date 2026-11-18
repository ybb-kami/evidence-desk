"""QA gate, independent expected literals; no provider calls, no key or external docs."""
import json,unittest,tempfile,time,hashlib
from pathlib import Path
from group_budget import GroupBudget,ENDPOINT,Denied
from official_input_bound import submitted_count
def H(s):return hashlib.sha256(s.encode()).hexdigest()
class FullWindow(unittest.TestCase):
 def test_exact_rates_and_third_model_denied(self):
    with tempfile.TemporaryDirectory() as d:
        now=int(time.time());g=GroupBudget(Path(d)/'trial',approval_sha=H('fixture'),price_sha=H('mock-priced'),verified_epoch=now,expiry_epoch=now+120,input_bound=submitted_count);g.provision()
        wire={'model':{'model':'deepseek-flash','messages':[{'role':'system','content':'Output JSON only.'},{'role':'user','content':'Give the word test in JSON.'}],'max_tokens':512,'stream':False,'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}},'search':{'query':'public','count':5,'need_content':False},'fetch':{'url':'https://arxiv.org/abs/1706.03762'}}
        def call(i,op):return g.reserve(seat=H('seat'),gid=H(str(i)),rid=H(str(i)+op),op=op,endpoint=ENDPOINT[op],wire=json.dumps(wire[op]).encode())
        result=call(0,'model');self.assertEqual(result['max_nano_usd'],315187200)
        call(0,'search');call(0,'fetch');call(1,'model');call(1,'search');call(1,'fetch')
        self.assertRaises(Denied,call,2,'model')
        with g._connect() as c:self.assertEqual(c.execute('SELECT COUNT(*), SUM(nano) FROM spend').fetchone(),(6,633374400))
