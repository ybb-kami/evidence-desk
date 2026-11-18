"""Offline extraction, conservative lexical retrieval, and duplicate hints."""
from collections import Counter
import hashlib
import math
import re
import unicodedata

import fitz

MAX_BYTES = 24 * 1024 * 1024
MAX_PAGES = 650
MAX_CHARS = 2_000_000
MAX_CHUNKS = 12_000
MAX_QUERY = 500
MAX_RESULTS = 8

_HAN = r"\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af"
_HAN_RUN = re.compile(f"[{_HAN}]+")
_HAN_SPLIT = re.compile(f"([{_HAN}]+)")
_WORDS = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
_ARXIV = re.compile(
    r"^arXiv:\s*((?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7}))(?:v([1-9]\d{0,5}))?"
    r"(?:\s+\[[a-zA-Z.\-]+\])?(?:\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4})?$",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    "a an and are as at be been by can could did do does for from had has have how "
    "i in is it its me of on or please show tell that the their these this those "
    "to us was were what when where which who why will with would you your".split()
)


def _normalized(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    # Prefer searchable words where the extractor preserved a discretionary split.
    text = re.sub(r"(?<=\w)(?:\u00ad|-)\s*\n\s*(?=\w)", "", text)
    return text.replace("\u00ad", "")


def _terms(text):
    normalized = _normalized(text)
    for run_or_word in _WORDS.findall(normalized):
        # PDF text can mix CJK and Latin without intervening whitespace.
        for part in _HAN_SPLIT.split(run_or_word):
            if not part:
                continue
            if _HAN_RUN.fullmatch(part):
                if len(part) == 1:
                    yield part
                else:
                    yield from (part[i:i + 2] for i in range(len(part) - 1))
            else:
                yield part


def _windows(text, width=1600, overlap=260):
    """Break long extracted blocks without joining text across a block/page."""
    start = 0
    while start < len(text):
        end = min(start + width, len(text))
        if end < len(text):
            # Preserve source line breaks and favor natural boundaries.
            earliest = start + width // 2
            boundary = max(text.rfind("\n", earliest, end), text.rfind(" ", earliest, end))
            if boundary >= earliest:
                end = boundary + 1
        fragment = text[start:end]
        excerpt = fragment.strip()
        if excerpt:
            yield excerpt, start + len(fragment) - len(fragment.lstrip())
        if end == len(text):
            break
        start = end - overlap
        # Do not cut a Latin word at the leading edge when avoidable.
        next_space = re.search(r"\s", text[start:start + 35])
        if next_space and not text[start - 1].isspace() and next_space.start() < end - start - 1:
            start += next_space.end()


def parse_pdf(data: bytes, name: str) -> dict:
    """Extract text-only blocks from an in-memory PDF; omit image and URI content."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("PDF must be a non-empty file of at most 24 MiB.")
    if b"%PDF-" not in data[:1024]:
        raise ValueError("Not a PDF file.")
    chunks, textless_pages, page_labels = [], [], []
    chars = 0
    arxiv_markers = set()
    content = hashlib.sha256()
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise ValueError("Password-protected PDF; unlock a local copy first.")
            pages = len(pdf)
            if not 1 <= pages <= MAX_PAGES:
                raise ValueError("PDF must contain between 1 and 650 pages.")
            for page in pdf:
                page_num = page.number + 1
                page_labels.append(page.get_label() or str(page_num))
                original_count = len(chunks)
                for raw in page.get_text("blocks", flags=fitz.TEXTFLAGS_BLOCKS, sort=True):
                    if raw[6] != 0:
                        continue
                    block_text = raw[4].strip()
                    marker = None
                    if page_num == 1:
                        x0, y0, x1, y1 = raw[:4]
                        width = page.cropbox.width
                        # arXiv stamps a narrow, rotated identifier at a first-page
                        # side margin. Do not infer identity from a prose citation.
                        if ((x1 < width * 0.15 or x0 > width * 0.85)
                                and x1 - x0 <= 32 and y1 - y0 >= 70):
                            marker = _ARXIV.fullmatch(" ".join(block_text.split()))
                            if marker:
                                arxiv_markers.add((marker[1].lower(), int(marker[2]) if marker[2] else None))
                    if not any(char.isalnum() for char in block_text):
                        continue
                    chars += len(block_text)
                    if chars > MAX_CHARS:
                        raise ValueError("PDF exceeds the extracted text limit (2 million characters).")
                    content.update(" ".join(_normalized(block_text).split()).encode("utf-8"))
                    content.update(b" ")
                    for part, (excerpt, offset) in enumerate(_windows(block_text), 1):
                        chunks.append({"page": page_num, "block": raw[5] + 1,
                                       "part": part, "offset": offset, "text": excerpt})
                        if marker:
                            chunks[-1]["arxiv_stamp"] = True
                        if len(chunks) > MAX_CHUNKS:
                            raise ValueError("PDF exceeds the extracted passage limit (12,000).")
                if len(chunks) == original_count:
                    textless_pages.append(page_num)
    except ValueError:
        raise
    except (RuntimeError, OSError) as exc:
        raise ValueError("Unable to extract text from this PDF.") from exc
    arxiv = None
    if len(arxiv_markers) == 1:
        arxiv_id, version = next(iter(arxiv_markers))
        arxiv = {"id": arxiv_id, "version": version}
    return {"id": hashlib.sha256(data).hexdigest(), "name": str(name), "pages": pages,
            "chunks": chunks, "size": len(data), "textless_pages": textless_pages,
            "page_labels": page_labels, "arxiv": arxiv, "text_hash": content.hexdigest(), "text_chars": chars}


def find_evidence(docs: list[dict], query: str) -> list[dict]:
    """Return ranked verbatim extracts, not generated answers or verified claims."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        return []
    query_terms = set(_terms(query)) - _STOPWORDS
    # Single Latin letters generally identify notation, not searchable evidence.
    query_terms = {term for term in query_terms
                   if len(term) > 1 or term.isdigit() or _HAN_RUN.fullmatch(term)}
    if not query_terms:
        return []
    minimum = len(query_terms) if len(query_terms) <= 3 else math.ceil(0.75 * len(query_terms))
    normalized_query = " ".join(_normalized(query).split())
    candidates = []
    for doc in docs:
        for chunk in doc["chunks"]:
            freqs = Counter(term for term in _terms(chunk["text"]) if term in query_terms)
            # A single Chinese character may be inside an indexed bigram.
            for term in query_terms - freqs.keys():
                if len(term) == 1 and _HAN_RUN.fullmatch(term):
                    count = chunk["text"].count(term)
                    if count:
                        freqs[term] = count
            if len(freqs) < minimum:
                continue
            exact = normalized_query in " ".join(_normalized(chunk["text"]).split())
            score = len(freqs) / len(query_terms)
            # Break ties by exact phrase, then by capped term occurrences.
            rank = (score, exact, sum(min(n, 3) for n in freqs.values()), -len(chunk["text"]))
            page = chunk["page"]
            try:
                label = doc["page_labels"][page - 1]
            except (IndexError, KeyError):
                label = str(page)
            candidates.append((rank, {"id": doc["id"], "name": doc["name"],
                                     "page_label": label, "score": round(score, 3), **chunk}))
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    results, seen = [], set()
    for _, row in candidates:
        key = row["id"], row["page"], row["block"]
        if key not in seen:
            results.append(row)
            seen.add(key)
            if len(results) >= MAX_RESULTS:
                break
    return results


def _signature(doc, limit=30_000):
    """Use unique, sufficiently varied 5-term shingles; refuse partial signatures."""
    tokens = []
    last_block, original = None, ""
    for chunk in doc["chunks"]:
        if chunk.get("arxiv_stamp"):
            continue
        block = chunk["page"], chunk["block"]
        if block != last_block:
            tokens.extend(_terms(original))
            if len(tokens) > limit:
                return set()
            last_block, original = block, chunk["text"]
        else:
            if "offset" not in chunk:
                return set()  # Cannot safely reconstruct old cached chunks.
            offset = chunk["offset"]
            if offset > len(original):
                original += " "
            original += chunk["text"][max(0, len(original) - offset):]
    tokens.extend(_terms(original))
    if len(tokens) > limit:
        return set()
    if len(tokens) < 80:
        return set()
    shingles = {tuple(tokens[i:i + 5]) for i in range(len(tokens) - 4)}
    return shingles if len(shingles) >= 70 else set()


def duplicates(newdoc: dict, docs: list[dict]) -> list[dict]:
    """Flag content similarity only. This does not establish version order."""
    matches, signature = [], None
    for doc in docs:
        match = {"id": doc["id"], "name": doc["name"]}
        if newdoc["id"] == doc["id"]:
            matches.append({**match, "reason": "identical_bytes", "score": 1.0})
            continue
        first_marker, second_marker = newdoc.get("arxiv"), doc.get("arxiv")
        shared_id = bool(first_marker and second_marker and first_marker["id"] == second_marker["id"])
        if (doc.get("text_chars", 0) >= 500 and newdoc.get("text_chars", 0) >= 500
                and doc.get("text_hash") and doc["text_hash"] == newdoc.get("text_hash")):
            matches.append({**match, "reason": "identical_extracted_text", "score": 1.0})
            continue
        if first_marker and second_marker and not shared_id:
            continue
        if signature is None:
            signature = _signature(newdoc)
        if not signature:
            continue
        other = _signature(doc)
        # A matching margin stamp lowers the advisory threshold, but cannot
        # substitute for substantial independent evidence from body text.
        threshold = 0.55 if shared_id else 0.9
        if not other or min(len(signature), len(other)) / max(len(signature), len(other)) < threshold:
            continue
        similarity = len(signature & other) / len(signature | other)
        if similarity >= threshold:
            row = {**match, "reason": "possible_version", "score": round(similarity, 3)}
            if shared_id:
                first, second = first_marker["version"], second_marker["version"]
                row.update(arxiv_id=first_marker["id"], new_version=first, existing_version=second)
                if similarity >= 0.65 and first and second and first != second:
                    highest = max(first, second)
                    row["advice"] = (
                        f"两份首页边栏均标有 arXiv:{first_marker['id']}，且正文高度相似。"
                        f"若要引用较新的版本，建议核对并使用 v{highest}；两份文件均保留。"
                    )
                else:
                    row["advice"] = "首页 arXiv 编号一致且正文有较多重合；请人工确认版本，不自动替换。"
            matches.append(row)
    order = {"identical_bytes": 0, "identical_extracted_text": 1, "possible_version": 2}
    return sorted(matches, key=lambda row: (order[row["reason"]], -row["score"], row["name"]))
