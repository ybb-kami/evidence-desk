import unittest,time,tempfile
from pathlib import Path
from group_budget import GroupBudget,Denied
from official_input_bound import submitted_count
from operator_runtime import runtime
class Factory(unittest.TestCase):
 def test_single_caller_and_actual_tokenizer(self):
  with tempfile.TemporaryDirectory() as d:
   n=int(time.time());cfg=dict(path=Path(d)/'ledger',approval_sha='a'*64,price_sha='b'*64,verified_epoch=n,expiry_epoch=n+60,seat_groups=2)
   with self.assertRaises(Denied):runtime(cfg,'test')
   GroupBudget(**cfg).provision()
   x=runtime(cfg,'test')
   self.assertIsNone(x['model_bridge']);self.assertIs(x['synthesis_bridge'].discovery,x['discovery_bridge']);self.assertIs(x['discovery_bridge'].caller.ledger.input_bound,submitted_count)
