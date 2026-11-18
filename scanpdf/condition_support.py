"""Bind a numeric reading to named conditions within the retrieved source."""

from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from evidence_context import sentences
from passage_answer import ASK_NUMBER, COMPARE


_SPLITS = {
    "validation-size": r"\bvalidat(?:ion|ing)\b|验证",
    "training-size": r"\btrain(?:ing)?\b|训练",
    "test-size": r"\btest(?:ing)?\b|测试",
}
_METRICS = {
    "latency": r"\b(?:latency|(?:inference|response|running|execution) time|wall.clock)\b|延迟|时延|耗时",
    "throughput": r"\b(?:throughput|(?:samples|tokens|words|images|requests) per second)\b|吞吐量",
    "accuracy": r"\baccuracy\b|准确率",
    "precision": r"\bprecision\b|精确率",
    "recall": r"\brecall\b|召回率",
    "error-rate": r"\berror rate\b|错误率",
    "perplexity": r"\bperplexity\b|困惑度",
    "bleu": r"\bbleu\b",
    "epochs": r"\bepochs?\b|训练轮数|训练多少轮|训练[^。！？]{0,12}多少轮",
    "memory": r"\b(?:memory capacity|VRAM|GPU memory|memory usage)\b|显存|内存容量",
}
_FIELD = {key: re.compile(pattern, re.I) for key, pattern in {**_SPLITS, **_METRICS}.items()}
_COUNT_REQUEST = re.compile(r"\b(?:size|number|count|words|samples|items|records|examples)\b|大小|数量|样本数|词数|多少")
_AGGREGATE = re.compile(r"\b(?:range|confidence interval|lower bound|upper bound)\b|范围|区间", re.I)
_EXCLUDE = re.compile(r"\b(?:rather than|instead of|as opposed to)\b|而不是|而非", re.I)

_NAME = r"(?:[A-Z][A-Za-z0-9_-]+|[A-Z]|\d+[A-Z][A-Za-z0-9_-]+)"
_NAME_PHRASE = rf"{_NAME}(?:[ \t]+{_NAME}){{0,3}}"
_NAMED_DATA = r"(?:corpus|corpora|data\s*sets?|benchmarks?|cohorts?)"
_NAMED_SETUP = r"(?:conditions?|groups?|settings?|configurations?|treatments?)"
_DATA_SIZE = re.compile(r"\b(large|small)\s+(?:datasets?|data sets?|corpora|corpus)\b", re.I)
_BOTH_SIZES = re.compile(
    r"\b(?:large\s+and\s+small|small\s+and\s+large)\s+(?:data\s*sets?|corpora)\b|大小数据集|(?:大型和小型|小型和大型)数据集",
    re.I,
)
_DATA_CONTEXT = re.compile(r"\b(?:experiment|experiments|text from|data from|dataset|corpus|benchmark)\b", re.I)
_ACRONYM = re.compile(
    rf"\b(?P<long>{_NAME_PHRASE})[ \t]+\((?P<abbr>[A-Z]{{2,8}})\)"
    rf"(?P<tail>(?:[ \t]+{_NAME}){{0,2}})"
)
_HARDWARE = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:GeForce\s+)?(?:RTX|GTX)\s+(?:[A-Z]+\s*)?\d{2,5}(?:\s+Ti)?"
    r"|(?:NVIDIA|AMD|Intel|Google)\s+(?:(?:Tesla|Xeon|TPU|GPU)\s+)?[A-Z]*\d{2,5}[A-Za-z]*"
    r"|[A-Z]{1,4}\d{2,5}[A-Z]?)(?![A-Za-z0-9])"
)
_HARDWARE_CONTEXT = re.compile(r"\b(?:CPUs?|GPUs?|TPUs?|accelerators?|hardware)\b|硬件|显卡|加速器", re.I)
_SETPOINT = re.compile(
    r"(?:(?:\b(?i:at|under|with)\s+)|在\s*)([+-]?\d+(?:\.\d+)?)\s*(°[CF]|K\b|[kM]?Pa\b|bar\b|GPUs?\b|CPUs?\b)",
)
_GENERIC = {
    "a", "an", "the", "this", "these", "both", "same", "different", "other", "another",
    "our", "the first", "validation", "training", "test", "small", "large", "data", "dataset",
    "under", "in", "on", "at", "for", "with", "from", "using", "of", "per", "each", "all",
}

