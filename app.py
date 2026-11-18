"""Evidence Desk: local single-user PDF workbench; no telemetry or background network."""
from __future__ import annotations
import argparse, hashlib, json, re, signal, sqlite3, threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from local_ocr_jobs import LocalOCRJobs, OCRJobError
from reader_cache import ReaderCache
from subscriptions import Subscriptions, SubscriptionError
try:
    from arxiv_import import ArxivImport, ArxivImportError
except ModuleNotFoundError as exc:
    if exc.name!='arxiv_import':raise
    ArxivImport=None
    class ArxivImportError(Exception):
        pass
from engine import parse_pdf, find_evidence, duplicates
from graph import build_graph
from revisions import describe_revision
import discover
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
            c.execute('CREATE TABLE IF NOT EXISTS archive(id TEXT PRIMARY KEY)')
            c.commit()
    def records(self, ids=None, *, archived=False):
        with closing(sqlite3.connect(self.db)) as c:
            # None asks for both states; always bind document IDs separately.
            filters, params = [], []
            if archived is not None:
                filters.append(('' if archived else 'NOT ') + 'EXISTS (SELECT 1 FROM archive WHERE archive.id=docs.id)')
            if ids:
                filters.append('id IN ('+','.join('?'*len(ids))+')')
                params.extend(ids)
            where = ' WHERE ' + ' AND '.join(filters) if filters else ''
            rows = c.execute('SELECT record FROM docs'+where+' ORDER BY rowid DESC',params)
            return [json.loads(x[0]) for x in rows]
    def pdf(self, docid):
        with closing(sqlite3.connect(self.db)) as c:
            row = c.execute('SELECT pdf FROM docs WHERE id=?',(docid,)).fetchone()
            return row[0] if row else None
    def add(self, data, name):
        digest = hashlib.sha256(data).hexdigest()
        with self.lock, closing(sqlite3.connect(self.db)) as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT record, EXISTS (SELECT 1 FROM archive WHERE archive.id=docs.id) '
                            'FROM docs WHERE id=?',(digest,)).fetchone()
            if row:
                doc = summary(json.loads(row[0]))
                result = {'doc':doc,'duplicates':[{'id':doc['id'],'name':doc['name'],'reason':'identical_bytes'}],'existing':True}
                if row[1]:
                    result['archived'] = True
                return result
            count,total,chars = c.execute('SELECT COUNT(*), COALESCE(SUM(length(pdf)),0), COALESCE(SUM(length(record)),0) FROM docs').fetchone()
            if count >= MAX_DOCS or total+len(data)>MAX_STORAGE:
                raise ValueError('本地文库达到容量上限。请换用新的数据目录。')
            doc = parse_pdf(data,name)
            related = duplicates(doc,self.records())
            record = json.dumps(doc,ensure_ascii=False)
            if len(record)+chars>MAX_TEXT: raise ValueError('文库索引达到容量上限，请使用新数据目录。')
            with c: c.execute('INSERT INTO docs VALUES (?,?,?)',(digest,record,data))
            return {'doc':summary(doc),'duplicates':related,'existing':False}
    def set_archived(self, docid, archived):
        with self.lock, closing(sqlite3.connect(self.db)) as c, c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT record FROM docs WHERE id=?',(docid,)).fetchone()
            if not row: raise Bad(404,'所选文献不在本地文库中。')
            if archived:
                c.execute('INSERT OR IGNORE INTO archive VALUES (?)',(docid,))
            else:
                c.execute('DELETE FROM archive WHERE id=?',(docid,))
            return {'doc':summary(json.loads(row[0])),'archived':archived}
    def supersede(self, keep, previous):
        with self.lock, closing(sqlite3.connect(self.db)) as c, c:
            c.execute('BEGIN IMMEDIATE')
            rows = c.execute('SELECT id, record FROM docs WHERE id IN (?,?) '
                             'AND NOT EXISTS (SELECT 1 FROM archive WHERE archive.id=docs.id)',(keep,previous))
            pair = {docid:json.loads(record) for docid,record in rows}
            if len(pair) != 2: raise Bad(409,'两份文献都必须仍在活动文库中。')
            hints = duplicates(pair[keep],[pair[previous]])
            if not any((h['reason']=='identical_extracted_text')
                       or (h.get('arxiv_id') and h.get('new_version') and h.get('existing_version')
                           and h['new_version']!=h['existing_version'] and h['score']>=0.65)
                       for h in hints):
                raise Bad(409,'没有足够的同作品线索。可保留两份，或单独归档指定文献。')
            c.execute('INSERT INTO archive VALUES (?)',(previous,))
            return {'doc':summary(pair[keep]),'archived_doc':summary(pair[previous])}

