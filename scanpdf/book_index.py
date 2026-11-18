"""Answer explicit book index questions from indexed source text and original offsets."""

from collections import defaultdict
import re
import unicodedata


_INDEX_REQUEST = re.compile(
    r"\b(?:back[- ]of[- ](?:the[- ])?book|book(?:['’]s)?|printed|subject|general|alphabetical|term|name|author)\s+index\b"
    r"|\bindex\s+(?:entr(?:y|ies)\s+(?:for|under)|lists?|cites?|gives?|shows?|mentions?|points?|refers?|records?|corresponds?)\b"
    r"|\b(?:listed|cited|referenced|shown|recorded|find|look up)\b[^?。]{0,80}\b(?:in|by|under)\s+(?:the\s+)?index\b"
    r"|(?:书[末后尾]|末页|末尾|本书|全书|教材)\s*(?:的\s*)?索引"
    r"|(?:按(?:照)?|查(?:阅|看|找)?)\s*索引|索引\s*(?:词条|条目|列(?:出|在)|把|将|指[向到]|标注)"
    r"|索引\s*(?:里|中|内)[^?。]{0,100}(?:列(?:出|在)|指[向到]|标注|印刷页|纸本页|页码)", re.I
)
_LOCATORS = re.compile(
    r"\b(?:page(?:s|\s+numbers?|\s+references?)?|folio(?:s)?|index\s+entr(?:y|ies)|where)\b|"
    r"页码|页号|第几页|(?:哪(?:一|几|些|个)?|什么)(?:个|的|印刷|纸本|纸质|书本|实体|物理|[0-9])*(?:页|页码)|指向|索引词条", re.I
)
_TOPIC = re.compile(r"^(?:(?:please\s+)?(?:explain|define)\b|why\b|how (?:does|do)\b[^?]{0,100}\bworks?\b|"
                    r"(?:请)?(?:解释|定义)|为什么)|(?:是什么|含义|原理)[?？。]*$", re.I)
_LAST_PAGE = re.compile(r"(?:末页|最后一页)|\b(?:last|final)\s+page\b", re.I)
_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”|(?<!\w)\'([^\']+)\'(?!\w)|‘([^’]+)’|「([^」]+)」|『([^』]+)』|《([^》]+)》|`([^`]+)`')
_TERMS = tuple(re.compile(pattern, re.I) for pattern in (
    r"^(?:请问\s*)?(.+?)\s*(?:在|于)\s*(?:这本书的|本书的|这本书|本书|教材|书末|书后的|书后)*索引(?:中|里|内)?\s*(?:列在|列出|对应|在哪|指向|标注)",
    r"索引(?:中|里|内)?[，,:：\s]*(?:查找|查询|查一下|核对|查看)\s*(.+?)\s*(?:列在|列出|对应|指向|标注|的页码|的印刷页)",
    r"索引\s*(?:中|里|内|条目|词条)?[，,:：\s]*(?:把|将|给|收录的|列出的)?\s*(.+?)\s*(?:所)?(?:指向|指到|列在|列出|对应|标注|在哪|是在哪|的(?:印刷|纸本|书本)?页码)",
    r"\bindex\s+(?:entry\s+)?(?:for|under)\s+(.+?)(?=\s+(?:list|give|cite|show|refer|mention|point|say|name)s?\b|$)",
    r"\bindex\b[^?。]{0,80}?\b(?:for|under)\s+(.+)$",
    r"\b(?:under|for)\s+(.+?)\s+(?:in|under|by)\s+(?:the\s+)?(?:book's\s+|back-of-book\s+|subject\s+)*index\b",
    r"\b(?:pages?|folios?)\s+(?:is|are|was|were)\s+(.+?)\s+(?:listed|cited|shown|given)\b[^?。]*\bindex\b",
    r"\bindex\s+(?:is|are|was|were)\s+(.+?)\s+(?:listed|cited|shown|given)\b",
))
_HEADING = r"(?:index(?: of (?:subjects|terms|names|authors))?|(?:subject|general|alphabetical|term|name|author) index|(?:主题|术语|人名|名词|总)?索引)"
_OTHER_SECTION = r"(?:table of contents|contents|bibliography|references|glossary|appendix(?:\s+[a-z0-9]+)?|目录|参考文献|术语表|附录(?:\s+[a-z0-9]+)?)"
_FOLIO = r"(?:\d{1,6}|[ivxlcdm]{1,10})"
_INDEX_HEADING = re.compile(rf"(?:{_FOLIO}\s+)?{_HEADING}(?:\s+{_FOLIO})?", re.I)
_OTHER_HEADING = re.compile(rf"(?:{_FOLIO}\s+)?{_OTHER_SECTION}(?:\s+{_FOLIO})?", re.I)
_REFERENCE = rf"{_FOLIO}(?:\s*[-‐‑‒–—−－~～至]\s*{_FOLIO})?"
_DELIMITER = r"[,，;；、]"
_REFERENCES = rf"{_REFERENCE}(?:\s*{_DELIMITER}\s*{_REFERENCE})*"
_ENTRY = re.compile(rf"(?P<term>\S(?:.*?\S)?)(?:\s*[,，、:：]\s*|\s*[.·…]{{2,}}\s*|\s{{2,}})(?P<pages>{_REFERENCES})", re.I)
_CONTINUATION = re.compile(rf"{_REFERENCES}\s*{_DELIMITER}?", re.I)
_RANGE = re.compile(r"\s*[-‐‑‒–—−~～至]\s*")


