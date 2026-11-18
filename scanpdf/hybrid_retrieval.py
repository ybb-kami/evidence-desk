"""Local passage retrieval using BM25, term proximity, and query coverage."""

from collections import Counter, defaultdict
from functools import lru_cache
import math
import os
from pathlib import Path
import re
import subprocess
import threading

import engine

if __package__:
    from .bilingual import local as bilingual
else:
    from bilingual import local as bilingual


_HERE = Path(__file__).resolve().parent
_TRANSLATION_PROCESS = threading.Lock()
_HAN = re.compile(rf"[{engine._HAN}]+")
_TOKEN = re.compile(r"\d+(?:\.\d+)+|[^\W_]+")
_LATIN = re.compile(r"[a-z]+\Z")
_STOP = engine._STOPWORDS | frozenset(
    "about according affect affects affected also answer approach approaches article author authors based "
    "between briefly compare compared define defined definition describe described describes different difference differences "
    "explain find get given influence influences many mean meaning mention mentioned method methods much "
    "must our paper provide rather really refer referred related report reported researcher researchers same "
    "say should state study them themselves they use used using versus way we work write".split()
)
_HAN_FILLER = re.compile(
    r"这篇论文|这项研究|论文中|文章中|该论文|该文章|本文|文中|作者|请问|请解释|请说明|"
    r"是否|为什么|怎么样|怎样|怎么|如何|什么|哪种|哪些|哪个|在哪里|多少|几种|分别|"
    r"使用了|采用了|提到|描述了|是|的|了|吗|呢|的确|有没有|进行|通过|以及|还有"
)
_CLAUSE = re.compile(
    r"[?？。;；]+|(?:[,，]\s*)?\b(?:and|or|as well as)\s+"
    r"(?=(?:what|which|why|where|when|who|how|whether|does|do|did|is|are|was|were|can|will)\b)"
    r"|[,，]\s*(?:以及|并且|另外|还有)|(?:以及|并且|又)(?=如何|怎样|怎么|是否|会不|是什么)",
    re.IGNORECASE,
)
_COORDINATION = re.compile(r"\b(?:and|versus|as well as)\b|以及|与|和", re.IGNORECASE)
_PAIRED = re.compile(r"\b(?:between|overlap|correlation|relationship|association)\b|之间|重合|重叠|相关", re.I)
_EXCLUDED = re.compile(r"\b(?:rather than|instead of|as opposed to)\b|而不是|而非", re.I)
_GREEK = dict(zip("αβγδεθλμπρστφω", (
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu", "pi", "rho", "sigma",
    "tau", "phi", "omega",
)))
_INDEXED_GREEK = re.compile(rf"(?<!\w)({'|'.join((*_GREEK, *_GREEK.values()))})[\s_-]*(\d+)(?!\w)")
_MATH = str.maketrans({"√": " square root ", "∑": " sum ", "∏": " product ",
                      "∞": " infinity ", "∇": " gradient "})


@lru_cache(maxsize=16384)
def _stem(word):
    if word in _GREEK:
        return _GREEK[word]
    if len(word) < 4 or not _LATIN.fullmatch(word):
        return word
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif word.endswith(("ches", "shes", "sses", "xes", "zes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is", "ous", "ics")):
        word = word[:-1]
    for suffix in ("ization", "isation", "izing", "ising", "ized", "ised", "ize", "ise"):
        if len(word) - len(suffix) >= 3 and word.endswith(suffix):
            return word[:-len(suffix)] + "ize"
    if len(word) > 7 and word.endswith(("tional", "sional", "mental", "oidal")):
        return word[:-2]
    if len(word) >= 8 and word.endswith("ement"):
        word = word[:-4]
    for suffix in ("ing", "ed"):
        if len(word) - len(suffix) >= 3 and word.endswith(suffix):
            word = word[:-len(suffix)]
            if word.endswith(("bb", "dd", "gg", "mm", "nn", "pp", "rr", "tt")):
                word = word[:-1]
            break
    if len(word) >= 5 and word.endswith("e") and not word.endswith(("ee", "oe", "ie")):
        return word[:-1]
    return word


def _terms(text, *, query=False):
    normalized = engine._normalized(text).translate(_MATH)
    normalized = _INDEXED_GREEK.sub(lambda match: _GREEK.get(match[1], match[1]) + match[2], normalized)
    if query:
        normalized = _HAN_FILLER.sub(" ", normalized)
    for run in _TOKEN.findall(normalized):
        for piece in engine._HAN_SPLIT.split(run):
            if not piece or piece in engine._STOPWORDS:
                continue
            if _HAN.fullmatch(piece):
                if len(piece) == 1:
                    yield piece
                else:
                    yield from (piece[i:i + 2] for i in range(len(piece) - 1))
            elif not query or piece not in _STOP:
                if len(piece) > 1 or piece.isnumeric() or piece in _GREEK:
                    yield _stem(piece)


