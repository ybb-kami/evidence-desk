"""Explicit publisher PDF acquisition in the active Evidence Desk library."""

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import fitz

from app import Bad, UPLOAD_LIMIT
from arxiv_tracking import CATEGORIES
from . import fetch, joss, references
from .schedule import CADENCES

ASSETS = Path(__file__).parent
REQUEST_LOCK = threading.Lock()
ERRORS = {
    fetch.UNAVAILABLE: (502, "本次未能连接 {}。可稍后重试，或手动导入已下载的 PDF。"),
    fetch.NOT_FOUND: (404, "{} 未找到该记录或版本的 PDF。"),
    fetch.TOO_LARGE: (413, "{} PDF 超出本地文件容量上限。"),
    fetch.INVALID_PDF: (502, "{} 返回的内容不是可用的 PDF。"),
    fetch.REDIRECT: (502, "{} 返回了重定向，本次下载未完成。"),
    fetch.BUSY: (429, "{} 请求过于频繁，请稍后重试。"),
}
PROVIDERS = {
    "arxiv": ("arXiv", "arxiv_downloads", "arxiv_id"),
    "acl": ("ACL Anthology", "acl_downloads", "doi"),
    "joss": (joss.VENUE, "joss_downloads", "doi"),
}
CREATE_RECEIPTS = """
    CREATE TABLE IF NOT EXISTS {table} (
        {field} TEXT NOT NULL, doc_id TEXT NOT NULL, retrieved_at TEXT NOT NULL,
        PRIMARY KEY ({field}, doc_id)
    )
"""


def _receipts(library, provider):
    _, table, field = PROVIDERS[provider]
    with closing(sqlite3.connect(library.db)) as db, db:
        db.execute(CREATE_RECEIPTS.format(table=table, field=field))
        return [
            _source(identifier, digest, timestamp, provider) | {"archived": bool(archived)}
            for identifier, digest, timestamp, archived in db.execute(
                f"SELECT a.{field}, a.doc_id, a.retrieved_at, "
                "EXISTS(SELECT 1 FROM archive WHERE archive.id=a.doc_id) "
                f"FROM {table} a JOIN docs ON docs.id=a.doc_id "
                "ORDER BY a.retrieved_at DESC LIMIT 25"
            )
        ]


def _source(identifier, digest, timestamp, provider):
    name, _, field = PROVIDERS[provider]
    return {
        field: identifier, "sha256": digest, "retrieved_at": timestamp,
        "provider": name, "url": fetch.pdf_url(identifier, provider),
    }


def _error(code, provider):
    status, message = ERRORS.get(code, ERRORS[fetch.UNAVAILABLE])
    return Bad(status, message.format(PROVIDERS[provider][0]))


def _acquire(library, identifier, provider):
    _, table, field = PROVIDERS[provider]
    if not REQUEST_LOCK.acquire(blocking=False):
        raise Bad(409, "另一份 PDF 正在下载，请稍后重试。")
    try:
        code, data = fetch.retrieve(identifier) if provider == "arxiv" else fetch.retrieve(identifier, provider)
        if code:
            raise _error(code, provider)
        if len(data) > UPLOAD_LIMIT:
            raise _error(fetch.TOO_LARGE, provider)
        try:
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if not pdf.is_pdf or pdf.is_encrypted or pdf.is_repaired or len(pdf) == 0:
                    raise ValueError("invalid PDF")
        except (fitz.FileDataError, ValueError, RuntimeError) as error:
            raise _error(fetch.INVALID_PDF, provider) from error

        timestamp = datetime.now(timezone.utc).isoformat()
        if provider == "arxiv":
            name = "arXiv-" + identifier.replace("/", "-")
        else:
            name = identifier.rsplit("/", 1)[1]
            if provider == "acl":
                name = "ACL-" + name
        name += ".pdf"
        digest = sha256(data).hexdigest()
        # Write provenance before inserting the content. History joins library
        # documents by the same digest, so incomplete imports stay invisible.
        with closing(sqlite3.connect(library.db)) as db, db:
            db.execute(CREATE_RECEIPTS.format(table=table, field=field))
            db.execute(
                f"INSERT INTO {table} VALUES (?, ?, ?) "
                f"ON CONFLICT({field}, doc_id) DO UPDATE SET retrieved_at=excluded.retrieved_at",
                (identifier, digest, timestamp),
            )
        try:
            result = library.add(data, name)
        except Exception:
            with closing(sqlite3.connect(library.db)) as db, db:
                db.execute(
                    f"DELETE FROM {table} WHERE {field}=? AND doc_id=? AND retrieved_at=? "
                    "AND NOT EXISTS(SELECT 1 FROM docs WHERE id=?)",
                    (identifier, digest, timestamp, digest),
                )
            raise
        return result | {"source": _source(identifier, digest, timestamp, provider)}
    finally:
        REQUEST_LOCK.release()


