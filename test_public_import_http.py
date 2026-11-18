"""Local HTTP integration with mocked arXiv feed and PDF. Never contacts arXiv."""
from contextlib import ExitStack
import hashlib,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import ProxyHandler,Request,build_opener
from urllib.error import HTTPError
from app import Handler,Library,ThreadingHTTPServer
from arxiv_import import ArxivImport
from test_arxiv_import import GOOD,pdf_bytes,record,result
class PublicImportHTTP(unittest.TestCase):
    def test_download_preview_then_confirm(self):
        data=pdf_bytes(); downloader=lambda _:data;loc=build_opener(ProxyHandler({}))
        with tempfile.TemporaryDirectory() as folder,ThreadingHTTPServer(('127.0.0.1',0),Handler) as srv:
            srv.library=Library(Path(folder));srv.enable_model=False;srv.arxiv_import=ArxivImport(fetch=downloader)
            th=threading.Thread(target=srv.serve_forever,daemon=True);th.start()
            base='http://127.0.0.1:'+str(srv.server_port)
            def call(path,payload=None,raw=False):
                req=Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
                with loc.open(req) as res:return res.read() if raw else json.load(res)
            try:
                with patch('arxiv_tracking.refresh',return_value=result(record())) as feed:
                    call('/api/tracking',{'category':'cs.CL','consent':True,'feedback':{}});feed.assert_called_once()
                preview=call('/api/arxiv-import/stage',{'id':GOOD,'consent':True})
                self.assertEqual(call('/api/library')['docs'],[])
                self.assertEqual(call('/api/arxiv-import/preview/'+preview['token'],raw=True),data)
                with self.assertRaises(HTTPError) as exc:call('/api/arxiv-import/commit',{'token':preview['token'],'confirm':False})
                self.assertEqual(exc.exception.code,403);exc.exception.close()
                added=call('/api/arxiv-import/commit',{'token':preview['token'],'confirm':True})
                self.assertEqual(added['doc']['id'],hashlib.sha256(data).hexdigest())
                self.assertEqual(call('/pdf/'+added['doc']['id'],raw=True),data)
            finally:srv.shutdown();th.join(2)
