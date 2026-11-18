"""Bounded multilingual comparison of original, source-anchored PDF passages."""

from collections import Counter
import re
from time import perf_counter
import unicodedata
from urllib.parse import urlencode

import app
from graph import _DOI_LINE, _clean_doi, _normalize_title
from local_review import _body_chunks
from semantic import DenseUnavailable, MODEL, REVISION
from synthesis import HEADING, NEXT_SECTION


MAX_PAGES = 3
MAX_PASSAGES = 5
MAX_CHARS = 360
MIN_SCORE = 0.84
MIN_MARGIN = 0.015
BODY = re.compile(
    r"^(?:(?:1|I)[.\s]+)?(?:introduction|background|materials and methods|methods|results|引言|绪论|背景|方法|结果)\b\s*[:：]?(?:\s*\n|$)",
    re.I,
)
SENTENCE_END = re.compile(r"[.?!](?=\s|$)|[。！？]")
REFERENCE_INDEX = re.compile(r"\[[\d\s,;–-]+\]")
NUMBER = re.compile(r"(?<!\w)[+−-]?\d+(?:[.,]\d+)*(?:\s*%)?(?!\w)")


def _anchor(doc, chunk, text):
    start = chunk["text"].find(text)
    if start < 0:
        raise ValueError("Version comparison requires a literal index span")
    coords = dict(id=doc["id"], page=chunk["page"], block=chunk["block"], part=chunk.get("part", 1))
    span = {} if chunk["text"].find(text, start + 1) != -1 else {"start": start, "end": start + len(text)}
    return {
        **coords, "text": text, "source": chunk.get("source", "native"),
        "source_url": "/source?" + urlencode({**coords, **span}),
        "pdf_url": f"/pdf/{doc['id']}#page={chunk['page']}",
    }


def _clip(text):
    text = text.strip()
    if len(text) <= MAX_CHARS:
        return text
    sample = text[:MAX_CHARS]
    boundary = max((m.end() for m in SENTENCE_END.finditer(sample) if m.end() >= 90), default=0)
    if not boundary:
        space = max(sample.rfind(" "), sample.rfind("\n"))
        boundary = space if space >= MAX_CHARS - 35 else MAX_CHARS
    return sample[:boundary].rstrip()


def _front(doc):
    return [chunk for chunk in _body_chunks(doc) if chunk["page"] <= MAX_PAGES]


def _title(doc, front):
    title = (doc.get("bibliographic_identity") or {}).get("title")
    if title:
        normalized = _normalize_title(title)
        for chunk in front:
            if chunk["page"] != 1:
                break
            lines = chunk["text"].split("\n")
            for start in range(len(lines)):
                for stop in range(start + 1, min(start + 6, len(lines) + 1)):
                    original = "\n".join(lines[start:stop]).strip()
                    if len(original) > MAX_CHARS:
                        break
                    if _normalize_title(original) == normalized:
                        return _anchor(doc, chunk, original)
    return None


def _abstract(doc, front):
    started = False
    for chunk in front:
        text = chunk["text"]
        match = HEADING.match(text)
        if match:
            started = True
            text = text[match.end():]
        end = NEXT_SECTION.search(text)
        if end:
            text = text[:end.start()]
        if started and len(text.strip()) >= 45:
            return _anchor(doc, chunk, _clip(text))
        if end:
            break
    return None


def _normalized(text):
    folded = unicodedata.normalize("NFKC", text)
    letters = r"(?<=[A-Za-z])[-\u00ad][ \t]*\n[ \t]*(?=[A-Za-z])"
    return " ".join(re.sub(letters, "", folded).replace("\u00ad", "").split())


def _passages(doc, front):
    excerpts, seen = [], set()
    started = False
    for chunk in front:
        text = chunk["text"]
        heading = BODY.match(text)
        if heading:
            started = True
            text = text[heading.end():]
        if not started or len(text.strip()) < 80 or chunk.get("part", 1) != 1:
            continue
        excerpt = _clip(text)
        key = _normalized(excerpt)
        if (len(key) < 80 or key in seen or not SENTENCE_END.search(key)
                or text.lstrip().startswith(("∗", "*", "†", "‡")) or re.match(r"^[a-z]", key)
                or sum(letter.isalpha() for letter in key) < len(key) * 0.5):
            continue
        seen.add(key)
        excerpts.append(_anchor(doc, chunk, excerpt))
        if len(excerpts) == MAX_PASSAGES:
            break
    return excerpts


def _markers(doc):
    chunks = (chunk for chunk in doc["chunks"] if chunk["page"] == 1)
    arxiv = doc.get("arxiv") or {}
    doi = (doc.get("bibliographic_identity") or {}).get("doi")
    markers = {}
    for chunk in chunks:
        if arxiv.get("id") and chunk.get("arxiv_stamp"):
            markers.setdefault("arxiv", {**arxiv, "anchor": _anchor(doc, chunk, chunk["text"])})
        if doi and "doi" not in markers:
            for line in chunk["text"].splitlines():
                if (match := _DOI_LINE.fullmatch(line.strip())) and _clean_doi(match[1]) == doi:
                    markers["doi"] = {"id": doi, "anchor": _anchor(doc, chunk, line.strip())}
                    break
    return markers


