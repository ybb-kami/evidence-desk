"""Two-click, bounded import of a recently verified public arXiv result."""
from __future__ import annotations

import hashlib
from http.client import HTTPException
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]{0,30}/\d{7})", re.ASCII)
TOKEN = re.compile(r"[a-f0-9]{32}")
PDF_ROOT = "https://arxiv.org/pdf/"
META_ROOT = "https://arxiv.org/abs/"
FEED_ENDPOINT = "https://export.arxiv.org/api/query"
MAX_BYTES = 24 * 1024**2
MAX_PAGES = 650
MAX_CANDIDATES = 12
REQUEST_TIMEOUT = 18
CANDIDATE_TTL = 30 * 60
PREVIEW_TTL = 10 * 60
COOLDOWN = 4


class ArxivImportError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _valid_id(value):
    return isinstance(value,str) and ID.fullmatch(value) is not None


def _open(req):
    return build_opener(ProxyHandler({}),_NoRedirect()).open(req,timeout=6)


def _download(ident):
    """Only the isolated, externally timed worker runs this live transport."""
    if not _valid_id(ident):
        return 7,b""
    req=Request(PDF_ROOT+ident,headers={"Accept":"application/pdf","User-Agent":"EvidenceDesk/0.4"})
    try:
        with _open(req) as response:
            if response.status!=200 or response.headers.get_content_type()!="application/pdf":
                return 5,b""
            announced=response.headers.get("Content-Length")
            if announced is not None:
                if not announced.isascii() or not announced.isdecimal() or len(announced)>12:
                    return 5,b""
                if int(announced)>MAX_BYTES:
                    return 6,b""
            data=response.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:
                return 6,b""
            if announced is not None and len(data)!=int(announced):
                return 5,b""
            return 0,data
    except HTTPError as exc:
        code=exc.code; exc.close()
        if code==429:
            return 3,b""
        if 300<=code<400:
            return 4,b""
        return 7,b""
    except (HTTPException,URLError,TimeoutError,OSError,ValueError):
        return 7,b""


def _transport(ident):
    try:
        p=subprocess.run([sys.executable,"-I",str(Path(__file__).resolve()),ident], stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=REQUEST_TIMEOUT,check=False)
    except subprocess.TimeoutExpired:
        raise ArxivImportError(504,"公开 PDF 下载超时，未自动重试。") from None
    except OSError:
        raise ArxivImportError(502,"无法启动公开 PDF 下载。") from None
    if p.returncode:
        code,msg={3:(429,"arXiv 限流。请稍后再试。"),4:(502,"拒绝了 arXiv 的跳转响应。"),
                  5:(415,"官方响应不是完整的 PDF 内容。"),6:(413,"公开 PDF 超过 24 MiB 限制。")}.get(
                      p.returncode,(502,"无法从固定 arXiv 端点取得公开 PDF。"))
        raise ArxivImportError(code,msg)
    return p.stdout


def _pdf_info(data):
    import fitz
    if not isinstance(data,bytes):
        raise ArxivImportError(415,"官方响应不是有效的 PDF 内容。")
    if len(data)>MAX_BYTES:
        raise ArxivImportError(413,"公开 PDF 超过 24 MiB 限制。")
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
        raise ArxivImportError(415,"官方响应不是完整的 PDF 内容。")
    try:
        with fitz.open(stream=data,filetype="pdf") as pdf:
            if not pdf.is_pdf or pdf.is_repaired or pdf.needs_pass or not 1<=len(pdf)<=MAX_PAGES:
                raise ValueError
            # Ensure the last physical page can also be resolved through the page tree.
            pdf.load_page(len(pdf)-1)
            return len(pdf),hashlib.sha256(data).hexdigest()
    except (RuntimeError,ValueError,IndexError):
        raise ArxivImportError(415,"公开 PDF 无法完整打开、已加密或超出页数限制。") from None


