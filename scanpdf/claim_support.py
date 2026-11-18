"""Conservative checks on an anchored answer's field and experimental conditions."""
import re

from condition_support import assess_conditions
from evidence_context import sentences, terms
from grounded_draft import _numbers
from passage_answer import FIELD
from measurement_units import supports_units

# Synonyms are only used to identify incompatible, explicitly named conditions.
_GROUPS = (
    (r"\btrain(?:ing)?\b|训练集", r"\bval(?:idation)?\b|验证集", r"\btest(?:ing)?\b|测试集"),
    (r"\bcontrol(?:s|led)?\b|对照组",r"\btreatment\b|治疗组|干预组"),
    (r"\bprecision\b|精确率",r"\brecall\b|召回率",r"\baccuracy\b|准确率"),
    (r"\bbaseline\b|基线",r"\bablated\b|消融"),
    (r"\bminimum\b|最小值",r"\bmedian\b|中位数",r"\bmaximum\b|最大值"),
)
_UNIT = (r"%|°[CF]|milliseconds?|microseconds?|nanoseconds?|seconds?|minutes?|hours?|days?|"
         r"milligrams?|kilograms?|grams?|kilometers?|kilometres?|meters?|metres?|liters?|litres?|"
         r"million|billion|trillion|[kMGT]?i?B|[kMGT]?Hz|[kMmunµμ]?s|[kmunµμ]?m|"
         r"[kmunµμ]?g|[kmunµμ]?L|[kMunµμ]?W|[kmunµμ]?V|[kM]?Pa|dB|rpm|fps|BLEU")
_SCALAR = re.compile(r"(?<![\w.])[+−-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?(?:\s*("
                     + _UNIT + r"))?(?!\w)", re.I)


def _sentence_around(hit, start, end):
    for a,b in sentences(hit["text"]):
        if a <= start < end <= b:
            return hit["text"][a:b]
    return hit["text"][start:end]


def _scalars(text):
    return [(m.group().strip(),(m[1] or "").casefold()) for m in _SCALAR.finditer(text)]


def check(question, english, hits, claim):
    hit = hits[claim["hit_index"]]
    raw = _sentence_around(hit, claim["answer_start"], claim["answer_end"])
    if not supports_units(claim["text"], claim.get("span") or raw):
        return "different_unit"
    query = question + " " + (english or "")
    if any(re.search(pattern, query, re.I) and not re.search(field, raw, re.I) for pattern,field in FIELD):
        return "field_missing"
    for group in _GROUPS:
        asked = [p for p in group if re.search(p,query,re.I)]
        mentioned = [p for p in group if re.search(p,raw,re.I)]
        if len(asked) == 1 and mentioned and asked[0] not in mentioned:
            return "different_condition"
    scope = assess_conditions(question, english, hits, claim)
    if scope.reason:
        return scope.reason
    # For a single stated measurement, look for another independently scoped
    # sentence supporting the same question at least as well but with a
    # different reading. Require the same unit and strong lexical overlap.
    if not _numbers(claim["text"]):
        return None
    query_words = terms(english or question)
    source_words = terms(raw)
    agreement = query_words & source_words
    valid = _scalars(raw)
    if len(agreement) < 2 or len(valid) != 1:
        return None
    for i,entry in enumerate(hits):
        for a,b in sentences(entry["text"]):
            other = entry["text"][a:b]
            if other == raw or (i, a, b) in scope.excluded_sentences:
                continue
            readings = _scalars(other)
            if (len(readings) != 1 or readings[0][1] != valid[0][1]
                    or _numbers(readings[0][0]) == _numbers(valid[0][0])):
                continue
            words = terms(other)
            if (agreement <= words and len(agreement)/max(1,len(query_words)) >= .6
                    and len(source_words & words)/max(1,len(source_words | words)) >= .40):
                return "ambiguous_measurement"
    return None
