"""Locate a numbered question in the selected PDF's actual exercise statements."""

from dataclasses import dataclass
import re
import unicodedata

from evidence_context import sentences


_DIGITS = r"[0-9０-９]{1,5}"
_CN = "零〇一二两三四五六七八九十百"
_NUMBER = rf"(?:{_DIGITS}|[{_CN}]{{1,5}})"
_POINT = r"[.．]"
_ID = rf"{_NUMBER}(?:[ \t]*{_POINT}[ \t]*{_NUMBER}){{0,3}}(?:[A-Za-z]|[（(][A-Za-z][)）])?"
_LABEL = r"Problems?|Exercises?|练习题|习题|练习|问题|第"
_MARKER = (rf"(?P<label>{_LABEL})\s*(?:[#:：]\s*)?(?P<number>{_ID})"
           rf"(?![0-9０-９A-Za-z{_CN}]|\s*{_POINT}\s*{_NUMBER})(?P<ordinal>\s*题)?")
_QUERY = re.compile(r"(?<![a-z0-9_])" + _MARKER, re.I)
_HEADING = re.compile(r"^[ \t]*" + _MARKER, re.I | re.M)
_RANGE = re.compile(rf"^\s*[-‐‑–—~～至到]\s*(?:{_LABEL}\s*)?{_NUMBER}", re.I)
_SECTION = re.compile(r"^(?:(?:chapter[- ]end )?(?:problems|exercises)|(?:本章|章末|课后)?(?:习题|练习题|练习))\s*[:：]?$", re.I)
_STOP = re.compile(r"^(?:(?:Chapter\s+\d+|第\s*[\d一二三四五六七八九十百]+\s*章)(?:\s|$)|"
                   r"(?:References|Bibliography|Solutions|Answers)(?:\s+(?:to|for)\s+(?:the\s+)?(?:Exercises|Problems))?\s*[:：]?$|"
                   r"(?:答案|习题解答|参考文献)\s*[:：]?$)", re.I)
_ASIDE = re.compile(r"^(?:(?:figure|fig\.|table|appendix|section)\s+[a-z0-9]|(?:图|表|附录|节)\s*[a-z0-9０-９]|"
                    r"(?:draft:|copyright|©|licensed under|this work is subject to)|"
                    r"(?:notes|problems|exercises)\s+\d+\s*$|\d{1,4}\s*$)", re.I)
_REFERENCE = re.compile(r"^(?:(?:see|refer to|as discussed|above|below|is (?:mentioned|referenced|discussed|listed)|"
                        r"follows from|provides an example|appears in|continues (?:on|from))\b|参见|详见|见(?:上文|下文)|如前所述)", re.I)
_PROMPT = re.compile(r"\b(?:show|prove|derive|determine|calculate|compute|find|consider|draw|explain|discuss|"
                     r"evaluate|verify|suppose|let|assume|compare|write|choose|give|devise|implement|"
                     r"what|which|why|where|how)\b|已知|假设|设想|求出|求解|证明|试证|请问|计算|推导|说明|解释|如何|为什么|[?？]", re.I)
_STARTER = re.compile(r"[\s:：.．*∗†‡—–-]*")
_SOLVE = re.compile(r"\b(?:solve|solution|answer to|work out|derive|prove|calculate|compute|how (?:do|can|would) (?:I|we|you))\b"
                    r"|求解|解答|答案|怎么解|怎么算|请证明|请计算|请推导", re.I)
_SAMPLE = re.compile(r"\bsampl(?:e[sd]?|ing)\b|采样|抽样", re.I)
_DIMENSION_ASK = re.compile(r"(?:哪(?:[一二三四五六七八九十\d]+)?个|哪些|什么)维度|维度(?:分别)?(?:是)?多少"
                            r"|\b(?:in )?(?:which|what) (?:\w+ )?dimensions\b", re.I)
