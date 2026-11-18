"""Evaluate extractive reader spans against the actual source and question."""
import os
import re
import threading
from pathlib import Path

from hybrid_retrieval import _terms, english_query


NUMERIC = re.compile(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|hundred|thousand|million|billion)\b", re.I)
ASK_NUMBER = re.compile(r"how (?:many|much|long)|what (?:percent|proportion|fraction|number|size|year|amount)|"
                        r"多少|几个|几种|几层|几张|几块|多大|多久|百分之", re.I)
COMPARE = re.compile(r"对比|比较|分别|各是|相差|差值|提升了多少|各自|versus|compare|difference|respectively", re.I)
# These words describe the request; they add no evidence about the requested field.
FILLER = set(_terms("paper study proposed proposed according authors reported experiment experiment model "
                    "research result method approach used those number values team article describe authors"))
FOCUS = re.compile(r"\b(?:how many|number of|what percentage of|what proportion of|how much)\s+"
                   r"(?:of\s+the\s+|of\s+|the\s+|different\s+)?([A-Za-z][\w-]*)", re.I)
WORD = re.compile(r"[A-Za-z][\w-]+")
# Match only explicit wording of the requested type. A nearby unrelated number
# does not substantiate a hardware, date, price, or other factual relation.
FIELD = (
    (r"\b(?:price|cost in dollars?|invoice|USD|dollars?)\b|美元|单价|发票", r"\$|\b(?:price|USD|dollars?|invoice)\b"),
    (r"\b(?:funded by|funder|funding agency)\b|资助机构", r"\b(?:fund(?:ed|ing)|grant|sponsor(?:ed)?)\b"),
    (r"\b(?:latency|running time|wall.clock)\b|耗时|时延", r"\b(?:latency|seconds?|minutes?|hours?|milliseconds?|ms)\b"),
    (r"\b(?:model of GPU|GPUs?|graphics cards?)\b|显卡", r"\b(?:GPUs?|graphics card|NVIDIA|TPUs?)\b"),
    (r"\bserial number\b|序列号", r"serial number|序列号"),
)
BAD_BOUNDARY = re.compile(r"^[.,;:!?%/]|^[\W_]+$")


class PassageReader:
    def __init__(self, model_path=None):
        self.path = model_path if model_path is not None else os.environ.get("EVIDENCE_QA_READER")
        self._reader = None
        self._lock = threading.Lock()

    def candidates(self, question, hits):
        if not self.path:
            return [], "disabled", ""
        translated = english_query(question) if re.search(r"[\u3400-\u9fff]", question) else question
        if not translated:
            return [], "translation_unavailable", ""
        if not self._lock.acquire(blocking=False):
            return [], "busy", translated
        try:
            if self._reader is None:
                from qa_reader import SpanReader
                self._reader = SpanReader(Path(self.path), threads=2, max_hits=5)
            return self._reader.read(translated, hits, limit=8), "ready", translated
        except (ImportError, OSError, RuntimeError, ValueError):
            return [], "unavailable", translated
        finally:
            self._lock.release()


def _surface(text):
    return set(_terms(text, query=True)) - FILLER


def assess_spans(question, english, candidates, hits):
    """Return source-bound candidates and, only on clear direct support, an extractive answer."""
    accepted = []
    scope = _surface(english)
    needs_number = bool(ASK_NUMBER.search(question + " " + english))
    focus = FOCUS.search(english)
    focus_tokens = _surface(focus[1]) if focus else set()
    for row in candidates:
        hit = hits[row["hit_index"]]
        quote = row["context"]
        span = row["answer"].strip()
        if (len(span) < 1 or len(span) > 200 or BAD_BOUNDARY.search(span)
                or hit["text"][row["context_start"]:row["context_end"]] != quote
                or hit["text"][row["answer_start"]:row["answer_end"]] != row["answer"]):
            continue
        if needs_number and not NUMERIC.search(span):
            continue
        if any(re.search(pattern, question + " " + english, re.I) and not re.search(source, quote, re.I)
               for pattern, source in FIELD):
            continue
        words = _surface(quote)
        coverage = len(words & scope) / max(1, len(scope))
        if not scope or not words.intersection(scope):
            continue
        # For a count, require the counted entity in its own source sentence.
        if focus_tokens and not focus_tokens.intersection(words):
            continue
        accepted.append((row, coverage))
    if not accepted:
        return [], None
    accepted.sort(key=lambda entry: (entry[0]["span_over_null"] + 8 * entry[1]), reverse=True)
    top, coverage = accepted[0]
    # This SQuAD-v1-family reader can extract a span even from unrelated sources.
    # Require independent surface evidence as well as a clear span preference.
    direct = (len(scope) >= 2 and coverage >= .65 and top["span_over_null"] >= 24
              and not COMPARE.search(question + " " + english))
    if needs_number and len(NUMERIC.findall(top["answer"])) > 4:
        direct = False
    for other, second_coverage in accepted[1:]:
        different_source = (other["page"], other["block"]) != (top["page"], top["block"])
        if (different_source and second_coverage >= coverage - .15 and
                other["span_over_null"] >= top["span_over_null"] - 4 and
                other["answer"].strip().casefold() != top["answer"].strip().casefold()):
            direct = False
    return [r for r, _ in accepted[:3]], top if direct else None