def _term_key(text):
    return " ".join(unicodedata.normalize("NFKC", text).replace("\u00ad", "").casefold().split())


def _request(question):
    # None delegates other questions. An empty term marks an explicit request
    # whose results can only be offered as candidates for manual inspection.
    text = " ".join(unicodedata.normalize("NFKC", question).split()).strip(" ?？。!")
    if not (_INDEX_REQUEST.search(text) and _LOCATORS.search(text)) or _TOPIC.search(text):
        return None
    quoted = [next(group for group in match.groups() if group is not None) for match in _QUOTED.finditer(text)]
    if quoted:
        term = quoted[0] if len(quoted) == 1 else ""
    else:
        term = next((match[1] for pattern in _TERMS if (match := pattern.search(text))), "")
    term = term.strip(" \t,:，：。?？'\"’‘“”")
    return term if 0 < len(term) <= 160 else ""


def _entries(text):
    lines = list(re.finditer(r"[^\r\n]+", text))
    position = 0
    while position < len(lines):
        first = last = lines[position]
        line = first[0].strip()
        # A trailing separator marks a wrapped locator list. A numeric-only
        # next line by itself could instead be the page's running folio.
        while re.search(_DELIMITER + r"\s*$", line) and position + 1 < len(lines):
            following = lines[position + 1]
            if (following.start() - last.end() > 2
                    or not _CONTINUATION.fullmatch(following[0].strip())):
                break
            line += " " + following[0].strip()
            last = following
            position += 1
        if entry := _ENTRY.fullmatch(line):
            term = entry["term"].strip()
            if term and any(char.isalpha() for char in term):
                refs = [_RANGE.sub("–", unicodedata.normalize("NFKC", ref.strip()))
                        for ref in re.split(_DELIMITER, entry["pages"])]
                yield term, refs, first.start() + len(first[0]) - len(first[0].lstrip()), last.start() + len(last[0].rstrip())
        position += 1


