"""Run from Evidence Desk root: python -m unittest -v partner_reader.test_bridge."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import fitz

import app

from .routes import handle


class ContractHandler(app.Handler):
    """Replicate the proposed dispatch, with the active main Handler and Library."""
    def serve(self, post):
        from urllib.parse import urlsplit
        path=urlsplit(self.path).path
        if path=="/translation" or path.startswith(("/api/bilingual/","/bilingual/")):
            if self.headers.get("Host")!=f"127.0.0.1:{self.server.server_port}":
                raise app.Bad(403,"loopback only")
            if post and self.headers.get("Origin") not in (None,"http://"+self.headers["Host"]):
                raise app.Bad(403,"same origin only")
            if handle(self,post,path,bad=app.Bad):
                return
        return super().serve(post)


class ReaderBridgeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        server=app.ThreadingHTTPServer(("127.0.0.1",0),ContractHandler)
        self.addCleanup(server.server_close)
        server.library=app.Library(Path(temp.name))
        server.enable_model=False
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.base=f"http://127.0.0.1:{server.server_port}"
        self.opener=build_opener(ProxyHandler({}))

    def request(self, path, data=None, kind=None, *, origin=None):
        headers={"Content-Type":kind} if kind else {}
        if origin:
            headers["Origin"]=origin
        req=Request(self.base+path,data=data,headers=headers)
        with self.opener.open(req,timeout=4) as response:
            return response.read(),response.headers

    def post(self,path,data,**opts):
        return self.request(path,json.dumps(data).encode(),"application/json",**opts)

    def assert_error(self,status,operation,*args,**opts):
        with self.assertRaises(HTTPError) as caught:
            operation(*args,**opts)
        with caught.exception as err:
            self.assertEqual(err.code,status)
            self.assertIn("error",json.loads(err.read()))

    def import_doc(self,data,name):
        from urllib.parse import quote
        result,_=self.request("/api/import?name="+quote(name),data,"application/pdf")
        return json.loads(result)["doc"]["id"]

    @staticmethod
    def fixture(kind):
        with fitz.open() as doc:
            page=doc.new_page(width=520,height=300)
            if kind!="blank":
                page.insert_text((45,60),"A local source paragraph.",fontsize=18)
                page.insert_text((45,120),"print(value + 1)",fontsize=16,fontname="cour")
            if kind=="derivative":
                doc.set_metadata({"title":"Local English OCR text-layer derivative"})
            return doc.tobytes()

    def test_original_page_crop_and_archived_exclusion(self):
        native=self.fixture("native")
        ident=self.import_doc(native,"native.pdf")
        root="/api/bilingual"
        html,headers=self.request("/translation")
        self.assertIn(b'/bilingual/reader.js',html)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        for asset in ("reader.js","export.js"):
            text,headers=self.request("/bilingual/"+asset)
            self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
            self.assertNotIn(b"Translator.create(",text)
        body,headers=self.request(root+"/page/"+ident)
        page=json.loads(body)
        self.assertEqual(page["page"],1)
        prose,code=page["segments"]
        self.assertEqual(prose["text"],"A local source paragraph.")
        self.assertEqual(code["protected"],[[0,len(code["text"])]])
        img,headers=self.request(root+"/crop/"+ident+"?page=1&block="+str(code["block"]))
        self.assertEqual(headers["Content-Type"],"image/png")
        self.assertTrue(img.startswith(b"\x89PNG\r\n\x1a\n"))
        with fitz.open(stream=img,filetype="png") as crop:
            self.assertLess(crop[0].rect.height,300)
        self.assertEqual(self.request("/pdf/"+ident)[0],native)
        self.assert_error(400,self.request,root+"/page/"+ident+"?page=2")
        self.assert_error(404,self.request,root+"/crop/"+ident+"?block=999")
        self.post("/api/archive",{"id":ident})
        self.assert_error(404,self.request,root+"/page/"+ident)

    def test_empty_scan_and_imported_derivative_are_reported_separately(self):
        blank=self.import_doc(self.fixture("blank"),"scan.pdf")
        derived=self.import_doc(self.fixture("derivative"),"scan.ocr.pdf")
        root="/api/bilingual/page/"
        raw=json.loads(self.request(root+blank)[0])
        self.assertEqual(raw["segments"],[])
        self.assertNotIn("source",raw)
        output=json.loads(self.request(root+derived)[0])
        self.assertEqual(output["source"],"ocr")
        self.assertTrue(output["segments"])

    def test_mock_both_directions_no_model_503_and_cross_origin(self):
        root="/api/bilingual"
        with patch("partner_reader.local.available",return_value=[]):
            self.assertEqual(json.loads(self.request(root+"/models")[0]),{"directions":[]})
            self.assert_error(503,self.post,root+"/translate",{"direction":"en-zh","text":"Test."})
        with patch("partner_reader.local.available",return_value=["en-zh","zh-en"]), \
                patch("partner_reader.local.translate",side_effect=["测试。","Test."]) as local:
            for direction,source,target in [("en-zh","Test.","测试。"),("zh-en","测试。","Test.")]:
                req={"direction":direction,"text":source}
                self.assertEqual(json.loads(self.post(root+"/translate",req)[0]),{"text":target})
            self.assertEqual(local.call_count,2)
            self.assert_error(403,self.post,root+"/translate",req,origin="http://evil.invalid")
            self.assert_error(400,self.post,root+"/translate",{**req,"url":"https://evil.invalid"})
            self.assertEqual(local.call_count,2)


if __name__=="__main__":
    unittest.main()
