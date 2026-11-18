import tempfile,unittest,hashlib
from pathlib import Path
from group_budget import GroupBudget,Denied
from group_dispatch import ApprovedGroupCaller
class Test(unittest.TestCase):
 def test_single_wire_same_after_commit(self):
    with tempfile.TemporaryDirectory() as d:
        g=GroupBudget(Path(d)/'trial',approval_sha='a'*64,price_sha='b'*64,verified_epoch=5,expiry_epoch=30,clock=lambda:10)
        g.provision()
        seen=[]
        def send(endpoint,wire):
            with g._connect() as c:seen.append(c.execute('SELECT wire_sha FROM spend').fetchone()[0])
            self.assertEqual(hashlib.sha256(wire).hexdigest(),seen[0]);raise TimeoutError()
        a=ApprovedGroupCaller(g,send)
        args=['c'*64,'d'*64,'search','e'*64,{'query':'公开论文','count':5,'need_content':False}]
        self.assertRaises(TimeoutError,a.call,*args);self.assertRaises(Denied,a.call,*args);self.assertEqual(len(seen),1)
