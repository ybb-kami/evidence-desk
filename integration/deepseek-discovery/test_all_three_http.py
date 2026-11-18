"""Independent loopback E2E: all consented web operations, ONE actual SQLite, no paid network."""
import sys,unittest,tempfile,time,threading,socket,json,http.client,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'candidate'/'gateway'))
import gateway,launcher
from synthesis_bridge import SynthesisBridge
from discovery_tickets import Tickets
from group_dispatch import ApprovedGroupCaller
from group_budget import GroupBudget,ENDPOINT
from audit_group_ledger import audit
class AllThree(unittest.TestCase):
 def test_verified_pdf_and_all_tools_share_single_group(self):
    self.run_scope(2)
 def test_one_pdf_all_tools(self):
    self.run_scope(1)
 def run_scope(self,n):
    app=HERE.parents[1]/"app.py"
    if not app.exists():app=HERE.parent/"evidence-desk"/"app.py"
    pdfs=[Path('/tmp/edesk-eval-'+x+'.pdf') for x in ['1706.03762v1','1810.04805']][:n]
    self.assertTrue(all(p.is_file() for p in pdfs))
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    origin='http://127.0.0.1:'+str(port)
    with tempfile.TemporaryDirectory() as d,launcher.demo_slots(app,Path(d)/'slots',2,origin) as slots:
        db=Path(d)/'single.db';now=int(time.time());g=GroupBudget(db,approval_sha='a'*64,price_sha='b'*64,verified_epoch=now,expiry_epoch=now+120,input_bound=__import__("official_input_bound").submitted_count);g.provision();seen=[]
        def mock(endpoint,wire):
            op=next(k for k,v in ENDPOINT.items() if v==endpoint)
            # Query a distinct connection BEFORE sending: same reserved exact bytes.
            with g._connect() as c:self.assertEqual(c.execute('SELECT wire_sha FROM spend WHERE op=?',(op,)).fetchone()[0],hashlib.sha256(wire).hexdigest())
            seen.append(op)
            if op=='search':return {'results':[{'url':'https://arxiv.org/abs/1706.03762','title':'Attention'}]}
            if op=='fetch':return {'content':'Untrusted web fixture.'}
            p=json.loads(wire);docs=json.loads(p['messages'][1]['content'])['documents'];refs=[x[0]['ref'] for x in docs];return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'claims':[{'text':'Mock.','refs':refs}]})}}]}
        broker=Tickets.grouped(ApprovedGroupCaller(g,mock));synth=SynthesisBridge.grouped(broker)
        threading.Thread(target=gateway.serve,args=(slots,port,origin,True),kwargs={'anonymous':True,'discovery_bridge':broker,'synthesis_bridge':synth,'asset_root':app.parent},daemon=True).start()
        def req(path,cookie='',obj=None,pdf=False):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=35)
            try:
                c.request('POST' if obj is not None else 'GET',path,obj if pdf else json.dumps(obj).encode() if obj is not None else None,{'Content-Type':'application/pdf' if pdf else 'application/json','Origin':origin,'Cookie':cookie})
                r=c.getresponse();raw=r.read();return r.status,r.getheader('Set-Cookie','').split(';')[0],json.loads(raw) if raw and r.getheader('Content-Type','').startswith('application/json') else None
            finally:c.close()
        for _ in range(40):
            try:req('/api/library');break
            except OSError:time.sleep(.05)
        a=req('/')[1];b=req('/')[1];self.assertNotEqual(a,b)
        for f in pdfs:self.assertEqual(req('/api/import?name='+f.name,a,f.read_bytes(),True)[0],200)
        for action,scope,payload in [('search','discovery',{'action':'search','value':'encoder'}),('fetch','discovery',{'action':'fetch','value':0}),('model','synthesis',{'query':'encoder','ids':[hashlib.sha256(f.read_bytes()).hexdigest() for f in pdfs]})]:
            base='/api/'+scope+'/';st,_,pre=req(base+'preview',a,payload);self.assertEqual(st,200);self.assertTrue(pre['online']);token=pre['token'];cons={'token':token,'consent':True}
            self.assertEqual(req(base+'confirm',b,cons)[0],409)
            st,_,out=req(base+'confirm',a,cons);self.assertEqual(st,200,out)
            if action=='model':
                self.assertEqual(out['status'],'draft_for_review'); self.assertEqual({e['doc_id'] for c in out['claims'] for e in c['citations']},set(payload['ids']));self.assertTrue(all(e['page']>=1 and e['block']>=1 for c in out['claims'] for e in c['citations']))
            self.assertEqual(req(base+'confirm',a,cons)[0],409)
        self.assertEqual(seen,['search','fetch','model'])
        result=audit(db);self.assertEqual(result['groups_with_reservations'],1);self.assertEqual(result['reserved_nano_usd'],316687200);print(result)
if __name__=='__main__':unittest.main()
