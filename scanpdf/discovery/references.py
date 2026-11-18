"""Resolve explicit identifiers in locally indexed bibliography entries."""

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from threading import Lock

from app import Bad, ID
from graph import _bibliography

from . import fetch

ARXIV = re.compile(
    r"(?:\barxiv\s*:\s*|https?://arxiv\.org/(?:abs|pdf)/|(?<![\w.])10\.48550/arxiv\.)"
    r"(" + fetch.ID.pattern + r")(?:\.pdf)?(?![\w/]|\.[\w.])",
    re.ASCII | re.IGNORECASE,
)
DOI = re.compile(r"(?<![\w.])(10\.18653/v1/[a-z0-9.-]+)(?![\w/?#%@=-])", re.ASCII | re.IGNORECASE)
SCHEMA = """
    CREATE TABLE IF NOT EXISTS reference_downloads (
        source_id TEXT NOT NULL, candidate_id TEXT NOT NULL, status TEXT NOT NULL,
        updated_at TEXT NOT NULL, imported_id TEXT, message TEXT,
        PRIMARY KEY (source_id, candidate_id)
    )
"""
ITEM_LOCK = Lock()


def _target(text):
    known = {("arxiv", match[1].lower()) for match in ARXIV.finditer(text)}
    known.update(("acl", doi) for match in DOI.finditer(text)
                 if (doi := fetch.acl_doi(match[1].rstrip("."))))
    if len(known) == 1:
        return (*next(iter(known)), None)
    if known:
        return None, None, "本条引文含多个受支持编号，请核对原页后单独获取。"
    return None, None, "本条引文未找到可核验的 arXiv 编号或受支持的 ACL DOI。"


def candidates(library, docid):
    if not isinstance(docid, str) or not ID.fullmatch(docid):
        raise Bad(400, "请选择一篇文库论文。")
    documents = library.records([docid])
    if not documents:
        raise Bad(404, "所选论文不在活动文库。")
    detected, entries = _bibliography(documents[0])
    with closing(sqlite3.connect(library.db)) as db, db:
        db.execute(SCHEMA)
        db.row_factory = sqlite3.Row
        attempts = {
            row["candidate_id"]: dict(row)
            for row in db.execute(
                "SELECT candidate_id, status, updated_at, imported_id, message "
                "FROM reference_downloads WHERE source_id=?", (docid,)
            )
        }
    rows = []
    for entry in entries:
        evidence = f"{entry['page']}:{entry['block']}:{entry['offset']}:{entry['text']}"
        key = sha256(evidence.encode()).hexdigest()
        provider, identifier, message = _target(entry["text"])
        status = {"status": "pending" if identifier else "unresolved", "message": message}
        rows.append(entry | {"candidate_id": key, "provider": provider, "identifier": identifier}
                    | status | attempts.get(key, {}))
    return {"detected": detected, "items": rows}


def import_candidate(library, request, acquire):
    if request.get("consent") is not True:
        raise Bad(403, "请确认逐篇向对应出版方发送勾选的公开编号。")
    docid, candidate_id = request.get("doc_id"), request.get("candidate_id")
    if set(request) != {"consent", "doc_id", "candidate_id"} or not isinstance(candidate_id, str) or not ID.fullmatch(candidate_id):
        raise Bad(400, "请从所选论文勾选参考文献。")
    if not ITEM_LOCK.acquire(blocking=False):
        raise Bad(409, "另一条参考文献正在获取，请稍后重试。")
    try:
        item = next((row for row in candidates(library, docid)["items"]
                     if row["candidate_id"] == candidate_id), None)
        if item is None:
            raise Bad(404, "该引文已变化，请重新读取来源论文。")
        if item["status"] in ("unresolved", "success"):
            return item
        imported_id = message = None
        try:
            result = acquire(library, item["identifier"], item["provider"])
            imported_id = result["doc"]["id"]
            status = "success"
            if result.get("archived"):
                message = "相同 PDF 已在归档。"
            elif result.get("existing"):
                message = "相同 PDF 已在文库。"
        except Bad as error:
            status = "not_found" if error.code == 404 else "failed"
            message = error.msg
        except ValueError as error:
            status = "failed"
            message = str(error)
        updated_at = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(library.db)) as db, db:
            db.execute(
                "INSERT INTO reference_downloads VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, candidate_id) DO UPDATE SET status=excluded.status, "
                "updated_at=excluded.updated_at, imported_id=excluded.imported_id, message=excluded.message",
                (docid, candidate_id, status, updated_at, imported_id, message),
            )
        return item | {"status": status, "updated_at": updated_at, "imported_id": imported_id, "message": message}
    finally:
        ITEM_LOCK.release()