_CLAUSE_BREAK = re.compile(
    r"\(|\)|[;；，]|(?<!\d),|,(?!\d)|"
    r"\s+(?:while|whereas|but)\s+|"
    r"\s+and\s+(?=(?:the\s+)?(?:remaining|following|training\s+set|validation\s+set|test\s+set|on\b|under\b)"
    r"|\d+\s+epochs?\s+for\b)",
    re.I,
)
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<value>[+−-]?\d+(?:,\d{3})*(?:\.\d+)?)(?![\d.,]\d)"
    r"(?:\s*(?P<unit>millions?|billions?|thousands?|万|亿|%|milliseconds?|microseconds?|"
    r"seconds?|minutes?|hours?|ms|us|µs|μs|s|min|h|GiB|GB|MiB|MB|epochs?|轮)(?![A-Za-z]))?",
    re.I,
)
_SCALE = {
    "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
    "万": 10_000, "亿": 100_000_000,
}
_TIME = {
    "microsecond": Decimal("0.000001"), "us": Decimal("0.000001"),
    "μs": Decimal("0.000001"), "millisecond": Decimal("0.001"), "ms": Decimal("0.001"),
    "second": 1, "s": 1, "minute": 60, "min": 60, "hour": 3600, "h": 3600,
}
_BYTES = {"mb": 1_000_000, "gb": 1_000_000_000, "mib": 1_048_576, "gib": 1_073_741_824}
_RANGE = re.compile(r"\s*(?:to|and|[-–—~～]|至|到)\s*", re.I)


@dataclass(frozen=True)
class Scope:
    axis: str
    aliases: frozenset


@dataclass(frozen=True)
class Reading:
    field: str
    hit: int
    sentence: tuple
    offsets: tuple
    values: frozenset
    scopes: tuple


@dataclass(frozen=True)
class Assessment:
    reason: str | None = None
    excluded_sentences: frozenset = frozenset()


