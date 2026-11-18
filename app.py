"""Evidence Desk: local single-user PDF workbench; no telemetry or background network."""
from __future__ import annotations
import argparse, hashlib, json, os, re, sqlite3, threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from engine import parse_pdf, find_evidence, duplicates
import provider

HERE = Path(__file__).resolve().parent
UPLOAD_LIMIT = 24 * 1024 * 1024
ID = re.compile(r'[0-9a-f]{64}\Z')
MAX_DOCS, MAX_STORAGE, MAX_TEXT = 50, 128 * 1024**2, 12 * 1024**2

def summary(doc):
    return {k:doc[k] for k in ('id','name','pages','size','textless_pages') if k in doc} | {'indexed_blocks':len(doc['chunks'])}

class Library:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / 'library.sqlite'
        self.lock = threading.Lock()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('CREATE TABLE IF NOT EXISTS docs(id TEXT PRIMARY KEY, record TEXT NOT NULL, pdf BLOB NOT NULL)')
            c.commit()
    def records(self, ids=None):
        with closing(sqlite3.connect(self.db)) as c:
            if ids:
                rows = c.execute('SELECT record FROM docs WHERE id IN ('+','.join('?'*len(ids))+') ORDER BY rowid DESC',ids)
            else:
                rows = c.execute('SELECT record FROM docs ORDER BY rowid DESC')
            return [json.loads(x[0]) for x in rows]
    def pdf(self, docid):
        with closing(sqlite3.connect(self.db)) as c:
            row = c.execute('SELECT pdf FROM docs WHERE id=?',(docid,)).fetchone()
            return row[0] if row else None
    def add(self, data, name):
        digest = hashlib.sha256(data).hexdigest()
        with self.lock, closing(sqlite3.connect(self.db)) as c:
            row = c.execute('SELECT record FROM docs WHERE id=?',(digest,)).fetchone()
            if row:
                doc = summary(json.loads(row[0]))
                return {'doc':doc,'duplicates':[{'id':doc['id'],'name':doc['name'],'reason':'identical_bytes'}],'existing':True}
            count,total,chars = c.execute('SELECT COUNT(*), COALESCE(SUM(length(pdf)),0), COALESCE(SUM(length(record)),0) FROM docs').fetchone()
            if count >= MAX_DOCS or total+len(data)>MAX_STORAGE:
                raise ValueError('本地文库达到容量上限。请换用新的数据目录。')
            doc = parse_pdf(data,name)
            related = duplicates(doc,self.records())
            record = json.dumps(doc,ensure_ascii=False)
            if len(record)+chars>MAX_TEXT: raise ValueError('文库索引达到容量上限，请使用新数据目录。')
            with c: c.execute('INSERT INTO docs VALUES (?,?,?)',(digest,record,data))
            return {'doc':summary(doc),'duplicates':related,'existing':False}

class Bad(Exception):
    def __init__(self, code, msg): self.code,self.msg = code,msg