_DIMENSIONS = re.compile(r"\b(?:in|at)\s+(\d{1,7}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)\d{1,7}){1,7})"
                         r"\s+(?:different\s+)?dimensions\b"
                         r"|(?:在|取)\s*(\d{1,7}(?:\s*(?:、|,|，|和|与)\s*\d{1,7}){1,7})\s*(?:个\s*)?维(?:度)?", re.I)
_PHYSICAL_PAGE = re.compile(r"\b(?:physical|PDF)\s+page\s+([1-9]\d{0,4})(?!\d|\.\d)|物理页\s*([1-9]\d{0,4})(?!\d|\.\d)", re.I)
_LOCATION_ONLY = re.compile(
    r"(?:(?:please|the|original|full|physical|printed|PDF|page|pages|book|textbook|of|in|on|for|where|"
    r"is|does|start|begin|find|locate|show|quote|give|me|statement|text|which)\b|"
    r"请|查找|找到|定位|显示|给出|教材|课本|书中|第|的|原文|题干|全文|位于|从|在|哪一页|哪页|哪里|"
    r"哪儿|物理页|开始|[\d\s?？。.,，:：])*", re.I)
_LETTERED = re.compile(r"^(?:\([a-z\d]+\)|[（][a-z\d]+[）]|[a-z][.)]\s|hint[:：]|提示[:：])", re.I)
_MATH_MARK = re.compile(r"[=∑∫√÷±≤≥Γπ∈∂]|\(\d+\.\d+\)\s*$")
_MAX_HITS = 8
_MAX_EXTRA_PAGES = 2
_MAX_SECTION_PAGES = 6


@dataclass(frozen=True)
class Heading:
    row: int
    start: int
    end: int
    family: str
    number: str
    section: int | None
    body: bool


def _decimal(token):
    if not all(char in _CN for char in token):
        return token
    digits = "零一二三四五六七八九"
    token = token.replace("〇", "零").replace("两", "二")
    if not any(unit in token for unit in "十百"):
        return "".join(str(digits.index(char)) for char in token)
    number = previous = 0
    for char in token:
        if char in "十百":
            number += (previous or 1) * (10 if char == "十" else 100)
            previous = 0
        else:
            previous = digits.index(char)
    return str(number + previous)


def _number(raw):
    value = re.sub(r"[\s()]", "", unicodedata.normalize("NFKC", raw)).casefold()
    suffix = value[-1] if value[-1].isascii() and value[-1].isalpha() else ""
    if suffix:
        value = value[:-1]
    return ".".join(_decimal(part) for part in value.split(".")) + suffix


def _marker(match):
    label = match["label"].casefold()
    if label == "第" and not match["ordinal"]:
        return None
    family = "problem" if label.startswith("problem") else "exercise" if label.startswith("exercise") else "any"
    return family, _number(match["number"])


def _request(question):
    normalized = unicodedata.normalize("NFKC", question)
    mentions = []
    ranged = False
    for match in _QUERY.finditer(normalized):
        marker = _marker(match)
        if marker is not None:
            mentions.append(marker)
            ranged |= bool(_RANGE.match(normalized[match.end():]))
    if not mentions:
        return None
    families = {family for family, _ in mentions} - {"any"}
    numbers = {number for _, number in mentions}
    return next(iter(families), "any"), numbers, ranged or len(families) > 1 or len(numbers) > 1


def _body_ready(raw, section):
    body = raw.strip()
    if len(body) < 5 or _REFERENCE.match(body):
        return False
    if _PROMPT.search(body):
        return True
    # In a marked exercise section, a declarative premise can precede a formula
    # or a separate continuation. Marginal lower-case prose is weak evidence.
    return section is not None and (body[0].isupper() or bool(re.match(r"[㐀-鿿]", body)))


