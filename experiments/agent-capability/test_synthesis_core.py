import copy,hashlib,json,unittest
from synthesis import prepare,generate,annotate
class Core(unittest.TestCase):
 def setUp(self):
    self.ids=['a'*64,'b'*64]
    self.w={'query':'encoder','schema':'evidence-desk-workflow-v1','papers':[{'id':id,'name':'PRIVATE_FILENAME_'+id,'status':'verified_evidence','source_status':'hash_matched','evidence':[{'body_verified':True,'page':2,'block':3,'part':1,'offset':0,'text':'The encoder was evaluated in this setting.'}]} for id in self.ids]}
    self.p=prepare('encoder',self.ids,self.w)
 def output(self,refs,stop='stop'):
    def fake(body):
        self.assertEqual(body,self.p['wire'])
        rows=[{'text':'Review both encoders.', 'refs':refs}]
        return json.dumps({'choices':[{'finish_reason':stop,'message':{'content':json.dumps({'claims':rows})}}]}).encode()
    return generate(self.p,send=fake)
 def test_private_metadata_stays_local(self):
    wire=self.p['wire']
    for field in [b'PRIVATE_FILENAME_',self.ids[0].encode(),b'"offset"',b'"page"']:self.assertNotIn(field,wire)
    self.assertEqual(hashlib.sha256(wire).hexdigest(),self.p['wire_sha256'])
 def test_grounded_draft(self):
    r=self.output(['E1','E2']);self.assertEqual(r['status'],'draft_for_review');self.assertEqual(r['claim_support'],'not_assessed')
    self.assertEqual([c['doc_id'] for c in r['claims'][0]['citations']],self.ids)
 def test_invented_reference(self):self.assertEqual(self.output(['E2','E99'])['claims'],[])
 def test_single_paper(self):self.assertEqual(self.output(['E2'])['claims'],[])
 def test_incomplete(self):self.assertEqual(self.output(['E1','E2'],'length')['claims'],[])
 def test_bad_source(self):
    self.w['papers'][0]['source_status']='hash_mismatch'
    self.assertNotIn('wire',prepare('encoder',self.ids,self.w))
 def test_empty_paper(self):
    self.w['papers'][0]['evidence']=[]
    self.assertNotIn('wire',prepare('encoder', self.ids,self.w))
 def test_changed_order(self):self.assertNotIn('wire',prepare('encoder',list(reversed(self.ids)),self.w))
 def test_malformed(self):self.assertFalse(generate(self.p,send=lambda _:b'{}')['claims'])
 def test_no_hard_slice_or_dropped_caveat(self):
    for text in ['The effect was positive. '+('x'*630)+' but only in the small cohort.',
                 'The effect was positive, but only in', 'Measurements were reported in Fig.',
                 'only when the temperature was low.']:
        self.w['papers'][0]['evidence'][0]['text']=text
        self.assertNotIn('wire',prepare('encoder',self.ids,self.w))
 def test_no_mid_window(self):
    self.w['papers'][0]['evidence'][0]['offset']=25
    self.assertNotIn('wire',prepare('encoder',self.ids,self.w))
 def test_preserve_qualified_observation_verbatim(self):
    text='The effect was positive. However, it disappeared after adjustment.'
    self.w['papers'][0]['evidence'][0]['text']=text
    self.assertEqual(prepare('encoder',self.ids,self.w)['sources']['E1']['text'],text)
 def test_reference_boundary_verified(self):
    readers={}
    for p in self.w['papers']:
        e=p['evidence'][0]
        readers[p['id']]={'doc':{'id':p['id']},'pages':[{'page':2,'blocks':[{'id':'p2-b3','text':e['text']}]},{'page':3,'blocks':[{'id':'p3-b2','text':'References'}]}]}
    self.assertIn('wire',prepare('encoder',self.ids,annotate(self.w,readers)))
    readers[self.ids[1]]['pages'][1]['page']=1
    self.assertNotIn('wire',prepare('encoder',self.ids,annotate(self.w,readers)))
 def test_missing_boundary_denies(self):
    self.assertNotIn('wire',prepare('encoder',self.ids,annotate(self.w,{})))
if __name__=='__main__':unittest.main()
