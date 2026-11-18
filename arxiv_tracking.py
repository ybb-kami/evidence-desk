"""Bounded, opt-in arXiv metadata refresh and local-only recommendation."""
import math
import re
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CATEGORIES = ("cs.AI", "cs.CL", "cs.CV", "cs.LG", "stat.ML", "math.CO", "math.OC")
ENDPOINT = "https://export.arxiv.org/api/query"
ROWS = 12
TIMEOUT = 7
MAX_BODY = 512 * 1024
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]{0,30}/\d{7})", re.ASCII)
SOURCE_ID = re.compile(r"https?://arxiv\.org/abs/(" + ARXIV_ID.pattern + r")(?:v[1-9]\d{0,5})?", re.ASCII)
REPO = re.compile(r"(?<![\w@./=-])https://github\.com/([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,38}))/([a-zA-Z0-9_][a-zA-Z0-9_.-]{0,99})(?=$|[\s)\]}>.,;])")
RESERVED = frozenset("topics orgs users marketplace collections settings features sponsors search login site security apps".split())
ATOM = "{http://www.w3.org/2005/Atom}"
OPEN_SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
_LOCK = threading.Lock()
_NEXT_REQUEST = 0.0
_STOP = frozenset("about after also among based been being both called can could does each first from had has have into more most new only other over paper results show than that their them these they this those three two using use used was were when where which while will with without would".split())
_TOKEN = re.compile(r"[a-z][a-z0-9]{2,31}|[\u3400-\u9fff]+")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download(category):
    """Worker entry: receives only the known public category, never a library."""
    if category not in CATEGORIES:
        return 7, b""
    query = urlencode({"search_query": "cat:" + category, "start": 0, "max_results": ROWS,
                       "sortBy": "submittedDate", "sortOrder": "descending"})
    req = Request(ENDPOINT + "?" + query, headers={"Accept": "application/atom+xml", "User-Agent": "EvidenceDesk/0.3"})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(req, timeout=4) as response:
            data = response.read(MAX_BODY + 1)
            return (0, data) if len(data) <= MAX_BODY else (6, b"")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        return (3 if code == 429 else 4 if 300 <= code < 400 else 5), b""
    except TimeoutError:
        return 2, b""
    except URLError as exc:
        return (2 if isinstance(exc.reason, TimeoutError) else 7), b""
    except (OSError, ValueError, HTTPException):
        return 7, b""


def _fetch(category):
    # Worker is killed on timeout, including a stalled DNS lookup or slow body.
    return subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), category],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)


def valid_feedback(feedback):
    return (isinstance(feedback, dict) and len(feedback) <= 100
            and all(isinstance(k, str) and ARXIV_ID.fullmatch(k) and type(v) is int and v in (-1, 1)
                    for k, v in feedback.items()))


def _clean(value, limit):
    return " ".join((value or "").split())[:limit]


def _date(value):
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value or "", re.ASCII):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _repository(summary, comment):
    for match in REPO.finditer(summary + " " + comment):
        owner, repo = match.groups()
        repo = repo.rstrip(".")
        if owner.lower() not in RESERVED and repo:
            return "https://github.com/" + owner + "/" + repo
    return None


def _parse(data):
    if len(data) > MAX_BODY:
        raise ValueError("response too large")
    decoded = data.decode("utf-8", errors="strict")
    # Decode first so UTF-16 or an encoding trick cannot bypass the declaration guard.
    if "\x00" in decoded or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", decoded, re.I):
        raise ValueError("unsupported XML")
    feed = ET.fromstring(decoded)
    if feed.tag != ATOM + "feed":
        raise ValueError("not Atom")
    total = feed.findtext(OPEN_SEARCH + "totalResults")
    entries = feed.findall(ATOM + "entry")
    if not entries and total != "0":
        raise ValueError("missing entries")
    items = {}
    for entry in entries[:ROWS]:
        def field(name):
            return entry.findtext(ATOM + name, default="")
        ident = SOURCE_ID.fullmatch(field("id").strip())
        title = _clean(field("title"), 500)
        published, updated = _date(field("published")), _date(field("updated"))
        if not ident or not title or not published or not updated:
            continue
        key = ident[1]
        abstract = _clean(field("summary"), 5000)
        authors = [_clean(author.findtext(ATOM + "name"), 100) for author in entry.findall(ATOM + "author")[:12]]
        item = {"id": key, "title": title, "authors": [a for a in authors if a], "published": published,
                "updated": updated, "url": "https://arxiv.org/abs/" + key,
                "repository_url": _repository(abstract, _clean(entry.findtext("{http://arxiv.org/schemas/atom}comment"), 3000)),
                "_abstract": abstract}
        if key not in items or updated > items[key]["updated"]:
            items[key] = item
    if entries and not items:
        raise ValueError("invalid entries or upstream error")
    return list(items.values())


