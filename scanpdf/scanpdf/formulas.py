"""Locate display math and transcribe only well-supported baseline arithmetic."""

import re
from collections import defaultdict
from statistics import median

import numpy as np
from PIL import Image
from scipy import ndimage


_LETTER = r"[A-Za-zΑ-Ωα-ω]"
_NUMBER = r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_TOKEN = re.compile(rf"{_NUMBER}|{_LETTER}|[=+\-−×÷*/()]")
_LABEL = re.compile(r"\(\s*(\d+(?:\s*\.\s*\d+)*[a-z]?)\s*\)", re.I)
_RELATION = re.compile(r"[=≈≠≤≥<>∝≡]")
_OPERATOR = re.compile(r"[-+−×÷*/^±]")
_MATH_GLYPH = re.compile(r"[∑∫√∏∞∂∇]")

_MATH_NAMES = {"cos", "cosh", "det", "exp", "inf", "lim", "log", "max",
               "min", "sin", "sinh", "sup", "tan", "tanh"}
_PROSE = {"and", "are", "for", "from", "in", "is", "of", "the", "to",
          "was", "were", "when", "where", "with"}


def _bounds(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _in_table(box, tables):
    x0, y0, x1, y1 = box
    area = max(1, (x1 - x0) * (y1 - y0))
    for table in tables:
        left, top, right, bottom = table["bbox"]
        overlap = (max(0, min(x1, right) - max(x0, left))
                   * max(0, min(y1, bottom) - max(y0, top)))
        if overlap >= area * 0.25:
            return True
    return False


def _split_label(words, line_height):
    for length in range(1, min(5, len(words) - 1) + 1):
        expression, suffix = words[:-length], words[-length:]
        label = _LABEL.fullmatch(" ".join(word["text"] for word in suffix))
        gap = suffix[0]["bbox"][0] - expression[-1]["bbox"][2]
        if label and gap >= 1.6 * line_height and all(
            word.get("confidence", 0) >= 65 for word in suffix
        ):
            return expression, re.sub(r"\s+", "", label.group(1))
    return words, None


def _math_evidence(text):
    names = [word.lower() for word in re.findall(r"[A-Za-z]{2,}", text)]
    if any(name in _PROSE for name in names):
        return False
    if sum(name not in _MATH_NAMES for name in names) > 1:
        return False
    has_letter = re.search(_LETTER, text)
    has_digit = re.search(r"\d", text)
    if _RELATION.search(text):
        sides = _RELATION.split(text)
        return (has_letter or has_digit) and len(sides) >= 2 and all(
            re.search(r"\w|[∑∫√∏∞∂∇]", side) for side in sides
        )
    return bool(_MATH_GLYPH.search(text) or
                (has_letter and _OPERATOR.search(text)))


def _arithmetic(text):
    """Validate explicit single-symbol arithmetic without rewriting OCR text."""
    tokens = _TOKEN.findall(text)
    if "".join(tokens) != re.sub(r"\s+", "", text):
        return False

    depth = 0
    needs_operand = True
    unary = False
    equalities = 0
    binary_ops = 0
    for token in tokens:
        if needs_operand:
            if token == "(":
                depth += 1
                unary = False
            elif token in {"+", "-", "−"} and not unary:
                unary = True
            elif re.fullmatch(rf"{_LETTER}|{_NUMBER}", token):
                needs_operand = False
                unary = False
            else:
                return False
        elif token == ")" and depth:
            depth -= 1
        elif token in {"+", "-", "−", "×", "÷", "*", "/", "="}:
            if token == "=":
                if depth or equalities:
                    return False
                equalities += 1
            binary_ops += 1
            needs_operand = True
        else:
            return False
    return not needs_operand and depth == 0 and binary_ops > 0


def _baseline_supported(words, ink, height):
    if min(word.get("confidence", 0) for word in words) < 85:
        return False
    operands = [word["bbox"] for word in words if
                re.search(rf"{_LETTER}|[0-9]", word["text"])]
    if len(operands) > 1:
        centers = [(b[1] + b[3]) / 2 for b in operands]
        if max(centers) - min(centers) > 0.48 * height:
            return False
        heights = [b[3] - b[1] for b in operands]
        if min(heights) < max(heights) * 0.52:
            return False
    x0, y0, x1, y1 = _bounds([word["bbox"] for word in words])
    margin = max(2, round(height * 0.27))
    y0, y1 = max(0, y0 - margin), min(ink.shape[0], y1 + margin)
    unexplained = ink[y0:y1, x0:x1].copy()
    border = max(1, round(height * 0.09))
    for word in words:
        left, top, right, bottom = word["bbox"]
        if not ink[top:bottom, left:right].any():
            return False
        unexplained[max(0, top - y0 - border):bottom - y0 + border,
                    max(0, left - x0 - border):right - x0 + border] = False
    labels, count = ndimage.label(unexplained)
    if count:
        sizes = np.bincount(labels.ravel())[1:]
        if sizes.max() >= max(4, 0.045 * height ** 2):
            return False
    return True


def _isolated(box, other_boxes, height):
    left, top, right, bottom = box
    for x0, y0, x1, y1 in other_boxes:
        if left < x1 and x0 < right:
            if top - height * 0.42 <= y1 and y0 <= bottom + height * 0.42:
                return False
    return True


def _fraction_regions(ink, height, page_left, page_right):
    """Find math-like stacked ink around an isolated horizontal bar."""
    min_bar = max(7, round(1.20 * height))
    bars = ndimage.binary_opening(ink, structure=np.ones((1, min_bar), dtype=bool))
    labels, _ = ndimage.label(bars)
    reach = max(4, round(1.5 * height))
    for rows, cols in ndimage.find_objects(labels):
        left, right, top, bottom = cols.start, cols.stop, rows.start, rows.stop
        bar_width = right - left
        if bottom - top > max(3, 0.24 * height) or bar_width > 10 * height:
            continue
        if not page_left < (left + right) / 2 < page_right:
            continue
        above = ink[max(0, top - reach):max(0, top - 1), left:right]
        below = ink[bottom + 1:bottom + reach, left:right]
        if not above.size or not below.size:
            continue
        upper_y, upper_x = np.nonzero(above)
        lower_y, lower_x = np.nonzero(below)
        min_ink = max(4, 0.07 * height ** 2)
        if upper_x.size < min_ink or lower_x.size < min_ink:
            continue
        if abs(np.median(upper_x) - np.median(lower_x)) > 0.35 * bar_width:
            continue
        if np.ptp(upper_y) < 0.21 * height or np.ptp(lower_y) < 0.21 * height:
            continue
        yield [left, max(0, top - reach) + int(upper_y.min()),
               right, bottom + 2 + int(lower_y.max())]


def detect_formulas(gray: Image.Image, words: list[dict], dpi: int,
                    tables: list[dict]) -> list[dict]:
    """Return conservative display math regions in raster pixel coordinates."""
    pixels = np.asarray(gray.convert("L"))
    ink = pixels < 165
    lines = defaultdict(list)
    for word in words:
        x0, y0, x1, y1 = word["bbox"]
        if word["text"].strip() and 0 <= x0 < x1 <= pixels.shape[1] and 0 <= y0 < y1 <= pixels.shape[0]:
            lines[word["line_id"]].append(word)
    for line in lines.values():
        line.sort(key=lambda word: word["bbox"][0])
    heights = [
        w["bbox"][3] - w["bbox"][1] for line in lines.values() for w in line
        if len(w["text"]) >= 3
    ]
    typical = max(5, median(heights) if heights else dpi / 24)
    boxes = {key: _bounds([word["bbox"] for word in line])
             for key, line in lines.items()}
    wide_lines = [b for b in boxes.values() if b[2] - b[0] >= pixels.shape[1] * 0.35]
    if len(wide_lines) >= 3:
        page_left = float(np.percentile([b[0] for b in wide_lines], 20))
        page_right = float(np.percentile([b[2] for b in wide_lines], 80))
    else:
        page_left, page_right = 0, pixels.shape[1]

    span = page_right - page_left
    label_lines = {}
    for key, line in lines.items():
        match = _LABEL.fullmatch(" ".join(word["text"] for word in line))
        if match and min(word.get("confidence", 0) for word in line) >= 65:
            label_box = boxes[key]
            if label_box[0] > page_left + span * 0.65:
                label_lines[key] = re.sub(r"\s+", "", match.group(1))

    matched_labels = set()
    found = []
    for key in sorted(lines, key=lambda key: (boxes[key][1], boxes[key][0])):
        line = lines[key]
        expression, label = _split_label(line, typical)
        box = boxes[key]
        expression_box = _bounds([w["bbox"] for w in expression])
        text = " ".join(word["text"] for word in expression)
        if not _math_evidence(text):
            continue
        label_key = None
        if label is None:
            yc = (box[1] + box[3]) / 2
            candidates = []
            for candidate in label_lines.keys() - matched_labels:
                label_box = boxes[candidate]
                vertical_delta = abs(yc - (label_box[1] + label_box[3]) / 2)
                gap = label_box[0] - box[2]
                if vertical_delta <= 0.45 * typical and gap >= 1.6 * typical:
                    candidates.append((vertical_delta, gap, candidate))
            if candidates:
                _, _, label_key = min(candidates)
                label = label_lines[label_key]
                box = _bounds([box, boxes[label_key]])
        if _in_table(box, tables):
            continue
        if not label and (expression_box[0] < page_left + span * 0.11 or
                          expression_box[2] > page_right - span * 0.11):
            continue
        if not _isolated(box, (b for k, b in boxes.items() if k not in (key, label_key)), typical):
            continue

        transcribed = _arithmetic(text) and _baseline_supported(expression, ink, typical)
        result = {
            "type": "formula",
            "bbox": box,
            "status": "transcribed" if transcribed else "detected_only",
            "method": "ocr_baseline_arithmetic" if transcribed else "ocr_layout",
            "text": text if transcribed else None,
        }
        if label:
            result["equation_number"] = label
        if label_key is not None:
            matched_labels.add(label_key)
        found.append(result)

    # OCR may miss the contents of a stacked numerator or denominator altogether.
    for box in _fraction_regions(ink, typical, page_left, page_right):
        left, top, right, bottom = box
        if _in_table(box, tables):
            continue
        center = (left + right) / 2
        if not page_left + 0.23 * (page_right - page_left) < center < page_right - 0.23 * (page_right - page_left):
            continue
        if not _isolated(box, (region["bbox"] for region in found), typical):
            continue
        nearby = [b for b in boxes.values()
                  if b[0] < right and left < b[2] and b[1] < bottom and top < b[3]]
        if any(b[2] - b[0] > (right - left) + 5 * typical for b in nearby):
            continue
        found.append({
            "type": "formula", "bbox": _bounds([box, *nearby]),
            "status": "detected_only", "method": "stacked_fraction_layout", "text": None,
        })

    return sorted(found, key=lambda value: (value["bbox"][1], value["bbox"][0]))
