"""Four isolated real app workers, four parallel first/repeat page loads; loopback only."""
import concurrent.futures as cf, http.client,socket,sys,tempfile,threading,time,re
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'gateway'))
import gateway,launcher
from synthesis_bridge import SynthesisBridge
def run(app):
 with socket.socket() as tmp:tmp.bind(('127.0.0.1',0));port=tmp.getsockname()[1]
 origin='http://127.0.0.1:'+str(port)
 def fetch(path,cookie=''):
  c=http.client.HTTPConnection('127.0.0.1',port,timeout=12)
  try:
   c.request('GET',path,headers={'Cookie':cookie});r=c.getresponse();return r.status,r.getheader('Set-Cookie','').split(';')[0],r.read()
  finally:c.close()
 with tempfile.TemporaryDirectory() as td,launcher.demo_slots(app,Path(td)/'seats',4,origin) as slots:
  threading.Thread(target=gateway.serve,args=(slots,port,origin,True),kwargs=dict(anonymous=True,synthesis_bridge=SynthesisBridge(),asset_root=app.parent),daemon=True).start()
  for _ in range(20):
   try:fetch('/api/library');break
   except OSError:time.sleep(.1)
  cookies=[fetch('/')[1] for _ in range(4)];assert len(set(cookies))==4 and all(cookies)
  assert fetch('/')[0]==503 and fetch('/reader.js')[0]==401
  out=Counter()
  def load(k):
   code,_,html=fetch('/',k);assert code==200
   assets=set(x.decode() for x in re.findall(rb'(?:src|href)="(/[^"]+\.(?:js|css))"',html));assert len(assets)>=19 and '/synthesis-panel.js' in assets
   paths=sorted(assets)+['/api/library']*5
   with cf.ThreadPoolExecutor(6) as pool:
    results=[r[0] for r in pool.map(lambda path:fetch(path,k),paths)]
   assert all(c==200 for c in results),Counter(results)
   return results
  for _ in range(3):
   with cf.ThreadPoolExecutor(4) as pool:
    for statuses in pool.map(load,cookies):out.update(statuses)
  print(dict(out),'four seats × three loads; unauthenticated asset rejected; paid disabled')
if __name__=='__main__':run(Path(sys.argv[1]).resolve())