class Bad(Exception):
    def __init__(self, code, msg): self.code,self.msg = code,msg

class Handler(BaseHTTPRequestHandler):
    server_version = 'EvidenceDesk'
    def log_message(self, fmt, *args): pass   # Do not log filenames, queries, or secrets.
    def payload(self, status, data, mime='application/json; charset=utf-8', attachment=None):
        if not isinstance(data,bytes):
            data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        for k,v in [('Content-Type',mime),('Content-Length',str(len(data))),('Cache-Control','no-store'),
                    ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
                    ('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; frame-ancestors 'self'; form-action 'none'; base-uri 'none'")]:
            self.send_header(k,v)
        if attachment:self.send_header("Content-Disposition","attachment; filename*=UTF-8''"+quote(attachment,safe=""))
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
    def json_request(self):
        if self.headers.get_content_type()!='application/json': raise Bad(415,'需要 JSON 请求。')
        try: req=json.loads(self.body(16384))
        except (ValueError,RecursionError): raise Bad(400,'无法读取 JSON。')
        if not isinstance(req,dict): raise Bad(400,'请求格式错误。')
        return req
    def document_ids(self, req, *fields):
        ids = [req.get(key) for key in fields]
        if set(req)!=set(fields) or any(not isinstance(i,str) or not ID.fullmatch(i) for i in ids):
            raise Bad(400,'文献选择无效。')
        if len(set(ids))!=len(ids): raise Bad(400,'请选择两份不同的文献。')
        return ids
    def serve(self, post):
        actual_port=self.server.server_port
        known={f'127.0.0.1:{actual_port}',f'localhost:{actual_port}'}
        host=self.headers.get('Host','').lower()
        if host not in known: raise Bad(403,'仅接受本机地址。')
        if post and (self.headers.get('Origin') not in (None,'http://'+host) or self.headers.get('Sec-Fetch-Site')=='cross-site'):
            raise Bad(403,'拒绝跨站请求。')
        u = urlsplit(self.path); path=u.path
        if path=='/translation' or path.startswith(('/api/bilingual/','/bilingual/')):
            try:from partner_reader.routes import handle as handle_bilingual
            except ModuleNotFoundError as exc:
                if exc.name not in ('partner_reader','partner_reader.routes'):raise
                raise Bad(503,'此安装未包含本地双语阅读组件。')
            if handle_bilingual(self,post,path,bad=Bad):return
        store=self.server.library
        if path.startswith('/api/arxiv-import') and getattr(self.server,'arxiv_import',None) is None:
            raise Bad(503,'当前安装未提供可选 arXiv 全文组件。')
        if not post:
            assets={'/':('index.html','text/html; charset=utf-8'),
                    '/index.html':('index.html','text/html; charset=utf-8'),
                    '/sample.pdf':('sample.pdf','application/pdf'),
                    '/csv-stats.js':('csv-stats.js','application/javascript; charset=utf-8')}
            for name in ('writing.js','reader.js','reader.css','tracking.js','review-panel.js','questions.js','page-objects.js','ocr-panel.js','scan-sidecar.js','compare-matrix.js','workflow-panel.js','subscriptions-ui.js','research_plan.js','research-plan-ui.js','bibliography-ui.js','walkthrough-ui.js','arxiv-import-ui.js','frontmatter-ui.js','radar.js','radar-ui.js'):
                assets['/'+name]=(name, 'text/css; charset=utf-8' if name.endswith('.css') else 'application/javascript; charset=utf-8')
            if path in assets:
                filename,mime=assets[path]
                if (HERE/filename).is_file(): return self.payload(200,(HERE/filename).read_bytes(),mime)
            if m:=re.fullmatch(r'/api/page-objects/([0-9a-f]{64})/([1-9][0-9]{0,2})',path):
                from page_objects import inspect_page
                data=store.pdf(m[1])
                if data is None:raise Bad(404,'找不到指定文献。')
                return self.payload(200,inspect_page(data,int(m[2])))
            if path.startswith('/api/frontmatter/') and ID.fullmatch(path[17:]):
                from frontmatter import first_page
                rows=store.records([path[17:]],archived=None)
                if not rows:raise Bad(404,'找不到所选文献。')
                return self.payload(200,first_page(rows[0]))
            if path.startswith('/api/reader/') and ID.fullmatch(path[12:]):
                params=parse_qs(u.query); mode=params.get('view',['all'])[0]
                value=params.get('page',['0'])[0]
                if mode not in ('all','outline','page') or not re.fullmatch(r'[0-9]{1,3}',value):
                    raise Bad(400,'阅读参数无效。')
                data=self.server.reader_cache.get(store,path[12:],mode,int(value))
                if data is None:raise Bad(404,'找不到指定文献。')
                return self.payload(200,data)
            if path=='/api/arxiv-import':return self.payload(200,self.server.arxiv_import.status())
            if path.startswith('/api/arxiv-import/preview/'):
                return self.payload(200,self.server.arxiv_import.preview(path[len('/api/arxiv-import/preview/'):]),'application/pdf')
            if path=='/api/subscriptions':return self.payload(200,self.server.subscriptions.status())
            if path=='/api/ocr':return self.payload(200,self.server.ocr_jobs.status())
            if path.startswith('/api/ocr/result/'):
                content,name=self.server.ocr_jobs.result(path[len('/api/ocr/result/'):])
                return self.payload(200,content,'application/pdf',attachment=name)
            if path=='/api/local-review':
                from local_review import collection_review
                return self.payload(200,collection_review(store.records()))
            if path=='/api/library':
                return self.payload(200,{'docs':[summary(d) for d in store.records()], 'deepseek_enabled':self.server.enable_model and provider.configured()})
            if path=='/api/archive':
                return self.payload(200,{'docs':[summary(d) for d in store.records(archived=True)]})
            if path=='/api/graph':
                docs=store.records()
                return self.payload(200,build_graph(docs) if docs else {'nodes':[],'edges':[]})
            if path.startswith('/pdf/') and ID.fullmatch(path[5:]):
                data=store.pdf(path[5:])
                if data is not None: return self.payload(200,data,'application/pdf')
        elif path=='/api/import':
            name=parse_qs(u.query).get('name',['document.pdf'])[0]
            name=re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]','_',name.replace('\\','/').split('/')[-1])[:160] or 'document.pdf'
            if self.headers.get_content_type() not in ('application/pdf','application/octet-stream'): raise Bad(415,'请上传 PDF 字节。')
            return self.payload(200,store.add(self.body(UPLOAD_LIMIT),name))
        elif path in ('/api/archive','/api/restore'):
            docid,=self.document_ids(self.json_request(),'id')
            return self.payload(200,store.set_archived(docid,path=='/api/archive'))
        elif path=='/api/supersede':
            keep,previous=self.document_ids(self.json_request(),'keep_id','archive_id')
            return self.payload(200,store.supersede(keep,previous))
        elif path=='/api/revisions':
            before,after=self.document_ids(self.json_request(),'before_id','after_id')
            pair={d['id']:d for d in store.records([before,after],archived=None)}
            if len(pair)!=2: raise Bad(404,'所选文献不在本地文库中。')
            return self.payload(200,{'before':summary(pair[before]),'after':summary(pair[after]),
                                     **describe_revision(pair[before],pair[after])})
        elif path=='/api/demo-import':
            if self.json_request()!={'confirm':True}:raise Bad(400,'请明确确认将两篇原创虚构资料加入本机文库。')
            records=[]
            for name in ('A-lantern.pdf','B-cloud.pdf'):
                entry=store.add((HERE/'demo'/name).read_bytes(),name)
                if entry.get('archived'):raise Bad(409,'此样例先前已归档，请自行从文库恢复后再试。')
                records.append(entry['doc'])
            return self.payload(200,{'docs':records})
        elif path=='/api/arxiv-import/stage':
            return self.payload(200,self.server.arxiv_import.stage(self.json_request()))
        elif path=='/api/arxiv-import/commit':
            return self.payload(200,self.server.arxiv_import.commit(self.json_request(),store.add))
        elif path=='/api/bibliography':
            from bibliography import export_refs
            req=self.json_request();ids=req.get('ids')
            if set(req)!={'ids'} or not isinstance(ids,list) or not 1<=len(ids)<=8 or any(not isinstance(i,str) or not ID.fullmatch(i) for i in ids) or len(set(ids))!=len(ids):raise Bad(400,'请明确选择 1 至 8 篇文献。')
            by_id={d['id']:d for d in store.records(ids)}
            if len(by_id)!=len(ids):raise Bad(404,'所选文献不存在。')
            return self.payload(200,export_refs([by_id[i] for i in ids]))
        elif path=='/api/subscriptions':
            return self.payload(200,self.server.subscriptions.configure(self.json_request()))
        elif path=='/api/subscriptions/read':
            if self.json_request():raise Bad(400,'清除未读标记不接受其他字段。')
            return self.payload(200,self.server.subscriptions.mark_read())
        elif path=='/api/ocr':
            docid,=self.document_ids(self.json_request(),'id')
            docs=store.records([docid])
            if not docs:raise Bad(404,'请先选择活动文献。')
            doc=docs[0]
            if doc['pages']>20 or len(doc['textless_pages'])!=doc['pages']:
                raise Bad(400,'仅支持不超过 20 页且无文本层的活动扫描件。')
            return self.payload(202,self.server.ocr_jobs.start(docid,doc['name'],store.pdf(docid)))
        elif path in ('/api/questions','/api/compare-evidence','/api/workflow'):
            req=self.json_request(); q=req.get('query');ids=req.get('ids')
            if (set(req)!={'query','ids'} or not isinstance(q,str) or not 1<=len(q.strip())<=500 or
                    not isinstance(ids,list) or not (2 if path=='/api/compare-evidence' else 1)<=len(ids)<=8 or
                    any(not isinstance(i,str) or not ID.fullmatch(i) for i in ids) or len(set(ids))!=len(ids)):
                raise Bad(400,'请填写问题并明确选择 1 至 8 篇本地文献。')
            docs=store.records(ids)
            if len(docs)!=len(ids):raise Bad(404,'未找到所选活动文献。')
            positions={docid:i for i,docid in enumerate(ids)}
            docs.sort(key=lambda doc:positions[doc['id']])
            if path=='/api/workflow':
                from workflow import run_workflow
                return self.payload(200,run_workflow(docs,q.strip(),store.pdf))
            if path=='/api/compare-evidence':
                from compare_evidence import compare_evidence
                return self.payload(200,compare_evidence(docs,q.strip()))
            from evidence_questions import locate_question
            return self.payload(200,locate_question(docs,q.strip()))
        elif path=='/api/tracking':
            req=self.json_request()
            if req.get('consent') is not True: raise Bad(403,'请确认本次访问公开 arXiv 类别。')
            if set(req)!={'category','consent','feedback'} or not isinstance(req['category'],str) or not isinstance(req['feedback'],dict):
                raise Bad(400,'仅接受公开类别、本机反馈和本次确认。')
            from arxiv_tracking import refresh
            result=refresh(req['category'],store.records(),req['feedback'],consent=True)
            if getattr(self.server,'arxiv_import',None) is not None and not result.get('error'):self.server.arxiv_import.remember(result)
            return self.payload(200,result)
        elif path=='/api/discover':
            req=self.json_request()
            if req.get('consent') is not True:
                raise Bad(403,'请先确认可将本次填写的公开检索词发送给 Crossref。')
            if set(req)!={'query','consent'}:
                raise Bad(400,'公开检索只接受手动输入的检索词和本次确认。')
            result=discover.search_public(req['query'])
            return self.payload(400 if (result['error'] or {}).get('code')=='invalid_query' else 200,result)
        elif path in ('/api/search','/api/verify','/api/compare'):
            req=self.json_request()
            query=req.get('query'); ids=req.get('ids',[])
            if not isinstance(query,str) or not 1<=len(query.strip())<=500: raise Bad(400,'请输入 1 到 500 字的检索词。')
            try: query=query.strip().encode('utf-8').decode('utf-8')
            except UnicodeError: raise Bad(400,'检索词包含无效字符。')
            if not isinstance(ids,list) or len(ids)>MAX_DOCS or any(not isinstance(i,str) or not ID.fullmatch(i) for i in ids): raise Bad(400,'文献选择无效。')
            if path=='/api/compare':
                if len(ids)!=2 or ids[0]==ids[1]: raise Bad(400,'请选择两篇不同的文献。')
                by_id={d['id']:d for d in store.records(ids)}
                if len(by_id)!=2: raise Bad(404,'所选文献不在本地文库中。')
                return self.payload(200,{'papers':[
                    {'id':i,'name':by_id[i]['name'],'hits':find_evidence([by_id[i]],query)} for i in ids
                ]})
            if path=='/api/verify':
                if req.get('consent') is not True: raise Bad(403,'请先确认可将问题和公开原文摘录发送给 DeepSeek。')
                if not ids or len(set(ids))!=len(ids) or set(req)!={'query','ids','consent'}:
                    raise Bad(400,'外部核选必须明确选择文献并重新确认，不支持默认全库。')
                scoped=store.records(ids)
                if len(scoped)!=len(ids):raise Bad(404,'所选文献已归档或不存在。')
                if not self.server.enable_model or not provider.configured():
                    return self.payload(200,{'status':'not_configured','quotes':[],'answer':'未启用 DeepSeek。离线检索仍可使用。'})
            hits=find_evidence(scoped if path=='/api/verify' else store.records(ids),query)
            if path=='/api/search': return self.payload(200,{'hits':hits})
            if not hits: return self.payload(200,{'status':'insufficient_evidence','quotes':[],'answer':'没有足够原文证据；没有发起模型请求。'})
            return self.payload(200,provider.summarize(query,hits))
        raise Bad(404,'没有找到资源。')
    def run_request(self,post):
        try: self.serve(post)
        except (Bad,OCRJobError,SubscriptionError,ArxivImportError) as e: self.payload(e.code,{'error':e.msg})
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
    server.subscriptions=Subscriptions(args.data/'arxiv-subscriptions.json')
    server.subscriptions.start()
    server.arxiv_import=ArxivImport() if ArxivImport else None
    server.reader_cache=ReaderCache()
    server.ocr_jobs=LocalOCRJobs()
    def on_term(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,on_term)
    server.library=Library(args.data);server.enable_model=args.enable_deepseek
    print(f'证据台已启动：http://127.0.0.1:{server.server_port} （仅本机；Ctrl+C 停止）',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.subscriptions.close()
        server.ocr_jobs.close()
        server.server_close()

if __name__=='__main__': main()
