# Provenance: ybb-kami/evidence-desk feature/scanpdf-parser @ abbb7140151f2e6f18813873e72e84b8da24724c, byline.py
# Source blob: 68fe8c1bde8106fbe5eae48a8ddf48c7f7121626. Code preserved; only this provenance header was added.
"""Locate an ordinal author in first-page, individually typeset contact blocks."""
import re
import unicodedata

_ORDINAL = re.compile(r"第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*(?:位|名|个)?\s*作者")
_DIGITS = "零一二三四五六七八九"
_CONTACT = re.compile(r"[^\s@,;{}]+@[^\s@,;{}]+\.[^\s@,;{}]+")


def author_number(query):
    matches = _ORDINAL.findall(unicodedata.normalize('NFKC', query))
    if len(matches) != 1:
        return None
    value = matches[0]
    if value.isascii():
        return int(value)
    if len(value) == 1 and value in _DIGITS:
        return _DIGITS.index(value)
    parts = value.split('十')
    if len(parts) == 2 and all(not v or (len(v) == 1 and v in _DIGITS) for v in parts):
        tens, ones = parts
        return (_DIGITS.index(tens) if tens else 1) * 10 + (_DIGITS.index(ones) if ones else 0)
    return None


def _contact_block(text):
    lines = text.strip().splitlines()
    if not 2 <= len(lines) <= 4 or not _CONTACT.fullmatch(lines[-1].strip()):
        return False
    name = lines[0].rstrip('∗*†‡0123456789¹²³⁴⁵⁶⁷⁸⁹ ').split()
    return (2 <= len(name) <= 6 and all(any(c.isalpha() for c in word)
            and all(c.isalpha() or c in ".-'’" for c in word) for word in name)
            and not any('@' in line for line in lines[:-1]))


def author_blocks(blocks, title):
    """Return original PDF block IDs in visual order. Reject ambiguous layouts."""
    if not title:
        return []
    blocks = [b for b in blocks if b[6] == 0]
    titles = [b for b in blocks if ' '.join(b[4].split()).casefold() == title.casefold()]
    abstracts = [b for b in blocks if b[4].strip().casefold() == 'abstract']
    if len(titles) != 1 or len(abstracts) != 1:
        return []
    top, bottom = titles[0][3], abstracts[0][1]
    band = [b for b in blocks if b[3] > top and b[1] < bottom
            and not (b[4].strip().lower().startswith("arxiv:") and b[3] - b[1] > 3 * (b[2] - b[0]))]
    if any(b[1] < top or b[3] > bottom for b in band):
        return []
    candidates = sorted(band, key=lambda b: (b[1], b[0]))
    if not candidates or not all(_contact_block(b[4]) for b in candidates):
        return []
    rows = []
    for block in candidates:
        if not rows or block[1] - rows[-1][0][1] > 4:
            if rows and block[1] < max(b[3] for b in rows[-1]):
                return []
            rows.append([])
        rows[-1].append(block)
    ordered = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        if any(a[2] > b[0] + 1 for a, b in zip(row, row[1:])):
            return []
        ordered.extend(b[5] + 1 for b in row)
    return ordered