def _identity(before, after):
    left, right = _markers(before), _markers(after)
    common_keys = sorted(left.keys() & right.keys())
    shared = [key for key in common_keys if left[key]["id"] == right[key]["id"]]
    differing = [key for key in common_keys if left[key]["id"] != right[key]["id"]]
    if shared and differing:
        status = "conflicting_identifiers"
    elif shared:
        status = "shared_identifier"
    elif differing:
        status = "different_identifiers"
    else:
        status = "unverified"
    return {"status": status, "shared": shared, "different": differing, "left": left, "right": right}


def _matrix(dense, left, right):
    if not left or not right:
        return []
    left_text = [_normalized(a["text"]) for a in left]
    right_text = [_normalized(a["text"]) for a in right]
    forward = [dense.score_texts(text, right_text) for text in left_text]
    reverse = [dense.score_texts(text, left_text) for text in right_text]
    return [[(value + reverse[j][i]) / 2 for j, value in enumerate(row)] for i, row in enumerate(forward)]


def _numbers(anchor):
    return Counter(NUMBER.findall(REFERENCE_INDEX.sub("", anchor["text"])))


def _pair(left, right, score=None, *, candidate=False):
    result = {"left": left, "right": right, "cosine": round(score, 4) if score is not None else None}
    if not left or not right:
        result["change"] = "unpaired"
    elif _normalized(left["text"]) == _normalized(right["text"]):
        result["change"] = "same_wording"
    else:
        left_numbers, right_numbers = _numbers(left), _numbers(right)
        if left_numbers != right_numbers:
            result["number_tokens"] = {"left": list(left_numbers - right_numbers),
                                       "right": list(right_numbers - left_numbers)}
        result["change"] = "similar_candidate" if candidate else "changed_wording"
    return result


def _align(left, right, scores):
    indices = {}
    unused = set(range(len(right)))
    keys = [_normalized(anchor["text"]) for anchor in right]
    for i, anchor in enumerate(left):
        j = next((j for j in sorted(unused) if _normalized(anchor["text"]) == keys[j]), None)
        if j is not None:
            indices[i] = j
            unused.remove(j)
    if scores:
        remaining = [i for i in range(len(left)) if i not in indices]
        candidates = []
        for i in remaining:
            rank = sorted(unused, key=lambda j: (-scores[i][j], j))
            if not rank:
                break
            j = rank[0]
            reverse = sorted(remaining, key=lambda k: (-scores[k][j], k))
            score = scores[i][j]
            if (score >= MIN_SCORE and reverse[0] == i
                    and (len(rank) == 1 or score - scores[i][rank[1]] >= MIN_MARGIN)
                    and (len(reverse) == 1 or score - scores[reverse[1]][j] >= MIN_MARGIN)):
                candidates.append((i, j))
        indices.update(candidates)
        unused.difference_update(j for _, j in candidates)
    rows = []
    for i, anchor in enumerate(left):
        j = indices.get(i)
        rows.append(_pair(anchor, right[j], scores[i][j] if scores else None, candidate=True)
                    if j is not None else _pair(anchor, None))
    rows.extend(_pair(None, right[j]) for j in sorted(unused))
    return rows


def compare(before, after, *, semantic, dense):
    front_left, front_right = _front(before), _front(after)
    title = _title(before, front_left), _title(after, front_right)
    abstract = _abstract(before, front_left), _abstract(after, front_right)
    left, right = _passages(before, front_left), _passages(after, front_right)
    title_score = abstract_score = passage_scores = None
    state = "disabled" if not semantic else "no_text"
    reason = None
    started = perf_counter()
    if semantic and (all(title) or all(abstract) or (left and right)):
        try:
            if dense is None:
                raise DenseUnavailable("model_missing")
            title_scores = _matrix(dense, [title[0]], [title[1]]) if all(title) else []
            abstract_scores = _matrix(dense, [abstract[0]], [abstract[1]]) if all(abstract) else []
            passage_scores = _matrix(dense, left, right)
        except DenseUnavailable as exc:
            reason = exc.reason
            state = "unavailable"
        else:
            title_score = title_scores[0][0] if title_scores else None
            abstract_score = abstract_scores[0][0] if abstract_scores else None
            state = "ready"
    elapsed = round((perf_counter() - started) * 1000)
    return {
        "before": app.summary(before), "after": app.summary(after),
        "identity": _identity(before, after),
        "title": _pair(*title, title_score), "abstract": _pair(*abstract, abstract_score),
        "passages": _align(left, right, passage_scores),
        "semantic": {"state": state, "reason": reason, "elapsed_ms": elapsed,
                     "model": MODEL if state == "ready" else None, "revision": REVISION if state == "ready" else None},
        "scope": {"first_pages": MAX_PAGES, "passages_per_side": MAX_PASSAGES, "excerpt_chars": MAX_CHARS,
                  "left_sampled": len(left), "right_sampled": len(right)},
    }