class _Index:
    def __init__(self, passages):
        self.terms = []
        self.postings = defaultdict(list)
        vocabulary = {}
        for index, passage in enumerate(passages):
            tokens = tuple(vocabulary.setdefault(term, term) for term in _terms(passage))
            self.terms.append(tokens)
            for term, count in Counter(tokens).items():
                self.postings[term].append((index, count))
        self.size = sum(bool(terms) for terms in self.terms)
        self.average = sum(map(len, self.terms)) / max(self.size, 1)
        self.has_latin = any(_LATIN.fullmatch(term) for term in self.postings)


@lru_cache(maxsize=3)
def _index(passages):
    # The exact ordered text is the cache key. OCR edits and scoped lookups
    # select a new entry even when they refer to the same PDF bytes.
    return _Index(passages)


@lru_cache(maxsize=128)
def _translate(query, python, model_root):
    if not python:
        return bilingual.translate("zh-en", query)
    if not _TRANSLATION_PROCESS.acquire(timeout=5):
        raise TimeoutError("Local translation is busy")
    try:
        result = subprocess.run(
            [python, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
             "from bilingual.local import translate; print(translate('zh-en', sys.stdin.read()))", str(_HERE)],
            input=query, text=True, encoding="utf-8", capture_output=True, timeout=5, check=True,
            env=dict(os.environ, EVIDENCE_TRANSLATION_MODELS=str(model_root)),
        )
    finally:
        _TRANSLATION_PROCESS.release()
    return result.stdout.strip()