def _subscription(handler, post):
    schedule = getattr(handler.server, "tracking_schedule", None)
    if schedule is None:
        raise Bad(503, "请从扫描工作台启动定期追踪。")
    if not post:
        return schedule.state() | {"categories": CATEGORIES}
    request = handler.json_request()
    if request == {"action": "stop"}:
        return schedule.stop()
    if request.get("action") != "subscribe":
        raise Bad(400, "请选择订阅或停止。")
    if request.get("consent") is not True:
        raise Bad(403, "请确认定期向 arXiv 发送所选公开分类。")
    category, cadence = request.get("category"), request.get("cadence")
    if (set(request) != {"action", "category", "cadence", "consent"}
            or category not in CATEGORIES or not isinstance(cadence, str) or cadence not in CADENCES):
        raise Bad(400, "请选择一个公开 arXiv 分类和刷新频率。")
    return schedule.subscribe(category, cadence)


def handle(handler, post, path):
    if not post and path == "/download":
        handler.payload(200, (ASSETS / "download.html").read_bytes(), "text/html; charset=utf-8")
        return True
    if path == "/api/download/subscription":
        handler.payload(200, _subscription(handler, post))
        return True
    if path == "/api/download/references":
        library = handler.server.library
        if post:
            result = references.import_candidate(library, handler.json_request(), _acquire)
        else:
            docid = parse_qs(urlsplit(handler.path).query).get("doc_id", [""])[0]
            result = references.candidates(library, docid)
        handler.payload(200, result)
        return True
    if post and path == "/api/download/joss/search":
        request = handler.json_request()
        query = request.get("query")
        if (set(request) != {"query"} or not isinstance(query, str) or not query.strip()
                or len(query) > 300 or not query.isprintable()):
            raise Bad(400, "请输入 1–300 字的单行公开论文题名。")
        handler.payload(200, joss.search(query))
        return True
    provider = path.removeprefix("/api/download/")
    if not path.startswith("/api/download/") or provider not in PROVIDERS:
        return False
    if not post:
        handler.payload(200, {"receipts": _receipts(handler.server.library, provider)})
        return True
    name, _, field = PROVIDERS[provider]
    request = handler.json_request()
    if request.get("consent") is not True:
        raise Bad(403, f"请确认本次可将公开编号发送给 {name}。")
    identifier = request.get(field)
    if set(request) != {field, "consent"} or fetch.pdf_url(identifier, provider) is None:
        if provider == "arxiv":
            raise Bad(400, "请输入完整 arXiv 编号，可附带版本号，例如 1706.03762v1。")
        if provider == "joss":
            raise Bad(400, "请选择本机题录中有开放许可的 JOSS 论文。")
        raise Bad(400, "请选择带有 ACL Anthology DOI 的公开题录。")
    if provider == "acl":
        identifier = fetch.acl_doi(identifier)
    elif provider == "joss":
        identifier = fetch.joss_doi(identifier)
        if not joss.published(identifier):
            raise Bad(400, "请先从本机 JOSS 题录选择带开放许可的论文。")
    handler.payload(200, _acquire(handler.server.library, identifier, provider))
    return True
