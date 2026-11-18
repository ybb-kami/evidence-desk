"""Choose compact source sentences while retaining offsets and competing measurements."""
from collections import Counter
from dataclasses import dataclass
import math
import re

from hybrid_retrieval import _EXCLUDED, _terms, english_query

SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])|(?<=[。！？])")
MAX_SOURCE_CHARS = 2600
MAX_SOURCES = 8
_WINDOW_CHARS = 620
_FILLER = set(_terms("paper study research report result experiment author approach model number according proposed"))
_CONDITION_LABEL = re.compile(
    r"\b(?:condition|cohort|configuration|dataset|group|regime|setting|stage|trial)\s+([a-z]|\d+)\b", re.I
)
_NUMBER_REQUEST = re.compile(
    r"\b(?:how (?:many|much|long)|what (?:percentage|proportion|value))\b|多少|[数总]量|比例", re.I
)
_NUMBER = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?")
_REFERENCE = re.compile(
    r"\b(fig(?:ure)?s?\.?|tables?|eq(?:uation)?s?\.?|sections?|algorithms?)\s*[(\[]?\s*"
    r"(\d+(?:[.-]\d+)*[a-z]?)\b|(图|表|式|算法|节)\s*[(（]?\s*(\d+(?:[.-]\d+)*[a-z]?)", re.I
)
_REFERENCE_KINDS = {"fig": "figure", "eq": "equation", "图": "figure", "表": "table", "式": "equation",
                    "算法": "algorithm", "节": "section"}


@dataclass(frozen=True)
class _Window:
    start: int
    end: int
    keywords: frozenset
    phrases: frozenset
    reader: bool
    has_number: bool


def _tokens(text):
    return tuple(word for word in _terms(text, query=True) if word not in _FILLER)


def terms(text):
    return set(_tokens(text))


def sentences(text):
    bounds = [0, *(match.end() for match in SENTENCE_END.finditer(text)), len(text)]
    for a, b in zip(bounds, bounds[1:]):
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b-1].isspace():
            b -= 1
        if b > a:
            yield a, b


def _condition_labels(text):
    return {"condition:" + match[1].casefold() for match in _CONDITION_LABEL.finditer(text)}


def _conditions(words):
    return {word for word in words if word.startswith("condition:")}


def _references(text):
    for kind, index, han_kind, han_index in _REFERENCE.findall(text):
        kind = kind.lower().rstrip(".s") or han_kind
        yield _REFERENCE_KINDS.get(kind, kind), (index or han_index).lower()


def _intervals(text):
    for start, end in sentences(text):
        while end - start >= 10:
            cut = min(start + _WINDOW_CHARS, end)
            if cut < end:
                whitespace = text.rfind(" ", start + 400, cut)
                if whitespace > 0:
                    cut = whitespace
            yield start, cut
            if cut == end:
                break
            # A long unpunctuated passage should retain phrases that straddle a cut.
            start = cut - 64
            while start < cut and not text[start - 1].isspace() and not text[start].isspace():
                start += 1
            if start == cut:
                start = cut - 64
            while start < end and text[start].isspace():
                start += 1


def _windows(text, suggested):
    intervals = set(_intervals(text))
    # Retain a bounded reader interval even where PDF punctuation differs.
    intervals.update((start, end) for start, end in suggested
                     if 0 <= start < end <= len(text) and end - start <= _WINDOW_CHARS)
    for start, end in sorted(intervals):
        raw = text[start:end]
        words = _tokens(raw)
        yield _Window(start, end, frozenset(words) | _condition_labels(raw),
                      frozenset(zip(words, words[1:])),
                      any(start <= left < right <= end for left, right in suggested), bool(_NUMBER.search(raw)))


def _query_features(query):
    wanted = set()
    excluded = set()
    phrases = set()
    references = set()
    for variant in filter(None, (query, english_query(query))):
        focus, *contrast = _EXCLUDED.split(variant, 1)
        words = _tokens(focus)
        wanted.update(set(words) | _condition_labels(focus))
        phrases.update(zip(words, words[1:]))
        references.update(_references(focus))
        if contrast:
            excluded.update(terms(contrast[0]) | _condition_labels(contrast[0]))
    return wanted, phrases, excluded - wanted, references


