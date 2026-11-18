"""Opt-in public bibliographic discovery; never receives document state."""
import json
import re
from datetime import datetime, timezone
from http.client import HTTPException
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ENDPOINT = "https://api.crossref.org/v1/works"
TIMEOUT = 5
ROWS = 5
MAX_BODY = 1024 * 1024
MAX_QUERY = 300
FIELDS = "DOI,title,author,issued,container-title,type"
# Deliberately conservative DOI subset; omit questionable links, do not resolve them here.
DOI = re.compile(r"10\.\d{4,9}/[A-Z0-9._;():/-]{1,256}", re.IGNORECASE | re.ASCII)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(req, timeout):
    # A fixed host, verified HTTPS, no environment proxies, auth, cookies, or redirects.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout)


def _clean(value, cap):
    return " ".join(value.split())[:cap] if isinstance(value, str) else ""


def _first(value, cap):
    return _clean(value[0], cap) if isinstance(value, list) and value else ""


def _item(record):
    if not isinstance(record, dict):
        return None
    doi = record.get("DOI")
    title = _first(record.get("title"), 500)
    if not title or not isinstance(doi, str) or not DOI.fullmatch(doi):
        return None
    if any(part in ("", ".", "..") for part in doi.split("/")):
        return None

    authors = []
    raw_authors = record.get("author")
    if isinstance(raw_authors, list):
        for person in raw_authors[:40]:
            if isinstance(person, dict):
                name = _clean(person.get("name"), 120) or " ".join(
                    filter(None, (_clean(person.get("given"), 60), _clean(person.get("family"), 60)))
                )
                if name:
                    authors.append(name)
    issued = record.get("issued")
    parts = issued.get("date-parts") if isinstance(issued, dict) else None
    year = None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        value = parts[0][0]
        if type(value) is int and 1000 <= value <= 9999:
            year = value
    return {
        "title": title, "authors": authors, "year": year, "doi": doi,
        "url": "https://doi.org/" + quote(doi, safe="/"),
        "venue": _first(record.get("container-title"), 250),
        "kind": _clean(record.get("type"), 80),
    }


def search_public(query: str) -> dict:
    """Search terms must be supplied by the caller after explicit user consent.

    Results are unverified bibliographic leads, not full text or scientific evidence.
    Failures are returned as stable error codes without disclosing the request URL.
    """
    started = monotonic()

    def result(items=None, code=None, message=None, **details):
        return {
            "provider": "Crossref", "endpoint": ENDPOINT,
            "retrieved_at": datetime.now(timezone.utc).isoformat() if code is None else None,
            "elapsed_ms": round((monotonic() - started) * 1000),
            "items": items or [],
            "error": {"code": code, "message": message, **details} if code else None,
        }

    if (not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY
            or not query.isprintable()):
        return result(code="invalid_query", message="请输入 1–300 字的单行公开检索词。")

    url = ENDPOINT + "?" + urlencode({
        "query.bibliographic": query.strip(), "rows": ROWS, "select": FIELDS,
    })
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "EvidenceDesk/0.2"})
    try:
        with _open(req, timeout=TIMEOUT) as response:
            data = response.read(MAX_BODY + 1)
    except HTTPError as exc:
        status = exc.code
        retry = exc.headers.get("Retry-After", "") if exc.headers else ""
        exc.close()
        if status == 429:
            hint = int(retry) if retry.isascii() and retry.isdecimal() and len(retry) <= 6 else None
            return result(code="rate_limited", message="Crossref 限流，请稍后手动重试。",
                          status=status, retry_after_seconds=hint)
        if 300 <= status < 400:
            return result(code="redirect_refused", message="已拒绝 Crossref 的跳转请求。", status=status)
        return result(code="http_error", message="Crossref 暂时未能返回结果。", status=status)
    except TimeoutError:
        return result(code="timeout", message="Crossref 请求超时，请稍后手动重试。")
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return result(code="timeout", message="Crossref 请求超时，请稍后手动重试。")
        return result(code="network_error", message="无法连接 Crossref；请检查网络或继续离线使用。")
    except (OSError, ValueError, HTTPException):
        return result(code="network_error", message="无法连接 Crossref；请检查网络或继续离线使用。")

    try:
        if len(data) > MAX_BODY:
            raise ValueError
        payload = json.loads(data)
        if (payload["status"] != "ok" or payload["message-type"] != "work-list"
                or not isinstance(payload["message"]["items"], list)):
            raise ValueError
        records = payload["message"]["items"]
    except (KeyError, TypeError, ValueError, RecursionError):
        return result(code="invalid_response", message="Crossref 返回了无法解析的检索结果。")

    items = []
    seen = set()
    for row in records[:ROWS]:
        item = _item(row)
        if item and item["doi"].lower() not in seen:
            items.append(item)
            seen.add(item["doi"].lower())
    return result(items)