def _headings(rows):
    section = None
    found = []
    for i, row in enumerate(rows):
        text = row["text"]
        if row.get("arxiv_stamp"):
            continue
        if _SECTION.fullmatch(text.strip()):
            section = i
            continue
        if _STOP.match(text.strip()):
            section = None
            continue
        if section is not None and row["page"] - rows[section]["page"] > _MAX_SECTION_PAGES:
            section = None
        markers = [match for match in _HEADING.finditer(text) if _marker(match) is not None
                   and not _RANGE.match(text[match.end():])]
        if not markers or (markers[0].start() and text[:markers[0].start()].strip()):
            continue
        candidates = []
        for n, match in enumerate(markers):
            end = markers[n + 1].start() if n + 1 < len(markers) else len(text)
            body = text[_STARTER.match(text, match.end()).end():end].strip()
            ready = _body_ready(body, section)
            # An isolated label can be an in-text link. Require a corroborating
            # exercises heading and a following statement on the same page.
            if not ready and (body or section is None or i + 1 == len(rows)
                              or rows[i + 1]["page"] != row["page"]
                              or _HEADING.match(rows[i + 1]["text"])
                              or not _body_ready(rows[i + 1]["text"], section)):
                continue
            family, number = _marker(match)
            candidates.append(Heading(i, match.start(), end, family, number, section, bool(body)))
        for n, heading in enumerate(candidates):
            end = candidates[n + 1].start if n + 1 < len(candidates) else len(text)
            found.append(Heading(heading.row, heading.start, end, heading.family, heading.number,
                                 heading.section, heading.body))
    return found


def _anchor(doc, row, start, end):
    labels = doc.get("page_labels", ())
    page = row["page"]
    return dict(row, id=doc["id"], name=doc["name"], part=row.get("part", 1),
                page_label=labels[page - 1] if 1 <= page <= len(labels) else str(page), start=start, end=end)


def _same_span(rows, first, other):
    left, right = rows[first.row], rows[other.row]
    return ((left["page"], left["block"], left.get("offset", 0) + first.start) ==
            (right["page"], right["block"], right.get("offset", 0) + other.start))


def _continuations(doc, rows, heading, next_heading):
    source = rows[heading.row]
    hits = [_anchor(doc, source, heading.start, heading.end)]
    if heading.end < len(source["text"]):
        return hits
    bridge = (heading.section is not None and next_heading and next_heading.section == heading.section
              and 0 <= rows[next_heading.row]["page"] - source["page"] <= _MAX_EXTRA_PAGES)
    ends_at = next_heading.row if next_heading is not None else len(rows)
    last = source["text"][heading.start:heading.end].rstrip()
    open_tail = not heading.body or not last.endswith((".", "?", "!", "。", "？", "！"))
    for row in rows[heading.row + 1:ends_at]:
        if row["page"] - source["page"] > _MAX_EXTRA_PAGES or len(hits) >= _MAX_HITS or _STOP.match(row["text"].strip()):
            break
        text = row["text"].strip()
        if (not text or _ASIDE.match(text) or _SECTION.fullmatch(text)
                or _HEADING.match(text) or _REFERENCE.match(text)):
            continue
        prior = hits[-1]
        same_block = (row["page"], row["block"]) == (prior["page"], prior["block"])
        if same_block:
            start = prior.get("offset", 0) + prior["end"] - row.get("offset", 0)
            if not 0 <= start < len(row["text"]):
                continue
        else:
            if not (bridge or _LETTERED.match(text) or (open_tail and (
                    row["page"] == source["page"] or bool(_MATH_MARK.search(text)) or text[0].islower()))):
                break
            start = 0
        hits.append(_anchor(doc, row, start, len(row["text"])))
        open_tail = not text.endswith((".", "?", "!", "。", "？", "！"))
    return hits


def _sampling_dimensions(hits, question):
    if not (_DIMENSION_ASK.search(question) and _SAMPLE.search(question)):
        return None
    found = {}
    for hit in hits:
        excerpt = hit["text"][hit["start"]:hit["end"]]
        for start, end in sentences(excerpt):
            sentence = excerpt[start:end]
            if not _SAMPLE.search(sentence):
                continue
            for match in _DIMENSIONS.finditer(sentence):
                values = tuple(re.findall(r"\d+", match[1] or match[2]))
                found.setdefault(values, dict(hit, start=hit["start"] + start, end=hit["start"] + end))
    return next(iter(found.items())) if len(found) == 1 else ((), None)