def _extend(hits, selected, wanted, excluded, requested_conditions, references, total):
    """Keep an adjacent lead-in or continuation when spare characters allow it."""
    spans = [list(pick) for pick in selected]
    for position, (index, start, end) in enumerate(selected):
        text = hits[index]["text"]
        unscoped = bool(references and references.isdisjoint(_references(text)))
        adjacent = []
        for a, b in sentences(text):
            if b <= start and not text[b:start].strip():
                adjacent.append((a, end))
            if a >= end and not text[end:a].strip():
                adjacent.append((start, b))
        eligible = []
        for a, b in adjacent:
            extra = text[a:start] + text[end:b]
            labels = _condition_labels(extra)
            more = terms(extra) | labels
            overlap = len(wanted & more)
            # A number from another experiment can look relevant when attached
            # to a source sentence. With an explicit figure/table/etc. scope,
            # require a matching reference before adding a numeric neighbor.
            if unscoped and _NUMBER.search(_REFERENCE.sub("", extra)):
                continue
            if (b-a <= _WINDOW_CHARS and len(extra) + total <= MAX_SOURCE_CHARS
                    and (overlap or len(extra) <= 125) and not (excluded & more)
                    and (not requested_conditions or not labels or requested_conditions & labels)
                    and not any(i == index and p < b and a < q for j, (i, p, q) in enumerate(spans) if j != position)):
                eligible.append((-overlap, len(extra), a, b))
        if eligible:
            _, added, a, b = min(eligible)
            spans[position] = index, a, b
            total += added
    return spans


def select_excerpts(hits, candidates=(), query=""):
    """Select mutually useful source windows without changing the retrieved passage order."""
    hits = hits[:MAX_SOURCES]
    if not hits:
        return {}
    wanted, phrases, excluded, references = _query_features(query)
    requested_conditions = _conditions(wanted)
    excluded_conditions = _conditions(excluded)
    numeric = bool(_NUMBER_REQUEST.search(query))
    suggested = [(row["hit_index"], row["context_start"], row["context_end"]) for row in candidates[:3]]
    windows = [list(_windows(hit["text"], [(a, b) for i, a, b in suggested if i == index]))
               for index, hit in enumerate(hits)]
    blocks = [(hit.get("id"), hit.get("page"), hit["block"]) if "block" in hit else (index,)
              for index, hit in enumerate(hits)]
    locations = {block: set() for block in blocks}
    for block, rows in zip(blocks, windows):
        for row in rows:
            locations[block].update(row.keywords & wanted)
    counts = Counter(word for words in locations.values() for word in words)
    weights = {word: 1 + math.log((1 + len(locations)) / (1 + count)) for word, count in counts.items()}
    weight_total = math.fsum(weights.values()) or 1

    def coverage(words):
        return math.fsum(weights[word] for word in words) / weight_total

    options = []
    for index, rows in enumerate(windows):
        for row in rows:
            matched = row.keywords & weights.keys()
            labels = _conditions(row.keywords)
            if labels & excluded_conditions and not labels & requested_conditions:
                continue
            phrase_score = len(row.phrases & phrases) / max(1, len(phrases))
            rank = 0.32 / (1 + index / 3)
            different_condition = bool(requested_conditions and labels and not requested_conditions & labels)
            score = (1.40 * coverage(matched) + 0.30 * phrase_score + 0.26 * row.reader + rank
                     + 0.10 * (numeric and row.has_number and bool(matched))
                     - 0.30 * bool(different_condition or excluded & row.keywords))
            normalized = " ".join(hits[index]["text"][row.start:row.end].split()).casefold()
            options.append((score, index, row.start, row.end, matched, normalized))

    selected = []
    per_block = Counter()
    covered = set()
    seen_text = set()
    total = 0
    while len(selected) < MAX_SOURCES:
        best = None
        for score, i, a, b, matched, normalized in options:
            count = per_block[blocks[i]]
            if (count >= 3 or normalized in seen_text or total + b-a > MAX_SOURCE_CHARS
                    or any(i == previous and a < end and start < b for previous, start, end in selected)):
                continue
            merit = score + 0.42 * coverage(matched - covered) - 0.18 * count
            if best is None or merit > best[0]:
                best = merit, i, a, b, matched, normalized
        if best is None:
            break
        _, i, a, b, matched, normalized = best
        selected.append((i, a, b))
        total += b-a
        covered.update(matched)
        seen_text.add(normalized)
        per_block[blocks[i]] += 1
    expanded = _extend(hits, selected, wanted, excluded, requested_conditions, references, total)
    return {label: (i, a, hits[i]["text"][a:b]) for label, (i, a, b) in enumerate(expanded, 1)}
