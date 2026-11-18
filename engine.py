"""Offline extraction, conservative lexical retrieval, and duplicate hints."""
from collections import Counter
import hashlib
from itertools import zip_longest
import math
import re
import unicodedata

import fitz

from graph import extract_identity

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
_GPU = ("gpu", "gpus", "graphics processing unit", "graphics processing units")
_OPTIMIZER = ("optimizer", "optimizers", "optimiser", "optimisers", "optimization algorithm",
              "optimisation algorithm")
_RESEARCH_TERMS = {
    "训练硬件": (*_GPU, "hardware", "tpu", "tpus", "cpu", "cpus", "accelerator", "accelerators"),
    "训练设备": (*_GPU, "hardware", "tpu", "tpus", "cpu", "cpus", "accelerator", "accelerators"),
    "图形处理器": _GPU,
    "显卡": _GPU,
    "优化器": _OPTIMIZER,
    "优化算法": _OPTIMIZER,
    "层归一化": ("layer normalization", "layer normalisation", "layer norm", "layernorm"),
    "批归一化": ("batch normalization", "batch normalisation", "batch norm", "batchnorm"),
    "残差连接": ("residual connection", "residual connections", "skip connection", "skip connections"),
    "学习率": ("learning rate", "learning rates"),
    "超参数": ("hyperparameter", "hyperparameters", "hyper parameter", "hyper parameters"),
    "训练步数": ("training steps", "steps"),
    "自注意力": ("self attention", "selfattention"),
    "激活函数": ("activation function", "activation functions"),
    "交叉熵": ("cross entropy", "crossentropy"),
    "权重衰减": ("weight decay",),
    "梯度裁剪": ("gradient clipping",),
    "困惑度": ("perplexity",),
    "贝塔": ("beta", "β"),
}
_QUERY_FILLER = re.compile(
    r"请问|请指出|请给出|告诉我|给出|列出|在论文中|在文中|论文中|文章中|这篇论文|这篇文章|该论文|本文|"
    r"该模型|这个模型|训练过程中|模型训练|训练时|训练|实验中|"
    r"分别是多少|分别是什么|分别为|分别|是什么|叫什么|是多少|有多少|多少|一共|总共|"
    r"用了哪种|使用了哪种|使用哪些|采用哪种|哪一种|哪种|什么|几个|几块|几张|"
    r"在哪里|哪里|何处|具体|设置为|设为|参数值|取值|型号|数量|个数|分数|得分|使用了|用到了|采用了|用了|使用|采用|"
    r"完整参考文献|参考文献|完整引文|引文|条目|引用|"
    r"以及|或者|并且|值|是|为|的|了|在|有|块|张|和|与|及|吗|呢"
)
_ARITHMETIC_SYMBOL = re.compile(r"(?<![a-z0-9])(?:β|beta)\s*[_-]?\s*(\d+)(?![a-z0-9])")
_EN_ALIASES = {word: _GPU for word in ("gpu", "gpus")}
_EN_ALIASES.update({word: _OPTIMIZER for word in _OPTIMIZER if " " not in word})
_EN_QUERY_FILLER = frozenset("article paper research authors used use using employed employ".split())
_MEASUREMENT = re.compile(r"硬件|设备|型号|数量|多少|几块|几张|how many|hardware|gpu models?")
_NUMBER_TERM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_HARDWARE_MODIFIER = rf"(?!(?:{_NUMBER_TERM}|and|for|from|of|on|the|to|with)\b)[a-z][a-z0-9]*"
_HARDWARE_DETAIL = re.compile(
    rf"\b{_NUMBER_TERM}\s+((?:{_HARDWARE_MODIFIER}\s+){{0,3}})(?:gpus?|cpus?|tpus?|accelerators?)\b"
)
_CITATION_QUERY = re.compile(r"参考文献|引文|引用|\b(?:cite|citation|reference)\b")
_REFERENCE_ENTRY = re.compile(r"^\s*\[\d+\]")


def _search_text(text):
    text = _ARITHMETIC_SYMBOL.sub(r" beta\1 ", _normalized(text))
    return " ".join(re.sub(r"(?<=\w)[\u2010-\u2014-](?=\w)", " ", text).split())