def _words(value):
    for token in _TOKEN.findall(value.lower()):
        if token[0] >= "\u3400":
            yield from (token[i:i+2] for i in range(len(token)-1))
        elif token not in _STOP:
            yield token


def _rank(items, docs, feedback):
    local_terms = []
    known = set()
    for doc in docs[:50]:
        marker = doc.get("arxiv") or {}
        if isinstance(marker.get("id"), str):
            known.add(marker["id"].lower())
        identity = doc.get("bibliographic_identity") or {}
        title = identity.get("title") or ""
        chunks = doc.get("chunks") or []
        first = " ".join((row.get("text") or "")[:900] for row in chunks[:12]
                         if row.get("page") in (1, 2) and not row.get("arxiv_stamp"))
        local_terms.append(set(_words(title[:500] + " " + first)))
    for item in items:
        words = set(_words(item["title"] + " " + item.pop("_abstract")))
        best = set()
        match_score = 0.0
        for terms in local_terms:
            common = words & terms
            score = len(common) / math.sqrt(len(terms) * len(words)) if terms and words else 0.0
            if score > match_score:
                best, match_score = common, score
        # Two shared terms are required before claiming a local match.
        relevance = round(match_score * 100) if len(best) >= 2 else 0
        vote = feedback.get(item["id"], 0)
        already = item["id"] in known
        repo_bonus = 8 if item["repository_url"] else 0
        base = relevance + repo_bonus - (100 if already else 0)
        reasons = [f"与活动文库最佳匹配共享 {len(best)} 个词项" if relevance else
                   ("未找到明显的本地词项重合" if local_terms else "活动文库为空，未计算相关度")]
        if repo_bonus:
            reasons.append("arXiv 摘要或备注提到 GitHub 仓库，未核实是否可用或可复现")
        if already:
            reasons.append("活动文库中已记录相同 arXiv 编号")
        item["recommendation"] = {"base_score": base, "score": base + 18 * vote,
                                  "vote": vote, "reasons": reasons}
    items.sort(key=lambda item: (item["recommendation"]["score"], item["published"], item["id"]), reverse=True)
    return items


def refresh(category, documents, feedback, *, consent=False):
    """Manual refresh only. Return source metadata with deterministic local scores."""
    started = monotonic()

    def result(items=None, code=None, message=None):
        return {"provider": "arXiv", "endpoint": ENDPOINT, "category": category if category in CATEGORIES else None,
                "retrieved_at": datetime.now(timezone.utc).isoformat() if code is None else None,
                "elapsed_ms": round((monotonic() - started) * 1000),
                "items": items or [], "error": {"code": code, "message": message} if code else None}

    if consent is not True:
        return result(code="consent_required", message="请先确认本次 arXiv 公开分类刷新。")
    if category not in CATEGORIES:
        return result(code="invalid_category", message="请选择列表内的公开 arXiv 分类。")
    if not valid_feedback(feedback):
        return result(code="invalid_feedback", message="本地反馈格式不正确。")
    if not _LOCK.acquire(blocking=False):
        return result(code="busy", message="已有 arXiv 刷新正在执行。")
    global _NEXT_REQUEST
    try:
        if monotonic() < _NEXT_REQUEST:
            return result(code="cooldown", message="请等待至少 3 秒再手动刷新。")
        try:
            proc = _fetch(category)
        except subprocess.TimeoutExpired:
            return result(code="timeout", message="arXiv 请求超时，未自动重试。")
        except OSError:
            return result(code="network_error", message="无法启动 arXiv 请求。")
        finally:
            _NEXT_REQUEST = monotonic() + 3
    finally:
        _LOCK.release()
    errors = {
        2: ("timeout", "arXiv 请求超时，未自动重试。"),
        3: ("rate_limited", "arXiv 限流，请稍后手动刷新。"),
        4: ("redirect_refused", "已拒绝 arXiv 请求跳转。"),
        5: ("http_error", "arXiv 暂时未能返回结果。"),
        6: ("invalid_response", "arXiv 返回内容超过处理上限。"),
        7: ("network_error", "无法连接 arXiv，可继续离线使用原文检索。"),
    }
    if proc.returncode:
        code, msg = errors.get(proc.returncode, errors[7])
        return result(code=code, message=msg)
    try:
        items = _parse(proc.stdout)
    except (ValueError, ET.ParseError, RecursionError):
        return result(code="invalid_response", message="arXiv 返回了无法解析的题录。")
    return result(_rank(items, documents, feedback))


if __name__ == "__main__":
    status, body = _download(sys.argv[1] if len(sys.argv) == 2 else None)
    sys.stdout.buffer.write(body)
    sys.exit(status)