def locate_exercise(doc, question):
    """Return verbatim exercise evidence, with a location or a bounded evidence gap."""
    requested = _request(question)
    if requested is None:
        return None
    family, numbers, ambiguous = requested
    responses = {"answer": None, "hits": [], "answer_method": "exercise_locator"}
    if ambiguous:
        return dict(responses, exercise={"status": "ambiguous", "numbers": sorted(numbers)},
                    gap="请指定一道完整编号的习题，再核对题干原文。")
    number, = numbers
    rows = doc.get("chunks", ())
    headings = _headings(rows)
    pages = {int(first or second) for first, second in _PHYSICAL_PAGE.findall(unicodedata.normalize("NFKC", question))}
    matches = [heading for heading in headings if heading.number == number and (not pages or rows[heading.row]["page"] in pages)]
    preferred = [heading for heading in matches if heading.family == family]
    unique = []
    for heading in preferred or matches:
        if not any(_same_span(rows, old, heading) for old in unique):
            unique.append(heading)
    if len(unique) != 1:
        ambiguous = len(unique) > 1
        message = ("所选范围内有多处同号题干。请指定物理页或选中原文段落后核对。" if ambiguous
                   else "所选范围内未找到这个完整编号的题干。章节号或正文转引无法单独确认题干。")
        hits = [_anchor(doc, rows[heading.row], heading.start, heading.end) for heading in unique[:_MAX_HITS]]
        return dict(responses, hits=hits, gap=message,
                    exercise={"status": "ambiguous" if ambiguous else "not_found", "number": number})
    heading = unique[0]
    next_heading = next((other for other in headings[headings.index(heading) + 1:]
                         if not _same_span(rows, heading, other)), None)
    hits = _continuations(doc, rows, heading, next_heading)
    page = rows[heading.row]["page"]
    exercise = {"status": "located", "number": number, "family": heading.family, "page": page,
                "pages": list(dict.fromkeys(hit["page"] for hit in hits))}
    result = dict(responses, hits=hits, exercise=exercise)
    if _SOLVE.search(question):
        return dict(result, gap="已定位题干。当前原文证据只支持题目要求，无法据此验证解答。")
    zh = re.search(r"[㐀-鿿]", question)
    dimension_field = _sampling_dimensions(hits, unicodedata.normalize("NFKC", question))
    if dimension_field is not None:
        values, support = dimension_field
        if not values:
            return dict(result, gap="已定位题干。当前原文证据没有明确给出一组所问的采样维度。")
        exercise.update(field="sampling_dimensions", values=list(values))
        supported = [support if hit["page"] == support["page"] and hit["block"] == support["block"]
                     and hit["part"] == support["part"] else hit for hit in hits]
        answer = (f"题干要求在 {'、'.join(values)} 维采样。" if zh
                  else f"The exercise specifies sampling in {', '.join(values)} dimensions.")
        return dict(result, answer=answer, hits=supported, gap=None)
    remainder = _QUERY.sub(" ", unicodedata.normalize("NFKC", question))
    if not _LOCATION_ONLY.fullmatch(remainder):
        exercise["read_statement"] = True
        gap = (f"已找到习题 {number}（物理第 {page} 页），所问内容仍需由题干原文核实。" if zh else
               f"Exercise {number} starts on physical page {page}. The requested detail still needs support from its statement.")
        return dict(result, answer=None, gap=gap)
    if zh:
        answer = f"习题 {number} 的题干从物理页 {page} 开始。下方可核对题干原文。"
    else:
        answer = f"Exercise {number} starts on physical page {page}. Its original statement is linked below."
    return dict(result, answer=answer, gap=None)
