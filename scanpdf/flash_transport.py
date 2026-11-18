"""Version-gated DeepSeek transport for the workbench's cited generator."""

from contextlib import contextmanager
import hmac
import http.client
import json
import os
import re
import ssl

from local_generation import LocalGenerator, MAX_OUTPUT
from provider import _decode


HOST = "api.deepseek.com"
MODEL_ALIAS = "deepseek-flash"
MODEL_NAME = "DeepSeek-V4.1-Flash"
MODEL_BADGE = "1017"
MODEL = f"{MODEL_NAME}-{MODEL_BADGE}"
MODEL_PATH = "/models"
CHAT_PATH = "/chat/completions"
METADATA_LIMIT = 65536
METADATA_TIMEOUT = 12
KEY_NAME = "DEEPSEEK_API_KEY"
USER_AGENT = "EvidenceDesk/1.0"

_MESSAGES = {
    "missing_key": "本机未配置 DeepSeek API 密钥。",
    "invalid_key": "DeepSeek API 密钥配置无效。",
    "metadata_unavailable": "暂时无法核对 DeepSeek 模型版本。",
    "unverified_model": "DeepSeek 未确认所需的 Flash 1017 模型版本。",
    "provider_unavailable": "DeepSeek 请求未完成。",
    "invalid_payload": "DeepSeek 请求格式无效。",
    "invalid_response": "DeepSeek 响应格式无效。",
    "credential_in_payload": "问题或原文含运行凭据，请核对后重试。",
    "authorization_failed": "无法核对本轮 DeepSeek 预算。",
}
_CITED_JSON = (
    ' Return one JSON object with exactly four keys: "status", "answer", "source", and "quote". '
    'Use status "answered" or "insufficient", a string for answer and quote, and an integer for source. '
    'For insufficient evidence, set source to 0 and answer and quote to empty strings. '
    'Output only the JSON object without markdown.'
)
_ALLOWED = frozenset(("messages", "temperature", "top_p", "max_tokens", "stream", "tools", "tool_choice",
                      "frequency_penalty", "presence_penalty", "stop", "stream_options", "logprobs", "top_logprobs"))


class FlashError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(_MESSAGES[reason])


def _api_key():
    key = os.environ.get(KEY_NAME, "").strip()
    if not key:
        raise FlashError("missing_key")
    if not re.fullmatch(r"[!-~]{1,512}", key):
        raise FlashError("invalid_key")
    return key


@contextmanager
def _response(method, path, key, *, body=None, accept="application/json", timeout, reason):
    # A direct HTTPS connection has no proxy handling or redirect behavior.
    conn = None
    try:
        conn = http.client.HTTPSConnection(HOST, 443, timeout=timeout, context=ssl.create_default_context())
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        if method == "GET":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = "Bearer " + key
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            raise FlashError(reason)
        yield response
    except (OSError, http.client.HTTPException):
        raise FlashError(reason) from None
    finally:
        if conn is not None:
            conn.close()


def _valid_badge(text):
    if isinstance(text, dict):
        translations = [value for locale, value in text.items()
                        if re.fullmatch(r"en(?:[-_][a-z]{2})?", locale, flags=re.I)]
    else:
        translations = [text]
    return bool(translations) and all(isinstance(value, str) and "".join(value.split()) == MODEL_BADGE
                                      for value in translations)


def _verify_model(key, timeout=METADATA_TIMEOUT):
    with _response("GET", MODEL_PATH, key, timeout=timeout, reason="metadata_unavailable") as response:
        raw = response.read(METADATA_LIMIT + 1)
    if len(raw) > METADATA_LIMIT:
        raise FlashError("unverified_model")
    try:
        data = _decode(raw)["data"]
        if not isinstance(data, list):
            raise TypeError
        matches = [entry for entry in data if isinstance(entry, dict) and entry.get("id") == MODEL_ALIAS]
        if len(matches) == 1 and matches[0].get("name") == MODEL_NAME:
            badge = matches[0]["client_metadata"]["deepseek-harness"]["badge"]["text"]
            if _valid_badge(badge):
                return
    except (ValueError, KeyError, TypeError, RecursionError):
        pass
    raise FlashError("unverified_model")


