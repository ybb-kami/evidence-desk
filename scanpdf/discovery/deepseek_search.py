"""Call the official DeepSeek Search and Fetch APIs with explicit per-call approval."""

from datetime import datetime, timezone
from http.client import HTTPException
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


PROVIDER = "DeepSeek Search API"
API_BASE = "https://api.deepseek.com/v1/tools"
MAX_TERMS = 128
RESULT_COUNT = 5
TIMEOUT = 10
MAX_RESPONSE = 1024 * 1024
MAX_FETCH_CHARS = 8000
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_NONPUBLIC_SUFFIXES = {"localhost", "local", "internal", "invalid", "test", "example", "onion", "arpa"}
_NUMERIC_SUFFIX = re.compile(r"[0-9]+|0x[0-9a-f]*", re.ASCII)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request):
    # Authorization is bound to the two official endpoints and standard TLS verification.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=TIMEOUT)


def _error(code, message, operation="search", **details):
    return {"code": code, "message": message, "operation": operation, **details}


def _send(operation, body, key, authorize):
    request = Request(f"{API_BASE}/{operation}", method="POST",
                      data=json.dumps(body, ensure_ascii=True).encode("ascii"),
                      headers={"Authorization": "Bearer " + key,
                               "Content-Type": "application/json", "Accept": "application/json"})
    # Ledger errors propagate, including a denial after an earlier Search succeeded.
    if authorize(operation) is not True:
        return None, _error("not_authorized", "未获本次 DeepSeek 联网请求的授权。", operation)
    try:
        with _open(request) as response:
            if response.status != 200:
                return None, _error("http_error", "DeepSeek 未返回检索内容。", operation,
                                    status=response.status)
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        if 300 <= status < 400:
            return None, _error("redirect_refused", "已拒绝 DeepSeek API 重定向。", operation, status=status)
        if status == 429:
            return None, _error("rate_limited", "DeepSeek 请求达到频率限制。", operation, status=status)
        return None, _error("http_error", "DeepSeek 未返回检索内容。", operation, status=status)
    except TimeoutError:
        return None, _error("timeout", "DeepSeek 联网请求超时。", operation)
    except (URLError, OSError, HTTPException, ValueError):
        return None, _error("network_error", "未能连接 DeepSeek 联网服务。", operation)
    try:
        if len(raw) > MAX_RESPONSE:
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, RecursionError):
        return None, _error("invalid_response", "DeepSeek 返回了无法读取的内容。", operation)
    return payload, None


def _public_url(value):
    if (not isinstance(value, str) or not 0 < len(value) <= 2048 or not value.isprintable()
            or any(char.isspace() or char == "\\" for char in value)):
        return False
    try:
        parts = urlsplit(value)
        labels = (parts.hostname or "").encode("idna").decode("ascii").lower().split(".")
        return (parts.scheme == "https" and parts.username is None and parts.password is None
                and parts.port in (None, 443) and len(labels) > 1 and labels[-1] not in _NONPUBLIC_SUFFIXES
                and not _NUMERIC_SUFFIX.fullmatch(labels[-1])
                and all(_HOST_LABEL.fullmatch(label) for label in labels))
    except (ValueError, UnicodeError):
        return False


def _text(value, maximum):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:maximum].encode("utf-8", "replace").decode("utf-8")


def _items(payload):
    if not isinstance(payload.get("results"), list):
        return None
    items = []
    seen = set()
    for row in payload["results"][:RESULT_COUNT]:
        if not isinstance(row, dict) or not _public_url(row.get("url")) or row["url"] in seen:
            continue
        seen.add(row["url"])
        items.append({"url": row["url"], "title": _text(row.get("title"), 500),
                      "snippet": _text(row.get("snippet"), 800), "summary": _text(row.get("summary"), 800),
                      "date": _text(row.get("date"), 80) or None})
    return items


def search(terms, *, authorize, fetch=False):
    """Search explicit user-entered terms. If requested, fetch the first eligible result.

    The caller provides a ledger authorizer returning True for each outgoing
    operation. Outcomes report bounded, untrusted external reference material.
    """
    result = {"provider": PROVIDER, "state": "error", "items": [], "fetch": None,
              "error": None, "retrieved_at": None}
    if (not isinstance(terms, str) or not terms.strip() or len(terms) > MAX_TERMS
            or not terms.isprintable() or type(fetch) is not bool):
        return result | {"error": _error("invalid_query", "请输入 1–128 字的单行公开检索词。")}
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return result | {"error": _error("not_configured", "尚未配置 DeepSeek API 密钥。")}
    if not re.fullmatch(r"[!-~]{1,512}", key) or key in terms:
        return result | {"error": _error("invalid_request", "DeepSeek 密钥或检索词设置无效。")}

    body = {"query": terms.strip(), "count": RESULT_COUNT, "need_content": False}
    payload, error = _send("search", body, key, authorize)
    if error is not None:
        return result | {"error": error}
    items = _items(payload)
    if items is None:
        return result | {"error": _error("invalid_response", "DeepSeek 返回了无法读取的检索结果。")}
    result.update(state="ok", items=items, retrieved_at=datetime.now(timezone.utc).isoformat())
    if not fetch or not items:
        return result

    url = items[0]["url"]
    page, error = _send("fetch", {"url": url}, key, authorize)
    if error is not None:
        return result | {"state": "partial", "error": error}
    content = page.get("content")
    if not isinstance(content, str) or not content.strip():
        return result | {"state": "partial",
                         "error": _error("invalid_response", "DeepSeek 返回了无法读取的网页内容。", "fetch")}
    excerpt = content[:MAX_FETCH_CHARS].encode("utf-8", "replace").decode("utf-8")
    return result | {"fetch": {"url": url, "text": excerpt,
                               "truncated": len(content) > MAX_FETCH_CHARS}}
