"""Bounded loopback-only gateway for a supervised, public-document judge demo."""
from pathlib import Path
import collections, hashlib, hmac, http.client, json, secrets, threading, time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SAFE_POST=set('/api/import /api/archive /api/restore /api/revisions /api/supersede /api/demo-import /api/workflow /api/compare-evidence /api/questions /api/search /api/compare /api/bibliography /api/ocr'.split())
LOGIN=b'<meta charset=utf-8><title>Judge login</title><p>Supervised demo. Public sample PDFs only.</p><form method=post action=/session><input type=password name=code required autocomplete=off><button>Enter</button></form>'
class Pool(ThreadingHTTPServer):
    request_queue_size=48
    daemon_threads=True
    def process_request(self,req,addr):
        if not self.pool.acquire(False):
            self.shutdown_request(req);return
        try: super().process_request(req,addr)
        except BaseException: self.pool.release();raise
    def process_request_thread(self,*args):
        try:super().process_request_thread(*args)
        finally:self.pool.release()

def serve(slots,port,origin,local_test=False,anonymous=False,rotate_slot=None,ttl=3600,model_bridge=None,synthesis_bridge=None,asset_root=None):
    if not 1<=len(slots)<=4 or len(set(slots.values()))!=len(slots) or any(len(k)<20 for k in slots):raise ValueError('invalid slots')
    u=urlsplit(origin)
    if u.path or u.query or u.fragment or u.username or u.password or not u.hostname or (u.scheme!='https' and not(local_test and u.scheme=='http' and u.hostname=='127.0.0.1')):raise ValueError('invalid origin')
    if not 1<=ttl<=3600:raise ValueError('invalid ttl')
    lock=threading.Lock();sessions={};attempts=collections.deque(maxlen=40)
    issued=set()
    # Only immutable release JS/CSS; never cache HTML, APIs or a session PDF.
    frozen_assets={}
    allowed="csv-stats.js writing.js reader.js reader.css tracking.js review-panel.js questions.js page-objects.js ocr-panel.js scan-sidecar.js compare-matrix.js workflow-panel.js subscriptions-ui.js research_plan.js research-plan-ui.js bibliography-ui.js walkthrough-ui.js arxiv-import-ui.js frontmatter-ui.js radar.js radar-ui.js".split()
    if asset_root:
        root=Path(asset_root).resolve()
        for name in allowed:
            asset=root/name
            if asset.is_file() and not asset.is_symlink() and asset.stat().st_size<=512*1024:
                frozen_assets["/"+name]=(asset.read_bytes(),"text/css" if name.endswith(".css") else "application/javascript")
    if synthesis_bridge is not None:
        frozen_assets["/synthesis-panel.js"]=(Path(__file__).with_name("synthesis-panel.js").read_bytes(),"application/javascript")
    asset_rate={}

    limits={p:[threading.BoundedSemaphore(2),collections.deque(maxlen=120),0,0] for p in slots.values()}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def setup(self):super().setup();self.connection.settimeout(12)
        def out(self,status,data=b'',mime='text/plain; charset=utf-8',extra=()):
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','private, no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            for k,v in extra:self.send_header(k,v)
            self.end_headers();self.wfile.write(data)
        def handle_one(self):
            post=self.command=='POST'
            if self.headers.get_all('Host')!=[u.netloc] or not self.path.startswith('/') or self.path.startswith('//'):return self.out(403)
            if post and (self.headers.get_all('Origin')!=[origin] or self.headers.get('Sec-Fetch-Site')=='cross-site'):return self.out(403)
            path=self.path.partition('?')[0]
            if '%' in path or '\\' in path or '..' in path:return self.out(400)
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))>1:return self.out(400)
            try:n=int(self.headers.get('Content-Length','0'))
            except ValueError:return self.out(400)
            if not 0<=n<=(24*1024**2 if path=='/api/import' else 65536):return self.out(413)
            if not post and n:return self.out(400)
            now=time.monotonic()
            if anonymous and path=='/session':return self.out(403)
            if path=='/session' and post:
                if n>1024:return self.out(413)
                with lock:
                    if len(attempts)==40 and now-attempts[0]<60:return self.out(429)
                    attempts.append(now)
                code=parse_qs(self.rfile.read(n).decode('ascii','replace')).get('code',[''])[0]
                backend=next((p for k,p in slots.items() if hmac.compare_digest(k.encode(),code.encode())),None)
                if not backend:return self.out(403)
                tok=secrets.token_urlsafe(32)
                with lock:sessions[backend]=(hashlib.sha256(tok.encode()).digest(),now+ttl)
                return self.out(303,extra=[('Location','/'),('Set-Cookie','judge='+tok+'; Path=/; HttpOnly; SameSite=Strict; Max-Age='+str(ttl)+('' if local_test else '; Secure'))])
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));v=cookie.get('judge')
            digest=hashlib.sha256((v.value if v else '').encode()).digest()
            with lock:p=next((k for k,(d,exp) in sessions.items() if exp>now and hmac.compare_digest(d,digest)),None)
            if not p and anonymous and path=='/' and not post:
                # A slot is never reused, even after expiry. Only a complete
                # operator restart with new empty stores replenishes capacity.
                with lock:
                    free=next((v for v in limits if v not in issued),None)
                    if free is not None:
                        tok=secrets.token_urlsafe(32);issued.add(free)
                        sessions[free]=(hashlib.sha256(tok.encode()).digest(),now+ttl)
                if free is None:return self.out(503,'本轮体验席位已满，请联系工作人员开启新一轮。'.encode())
                return self.out(303,extra=[('Location','/'),('Set-Cookie','judge='+tok+'; Path=/; HttpOnly; SameSite=Strict; Max-Age='+str(ttl)+('' if local_test else '; Secure'))])
            if not p:return self.out(200,LOGIN,'text/html; charset=utf-8') if path=='/' and not post else self.out(401)
            if (post and path not in SAFE_POST and not ((path=="/api/verify" and model_bridge is not None or path in ("/api/synthesis/preview","/api/synthesis/confirm") and synthesis_bridge is not None))) or (not post and path.startswith(('/api/arxiv','/api/subscriptions','/api/bilingual','/translation'))):return self.out(403)
            with lock:
                if p not in sessions or p not in limits or sessions[p][1]<=time.monotonic() or not hmac.compare_digest(digest,sessions[p][0]):return self.out(401)
                if not post and path in frozen_assets:
                    aq=asset_rate.setdefault(p,collections.deque(maxlen=360))
                    if len(aq)==360 and now-aq[0]<60:return self.out(429)
                    aq.append(now)
                    static_reply=frozen_assets[path]
                else: static_reply=None
            if static_reply is not None:
                return self.out(200,static_reply[0],static_reply[1]+"; charset=utf-8")
            with lock:
                if p not in limits:return self.out(401)
                sem,q,_,_=limits[p]
                if len(q)==120 and now-q[0]<60:return self.out(429)
                q.append(now)
                if path=='/api/import' and post:
                    if limits[p][2]+n>96*1024**2 or limits[p][3]>=16:return self.out(429)
                    limits[p][2]+=n;limits[p][3]+=1
            if not sem.acquire(timeout=2 if not post else 0):return self.out(429)
            with lock:
                if p not in sessions or sessions[p][1]<=time.monotonic() or not hmac.compare_digest(digest,sessions[p][0]):
                    sem.release();return self.out(401)
            c=http.client.HTTPConnection('127.0.0.1',p,timeout=100)
            try:
                body=self.rfile.read(n) if n else None
                if n and len(body)!=n:return self.out(400)
                if synthesis_bridge is not None and not post and path=="/synthesis-panel.js":
                    return self.out(200,Path(__file__).with_name("synthesis-panel.js").read_bytes(),"application/javascript; charset=utf-8")
                if post and ((path=="/api/verify" and model_bridge is not None) or (synthesis_bridge is not None and path in ("/api/synthesis/preview","/api/synthesis/confirm"))):
                    def backend_json(path,payload=None):
                        con=http.client.HTTPConnection('127.0.0.1',p,timeout=25)
                        try:
                            con.request('POST' if payload is not None else 'GET',path,json.dumps(payload).encode() if payload is not None else None,{'Content-Type':'application/json'})
                            rr=con.getresponse();b=rr.read(4*1024**2+1)
                            if rr.status!=200 or len(b)>4*1024**2:raise ValueError('unavailable')
                            return json.loads(b)
                        finally:con.close()
                    try:
                        if path.startswith("/api/synthesis/"):
                            code,obj=synthesis_bridge.handle(path.rsplit("/",1)[1],digest.hex(),json.loads(body),backend_json)
                            return self.out(code,json.dumps(obj).encode(),"application/json")
                        code,obj=model_bridge.verify(digest.hex(),body,lambda:backend_json('/api/library'),lambda req:backend_json('/api/search',req))
                    except (ValueError,TypeError,KeyError):return self.out(400)
                    return self.out(code,json.dumps(obj).encode(),'application/json')
                c.request(self.command,self.path,body,{'Content-Type':self.headers.get('Content-Type','application/octet-stream')})
                r=c.getresponse();data=r.read(32*1024**2+1)
                if len(data)>32*1024**2:return self.out(502)
                if path=='/' and not post and r.status==200 and synthesis_bridge is not None:
                    data=data.replace(b'</body>',b'<script src="/synthesis-panel.js" defer></script></body>',1)
                if path=='/api/library' and not post and r.status==200 and model_bridge is not None:
                    obj=json.loads(data);obj['deepseek_enabled']=True;data=json.dumps(obj).encode()
                self.out(r.status,data,r.getheader('Content-Type','application/octet-stream'))
            finally:c.close();sem.release()
        def do_GET(self):
            try:self.handle_one()
            except (OSError,http.client.HTTPException):return
        do_POST=do_GET
    s=Pool(('127.0.0.1',port),Handler);s.pool=threading.BoundedSemaphore(32)
    stop=threading.Event()
    def reap():
        while not stop.wait(min(2,ttl/2)):
            if not anonymous or rotate_slot is None:continue
            with lock:
                for p,(_,end) in list(sessions.items()):
                    if time.monotonic()<end:continue
                    oldsem=limits[p][0]
                    if not oldsem.acquire(False):continue
                    if not oldsem.acquire(False):
                        oldsem.release();continue
                    del sessions[p];del limits[p];asset_rate.pop(p,None)
                    try:
                        new=rotate_slot(p)
                        if type(new) is not int or not 0<new<65536 or new in limits:continue
                        issued.discard(new)
                        limits[new]=[threading.BoundedSemaphore(2),collections.deque(maxlen=120),0,0]
                    except Exception:pass # retire failed seat, never return its store
    cleaner=threading.Thread(target=reap,daemon=True);cleaner.start()
    try:s.serve_forever()
    finally:stop.set();cleaner.join();s.server_close()
