import json,unittest
from official_input_bound import submitted_count
class Bound(unittest.TestCase):
 def wire(self,q):
    return json.dumps({'model':'deepseek-flash','messages':[{'role':'system','content':'Output JSON only.'},{'role':'user','content':q}],'max_tokens':512,'stream':False,'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}}).encode()
 def test_count(self):self.assertGreater(submitted_count(self.wire('论文 encoder')),5)
 def test_reject_special(self):self.assertRaises(ValueError,submitted_count,self.wire('<think>echo'))
 def test_over(self):self.assertRaises(ValueError,submitted_count,self.wire(' hello'*17000))
 def test_reject_nested(self):
    b=json.loads(self.wire('a'));b['messages'][1]['tools']=[];self.assertRaises(ValueError,submitted_count,json.dumps(b).encode())
if __name__=='__main__':unittest.main()