def _query_groups(query):
    """Represent each topic as alternatives that count as a single matched concept."""
    groups = {}
    normalized = _search_text(query)
    remaining = normalized
    for chinese, aliases in sorted(_RESEARCH_TERMS.items(), key=lambda entry: -len(entry[0])):
        if chinese in remaining:
            groups.setdefault(aliases, set()).add(chinese)
            remaining = remaining.replace(chinese, " ")

    # Keep unmatched subject terms, including Han bigrams, as normal evidence requirements.
    if _HAN_RUN.search(normalized) and (groups or re.search(r"[a-z]{2,}", normalized)):
        remaining = _QUERY_FILLER.sub(" ", remaining)
    terms = set(_terms(remaining)) - _STOPWORDS
    if groups or any(term in _EN_ALIASES for term in terms):
        terms -= _EN_QUERY_FILLER
    for term in terms:
        if len(term) > 1 or term.isdigit() or _HAN_RUN.fullmatch(term):
            groups.setdefault(_EN_ALIASES.get(term, (term,)), set()).add(term)
    return [(tuple(sorted(original)), alternatives) for alternatives, original in groups.items()]


def _group_matches(groups, passage, tokens, frequencies):
    phrase_text = f" {' '.join(tokens)} "
    total = 0
    matched = 0
    for original, alternatives in groups:
        count = max(
            (phrase_text.count(f" {term} ") if " " in term else
             passage.count(term) if _HAN_RUN.fullmatch(term)
             else frequencies.get(term, 0))
            for term in (*original, *alternatives)
        )
        if count:
            matched += 1
            total += min(count, 3)
    return matched, total


def _normalized(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    # Prefer searchable words where the extractor preserved a discretionary split.
    text = re.sub(r"(?<=\w)(?:\u00ad|-)\s*\n\s*(?=\w)", "", text)
    return text.replace("\u00ad", "")


def _terms(text):
    yield from _normalized_terms(_normalized(text))


def _normalized_terms(normalized):
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
            identity = extract_identity(pdf[0])
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
            "page_labels": page_labels, "arxiv": arxiv, "bibliographic_identity": identity, "text_hash": content.hexdigest(), "text_chars": chars}


def find_evidence(docs: list[dict], query: str) -> list[dict]:
    """Return ranked verbatim extracts, not generated answers or verified claims."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        return []

    hits = _ranked_evidence(docs, query)
    if hits:
        return hits
    # A conjunction between explicit research topics can refer to separate passages.
    parts = re.split(r"以及|和|与|及", query, maxsplit=1)
    if len(parts) != 2 or not all(any(term in part for term in _RESEARCH_TERMS) for part in parts):
        return []
    branches = [_ranked_evidence(docs, part) for part in parts]
    if not all(branches):
        return []
    result, seen = [], set()
    for pair in zip_longest(*branches):
        for row in pair:
            if row is None:
                continue
            key = row["id"], row["page"], row["block"]
            if key not in seen:
                result.append(row)
                seen.add(key)
                if len(result) == MAX_RESULTS:
                    return result
    return result


def _ranked_evidence(docs, query):
    groups = _query_groups(query)
    if not groups:
        return []
    minimum = len(groups) if len(groups) <= 3 else math.ceil(0.75 * len(groups))
    normalized_query = _search_text(query)
    training_context = bool(re.search(r"训练|\btrain(?:ed|ing)?\b", normalized_query))
    hardware_detail = bool(_MEASUREMENT.search(normalized_query)) and any(
        "gpu" in alternatives for _, alternatives in groups
    )
    citation_query = bool(_CITATION_QUERY.search(normalized_query))
    candidates = []
    for doc in docs:
        for chunk in doc["chunks"]:
            passage = _search_text(chunk["text"])
            tokens = list(_normalized_terms(passage))
            frequencies = Counter(tokens)
            matched, occurrences = _group_matches(groups, passage, tokens, frequencies)
            if matched < minimum:
                continue
            exact = normalized_query in passage
            score = matched / len(groups)
            training = sum(frequencies[term] for term in ("train", "trained", "training"))
            reference_entry = bool(_REFERENCE_ENTRY.match(passage))
            detail_words = max(
                (len(detail[1].split()) + 1 for detail in _HARDWARE_DETAIL.finditer(passage)),
                default=0,
            ) if hardware_detail else 0
            substantive = len(frequencies) >= 8
            # Prefer direct phrases and evidence with the context requested in the question.
            rank = (score, exact, reference_entry == citation_query, detail_words,
                    min(training, 3) if training_context else 0, substantive,
                    occurrences, -len(chunk["text"]))
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