class ArxivImport:
    """One ephemeral download slot; save only via a separate confirmed request."""
    def __init__(self, *, fetch=None, clock=monotonic):
        self.fetch,self.clock=fetch if fetch is not None else _transport,clock
        self.simulated=fetch is not None
        self.lock=threading.Lock()
        self.candidates={}
        self.candidates_until=0
        self.pending=None
        self.next_request=0
        self.busy=False
        self.generation=0

    def remember(self,result):
        """Call only with a result returned by the verified manual feed transport."""
        if (not isinstance(result,dict) or result.get("provider")!="arXiv"
                or result.get("endpoint")!=FEED_ENDPOINT or result.get("error") is not None
                or not result.get("retrieved_at") or not isinstance(result.get("items"),list)):
            return
        candidates={}
        for item in result["items"][:MAX_CANDIDATES]:
            if not isinstance(item,dict):
                continue
            ident,title=item.get("id"),item.get("title")
            if not _valid_id(ident) or not isinstance(title,str) or not title.strip() or item.get("url")!=META_ROOT+ident:
                continue
            candidates[ident]={"id":ident,"title":" ".join(title.split())[:500],"source":META_ROOT+ident}
        with self.lock:
            self.candidates=candidates
            self.candidates_until=self.clock()+CANDIDATE_TTL
            self.generation+=1

    def _purge(self,now):
        if now>=self.candidates_until:
            self.candidates={}
        if self.pending is not None and now>=self.pending[0]:
            self.pending=None

    def status(self):
        with self.lock:
            self._purge(self.clock())
            return {"candidates":[dict(row) for row in self.candidates.values()],
                    "preview":dict(self.pending[1]) if self.pending else None,
                    "busy":self.busy,"source_mode":"simulation" if self.simulated else "public_arxiv"}

    def stage(self,req):
        if not isinstance(req,dict) or set(req)!={"id","consent"} or not _valid_id(req["id"]):
            raise ArxivImportError(400,"请选择最近官方 arXiv 刷新的有效题录。")
        if req.get("consent") is not True:
            raise ArxivImportError(403,"请明确同意从 arXiv 下载所选公开 PDF。")
        ident=req["id"]
        with self.lock:
            now=self.clock()
            self._purge(now)
            if ident not in self.candidates:
                raise ArxivImportError(409,"此题录未在近期官方刷新中验证，请先手动刷新。")
            if self.busy:
                raise ArxivImportError(409,"已有公开 PDF 下载正在进行。")
            if now<self.next_request:
                raise ArxivImportError(429,"下载已限速，请稍后再试。")
            candidate=dict(self.candidates[ident])
            generation=self.generation
            self.busy=True
        try:
            data=self.fetch(ident)
            pages,digest=_pdf_info(data)
            meta={**candidate,"token":secrets.token_hex(16),"pdf_source":PDF_ROOT+ident,
                  "bytes":len(data),"pages":pages,"sha256":digest,
                  "filename":"arxiv-"+ident.replace("/","-")+".pdf","mime":"application/pdf"}
            with self.lock:
                if generation!=self.generation:
                    raise ArxivImportError(409,"最近刷新结果已改变，请重新选择题录。")
                self.pending=(self.clock()+PREVIEW_TTL,meta,data)
                return dict(meta)
        except ArxivImportError as exc:
            if exc.code==429:
                with self.lock:
                    self.next_request=self.clock()+3600
            raise
        finally:
            with self.lock:
                self.busy=False
                self.next_request=max(self.next_request,self.clock()+COOLDOWN)

    def _selected(self,token):
        self._purge(self.clock())
        if (not isinstance(token,str) or not TOKEN.fullmatch(token)
                or not self.pending or not secrets.compare_digest(token,self.pending[1]["token"])):
            raise ArxivImportError(404,"未找到有效的待导入预览。")
        return self.pending[1],self.pending[2]

    def preview(self,token):
        with self.lock:
            return self._selected(token)[1]

    def commit(self,req,add):
        if not isinstance(req,dict) or set(req)!={"token","confirm"} or req.get("confirm") is not True:
            raise ArxivImportError(403,"请再次明确确认将预览的 PDF 导入本机文库。")
        with self.lock:
            if self.busy:
                raise ArxivImportError(409,"另一个公开 PDF 正在下载，请等待结束。")
            meta,data=self._selected(req["token"])
            result=add(data,meta["filename"])
            self.pending=None
            return result


if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit(7)
    status,payload=_download(sys.argv[1])
    if not status:
        sys.stdout.buffer.write(payload)
    raise SystemExit(status)
