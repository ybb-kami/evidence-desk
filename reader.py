"""Local, conservative document outline and numbered citation concordance."""
from collections import Counter
import re

from graph import _blocks, _HEADING, _END

_COMMON = re.compile(
    r"abstract|introduction|background|related work|preliminaries|methods?|"
    r"materials and methods|methodology|experimental setup|experiments?|results?|"
    r"discussion|conclusions?|limitations?|acknowledg(?:e)?ments?|"
    r"摘要|引言|绪论|背景|相关工作|方法|材料与方法|实验|结果|讨论|结论|局限性|致谢|附录",
    re.I,
)
_NUMBERED_HEADING = re.compile(r"(\d{1,2}(?:\.\d{1,2}){0,2})\.?[ \t]+([^\d\W].*)$")
_APPENDIX = re.compile(r"(?:appendix(?:\s+[A-Z])?(?:[.:]\s*.+)?|附[录錄](?:\s*[A-Z甲乙丙丁])?)", re.I)
_ENTRY = re.compile(r"(?m)^\s*\[([1-9]\d{0,3})\][ \t]+(?=\S)")
_NUM = r"[1-9]\d{0,3}"
_ITEM = rf"{_NUM}(?:[ \t]*[-–][ \t]*{_NUM})?"
_CITATION = re.compile(rf"\[[ \t]*({_ITEM}(?:[ \t]*[,;][ \t]*{_ITEM}){{0,11}})[ \t]*\]")
_MAX_OCCURRENCES = 5000


def _section(line):
    if _HEADING.fullmatch(line) or _COMMON.fullmatch(line) or _APPENDIX.fullmatch(line):
        return 1
    match = _NUMBERED_HEADING.fullmatch(line)
    if not match:
        return None
    title = match[2]
    if (
        2 <= len(title) <= 100
        and len(title.split()) <= 13
        and not re.search(r"[.!?。！？;,；=<>:@\\]", title)
    ):
        return match[1].count(".") + 1
    return None


def _numbers(value):
    result = []
    for item in re.split(r"[,;]", value):
        endpoints = [int(n.strip()) for n in re.split(r"[-–]", item)]
        start, end = endpoints[0], endpoints[-1]
        if not 0 <= end - start <= 10:
            return []
        result.extend(range(start, end + 1))
    return list(dict.fromkeys(result)) if len(result) <= 30 else []


def build_reader(doc):
    """Produce extraction-layer anchors; originals and their page links remain separate."""
    pages = [
        {"page": page, "label": label, "blocks": []}
        for page, label in enumerate(
            doc.get("page_labels") or [str(p) for p in range(1, doc["pages"] + 1)], 1
        )
    ]
    outline, candidates, bodies = [], [], []
    bibliography = False
    for page, block, chunks, _, raw in _blocks(doc):
        if chunks[0].get("arxiv_stamp"):
            continue
        text = raw.strip()
        if not text:
            continue
        first, _, rest = text.partition("\n")
        line = " ".join(first.split())
        # Some PDFs extract a numbered section label onto its own line.
        if re.fullmatch(r"\d{1,2}(?:\.\d{1,2}){0,2}\.?", line) and rest and "\n" not in rest:
            line += " " + " ".join(rest.split())
        is_bibliography = bool(_HEADING.fullmatch(line))
        if is_bibliography:
            bibliography = True
        elif bibliography and _END.match(line):
            bibliography = False

        anchor = f"p{page}-b{block}"
        record = {"id": anchor, "text": text}
        pages[page - 1]["blocks"].append(record)
        level = None if bibliography and not is_bibliography else _section(line)
        if level:
            outline.append({"id": anchor, "page": page, "title": line, "level": level})
            record["heading"] = line

        if bibliography:
            entry_text = rest if is_bibliography else text
            prefix = len(first) + 1 if is_bibliography else 0
            matches = list(_ENTRY.finditer(entry_text))
            for i, match in enumerate(matches):
                stop = matches[i + 1].start() if i + 1 < len(matches) else len(entry_text)
                entry = entry_text[match.start():stop].strip()
                if len(entry) >= 15 and re.search(r"[^\W\d_]{3}", entry):
                    number = int(match[1])
                    start = prefix + match.start(1) - 1
                    end = prefix + match.end(1) + 1
                    entry_id = f"{anchor}-r{number}"
                    candidates.append((record, {"number": number, "page": page, "id": entry_id,
                                                "text": entry, "occurrences": []}, start, end))
                else:
                    # Even a malformed entry makes a competing number ambiguous.
                    candidates.append((record, {"number": int(match[1]), "text": ""}, 0, 0))
        else:
            bodies.append((page, record))

    counts = Counter(r["number"] for _, r, _, _ in candidates)
    references = {}
    for record, ref, start, end in candidates:
        number = ref["number"]
        if ref["text"] and counts[number] == 1:
            references[number] = ref
            record.setdefault("entries", []).append({"id": ref["id"], "start": start, "end": end})
    matched = 0
    for page, record in bodies:
        links = []
        for match in _CITATION.finditer(record["text"]):
            numbers = _numbers(match[1])
            if not numbers or any(n not in references for n in numbers):
                continue
            # Python offsets count Unicode code points, as does JS Array.from().
            if matched >= _MAX_OCCURRENCES:
                break
            matched += 1
            link_id = f'{record["id"]}-c{len(links) + 1}'
            links.append({"id": link_id, "start": match.start(), "end": match.end(), "numbers": numbers})
            for number in numbers:
                references[number]["occurrences"].append({"id": link_id, "page": page})
        if links:
            record["citations"] = links
    return {"doc": {"id": doc["id"], "name": doc["name"]}, "pages": pages,
            "outline": outline, "references": list(references.values()),
            "ambiguous_numbers": sorted(n for n, count in counts.items() if count > 1),
            "occurrences": matched,
            "at_occurrence_limit": matched >= _MAX_OCCURRENCES}
