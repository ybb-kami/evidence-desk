"""Local integration on public PDFs; mock model, no credentials or egress."""
import argparse,hashlib,http.client,json,os,secrets,socket,threading,tempfile,time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent/'candidate/gateway'))
import gateway,launcher
from synthesis_bridge import SynthesisBridge

def run(app,paths,fake):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    origin=f'http://127.0.0.1:{port}';calls=[]
    from group_budget import GroupBudget
    from group_dispatch import ApprovedGroupCaller
    from discovery_tickets import Tickets
    temp=tempfile.TemporaryDirectory();now=int(time.time())
    g=GroupBudget(Path(temp.name)/'ledger',approval_sha='a'*64,price_sha='b'*64,verified_epoch=now,expiry_epoch=now+600,input_bound=__import__("official_input_bound").submitted_count)
    g.provision()
    def outbound(endpoint,wire):
        calls.append(endpoint)
        if endpoint.endswith('/search'):return {'results':[{'url':'https://arxiv.org/abs/1706.03762','title':'Attention'}]}
        if endpoint.endswith('/fetch'):return {'content':'mock public page'}
        return json.loads(fake(wire))
    disc=Tickets.grouped(ApprovedGroupCaller(g,outbound));b=SynthesisBridge.grouped(disc)
    def req(method,path,cookie='',body=None,mime='application/json'):
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=45)
        try:
            h={'Origin':origin,'Cookie':cookie,'Content-Type':mime}
            c.request(method,path,body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None,h)
            r=c.getresponse();data=r.read();return r.status,r.getheader('Set-Cookie','').split(';')[0],data
        finally:c.close()
    with tempfile.TemporaryDirectory() as d,launcher.demo_slots(app,Path(d)/'new',2,origin) as slots:
        threading.Thread(target=gateway.serve,args=(slots,port,origin,True),kwargs={'anonymous':True,'synthesis_bridge':b,'discovery_bridge':disc},daemon=True).start()
        for i in range(30):
            try:_,a,_=req('GET','/');break
            except OSError:time.sleep(.1)
        _,other,_=req('GET','/')
        assert a!=other and a and other
        assert b'synthesis-panel.js' in req('GET','/',a)[2]
        assert req('GET','/synthesis-panel.js',a)[0]==200
        ids=[]
        for x in paths:
            raw=x.read_bytes()
            assert req('POST','/api/import?name='+x.name,a,raw,'application/pdf')[0]==200
            ids.append(hashlib.sha256(raw).hexdigest())
        scope={'query':'encoder','ids':ids}
        assert req('POST','/api/synthesis/preview',other,scope)[0]!=200 and not calls
        pre=json.loads(req('POST','/api/synthesis/preview',a,scope)[2])
        assert pre['status']=='ready' and hashlib.sha256(pre['wire_text'].encode()).hexdigest()==pre['wire_sha256'] and not calls,pre
        confirm={'consent':True,'token':pre['token']}
        assert req('POST','/api/synthesis/confirm',other,confirm)[0]==409
        assert req('POST','/api/synthesis/confirm',a,dict(confirm,consent=False))[0]==400 and not calls
        code,_,r=req('POST','/api/synthesis/confirm',a,confirm);result=json.loads(r)
        assert code==200 and len(calls)==1,(code,result)
        assert req('POST','/api/synthesis/confirm',a,confirm)[0]==409 and len(calls)==1
        def post(path,body,cookie=a):return req('POST','/api/discovery/'+path,cookie,body)
        for action,value in [('search','attention'),('fetch',0)]:
            preview=json.loads(post('preview',{'action':action,'value':value})[2])
            consent={'token':preview['token'],'consent':True}
            assert post('confirm',consent,other)[0]==409
            assert post('confirm',consent)[0]==200
            assert post('confirm',consent)[0]==409
        with g._connect() as db:assert db.execute("SELECT COUNT(DISTINCT gid),COUNT(*),SUM(nano) FROM spend").fetchone()==(1,3,316687200)
        print('SAME GROUP + SAME LEDGER: 3 operations, 316687200 nanoUSD reserved using MOCK transport')
        return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--app',type=Path,required=True);p.add_argument('--docs',nargs=2,type=Path,required=True);a=p.parse_args()
    def fake(wire):
        content=json.loads(json.loads(wire)['messages'][1]['content']);rows=content['documents']
        refs=[r[0]['ref'] for r in rows]
        return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'claims':[{'text':'Synthetic review fixture: compare the selected excerpts before making any scientific claim.','refs':refs}]})}}]}).encode()
    result=run(a.app,a.docs,fake);assert result['claims'] and result['claim_support']=='not_assessed',result
    print('HTTP PASS: 2 isolated seats, exact preview digest, once-only explicit consent, local mock send, page/block-linked draft, no real egress.')
