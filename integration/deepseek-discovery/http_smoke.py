from pathlib import Path
import http.client,json,socket,sys,threading,time,tempfile
sys.path.insert(0,str(Path(__file__).parent/'candidate/gateway'))
import gateway
from discovery_tickets import Tickets,Ledger
def smoke():
 with tempfile.TemporaryDirectory() as td:
  ledger=Ledger(Path(td)/'ledger',tool_limit_micro=1500,seat_limit_micro=1500,valid_until=time.time()+30);ledger.initialize_for_test()
  calls=[]
  def mock(e,b):
   calls.append((e,b));return {'results':[{'title':'Attention','url':'https://arxiv.org/abs/1706.03762'}],'content':'Public fixture'}
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  origin=f'http://127.0.0.1:{port}'
  threading.Thread(target=gateway.serve,args=({'a'*24:55001,'b'*24:55002},port,origin,True),kwargs={'anonymous':True,'discovery_bridge':Tickets(ledger,mock)},daemon=True).start()
  def req(path,cookie='',body=None):
   c=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
   try:
    c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),{'Cookie':cookie,'Origin':origin,'Content-Type':'application/json'})
    r=c.getresponse();return r.status,r.getheader('Set-Cookie','').split(';')[0],r.read()
   finally:c.close()
  for _ in range(20):
   try:a=req('/')[1];break
   except OSError:time.sleep(.1)
  b=req('/')[1];assert a and b and a!=b
  p='/api/discovery/preview';q='/api/discovery/confirm'
  def prev(s,ac,val):return req(p,s,{'action':ac,'value':val})
  def con(s,t):return req(q,s,{'token':t,'consent':True})
  token=lambda x:json.loads(x[2])['token']
  assert prev('','search','public')[0]==401
  t=token(prev(a,'search','public'));assert con(b,t)[0]==409 and not calls
  assert con(a,t)[0]==200 and con(a,t)[0]==409 and prev(b,'fetch',0)[0]==409
  f=con(a,token(prev(a,'fetch',0)));assert json.loads(f[2])['verified_local_pdf'] is False
  assert con(b,token(prev(b,'search','public')))[0]==409 and len(calls)==2
  print('2 live HTTP seats, mock provider: preview, consent, replay, scope, fetch, persistent cap PASS')
if __name__=='__main__':smoke()
