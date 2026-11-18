"""Offline, conservative, evidence-linked local bibliography graph."""
from collections import Counter, defaultdict
import re
import unicodedata

_ARX = re.compile(r"(?i)(?:arxiv\s*:\s*|https?://arxiv\.org/(?:abs|pdf)/)((?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7}))(?:v\d+)?(?![\w/])")
_DOI = re.compile(r"(?i)\b10\.\d{4,9}/[-._;()/:A-Z0-9]+")
_DOI_LINE = re.compile(r"(?i)^(?:(?:doi\s*:\s*)|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)[. ]*$")
_HEADING = re.compile(r"(?i)^(?:(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+)?(?:references|bibliography|works cited|参考文献|參考文獻)\s*:?$")
_END = re.compile(r"(?i)^(?:appendi(?:x|ces)\b|supplement(?:al|ary)? (?:material|information)\b|acknowledg(?:e)?ments?\b|附录|附錄|致谢|致謝)")
_NUMBER = re.compile(r"(?m)^\s*\[\d{1,4}\]\s+")
_YEAR = re.compile(r"(?<!\w)(?:(?:18|19|20)\d\d)[a-z]?(?:\)|\.|[,:])\s+", re.I)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_CJK = re.compile(r"[\u3400-\u9fff]")


def _normalize_title(text):
    return " ".join(_WORD.findall(unicodedata.normalize("NFKC", text).casefold()))


def _clean_doi(text):
    text = text.lower().rstrip(".,;:")
    while text.endswith(")") and text.count(")") > text.count("("):
        text = text[:-1]
    return text


