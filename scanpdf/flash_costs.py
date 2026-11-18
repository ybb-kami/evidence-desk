"""Reserve a worst-case USD charge for this operation before it leaves the workbench."""

from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import os

from paid_budget import BudgetExceeded

_RATE_NAMES = {
    "input": "DEEPSEEK_INPUT_USD_PER_MILLION",
    "output": "DEEPSEEK_OUTPUT_USD_PER_MILLION",
    "search": "DEEPSEEK_SEARCH_USD_PER_CALL",
    "fetch": "DEEPSEEK_FETCH_USD_PER_CALL",
}


def public_error(error):
    reason = getattr(error, "reason", "")
    messages = {
        "unknown_price": "尚未配置已核实的 DeepSeek 美元单价，本次付费操作已停止。",
        "money_limit": "演示预留金额已到 0.71 美元上限。",
        "round_limit": "演示已达 100 个付费轮次。",
        "model_limit": "演示已达 100 次模型调用。",
        "round_kind_limit": "本轮已达该付费操作次数上限。",
        "budget_unavailable": "无法读取演示预算账本，本次付费操作已停止。",
        "request_limit": "请求超过本次演示的输入输出上限。",
    }
    from flash_transport import FlashError
    return str(error) if isinstance(error, FlashError) else messages.get(reason, "本次付费操作未完成。")


def _rate(name):
    raw = os.environ.get(_RATE_NAMES[name], "").strip()
    try:
        rate = Decimal(raw)
    except InvalidOperation:
        rate = Decimal(0)
    if not rate.is_finite() or not Decimal("0.000000001") <= rate <= 1000:
        raise BudgetExceeded("unknown_price")
    return rate


def rates_ready(kind):
    try:
        for name in ("input", "output") if kind == "model" else (kind,):
            _rate(name)
    except BudgetExceeded:
        return False
    return True


def authorize(ticket, kind, payload=None):
    if kind == "model":
        if not isinstance(payload, dict):
            raise BudgetExceeded("invalid_cost")
        output = payload.get("max_tokens")
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if type(output) is not int or not 1 <= output <= 2400 or size > 32000:
            raise BudgetExceeded("request_limit")
        # Each UTF-8 byte can occupy at most one byte-fallback token. Include
        # the full serialized body and an additional allowance for chat framing.
        input_bound = size + 2048
        usd = (input_bound * _rate("input") + output * _rate("output")) / 1_000_000
    elif kind in ("search", "fetch"):
        usd = _rate(kind)
    else:
        raise BudgetExceeded("invalid_cost")
    cost = int((usd * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))
    ticket.reserve(kind, cost)
    return True
