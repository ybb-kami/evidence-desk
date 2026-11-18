"""Same outgoing bytes, different verified source coordinates: deny before charge."""
import copy,hashlib,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'gateway'))
from synthesis_bridge import SynthesisBridge
class TestAnchorBinding(unittest.TestCase):
 def test_moved_source_with_same_wire_consumes_consent_without_reservation(self):
    ids=['a'*64,'b'*64];calls=[]
    class Gate:
        def reserve(self,**kw):calls.append('reserve')
    bridge=SynthesisBridge(Gate(),lambda wire:calls.append('send'))
    loc=[1,1]
    def backend(path,data=None):
        if path=='/api/library':return {'docs':[{'id':id} for id in ids]}
        def e(i):return {'page':loc[i],'block':2,'part':1,'offset':0,'text':'We tested both encoders.'}
        if path=='/api/workflow':return {'schema':'evidence-desk-workflow-v1','query':'encoder','papers':[{'id':id,'status':'verified_evidence','source_status':'hash_matched','evidence':[e(i)]} for i,id in enumerate(ids)]}
        id=path.rsplit('/',1)[-1];i=ids.index(id)
        return {'doc':{'id':id},'pages':[{'page':loc[i],'blocks':[{'id':f'p{loc[i]}-b2','text':e(i)['text']}]},{'page':9,'blocks':[{'id':'p9-b1','text':'References'}]}]}
    args={'ids':ids,'query':'encoder'}
    code,p=bridge.handle('preview','s',args,backend);self.assertEqual(code,200)
    loc[1]=2
    new=bridge._prepare('encoder',ids,backend)
    self.assertEqual(p['wire_sha256'],hashlib.sha256(new['wire']).hexdigest())
    consent={'consent':True,'token':p['token']}
    self.assertEqual(bridge.handle('confirm','s',consent,backend)[0],409)
    loc[1]=1
    self.assertEqual(bridge.handle('confirm','s',consent,backend)[0],409)
    self.assertEqual(calls,[])
if __name__=='__main__':unittest.main()
