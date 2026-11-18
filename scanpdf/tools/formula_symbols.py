"""Review narrowly corroborated LaTeX ambiguities against the scanned crop."""

from collections import Counter
from difflib import SequenceMatcher
import re


_WORD = r"[A-Za-z]+(?:[ \t]+[A-Za-z]+)*"
_SCRIPT_MINUS = r"\s*_\s*\{\s*-\s*\}\s*"
_IDENTIFIER = re.compile(r"[A-Za-z]+(?:_[A-Za-z]+)*")
_SCRIPTED_WORD = re.compile(rf"(?<![A-Za-z\\]){_WORD}(?:{_SCRIPT_MINUS}{_WORD})+")
_ASSIGNMENT = re.compile(rf"^\s*({_WORD})\s*=")
_LATEX_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\S")


def _tokens(text):
    return _LATEX_TOKEN.findall(text)


def _inside(outer, inner):
    a, b, c, d = outer
    x, y, u, v = inner
    return a <= x < u <= c and b <= y < v <= d


def _named_assignment(drafts, words):
    """Choose a name only if one scale is independently corroborated in the same box."""
    first, second = (_ASSIGNMENT.match(draft) for draft in drafts)
    if not first or not second or _tokens(drafts[0][first.end():]) != _tokens(drafts[1][second.end():]):
        return None
    names = [re.sub(r"\s+", "", match[1]) for match in (first, second)]
    a, b = names
    if len(a) < 2 or len(a) != len(b) or sum(x != y for x, y in zip(a, b)) != 1:
        return None
    relations = [word["bbox"] for word in words if word["text"] == "="]
    if not relations:
        return None
    equal_x, equal_top, _, equal_bottom = min(relations)
    hits = [
        (index, word) for index, name in enumerate(names) for word in words
        if name == word["text"] and word.get("confidence", 0) >= 85
        and word["bbox"][2] <= equal_x
        and word["bbox"][1] < equal_bottom and equal_top < word["bbox"][3]
    ]
    if len(hits) != 1:
        return None
    index, word = hits[0]
    change = {
        "kind": "identifier_disagreement",
        "source": names[1 - index],
        "suggestion": names[index],
        "ocr_text": word["text"],
        "bbox_px": word["bbox"],
    }
    return drafts[index], change


def _underline_boxes(image, box, origin):
    """Locate thin internal strokes below the main glyphs in a scanned word."""
    x, y = origin
    left, top, right, bottom = box
    word = image.crop((left - x, top - y, right - x, bottom - y))
    width, height = word.size
    mask = word.point(lambda value: 255 if value < 175 else 0)
    data = mask.tobytes()
    strokes = []
    for row in range(round(height * .65), height):
        scan = data[row * width:(row + 1) * width]
        for match in re.finditer(rb"\xff+", scan):
            start, end = match.span()
            if not width * .10 <= start < end <= width * .90:
                continue
            if not height * .35 <= end - start <= height * .87:
                continue
            previous = next((part for part in strokes if part[3] == row and
                             abs(part[0] - start) <= 2 and abs(part[2] - end) <= 2), None)
            if previous:
                previous[0] = min(previous[0], start)
                previous[2] = max(previous[2], end)
                previous[3] = row + 1
            else:
                strokes.append([start, row, end, row + 1])
    supported = []
    for start, upper, end, lower in strokes:
        if (end - start) < 4 * (lower - upper):
            continue
        before = mask.crop((max(0, start - height), 0, start, upper)).getbbox()
        after = mask.crop((end, 0, min(width, end + height), upper)).getbbox()
        if before and after and min(before[3] - before[1], after[3] - after[1]) >= height * .3:
            supported.append([left + start, top + upper, left + end, top + lower])
    return supported


def _literal_underscores(draft, words, image, origin):
    """Match a subscript-minus reading to actual horizontal ink and a written OCR identifier."""
    matches = [
        (match, re.sub(r"\s+", "", re.sub(_SCRIPT_MINUS, "_", match[0])))
        for match in _SCRIPTED_WORD.finditer(draft)
    ]
    counts = Counter(name for _, name in matches)
    if not counts:
        return draft, []
    witnesses = {name: {} for name in counts}
    for word in words:
        names = set(_IDENTIFIER.findall(word["text"])).intersection(counts)
        if len(names) != 1:
            continue
        name, = names
        strokes = _underline_boxes(image, word["bbox"], origin)
        if len(strokes) == name.count("_"):
            key = tuple(tuple(stroke) for stroke in strokes)
            witnesses[name][key] = (word, strokes)
    supports = {
        name: iter(sorted(found.values(), key=lambda item: item[1]))
        for name, found in witnesses.items() if len(found) == counts[name]
    }
    output, changes, cursor = [], [], 0
    for match, name in matches:
        if name not in supports:
            continue
        word, strokes = next(supports[name])
        output.extend((draft[cursor:match.start()], name.replace("_", r"\_")))
        cursor = match.end()
        changes.append({
            "kind": "literal_underscore",
            "source": match[0],
            "suggestion": name,
            "ocr_text": word["text"],
            "bbox_px": word["bbox"],
            "strokes_px": strokes,
        })
    output.append(draft[cursor:])
    return "".join(output), changes


def review_latex(source, magnified, page, expression, box):
    """Expose disagreements and propose a draft only where the image offers corroboration."""
    a, b = _tokens(source), _tokens(magnified)
    differences = [{
        "source": "".join(a[i:k]),
        "magnified": "".join(b[j:l]),
        "prefix": "".join(a[max(0, i - 5):i]),
        "suffix": "".join(a[k:k + 5]),
    } for op, i, k, j, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        if op != "equal"]
    words = [
        word for line in page["lines"] for word in line["words"]
        if _inside(box, word["bbox"])
    ]
    latex, changes = source, []
    if differences:
        supported = _named_assignment((source, magnified), words)
        if not supported:
            return {"latex": None, "checks": [], "differences": differences}
        latex, check = supported
        changes.append(check)
    latex, underscore_checks = _literal_underscores(latex, words, expression, box[:2])
    changes.extend(underscore_checks)
    return {"latex": latex if changes else None, "checks": changes, "differences": differences}
