"""Download/import contract with mocked transport; no live request is sent."""
from email.message import Message
import hashlib
import io
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import fitz

from arxiv_import import (
    ArxivImport, ArxivImportError, CANDIDATE_TTL, FEED_ENDPOINT, MAX_BYTES,
    META_ROOT, PDF_ROOT, PREVIEW_TTL, _NoRedirect, _download, _pdf_info,
)

GOOD = "1706.03762"


def pdf_bytes():
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((55,55),"A synthetic local preview fixture.")
        return doc.tobytes()


def record(ident=GOOD, url=None):
    return {"id":ident,"title":"A public test record",
            "url":url if url is not None else META_ROOT+ident}


def result(*items):
    return {"provider":"arXiv","endpoint":FEED_ENDPOINT,"retrieved_at":"2025-01-01T00:00:00Z",
            "items":list(items),"error":None}


class NetworkReply(io.BytesIO):
    def __init__(self,data,kind="application/pdf",length=None):
        super().__init__(data)
        self.status=200
        self.headers=Message()
        self.headers["Content-Type"]=kind
        if length is not None:
            self.headers["Content-Length"]=str(length)


class ArxivImportTests(unittest.TestCase):
    def setUp(self):
        self.now=100
        self.pdf=pdf_bytes()
        self.fetch=Mock(return_value=self.pdf)
        self.add=Mock(return_value={"doc":{"id":"imported"},"existing":False})
        self.service=ArxivImport(fetch=self.fetch,clock=lambda:self.now)

    def fail(self,status,call,*args):
        with self.assertRaises(ArxivImportError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code,status)

    def test_two_clicks_preview_exact_bytes_and_local_import_only_once(self):
        self.service.remember(result(record()))
        self.assertEqual(self.service.status()["source_mode"],"simulation")
        self.fail(403,self.service.stage,{"id":GOOD,"consent":False})
        self.fetch.assert_not_called()
        meta=self.service.stage({"id":GOOD,"consent":True})
        self.fetch.assert_called_once_with(GOOD)
        self.add.assert_not_called()
        self.assertEqual(meta["pdf_source"],PDF_ROOT+GOOD)
        self.assertEqual(meta["source"],META_ROOT+GOOD)
        self.assertEqual(meta["pages"],1)
        self.assertEqual(meta["sha256"],hashlib.sha256(self.pdf).hexdigest())
        self.assertEqual(self.service.preview(meta["token"]),self.pdf)
        self.fail(403,self.service.commit,{"token":meta["token"],"confirm":False},self.add)
        self.add.assert_not_called()
        self.assertEqual(self.service.commit({"token":meta["token"],"confirm":True},self.add),
                         {"doc":{"id":"imported"},"existing":False})
        self.add.assert_called_once_with(self.pdf,"arxiv-1706.03762.pdf")
        self.fail(404,self.service.commit,{"token":meta["token"],"confirm":True},self.add)
        self.fail(404,self.service.preview,meta["token"])

    def test_no_arbitrary_urls_stale_candidate_unverified_entry_or_workflow(self):
        self.service.remember(result(record(url="https://attacker.invalid")))
        self.fail(409,self.service.stage,{"id":GOOD,"consent":True})
        for invalid in ("https://arxiv.org/pdf/1706.03762","1706.03762?target=bad","../../secret","1706.03762v1","1706.03762\n"):
            self.fail(400,self.service.stage,{"id":invalid,"consent":True})
        self.service.remember(result(record()))
        self.now+=CANDIDATE_TTL
        self.fail(409,self.service.stage,{"id":GOOD,"consent":True})
        self.fetch.assert_not_called()
        self.add.assert_not_called()

    def test_invalid_download_does_not_store_and_preview_expires(self):
        self.service.remember(result(record()))
        for invalid in (b"<html>Paywall</html>",b"%PDF-1.7\nnot a file\n%%EOF",b"0"*(MAX_BYTES+1)):
            self.fetch.return_value=invalid
            with self.assertRaises(ArxivImportError):
                self.service.stage({"id":GOOD,"consent":True})
            self.assertIsNone(self.service.status()["preview"]); self.now+=5
        self.fetch.return_value=self.pdf
        meta=self.service.stage({"id":GOOD,"consent":True})
        self.now+=PREVIEW_TTL
        self.fail(404,self.service.preview,meta["token"])
        self.add.assert_not_called()

    def test_downloader_fixed_host_no_redirect_pdf_mime_cap_and_length(self):
        with patch("arxiv_import._open",return_value=NetworkReply(self.pdf,length=len(self.pdf))) as net:
            self.assertEqual(_download(GOOD),(0,self.pdf))
            req=net.call_args.args[0]
            self.assertEqual(req.full_url,PDF_ROOT+GOOD)
            self.assertEqual(req.get_method(),"GET")
            self.assertEqual(set(dict(req.header_items())),{"Accept","User-agent"})
            self.assertEqual(req.get_header("Accept"),"application/pdf")
            self.assertEqual(_download("https://arxiv.org/x"),(7,b""))
            self.assertEqual(net.call_count,1)
        checks=[(NetworkReply(self.pdf,"text/html"),5),(NetworkReply(self.pdf,length=MAX_BYTES+1),6),
                (NetworkReply(self.pdf,length=1),5),(NetworkReply(self.pdf,length="bad"),5)]
        for reply,status in checks:
            with patch("arxiv_import._open",return_value=reply):
                self.assertEqual(_download(GOOD),(status,b""))
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,"",{},"https://evil.invalid"))
        with patch("arxiv_import._open",side_effect=HTTPError(PDF_ROOT+GOOD,302,"redirect",None,None)):
            self.assertEqual(_download(GOOD),(4,b""))

    def test_pdf_validator_rejects_encryption(self):
        with fitz.open() as doc:
            doc.new_page()
            encrypted=doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw="secret32",user_pw="pass32")
        self.fail(415,_pdf_info,encrypted)


if __name__=="__main__":
    unittest.main()