def _index_pages(doc):
    total = doc["pages"]
    tail = min(64, max(16, (total + 4) // 5))
    by_page = defaultdict(list)
    for chunk in doc["chunks"]:
        if total - tail < chunk["page"] <= total:
            by_page[chunk["page"]].append(chunk)
    active = None
    for page, chunks in sorted(by_page.items()):
        headers = [" ".join(chunk["text"].split()) for chunk in chunks[:4] if chunk.get("part", 1) == 1]
        heading = any(_INDEX_HEADING.fullmatch(text) for text in headers)
        if (not heading and active != page - 1) or any(_OTHER_HEADING.fullmatch(text) for text in headers):
            continue
        last_parts = defaultdict(int)
        for chunk in chunks:
            last_parts[chunk["block"]] = max(last_parts[chunk["block"]], chunk.get("part", 1))
        records = []
        for chunk in chunks:
            part = chunk.get("part", 1)
            for entry in _entries(chunk["text"]):
                _, _, start, end = entry
                # The other overlapping passage usually preserves a line cut
                # at a passage boundary and its intact source label or locator.
                if (start == 0 and part > 1) or (end == len(chunk["text"]) and part < last_parts[chunk["block"]]):
                    continue
                records.append((chunk, *entry))
        if len({_term_key(record[1]) for record in records}) < 2:
            continue
        active = page
        yield page, records


def answer(doc, question, anchor=None):
    term = _request(question)
    if term is None:
        return None
    wording = _term_key(question).replace(_term_key(term), "", 1) if term else question
    zh = bool(re.search(r"[\u3400-\u9fff]", wording))
    result = {"answer_method": "book_index", "answer": None, "gap": None, "hits": []}
    final_page_only = _LAST_PAGE.search(question)
    pages = _index_pages(doc) if term or anchor is not None else ()
    scope = {page: rows for page, rows in pages if not final_page_only or page == doc["pages"]}
    if anchor is not None and anchor["page"] not in scope:
        return dict(result, gap=("所选证据段落不属于本次可核对的书末索引范围。" if zh else
                                 "The selected passage is outside the book index scope for this question."))
    if not term:
        return dict(result, index_status="unresolved",
                    gap=("要核对确切印刷页码，请一次指定一个完整的索引词条。" if zh else
                         "Specify one complete index entry label to verify its printed page references."))
    key = _term_key(term)
    labels = doc.get("page_labels", ())
    matches = {}
    for page, records in scope.items():
        label = labels[page - 1] if 0 < page <= len(labels) else str(page)
        for chunk, source_term, refs, start, end in records:
            block, part = chunk["block"], chunk.get("part", 1)
            if (anchor is not None and (page, block, part) != (anchor["page"], anchor["block"], anchor.get("part", 1))) or _term_key(source_term) != key:
                continue
            offset = chunk.get("offset")
            source = (page, block, offset + start, offset + end) if type(offset) is int else (page, block, part, start, end)
            hit = dict(chunk, id=doc["id"], name=doc["name"], part=part, page_label=label, start=start, end=end)
            matches.setdefault(source, (hit, source_term, refs))
    if len(matches) != 1:
        if matches:
            gap = (f"索引中有多个「{term}」条目。请选中具体索引段落，或提供更完整的词条。" if zh else
                   f"Multiple index entries match ‘{term}’. Select a specific index passage or provide its full label.")
            return dict(result, gap=gap, hits=[match[0] for match in list(matches.values())[:5]])
        gap = (f"在可核对的索引范围内没有找到「{term}」的完整词条和印刷页码。" if zh else
               f"No exact entry with printed page references for ‘{term}’ was found in the available index.")
        return dict(result, gap=gap, index_status="unresolved")
    hit, source_term, refs = next(iter(matches.values()))
    page, label = hit["page"], hit["page_label"]
    if zh:
        location = f"PDF 物理第 {page} 页" + (f"（PDF 页标签 {label}）" if label != str(page) else "")
        text = f"索引词「{source_term}」列出的印刷页码为 {'、'.join(refs)}。索引条目位于 {location}。"
    else:
        location = f"PDF physical page {page}" + (f" (page label {label})" if label != str(page) else "")
        text = f"The index entry ‘{source_term}’ lists printed page references {', '.join(refs)}. The entry is on {location}."
    source = {key: hit[key] for key in ("id", "page", "block", "part", "start", "end")}
    return dict(result, answer=text, hits=[hit], book_index={"term": source_term, "printed_pages": refs,
                "physical_page": page, "page_label": label, "anchor": source})