class Handler(BaseHTTPRequestHandler):
    server_version = 'EvidenceDesk'
    def log_message(self, fmt, *args): pass   # Do not log filenames, queries, or secrets.
    def payload(self, status, data, mime='application/json; charset=utf-8'):
        if not isinstance(data,bytes):
            data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        for k,v in [('Content-Type',mime),('Content-Length',str(len(data))),('Cache-Control','no-store'),
                    ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
                    ('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; frame-ancestors 'self'; form-action 'none'; base-uri 'none'")]:
            self.send_header(k,v)
        self.end_headers()
        self.wfile.write(data)
    def body(self, limit):
        raw=self.headers.get('Content-Length','')
        if not raw.isdecimal() or len(raw)>12: raise Bad(411,'请求长度无效。')
        n=int(raw)
        if n>limit: raise Bad(413,'文件或请求超过本地处理上限。')
        self.connection.settimeout(25)
        data=self.rfile.read(n)
        if len(data)!=n: raise Bad(400,'上传未完成。')
        return data
    def serve(self, post):
        actual_port=self.server.server_port
        known={f'127.0.0.1:{actual_port}',f'localhost:{actual_port}'}
        host=self.headers.get('Host','').lower()
        if host not in known: raise Bad(403,'仅接受本机地址。')
        if post and (self.headers.get('Origin') not in (None,'http://'+host) or self.headers.get('Sec-Fetch-Site')=='cross-site'):
            raise Bad(403,'拒绝跨站请求。')
        u = urlsplit(self.path); path=u.path
        store=self.server.library
        if not post:
            if path in ('/','/index.html','/sample.pdf'):
                filename='sample.pdf' if path=='/sample.pdf' else 'index.html'
                return self.payload(200,(HERE/filename).read_bytes(),'application/pdf' if path=='/sample.pdf' else 'text/html; charset=utf-8')
            if path=='/api/library':
                return self.payload(200,{'docs':[summary(d) for d in store.records()], 'deepseek_enabled':self.server.enable_model and provider.configured()})
            if path.startswith('/pdf/') and ID.fullmatch(path[5:]):
                data=store.pdf(path[5:])
                if data is not None: return self.payload(200,data,'application/pdf')
        elif path=='/api/import':
            name=parse_qs(u.query).get('name',['document.pdf'])[0]
            name=re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]','_',name.replace('\\','/').split('/')[-1])[:160] or 'document.pdf'
            if self.headers.get_content_type() not in ('application/pdf','application/octet-stream'): raise Bad(415,'请上传 PDF 字节。')
            return self.payload(200,store.add(self.body(UPLOAD_LIMIT),name))
        elif path in ('/api/search','/api/verify'):
            if self.headers.get_content_type()!='application/json': raise Bad(415,'需要 JSON 请求。')
            try: req=json.loads(self.body(16384))
            except (ValueError,UnicodeError): raise Bad(400,'无法读取 JSON。')
            if not isinstance(req,dict): raise Bad(400,'请求格式错误。')
            query=req.get('query'); ids=req.get('ids',[])
            if not isinstance(query,str) or not 1<=len(query.strip())<=500: raise Bad(400,'请输入 1 到 500 字的检索词。')
            if not isinstance(ids,list) or len(ids)>MAX_DOCS or any(not isinstance(i,str) or not ID.fullmatch(i) for i in ids): raise Bad(400,'文献选择无效。')
            if path=='/api/verify':
                if req.get('consent') is not True: raise Bad(403,'请先确认可将问题和公开原文摘录发送给 DeepSeek。')
                if not self.server.enable_model or not provider.configured():
                    return self.payload(200,{'status':'not_configured','quotes':[],'answer':'未启用 DeepSeek。离线检索仍可使用。'})
            hits=find_evidence(store.records(ids),query.strip())
            if path=='/api/search': return self.payload(200,{'hits':hits})
            if not hits: return self.payload(200,{'status':'insufficient_evidence','quotes':[],'answer':'没有足够原文证据；没有发起模型请求。'})
            return self.payload(200,provider.summarize(query.strip(),hits))
        raise Bad(404,'没有找到资源。')
    def run_request(self,post):
        try: self.serve(post)
        except Bad as e: self.payload(e.code,{'error':e.msg})
        except ValueError as e: self.payload(400,{'error':str(e)})
        except (ConnectionError,TimeoutError): return
        except RuntimeError: self.payload(502,{'error':'操作未完成。模型或解析组件不可用，请检查本机设置。'})
        except Exception: self.payload(500,{'error':'本机处理失败。请检查存储目录和程序版本。'})
    def do_GET(self): self.run_request(False)
    def do_POST(self): self.run_request(True)

def main():
    p=argparse.ArgumentParser(description='证据台（仅绑定回环地址）')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--data',type=Path,default=HERE/'data')
    p.add_argument('--enable-deepseek',action='store_true',help='允许用户逐次确认向 DeepSeek 发送问题和证据摘录')
    args=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.library=Library(args.data);server.enable_model=args.enable_deepseek
    print(f'证据台已启动：http://127.0.0.1:{server.server_port} （仅本机；Ctrl+C 停止）',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
