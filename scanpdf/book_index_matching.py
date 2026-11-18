"""Match requested phrases to labels that occur in a book's original index."""

from collections import defaultdict
import re
import unicodedata


_HAN = r"\u3400-\u9fff"
_WORDS = re.compile(rf"[{_HAN}]|[^\W_{_HAN}]+|[+&/∑*=<>]")
_EN_FRAME = frozenset((
    "a according all also alphabetical am an and any appear appears are as associated at back be book book's books "
    "can check citation cite cites cited could corresponds corresponding does each end entries entry exactly final find folio folios "
    "for from give gives given have has i in index indexed is it labels last list listed lists locate look me need numbers of "
    "on page pages please pointed point points printed reader record recorded references reference respectively see show shown "
    "subject tell the their them there these this those to up using want was we were what where which with would you your"
).split())
_ZH_FRAME = re.compile(
    r"印刷页码|纸本页码|物理页码|印刷页号|印刷页|纸本页|物理页|参考页码|页码|页号|页数|"
    r"书末|书尾|书后|末尾|末页|最后一页|索引|词条|条目|教材|本书|这本书|全书|"
    r"请问|请|查阅|查看|查找|查一下|查询|核对|找出|找到|告诉|我想知道|我想|"
    r"对应|指向|指到|指引|跳到|提到|列出|列在|标注|记录|收录|参考|"
    r"各自|分别|各是|各有|哪些|哪个|哪一|哪几|哪里|什么|多少|第几|以及|还有|"
    r"按照|根据|在|中|里|内|到|把|将|是|的|和|与|及|有|都|能|可以|我|们|页|吗|呢|了"
)
_COORDINATE = re.compile(r"\s*(?:[,，、;；]|\band\b|\bas well as\b|以及|和|与)\s*", re.I)
_NEGATION = re.compile(r"\b(?:not|except|rather than|instead of|versus|or)\b|不要|而非|而不是|不包括|除了|或者", re.I)
_PARENTHETICAL = re.compile(r"^(.+?)\s*\(([^()]{2,100})\)$")
_ACRONYM = re.compile(r"[A-Z][A-Z0-9-]{1,11}")


def words(text):
    return tuple(_WORDS.findall(unicodedata.normalize("NFKC", text).casefold().replace("\u00ad", "")))


def _plural(key):
    if not key or not re.fullmatch(r"[a-z]{4,}", key[-1]):
        return key
    last = key[-1]
    if len(last) > 4 and last.endswith("ies"):
        last = last[:-3] + "y"
    elif last.endswith(("shes", "ches", "xes", "sses", "zes")):
        last = last[:-2]
    elif last.endswith("s") and not last.endswith(("ss", "us", "is", "ics", "ous")):
        last = last[:-1]
    return (*key[:-1], last)


def _aliases(label):
    """Only parenthetical translations or acronym initials establish aliases."""
    match = _PARENTHETICAL.fullmatch(unicodedata.normalize("NFKC", label))
    if not match:
        return ()
    head, tail = match.groups()
    if bool(re.search(rf"[{_HAN}]", head)) != bool(re.search(rf"[{_HAN}]", tail)):
        return head, tail
    for long_form, acronym in ((head, tail), (tail, head)):
        initial = "".join(token[0] for token in words(long_form) if token not in {"of", "and", "the", "for", "in"})
        if _ACRONYM.fullmatch(acronym) and initial == acronym.replace("-", "").casefold():
            return head, tail
    return ()


def vocabulary(labels):
    exact, forms, plurals = defaultdict(set), defaultdict(set), defaultdict(set)
    for label in labels:
        exact[words(label)].add(label)
        for phrase in (label, *_aliases(label)):
            key = words(phrase)
            if key:
                forms[key].add(label)
                plurals[_plural(key)].add(label)
    return exact, forms, plurals


def candidates(vocab, phrase, *, translate=False):
    exact, forms, plurals = vocab
    key = words(phrase)
    matches = exact.get(key) or forms.get(key) or plurals.get(_plural(key))
    if matches or not translate or not re.search(rf"[{_HAN}]", phrase) or len(phrase) > 80:
        return matches or set()
    from hybrid_retrieval import english_query

    translated = english_query(phrase)
    # Translation can propose a phrase, but each token must then match an actual
    # label; an isolated generic word cannot establish a translated concept.
    return candidates(vocab, translated) if len(words(translated)) >= 2 else set()


def _frame_only(tokens):
    han = "".join(token if re.fullmatch(rf"[{_HAN}]", token) else " " for token in tokens)
    return not _ZH_FRAME.sub("", han).strip() and all(
        token in _EN_FRAME or re.fullmatch(rf"[{_HAN}]", token) for token in tokens)


def natural_terms(text, vocab):
    """A natural request resolves only if index labels cover its subject words."""
    if _NEGATION.search(text):
        return []
    _, forms, plurals = vocab
    tokens = words(text)
    lengths = sorted({len(key) for key in forms if len(key) <= len(tokens)}, reverse=True)
    terms, remainder = [], []
    position = 0
    while position < len(tokens):
        for length in lengths:
            phrase = tokens[position:position + length]
            if len(phrase) == length and (phrase in forms or _plural(phrase) in plurals) and not _frame_only(phrase):
                terms.append(" ".join(phrase))
                position += length
                break
        else:
            remainder.append(tokens[position])
            position += 1
    return list(dict.fromkeys(terms)) if _frame_only(remainder) else []


def has_exclusion(text):
    return bool(_NEGATION.search(text))


def split_terms(phrase, vocab):
    if not phrase or _NEGATION.search(phrase):
        return []
    if candidates(vocab, phrase):
        return [phrase]
    parts = [part.strip(" ,，。:：") for part in _COORDINATE.split(phrase) if part.strip(" ,，。:：")]
    # A recognized subentry must be a complete list item. A shared prefix in
    # an unknown item is never used to infer an entry.
    return parts if len(parts) <= 6 else []
