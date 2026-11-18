"""Answer explicit abbreviation questions from a document's own parenthetical definitions."""

import re
import unicodedata

# Keep the source spelling and offsets while joining PDF line-break hyphenation.
_WORD = re.compile(r"[A-Za-z0-9]+(?:[-\u2010\u00ad][ \t]*\n[ \t]*[a-z]+)*")
_WRAP = re.compile(r"[-\u2010\u00ad][ \t]*\n[ \t]*(?=[a-z])")
_JOIN = re.compile(r"[\s\-\u2010-\u2014]*")
_FUNCTION_WORDS = {"of", "the", "to", "for", "in", "on", "from", "and", "with", "a", "an", "by"}
_SHORT = r"(?P<term>[A-Za-z][A-Za-z0-9-]{1,11})"
_QUESTIONS = tuple(re.compile(pattern) for pattern in (
    r"(?i:(?:what (?:does|did) (?:the (?:acronym|abbreviation) )?))" + _SHORT
    + r"(?i: (?:stand for|abbreviate)(?: in (?:this|the) (?:paper|article|study))?)",
    r"(?i:(?:what is (?:the (?:full (?:form|name)|expansion) of )|expand (?:the (?:acronym|abbreviation) )?))" + _SHORT,
    r"(?:(?:请问|请写出|请给出|请展开|本文中|文中|论文中)\s*)*" + _SHORT
    + r"\s*(?:的?(?:英语|英文)?全称(?:是什么|是啥)?|是(?:什么|哪个(?:英文)?词组)的缩写|代表(?:什么|哪个)(?:英文)?(?:全称|词组))",
))


def asked_term(question):
    q = " ".join(question.strip(" \t\r\n?？。.!").split())
    for pattern in _QUESTIONS:
        match = pattern.fullmatch(q)
        if match and sum(c.isupper() for c in match["term"]) >= 2:
            return match["term"]
    return None


def _initials(words, target):
    # A function word, e.g. "with" in SWAG, may either supply an initial or be skipped.
    positions = {0}
    for word in words:
        normalized = _WRAP.sub("", word.group()).lower()
        advanced = {i + 1 for i in positions if i < len(target) and normalized[0] == target[i]}
        positions = advanced | positions if normalized in _FUNCTION_WORDS else advanced
        if not positions:
            return False
    return len(target) in positions


def _long_form(fragment, term, *, suffix):
    words = list(_WORD.finditer(fragment))
    if not words:
        return None
    target = term.replace("-", "").casefold()
    if suffix:
        if not _JOIN.fullmatch(fragment[words[-1].end():]):
            return None
        stop = len(words)
        begin = stop - 1
        while begin and stop - begin < len(target) + 5 and _JOIN.fullmatch(fragment[words[begin-1].end():words[begin].start()]):
            begin -= 1
    else:
        begin, stop = 0, len(words)
        if (not _JOIN.fullmatch(fragment[:words[0].start()]) or not _JOIN.fullmatch(fragment[words[-1].end():])
                or any(not _JOIN.fullmatch(fragment[a.end():b.start()]) for a, b in zip(words, words[1:]))):
            return None
    for first in range(stop - len(target), begin - 1, -1) if suffix else (0,):
        subset = words[first:stop]
        if subset and _initials(subset, target):
            return subset[0].start(), subset[-1].end()
    return None


def _definitions(text, term):
    token = re.escape(term)
    # Both "long form (ABC)" and "ABC (long form)" occur in research articles.
    for marker in re.finditer(r"[（(]\s*" + token + r"\s*[）)]", text):
        offset = max(0, marker.start() - 220)
        source = text[offset:marker.start()]
        found = _long_form(source, term, suffix=True)
        if found:
            start, end = (offset + x for x in found)
            yield text[start:end], start, marker.end()
    for marker in re.finditer(r"(?<![\w-])" + token + r"\s*[（(]([^()（）]{3,200})[）)]", text):
        found = _long_form(marker[1], term, suffix=False)
        if found:
            yield marker[1][found[0]:found[1]], marker.start(), marker.end()


def answer(doc, question, anchor=None):
    term = asked_term(question)
    if term is None:
        return None
    definitions, mentions = {}, []
    labels = doc.get("page_labels", ())
    for chunk in [anchor] if anchor is not None else doc["chunks"]:
        if term not in chunk["text"]:
            continue
        page = chunk["page"]
        hit = dict(chunk, id=doc["id"], name=doc["name"], part=chunk.get("part", 1),
                   page_label=labels[page - 1] if 0 < page <= len(labels) else str(page))
        if len(mentions) < 4 and re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", chunk["text"]):
            mentions.append(hit)
        for phrase, start, end in _definitions(chunk["text"], term):
            display = " ".join(_WRAP.sub("", phrase).split())
            key = re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", display).casefold())
            definitions.setdefault(key, (display, dict(hit, start=start, end=end)))
            if len(definitions) >= 3:
                break
        if len(definitions) >= 3:
            break
    values = list(definitions.values())
    zh = re.search(r"[\u3400-\u9fff]", question)
    if len(values) == 1:
        form, hit = values[0]
        return {"answer": f"{term} 的全称是 {form}。" if zh else f"{term} stands for {form}.",
                "answer_method": "abbreviation", "hits": [hit], "gap": None}
    if values:
        alternatives = " / ".join(form for form, _ in values)
        gap = (f"所选范围给出多个 {term} 的展开形式（{alternatives}）。请指定段落。" if zh else
               f"The selected text gives multiple expansions for {term} ({alternatives}). Please specify a passage.")
    else:
        gap = (f"所选范围没有找到与 {term} 首字母相符的括号全称。" if zh else
               f"No matching parenthetical expansion for {term} was found in the selected text.")
    return {"answer": None, "answer_method": "abbreviation", "hits": [hit for _, hit in values] or mentions, "gap": gap}