def _name(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _trim_name(text):
    words = text.strip().split()
    while words and words[0].casefold() in _GENERIC:
        words.pop(0)
    return " ".join(words)


def _scope(axis, *names):
    return Scope(axis, frozenset(_name(name) for name in names if name))


def _named_patterns(label):
    return (
        re.compile(rf"\b(?i:{label})\s+(?:(?i:called|named)\s+)?(?P<name>{_NAME_PHRASE})"),
        re.compile(rf"\b(?P<name>{_NAME_PHRASE})\s+(?i:{label})\b"),
    )


_SCOPE_NAMES = {"dataset": _named_patterns(_NAMED_DATA), "condition": _named_patterns(_NAMED_SETUP)}
_CHINESE_NAME = re.compile(
    rf"(?P<name>{_NAME_PHRASE}|[甲乙丙丁])\s*(?P<axis>语料库|语料|数据集|条件|组)"
)


def _scope_marks(text, hardware=False, data_context=False):
    for axis, patterns in _SCOPE_NAMES.items():
        for side, pattern in enumerate(patterns):
            for match in pattern.finditer(text):
                name = _trim_name(match["name"])
                if side == 0 and re.fullmatch(r"[A-Z]", match["name"]):
                    name = match["name"]
                if name and len(name) <= 50:
                    yield match.span("name"), _scope(axis, name)
    for match in _DATA_SIZE.finditer(text):
        yield match.span(1), _scope("dataset-size", match[0], match[1] + " dataset")
    for match in _CHINESE_NAME.finditer(text):
        axis = "dataset" if match["axis"] in {"语料库", "语料", "数据集"} else "condition"
        yield match.span("name"), _scope(axis, match["name"])
    if data_context or _DATA_CONTEXT.search(text):
        for match in _ACRONYM.finditer(text):
            long = _trim_name(match["long"])
            if not long or "".join(word[0] for word in long.split()).upper() != match["abbr"]:
                continue
            tail = match["tail"]
            yield match.span(), _scope("dataset", long, long + tail, match["abbr"], match["abbr"] + tail)
    if hardware or _HARDWARE_CONTEXT.search(text):
        for match in _HARDWARE.finditer(text):
            name = re.sub(r"^(?:NVIDIA|AMD|Intel|Google)\s+(?:Tesla\s+)?", "", match[0])
            yield match.span(), _scope("hardware", match[0], name)
    for match in _SETPOINT.finditer(text):
        unit = match[2].casefold().rstrip("s")
        yield match.span(), _scope("setting:" + unit, match[1] + unit, match[1] + " " + unit)


def _clauses(text, start, end):
    depth, current = 0, start
    for match in _CLAUSE_BREAK.finditer(text, start, end):
        if match[0] == "(":
            depth += 1
        elif match[0] == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            yield current, match.start()
            current = match.end()
    yield current, end


def _number(match, field):
    value = Decimal(match["value"].replace(",", "").replace("−", "-"))
    unit = _name(match["unit"] or "").rstrip("s")
    if unit in _SCALE:
        return value * _SCALE[unit], ""
    unit = _name(match["unit"] or "")
    singular = unit[:-1] if unit.endswith("s") and len(unit) > 2 else unit
    if field == "latency" and singular in _TIME:
        return value * _TIME[singular], "time"
    if field == "memory" and unit in _BYTES:
        return value * _BYTES[unit], "bytes"
    return value, unit


def _measurement(text, field, mark_offsets):
    eligible = [m for m in _NUMBER.finditer(text) if not any(
        start <= m.start("value") < end for start, end in mark_offsets
    )]
    if field in {"latency", "memory"}:
        dimension = "time" if field == "latency" else "bytes"
        typed = [m for m in eligible if _number(m, field)[1] == dimension]
        if typed:
            eligible = typed
    if not eligible:
        return None
    if len(eligible) > 1:
        if len(eligible) != 2:
            return None
        a, b = eligible
        separator = text[a.end():b.start()]
        if re.fullmatch(r"\s*\(", separator) and text[b.end():].lstrip().startswith(")"):
            # An approximation followed by a precise parenthetical count.
            if (a["unit"] or "").casefold().rstrip("s") in _SCALE:
                return [a, b], frozenset({_number(b, field)})
        if not (_RANGE.fullmatch(separator) and (
            "-" in separator or any(c in separator for c in "–—~～至到")
            or "to" in separator.casefold() or re.search(r"\bbetween\s*$", text[:a.start()], re.I)
        )):
            return None
    return eligible, frozenset(_number(m, field) for m in eligible)


def _query_fields(question):
    fields = [field for field in _METRICS if _FIELD[field].search(question)]
    if fields:
        return fields
    if ASK_NUMBER.search(question) or _COUNT_REQUEST.search(question):
        return [field for field in _SPLITS if _FIELD[field].search(question)]
    return []


def _readings(hits, fields, doc_id):
    hardware_fields = {"latency", "throughput", "memory"}
    for i, hit in enumerate(hits[:8]):
        if hit.get("id") != doc_id:
            continue
        source = hit["text"]
        active = {}
        for a, b in sentences(source):
            whole = source[a:b]
            hardware = bool(hardware_fields.intersection(fields) or _HARDWARE_CONTEXT.search(whole))
            data_context = bool(_DATA_CONTEXT.search(whole))
            for start, end in _clauses(source, a, b):
                text = source[start:end]
                marks = list(_scope_marks(text, hardware, data_context))
                local = {}
                for _, scope in marks:
                    local.setdefault(scope.axis, []).append(scope.aliases)
                for axis, aliases in local.items():
                    first = aliases[0]
                    if all(first & other for other in aliases[1:]):
                        active[axis] = Scope(axis, frozenset().union(*aliases))
                    else:
                        active.pop(axis, None)
                used = [field for field in fields if _FIELD[field].search(text)]
                if len(used) != 1:
                    continue
                field, = used
                measured = _measurement(text, field, [pos for pos, _ in marks])
                if measured:
                    numbers, values = measured
                    yield Reading(field, i, (a, b), tuple((start+m.start(), start+m.end()) for m in numbers),
                                  values, tuple(active.values()))


def _alias_in(question, alias):
    if len(alias) == 1 and alias.isascii():
        # Single-letter labels need an explicit marker ("condition A"). The
        # ordinary English article "a" cannot choose a condition.
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", question))


def _requested(question, known, hardware):
    focused = _EXCLUDE.split(question, maxsplit=1)[0]
    normalized = _name(focused)
    result = {}
    for scope in known:
        if any(_alias_in(normalized, alias) for alias in scope.aliases):
            result.setdefault(scope.axis, []).append(scope)
    for _, scope in _scope_marks(focused, hardware):
        aliases = result.setdefault(scope.axis, [])
        if not any(scope.aliases & item.aliases for item in aliases):
            aliases.append(scope)
    if _BOTH_SIZES.search(focused):
        result["dataset-size"] = [_scope("dataset-size", name + " dataset", name + " datasets")
                                  for name in ("large", "small")]
    return result


def _scope_relation(scoped, requested):
    return {axis: any(scoped[axis].aliases & scope.aliases for scope in asked)
            for axis, asked in requested.items() if axis in scoped}


def assess_conditions(question, english, hits, claim):
    """Find unsupported scope and identify explicitly irrelevant comparisons."""
    english = english or ""
    focus = _EXCLUDE.split(question + " " + english, maxsplit=1)[0]
    fields = _query_fields(focus)
    if not fields or not _NUMBER.search(claim["text"]):
        return Assessment()
    readings = list(_readings(hits, fields, hits[claim["hit_index"]].get("id")))
    known = {scope for reading in readings for scope in reading.scopes}
    hardware = any(scope.axis == "hardware" for scope in known)
    requested = _requested(question, known, hardware)
    for axis, scopes in _requested(english, known, hardware).items():
        requested.setdefault(axis, scopes)
    annotated = [(reading, {scope.axis: scope for scope in reading.scopes}) for reading in readings]
    cited = []
    for reading, scoped in annotated:
        if reading.hit != claim["hit_index"] or not any(
            claim["answer_start"] <= start and end <= claim["answer_end"] for start, end in reading.offsets
        ):
            continue
        claim_values = {_number(match, reading.field)[0] for match in _NUMBER.finditer(claim["text"])}
        selected_values = claim_values.intersection(value for value, _ in reading.values)
        if selected_values:
            relation = _scope_relation(scoped, requested)
            matches = len(relation) == len(requested) and all(relation.values())
            cited.append((reading, scoped, selected_values, matches))
    supported = {(reading.field, value) for reading, _, values, matches in cited if matches for value in values}
    if any((reading.field, value) not in supported
           for reading, _, values, matches in cited if not matches for value in values):
        return Assessment("different_condition")

    is_comparison = COMPARE.search(focus) or _AGGREGATE.search(focus)
    excluded = set()
    relevant = set()
    for reading, scoped, _, matches in cited:
        if not matches:
            continue
        for other, other_scopes in annotated:
            if other.field != reading.field:
                continue
            sentence = (other.hit, *other.sentence)
            relation = _scope_relation(other_scopes, requested)
            if False in relation.values():
                excluded.add(sentence)
                continue
            relevant.add(sentence)
            if is_comparison or other.values == reading.values or len(relation) != len(requested):
                continue
            # A difference needs two distinct, named conditions on the same axis,
            # and comparable units. Unlabelled context alone cannot prove ambiguity.
            if {unit for _, unit in other.values} != {unit for _, unit in reading.values}:
                continue
            if any(scope.axis in scoped and scope.axis not in requested
                   and not scope.aliases & scoped[scope.axis].aliases for scope in other.scopes):
                return Assessment("ambiguous_measurement")
    return Assessment(excluded_sentences=frozenset(excluded - relevant))