def get_status(*, verify=False):
    """Read local configuration, optionally checking the free official model list."""
    try:
        key = _api_key()
        if verify:
            _verify_model(key)
    except FlashError as exc:
        return {"status": "unavailable", "reason": exc.reason, "message": str(exc), "model": MODEL,
                "configured": exc.reason not in {"missing_key", "invalid_key"}, "verified": False}
    return {"status": "ready" if verify else "configured", "configured": True, "verified": verify, "model": MODEL}


def _payload(original):
    if not isinstance(original, dict):
        raise FlashError("invalid_payload")
    payload = {key: value for key, value in original.items() if key in _ALLOWED}
    messages = payload.get("messages")
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(message, dict) or not isinstance(message.get("content"), str)
                   for message in messages) or type(payload.get("stream", False)) is not bool):
        raise FlashError("invalid_payload")
    payload.update(model=MODEL_ALIAS, thinking={"type": "disabled"}, stream=payload.get("stream", False))
    if "response_format" in original:
        format_ = original["response_format"]
        if not isinstance(format_, dict) or format_.get("type") not in ("json_schema", "json_object", "text"):
            raise FlashError("invalid_payload")
        kind = format_["type"]
        payload["response_format"] = {"type": "json_object" if kind == "json_schema" else kind}
        if kind == "json_schema":
            if messages[0].get("role") == "system":
                payload["messages"] = [dict(messages[0], content=messages[0]["content"] + _CITED_JSON), *messages[1:]]
            else:
                payload["messages"] = [{"role": "system", "content": _CITED_JSON.strip()}, *messages]
    return payload


def _contains_key(value, key):
    if isinstance(value, str):
        return key in value
    if isinstance(value, dict):
        return any(_contains_key(name, key) or _contains_key(item, key) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, key) for item in value)
    return False


def _authorize(ticket, payload):
    # Import here so callers can inspect the transport's status without a ticket.
    from flash_costs import authorize

    try:
        if authorize(ticket, "model", payload) is False:
            raise FlashError("authorization_failed")
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
        # LocalGenerator handles these types as answer validation errors. Keep
        # budget and configuration failures distinguishable to the outer route.
        raise FlashError("authorization_failed") from None


class FlashGenerator(LocalGenerator):
    answer_method = "deepseek_cited_draft"

    def __init__(self, ticket):
        super().__init__(url="", model=MODEL)
        self.url = f"https://{HOST}{CHAT_PATH}"
        self.address = HOST, 443
        self.ticket = ticket

    @staticmethod
    def get_status(*, verify=False):
        return get_status(verify=verify)

    @contextmanager
    def connection(self, payload, timeout=75):
        key = _api_key()
        outgoing = _payload(payload)
        try:
            if _contains_key(outgoing, key):
                raise FlashError("credential_in_payload")
            body = json.dumps(outgoing, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            raise FlashError("invalid_payload") from None
        self.ticket.check_model_capacity()
        _verify_model(key, min(timeout, METADATA_TIMEOUT))
        _authorize(self.ticket, outgoing)
        accept = "text/event-stream" if outgoing["stream"] else "application/json"
        with _response("POST", CHAT_PATH, key, body=body, accept=accept,
                       timeout=timeout, reason="provider_unavailable") as response:
            yield response

    def legacy_transport(self, body, key):
        """Support existing JSON call sites without changing their response schema."""
        actual = _api_key()
        if not isinstance(key, str) or not key.isascii() or not hmac.compare_digest(actual, key):
            raise FlashError("invalid_key")
        try:
            outgoing = _decode(body)
            if not isinstance(outgoing, dict) or outgoing.get("stream", False) is not False:
                raise ValueError
        except (ValueError, TypeError, RecursionError):
            raise FlashError("invalid_payload") from None
        with self.connection(outgoing, timeout=30) as response:
            result = response.read(MAX_OUTPUT + 1)
        if len(result) > MAX_OUTPUT:
            raise FlashError("invalid_response")
        return result