def english_query(query):
    """Return the optional local Chinese-to-English translation, or an empty string."""
    if not isinstance(query, str) or len(query) > engine.MAX_QUERY or not _HAN.search(query):
        return ""
    try:
        root = bilingual.root().resolve()
        python = os.environ.get("EVIDENCE_RAG_TRANSLATION_PYTHON", "")
        model = root / "zh-en"
        if not (model / "model/model.bin").is_file() or not (model / "sentencepiece.model").is_file():
            return ""
        if not python and "zh-en" not in bilingual.available():
            return ""
        return _translate(query, python, root)
    except (ImportError, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return ""


def _query_variants(query, translated):
    options = {}
    for text in (translated, query):
        if not text:
            continue
        whole = tuple(_terms(text, query=True))
        if whole:
            options.setdefault(whole, -1)
        clauses = [tuple(_terms(clause, query=True)) for clause in _CLAUSE.split(text)]
        clauses = [clause for clause in clauses if clause]
        if len(clauses) > 1:
            for branch, terms in enumerate(clauses):
                if len(set(terms)) >= 2:
                    options.setdefault(terms, branch)
        elif len(whole) >= 4 and not _PAIRED.search(text):
            coordinated = [tuple(_terms(clause, query=True)) for clause in _COORDINATION.split(text)]
            if 2 <= len(coordinated) <= 3 and all(len(set(terms)) >= 2 for terms in coordinated):
                for branch, terms in enumerate(coordinated):
                    options.setdefault(terms, branch)
    return options


def _locality(tokens, ordered, matches, weights):
    if len(matches) < 2:
        return 0.0
    pairs = {(left, right) for left, right in zip(ordered, ordered[1:])
             if left != right and left in matches and right in matches}
    positions = [(i, term) for i, term in enumerate(tokens) if term in matches]
    seen, left = Counter(), 0
    closest = len(tokens)
    for right, (_, term) in enumerate(positions):
        seen[term] += 1
        while len(seen) == len(matches):
            closest = min(closest, positions[right][0] - positions[left][0] + 1)
            left_term = positions[left][1]
            seen[left_term] -= 1
            if not seen[left_term]:
                del seen[left_term]
            left += 1
    proximity = min(1.0, (len(matches) * 3 / closest) ** 0.5)
    adjacent = sum(min(weights[a], weights[b]) for a, b in pairs.intersection(zip(tokens, tokens[1:])))
    available = sum(min(weights[a], weights[b]) for a, b in pairs)
    return proximity * 0.6 + (adjacent / available if available else 0.0) * 0.4


def _rank(index, ordered, recall=False, qualifiers=()):
    terms = tuple(dict.fromkeys((*ordered, *qualifiers)))
    count = len(terms)
    expanded = recall and count > 2
    minimum = count
    if count > 2:
        minimum = 2 if expanded else max(2, math.ceil(count * 0.55))
    common = {term: index.postings.get(term, ()) for term in terms}
    if sum(bool(rows) for rows in common.values()) < minimum:
        return
    numeric = {term for term in terms if term[0].isdigit()}
    if expanded:
        # Out-of-vocabulary prose does not vote against an otherwise useful candidate.
        # An explicitly requested number still has to occur in the source.
        if any(not common[term] for term in numeric):
            return
        common = {term: rows for term, rows in common.items() if rows}
    weights = {term: math.log1p((index.size - len(rows) + 0.5) / (len(rows) + 0.5))
               for term, rows in common.items()}
    total = sum(weights.values())
    overlaps = defaultdict(dict)
    for term, rows in common.items():
        for passage, frequency in rows:
            overlaps[passage][term] = frequency
    for passage, frequencies in overlaps.items():
        if len(frequencies) < minimum or not numeric.issubset(frequencies):
            continue
        cover = sum(weights[term] for term in frequencies) / total
        if cover < (0.24 if expanded else 0.55):
            continue
        tokens = index.terms[passage]
        normalization = 1.2 * (0.25 + 0.75 * len(tokens) / index.average)
        bm25 = sum(weights[term] * frequency / (frequency + normalization)
                   for term, frequency in frequencies.items()) / total
        proximity = _locality(tokens, ordered, frequencies, weights)
        if expanded:
            informative = any(weights[term] >= math.log(2) for term in frequencies)
            # Keep a short lexical overlap only if it is local. A common-word
            # overlap needs the stronger signal of an adjacent query phrase.
            if ((not informative and proximity <= 0.60)
                    or (len(frequencies) == 2 and proximity < 0.50)):
                continue
        # Short headings can locate a topic, but a fuller source usually
        # carries more context. Exact short source passages still qualify.
        substance = min(1.0, 0.76 + 0.04 * len(tokens))
        score = (0.50 * cover + 0.30 * bm25 + 0.20 * cover * proximity) * substance
        yield passage, score


def retrieve(doc, query, limit=8):
    """Return source passages with original anchors and a ranking score."""
    return _retrieve(doc, query, limit, recall=False)


def retrieve_candidates(doc, query, limit=8):
    """Return broader lexical candidates for fusion with an operational dense index."""
    return [dict(hit, retrieval_candidate=True) for hit in _retrieve(doc, query, limit, recall=True)]


def _retrieve(doc, query, limit, *, recall):
    if (not isinstance(query, str) or not query.strip() or len(query) > engine.MAX_QUERY
            or type(limit) is not int or limit <= 0):
        return []
    chunks = doc.get("chunks", ())
    if not chunks:
        return []
    index = _index(tuple(chunk["text"] if not chunk.get("arxiv_stamp") else "" for chunk in chunks))
    if not index.size:
        return []
    translated = english_query(query) if index.has_latin else ""
    qualifiers = ()
    if recall:
        query = _EXCLUDED.split(query, maxsplit=1)[0]
        translated = _EXCLUDED.split(translated, maxsplit=1)[0]
        qualifiers = tuple(term for term in _terms(query, query=True) if term[0].isdigit())
    candidates = defaultdict(dict)
    for terms, branch in _query_variants(query, translated).items():
        for position, score in _rank(index, terms, recall, qualifiers):
            scores = candidates[position]
            scores[branch] = max(scores.get(branch, 0), score)
    if not candidates:
        return []

    best = defaultdict(float)
    for scores in candidates.values():
        for branch, score in scores.items():
            best[branch] = max(best[branch], score)
    fraction = 0.45 if recall else 0.65
    candidates = {position: {branch: score for branch, score in scores.items()
                             if score >= best[branch] * fraction}
                  for position, scores in candidates.items()}
    candidates = {position: scores for position, scores in candidates.items() if scores}
    results, selected_branches = [], set()
    labels = doc.get("page_labels", ())
    while candidates and len(results) < limit:
        position = max(candidates, key=lambda p: (
            max(score * (1.0 if branch == -1 or branch not in selected_branches else 0.80)
                for branch, score in candidates[p].items()),
            candidates[p].get(-1, 0.0), -p,
        ))
        scores = candidates.pop(position)
        chunk = chunks[position]
        page, block = chunk["page"], chunk["block"]
        tokens = index.terms[position]
        selected_branches.update(scores)
        candidates = {p: other_scores for p, other_scores in candidates.items()
                      if ((chunks[p]["page"], chunks[p]["block"]) != (page, block)
                          and index.terms[p] != tokens)}
        results.append(dict(chunk, id=doc["id"], name=doc["name"], score=round(max(scores.values()), 4),
                            page_label=labels[page - 1] if 0 < page <= len(labels) else str(page)))
    return results
