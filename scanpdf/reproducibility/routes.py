"""Read paper repository links locally and check one after explicit consent."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time
from urllib.parse import parse_qs

from app import Bad, ID

from . import github, snapshot
from .references import REFERENCE_ID, find_references

HERE = Path(__file__).resolve().parent
PATHS = {"/reproducibility", "/reproducibility.js", "/api/reproducibility/references",
         "/api/reproducibility/audit"}
CACHE_TTL = 3600
ERROR_TTL = 120
CACHE_MAX = 64
CHECK_LOCK = Lock()
ERRORS = {
    "timeout": "本次 GitHub 请求超时。",
    "redirect": "GitHub 返回了重定向，此原文引用尚未核验。",
    "not_found": "GitHub API 未返回对应的公开仓库或提交。",
    "rate_limited": "GitHub 公共 API 当前已达到请求限制。",
    "unavailable": "本次未能读取 GitHub 公共 API。",
    "forbidden": "GitHub 公共 API 请求返回 403，本次未取得实时结果。",
    "invalid_proxy": "当前 HTTPS 代理设置不适用于无凭据的公开请求。",
    "response_limit": "GitHub 文件列表超过本次检查的大小上限。",
    "invalid_response": "GitHub 返回的数据缺少可核对的提交或文件列表。",
    "repository_mismatch": "GitHub 返回的仓库标识与原文引用不符。",
    "commit_mismatch": "GitHub 返回的提交与原文引用的提交不符。",
}


def _document(library, docid):
    if not isinstance(docid, str) or not ID.fullmatch(docid):
        raise Bad(400, "请选择本地文献。")
    docs = library.records([docid])
    if len(docs) != 1:
        raise Bad(404, "所选文献不在当前活动文库。")
    return docs[0]


def references(library, docid):
    doc = _document(library, docid)
    items, truncated = find_references(doc)
    snapshots = snapshot.load_for(library, docid) if items else {}
    for item in items:
        cached = snapshots.get(item["id"])
        if cached and snapshot.matches_reference(cached, item):
            item["snapshot"] = cached
    return {"document": {"name": doc["name"], "sha256": doc["id"]}, "references": items,
            "truncated": truncated, "pending_pages": doc.get("textless_pages", [])}


def _open_cache(library):
    return closing(sqlite3.connect(library.db))


def _cached(library, key):
    with _open_cache(library) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS repository_evidence_v1 "
                   "(key TEXT PRIMARY KEY, checked INTEGER NOT NULL, result TEXT NOT NULL)")
        row = db.execute("SELECT checked,result FROM repository_evidence_v1 WHERE key=?", (key,)).fetchone()
    if row and 0 <= time.time() - row[0] < CACHE_TTL:
        try:
            result = json.loads(row[1])
        except (TypeError, ValueError):
            return None
        if isinstance(result, dict):
            if result.get("status") == "checked" or (result.get("status") == "unavailable"
                    and time.time() - row[0] < ERROR_TTL):
                return result
    return None


def _save(library, key, result):
    with _open_cache(library) as db, db:
        db.execute("INSERT INTO repository_evidence_v1 VALUES (?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET checked=excluded.checked,result=excluded.result",
                   (key, int(time.time()), json.dumps(result, ensure_ascii=False)))
        db.execute("DELETE FROM repository_evidence_v1 WHERE key IN "
                   "(SELECT key FROM repository_evidence_v1 ORDER BY checked DESC, key LIMIT -1 OFFSET ?)",
                   (CACHE_MAX,))


def audit(library, request):
    if request.get("consent") is not True:
        raise Bad(403, "请先确认本次公开仓库检查。")
    ref_id = request.get("reference")
    if (set(request) != {"id", "reference", "consent"} or not isinstance(ref_id, str)
            or not REFERENCE_ID.fullmatch(ref_id)):
        raise Bad(400, "请选择一条来自当前论文的 GitHub 引用。")
    doc = _document(library, request.get("id"))
    candidates, _ = find_references(doc)
    match = next((item for item in candidates if item["id"] == ref_id), None)
    if match is None:
        raise Bad(404, "未在所选文献的索引中找到这条引用。")
    owner, repo, cited = (match[key] for key in ("owner", "repo", "cited_commit"))
    key = f"{owner.lower()}/{repo.lower()}@{cited or 'default'}"
    if not CHECK_LOCK.acquire(blocking=False):
        raise Bad(429, "另一项仓库检查正在运行。")
    try:
        stored = _cached(library, key)
        if stored:
            return {"reference": match, "audit": stored, "cached": True}
        try:
            result = github.inspect(owner, repo, cited)
        except github.CheckError as error:
            current = datetime.now(timezone.utc).isoformat(timespec="seconds")
            result = {"status": "unavailable", "checked_at": current, "reason": error.code,
                      "message": ERRORS.get(error.code, ERRORS["unavailable"])}
        _save(library, key, result)
        return {"reference": match, "audit": result, "cached": False}
    finally:
        CHECK_LOCK.release()


def handle(handler, post, url):
    path = url.path
    if path not in PATHS:
        raise Bad(404, "未找到代码复现证据页面。")
    expected_post = path == "/api/reproducibility/audit"
    if post != expected_post:
        raise Bad(405, "该功能的请求方法无效。")
    if path in {"/reproducibility", "/reproducibility.js"}:
        filename, mime = ("index.html", "text/html") if path == "/reproducibility" else ("reader.js", "application/javascript")
        return handler.payload(200, (HERE / filename).read_bytes(), f"{mime}; charset=utf-8")
    if handler.headers.get("Sec-Fetch-Site") == "cross-site":
        raise Bad(403, "请选择本机工作台上的文献。")
    store = handler.server.library
    if expected_post:
        if url.query:
            raise Bad(400, "仓库检查只接受所选论文和引用。")
        return handler.payload(200, audit(store, handler.json_request()))
    try:
        query = parse_qs(url.query, keep_blank_values=True, max_num_fields=1, strict_parsing=True)
    except ValueError:
        raise Bad(400, "请选择一份本地文献。") from None
    if set(query) != {"id"} or len(query["id"]) != 1:
        raise Bad(400, "请选择一份本地文献。")
    return handler.payload(200, references(store, query["id"][0]))
