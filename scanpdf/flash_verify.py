"""Apply the workbench's budgeted Flash transport to legacy quote verification."""

import app
import provider
from paid_budget import BudgetExceeded


def handle(handler):
    req = handler.json_request()
    question, ids = req.get("query"), req.get("ids", [])
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise app.Bad(400, "请输入 1 到 500 字的检索词。")
    try:
        question = question.strip().encode("utf-8").decode("utf-8")
    except UnicodeError:
        raise app.Bad(400, "检索词包含无效字符。") from None
    if (not isinstance(ids, list) or len(ids) > app.MAX_DOCS
            or any(not isinstance(doc_id, str) or not app.ID.fullmatch(doc_id) for doc_id in ids)):
        raise app.Bad(400, "文献选择无效。")
    if req.get("consent") is not True:
        raise app.Bad(403, "请先确认可将问题和公开原文摘录发送给 DeepSeek。")
    if not handler.server.enable_model or not provider.configured():
        return handler.payload(200, {"status": "not_configured", "quotes": [],
                                     "answer": "未启用 DeepSeek。离线检索仍可使用。"})
    hits = app.find_evidence(handler.server.library.records(ids), question)
    if not hits:
        return handler.payload(200, {"status": "insufficient_evidence", "quotes": [],
                                     "answer": "没有足够原文证据。"})
    try:
        flash = handler.flash_model()
    except BudgetExceeded:
        raise app.Bad(429, "无法为本次 DeepSeek 请求预留预算。") from None
    denied = None

    def transport(body, key):
        nonlocal denied
        try:
            return flash.legacy_transport(body, key)
        except BudgetExceeded as exc:
            denied = exc.reason
            raise

    try:
        result = provider.summarize(question, hits, transport=transport)
    except RuntimeError:
        if denied:
            raise app.Bad(429, "无法为本次 DeepSeek 请求预留预算。") from None
        raise app.Bad(502, "本次未能调用 DeepSeek。") from None
    if result["status"] == "supported":
        result["answer"] = f"{flash.model} · {result['answer']}"
    return handler.payload(200, dict(result, model=flash.model))
