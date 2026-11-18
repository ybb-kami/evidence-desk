"""Explicit arXiv acquisition in the active Evidence Desk library."""

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import fitz

from app import Bad, UPLOAD_LIMIT
from . import fetch

ASSETS = Path(__file__).parent
REQUEST_LOCK = threading.Lock()
ERRORS = {
    fetch.UNAVAILABLE: (502, "本次未能连接 arXiv。可稍后重试，或手动导入已下载的 PDF。"),
    fetch.NOT_FOUND: (404, "arXiv 未找到该编号或版本。"),
    fetch.TOO_LARGE: (413, "arXiv PDF 超出本地文件容量上限。"),
    fetch.INVALID_PDF: (502, "arXiv 返回的内容不是可用的 PDF。"),
    fetch.REDIRECT: (502, "arXiv 返回了重定向，本次下载未完成。"),
    fetch.BUSY: (429, "arXiv 请求过于频繁，请稍后重试。"),
}
CREATE_RECEIPTS = """
    CREATE TABLE IF NOT EXISTS arxiv_downloads (
        arxiv_id TEXT NOT NULL, doc_id TEXT NOT NULL, retrieved_at TEXT NOT NULL,
        PRIMARY KEY (arxiv_id, doc_id)
    )
"""


def _receipts(library):
    with closing(sqlite3.connect(library.db)) as db, db:
        db.execute(CREATE_RECEIPTS)
        return [
            _source(identifier, digest, timestamp) | {"archived": bool(archived)}
            for identifier, digest, timestamp, archived in db.execute(
                "SELECT a.arxiv_id, a.doc_id, a.retrieved_at, "
                "EXISTS(SELECT 1 FROM archive WHERE archive.id=a.doc_id) "
                "FROM arxiv_downloads a JOIN docs ON docs.id=a.doc_id "
                "ORDER BY a.retrieved_at DESC LIMIT 25"
            )
        ]


def _source(identifier, digest, timestamp):
    return {
        "arxiv_id": identifier, "sha256": digest, "retrieved_at": timestamp,
        "provider": "arXiv", "url": fetch.ENDPOINT + identifier,
    }


def _acquire(library, identifier):
    if not REQUEST_LOCK.acquire(blocking=False):
        raise Bad(409, "另一份 PDF 正在下载，请稍后重试。")
    try:
        code, data = fetch.retrieve(identifier)
        if code:
            raise Bad(*ERRORS.get(code, ERRORS[fetch.UNAVAILABLE]))
        if len(data) > UPLOAD_LIMIT:
            raise Bad(*ERRORS[fetch.TOO_LARGE])
        try:
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if not pdf.is_pdf or pdf.is_encrypted or pdf.is_repaired or len(pdf) == 0:
                    raise ValueError("invalid PDF")
        except (fitz.FileDataError, ValueError, RuntimeError) as error:
            raise Bad(*ERRORS[fetch.INVALID_PDF]) from error

        timestamp = datetime.now(timezone.utc).isoformat()
        name = "arXiv-" + identifier.replace("/", "-") + ".pdf"
        digest = sha256(data).hexdigest()
        # Write provenance before inserting the content. History joins library
        # documents by the same digest, so incomplete imports stay invisible.
        with closing(sqlite3.connect(library.db)) as db, db:
            db.execute(CREATE_RECEIPTS)
            db.execute(
                "INSERT INTO arxiv_downloads VALUES (?, ?, ?) "
                "ON CONFLICT(arxiv_id, doc_id) DO UPDATE SET retrieved_at=excluded.retrieved_at",
                (identifier, digest, timestamp),
            )
        try:
            result = library.add(data, name)
        except Exception:
            with closing(sqlite3.connect(library.db)) as db, db:
                db.execute(
                    "DELETE FROM arxiv_downloads WHERE arxiv_id=? AND doc_id=? AND retrieved_at=? "
                    "AND NOT EXISTS(SELECT 1 FROM docs WHERE id=?)",
                    (identifier, digest, timestamp, digest),
                )
            raise
        return result | {"source": _source(identifier, digest, timestamp)}
    finally:
        REQUEST_LOCK.release()


def handle(handler, post, path):
    if not post and path == "/download":
        handler.payload(200, (ASSETS / "download.html").read_bytes(), "text/html; charset=utf-8")
    elif not post and path == "/api/download/arxiv":
        handler.payload(200, {"receipts": _receipts(handler.server.library)})
    elif post and path == "/api/download/arxiv":
        request = handler.json_request()
        if request.get("consent") is not True:
            raise Bad(403, "请确认本次可将公开编号发送给 arXiv。")
        if set(request) != {"arxiv_id", "consent"} or not fetch.valid_id(request["arxiv_id"]):
            raise Bad(400, "请输入完整 arXiv 编号，可附带版本号，例如 1706.03762v1。")
        handler.payload(200, _acquire(handler.server.library, request["arxiv_id"]))
    else:
        return False
    return True
