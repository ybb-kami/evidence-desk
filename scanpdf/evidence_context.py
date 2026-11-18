"""Choose compact source sentences while retaining offsets and competing measurements."""
import re

from hybrid_retrieval import _terms

SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])|(?<=[。！？])")
MAX_SOURCE_CHARS = 2600
MAX_SOURCES = 8
_FILLER = set(_terms("paper study research report result experiment author approach model number according proposed"))


def terms(text):
    return set(_terms(text, query=True)) - _FILLER


def sentences(text):
    bounds = [0, *(match.end() for match in SENTENCE_END.finditer(text)), len(text)]
    for a, b in zip(bounds, bounds[1:]):
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b-1].isspace():
            b -= 1
        if b > a:
            yield a, b


def select_excerpts(hits, candidates=(), query=""):
    """Rank within returned passages. Every selected interval is a verbatim substring."""
    query_terms = terms(query)
    suggested = {(row["hit_index"],row["context_start"],row["context_end"]) for row in candidates[:3]}
    options = []
    for index, hit in enumerate(hits[:8]):
        text = hit["text"]
        for start, end in sentences(text):
            if end-start < 10:
                continue
            # PDF extractors sometimes return an entire column without punctuation.
            # Window that span on whitespace and rank each window using the question.
            a = start
            while a < end:
                b = min(a + 620, end)
                if b < end:
                    cut = text.rfind(" ", a+340, b)
                    if cut >= 0:
                        b = cut
                raw = text[a:b]
                matched = len(query_terms & terms(raw)) / max(1, len(query_terms))
                reader = any(i == index and a <= c and d <= b for i,c,d in suggested)
                score = 1.25 * matched + 0.26 * reader + 0.38 / (1 + index/2)
                options.append((score, index, a, b))
                if b == end:
                    break
                a = b
                while a < end and text[a].isspace():
                    a += 1
    selected = {}
    per_hit = {}
    seen_text = set()
    total = 0
    for _, i, a, b in sorted(options, key=lambda x:(-x[0], x[1], x[2])):
        raw = hits[i]["text"][a:b]
        normalized = " ".join(raw.split()).casefold()
        if (normalized in seen_text or per_hit.get(i,0) >= 3 or total+len(raw)>MAX_SOURCE_CHARS):
            continue
        selected[len(selected)+1] = i,a,raw
        total += len(raw)
        seen_text.add(normalized)
        per_hit[i] = per_hit.get(i,0)+1
        if len(selected) == MAX_SOURCES:
            break
    return selected