def extract_identity(page):
    """Select a distinctive title (visible first-page typesetting), not a filename."""
    candidates, sizes, doi = [], [], set()
    visible_lines = []
    for block in page.get_text("dict", flags=fitz_text_flags())["blocks"]:
        rows = []
        for line in block.get("lines", ()):
            if abs(line["dir"][0]) < 0.98:
                continue
            spans = line["spans"]
            line_text = " ".join(s["text"].strip() for s in spans).strip()
            if not line_text:
                continue
            size = max(s["size"] for s in spans)
            sizes.extend([size] * min(len(line_text), 150))
            if (line["bbox"][1] < page.rect.height * 0.48
                    and (match := _DOI_LINE.fullmatch(line_text))):
                doi.add(_clean_doi(match[1]))
            rows.append((line_text, size, line["bbox"][1], line["bbox"][3]))
        runs = []
        for row in rows:
            if (runs and abs(runs[-1][-1][1] - row[1]) < 0.4
                    and -2 <= row[2] - runs[-1][-1][3] <= row[1]):
                runs[-1].append(row)
            else:
                runs.append([row])
        candidates.extend((run[0][1], run[0][2], " ".join(r[0] for r in run))
                          for run in runs if run[0][2] < page.rect.height * 0.42)
        visible_lines.extend((row[2], row[0], len(run) == 1) for run in runs for row in run
                             if row[2] < page.rect.height * 0.30)
    def distinct_title(candidate):
        plain = _normalize_title(candidate)
        return (12 <= len(plain) <= 230
                and (len(plain.split()) >= 4 or len(_CJK.findall(plain)) >= 8)
                and not re.search(r"@|https?://|copyright|university|affiliation|technical report", candidate, re.I)
                and not candidate.endswith(".") and candidate.count(",") < 2
                and not _HEADING.fullmatch(candidate))

    title = None
    if candidates and sizes:
        candidates.sort(key=lambda c: (-c[0], c[1]))
        size, _, candidate = candidates[0]
        # Refuse ties and repeated/byline/contact/heading-like typography.
        next_size = candidates[1][0] if len(candidates) > 1 else 0
        median = sorted(sizes)[len(sizes) // 2]
        if size >= median * 1.19 and size >= next_size * 1.08 and distinct_title(candidate):
            title = candidate
    if title is None and visible_lines:
        # Same-font manuscripts can still identify their title when the PDF's
        # metadata corroborates the first isolated visible line on page one.
        metadata_title = (page.parent.metadata or {}).get("title", "")
        visible_lines.sort(key=lambda line: line[0])
        top, visible, isolated = visible_lines[0]
        if (isolated and (len(visible_lines) == 1 or visible_lines[1][0] >= top + 2)
                and isinstance(metadata_title, str)
                and " ".join(metadata_title.split()) == " ".join(visible.split())
                and distinct_title(visible)):
            title = visible
    return {"title": title, "doi": next(iter(doi)) if len(doi) == 1 else None}


def fitz_text_flags():
    import fitz
    return fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES


def _blocks(doc):
    groups = defaultdict(list)
    for c in doc.get("chunks", ()):
        groups[c["page"], c["block"]].append(c)
    for (page, block), chunks in sorted(groups.items()):
        chunks.sort(key=lambda c: c.get("offset", 0))
        base = chunks[0].get("offset", 0)
        text = chunks[0]["text"]
        for c in chunks[1:]:
            offset = c.get("offset")
            if offset is None or offset - base > len(text):
                # Cannot safely reconstruct discontinuous block; use first chunk only.
                break
            text += c["text"][max(0, len(text) - offset + base):]
        yield page, block, chunks, base, text


def _bibliography(doc):
    active, detected = False, False
    entries = []
    for page, block, chunks, base, text in _blocks(doc):
        first, _, rest = text.partition("\n")
        if _HEADING.fullmatch(" ".join(first.split())):
            active = detected = True
            if not rest.strip():
                continue
            base += len(first) + 1
            text = rest
        if not active:
            continue
        if _END.match(first.strip()):
            break
        marks = list(_NUMBER.finditer(text))
        if marks:
            spans = [(m.start(), marks[i + 1].start() if i + 1 < len(marks) else len(text), True)
                     for i, m in enumerate(marks)]
        elif _author_year(text):
            spans = [(0, len(text), False)]
        else:
            continue
        for start, stop, numbered in spans:
            raw = text[start:stop].strip()
            if not 15 <= len(raw) <= 3000:
                continue
            chunk = next((c for c in reversed(chunks) if c.get("offset", 0) <= base + start), chunks[0])
            entries.append({"page": page, "block": block, "part": chunk.get("part", 1),
                            "offset": base + start, "text": raw, "numbered": numbered})
    return detected, entries


def _flat(text):
    return " ".join(re.sub(r"(?<=\w)[-\u00ad]\s*\n\s*(?=\w)", "", text).split())


def _author_year(text):
    flat = _flat(text)
    for m in _YEAR.finditer(flat[:350]):
        prior = flat[:m.start()].strip()
        if 3 <= len(prior) <= 330 and re.search(r"[.\(,]$", prior) and len(_WORD.findall(prior)) >= 2:
            return m.end()
    return None


def _reference_titles(raw, numbered):
    flat = _flat(_NUMBER.sub("", raw, count=1)) if numbered else _flat(raw)
    index = _author_year(flat)
    possible = [flat[index:]] if index else []
    if numbered:
        # Ignore periods inside abbreviated names; only genuinely delimited fields.
        for m in re.finditer(r"(?<![.!?])\.(?=\s)", flat[:350]):
            prior = flat[:m.start()]
            if (len(_WORD.findall(prior)) >= 2 and not re.search(r"\b[A-Z]$", prior)
                    and not re.search(r"\d$", prior)):
                possible.append(flat[m.end():].lstrip())
                break
    for section in possible:
        field = re.split(r"[。!?]|\.(?=\s|$)", section, maxsplit=1)[0].strip()
        title = _normalize_title(field)
        if title:
            yield title


def _keys(doc):
    identity = doc.get("bibliographic_identity") or {}
    arxiv = (doc.get("arxiv") or {}).get("id")
    title = identity.get("title")
    return (arxiv.lower() if arxiv else None, identity.get("doi"),
            _normalize_title(title) if title else None)


def _works(docs):
    """Collapse exact bytes/text and corroborated arXiv versions; no fuzzy merges."""
    docs = {doc["id"]: doc for doc in docs}
    parent = {id_: id_ for id_ in docs}
    evidence = {}
    def root(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    for id_, doc in docs.items():
        arxiv, doi, title = _keys(doc)
        aliases = []
        if doc.get("text_chars", 0) >= 500 and doc.get("text_hash"):
            aliases.append(("text", doc["text_hash"]))
        if arxiv and title:
            aliases.append(("arxiv_title", arxiv, title))
        if doi and title:
            aliases.append(("doi_title", doi, title))
        for alias in aliases:
            prior = evidence.setdefault(alias, id_)
            parent[root(id_)] = root(prior)
    partitions = defaultdict(list)
    for id_ in docs:
        partitions[root(id_)].append(docs[id_])
    # Keep the newest stamped version as the visible node. Evidence location remains
    # attached to whichever PDF it came from.
    groups = {}
    for copies in partitions.values():
        copies.sort(key=lambda d: (-((d.get("arxiv") or {}).get("version") or 0), d["id"]))
        groups[copies[0]["id"]] = copies
    return groups


def _pagerank(ids, pairs, damping=0.85):
    if not ids:
        return {}
    outgoing = defaultdict(set)
    for source, target in pairs:
        outgoing[source].add(target)
    n = len(ids)
    rank = dict.fromkeys(ids, 1 / n)
    for _ in range(100):
        base = (1 - damping + damping * sum(rank[i] for i in ids if not outgoing[i])) / n
        updated = dict.fromkeys(ids, base)
        for source, destinations in outgoing.items():
            for target in sorted(destinations):
                updated[target] += damping * rank[source] / len(destinations)
        if sum(abs(updated[id_] - rank[id_]) for id_ in ids) < 1e-12:
            rank = updated
            break
        rank = updated
    return rank


def build_graph(docs):
    groups = _works(docs)
    indices = [defaultdict(set) for _ in range(3)]
    keys_by_group = {id_: [set(), set(), set()] for id_ in groups}
    for id_, aliases in groups.items():
        for alias in aliases:
            for i, key in enumerate(_keys(alias)):
                if key:
                    indices[i][key].add(id_)
                    keys_by_group[id_][i].add(key)
    edges, unresolved = {}, Counter()
    missing, recognized = [], 0
    for source, copies in sorted(groups.items()):
        seen_refs = set()
        for doc in copies:
            detected, entries = _bibliography(doc)
            if not detected:
                missing.append(doc["id"])
            for entry in entries:
                raw = entry["text"]
                fingerprint = _normalize_title(_flat(raw))
                if fingerprint in seen_refs:
                    continue
                seen_refs.add(fingerprint)
                recognized += 1
                flat = _flat(raw)
                arxiv_ids = {m[1].lower() for m in _ARX.finditer(flat)}
                dois = {_clean_doi(m[0]) for m in _DOI.finditer(flat)}
                titles = set(_reference_titles(raw, entry["numbered"]))
                matches = [(method, value, indices[i][value])
                           for i, (method, values) in enumerate(
                               (("arxiv", arxiv_ids), ("doi", dois), ("exact_title", titles)))
                           for value in sorted(values) if value in indices[i]]
                if not matches:
                    unresolved["no_local_match"] += 1
                    continue
                if any(len(ids) != 1 for _, _, ids in matches):
                    unresolved["ambiguous"] += 1
                    continue
                candidates = {next(iter(ids)) for _, _, ids in matches}
                if len(candidates) != 1:
                    unresolved["conflicting"] += 1
                    continue
                target = next(iter(candidates))
                known = keys_by_group[target]
                if (any(known[i] and ids - known[i] for i, ids in enumerate((arxiv_ids, dois)))
                        or len(arxiv_ids) > 1 or len(dois) > 1):
                    unresolved["conflicting"] += 1
                    continue
                if source == target:
                    unresolved["same_work"] += 1
                    continue
                method, value, _ = matches[0]
                anchor = {key: val for key, val in entry.items() if key != "numbered"}
                try:
                    anchor["page_label"] = doc["page_labels"][anchor["page"] - 1]
                except (IndexError, KeyError):
                    anchor["page_label"] = str(anchor["page"])
                edges.setdefault((source, target), {
                    "source": source, "target": target, "source_document": doc["id"],
                    "source_document_name": doc["name"], "method": method, "matched": value, **anchor,
                })
    ranks = _pagerank(sorted(groups), edges)
    indeg = Counter(v for _, v in edges)
    outdeg = Counter(u for u, _ in edges)
    return {
        "nodes": [{"id": id_, "name": copies[0]["name"],
                   "title": (copies[0].get("bibliographic_identity") or {}).get("title"),
                   "documents": [{"id": d["id"], "name": d["name"],
                                  "arxiv": d.get("arxiv")} for d in copies],
                   "indegree": indeg[id_], "outdegree": outdeg[id_], "pagerank": ranks[id_]}
                  for id_, copies in sorted(groups.items())],
        "edges": list(edges.values()), "unresolved": sum(unresolved.values()),
        "unresolved_by_reason": dict(unresolved), "recognized_entries": recognized,
        "no_bibliography_detected": sorted(set(missing)),
    }
