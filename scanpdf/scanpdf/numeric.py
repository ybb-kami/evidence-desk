"""Re-read numeral-shaped table cells from the scan without changing page OCR."""

from collections import Counter, defaultdict
from itertools import groupby, islice
import re
from subprocess import TimeoutExpired
import unicodedata

import numpy as np
from PIL import Image, ImageOps

from .ocr import recognize

CHARACTERS = "0123456789.,-+eEKkMmBbTt%"
DECIMAL = r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
VALUE = re.compile(rf"{DECIMAL}(?:[eE][+-]?[0-9]{{1,3}}|[kKmMbBtT%])?")
POWER_HINT = re.compile(rf"{DECIMAL}\s*[-·⋅×x*.]\s*10", re.I)
HAS_DIGIT = re.compile(r"[0-9]")
DASHES = str.maketrans("−‐‑–—", "-----")
STRIP_LENGTH = 35
MAX_CROP_WIDTH = 420
MAX_CELLS_PER_PAGE = 400
MAX_INDIVIDUAL_RETRIES = 16
CONFIDENT = 70
PRIMARY_CONFIDENT = 86


def _clean(text):
    return " ".join(unicodedata.normalize("NFKC", text).translate(DASHES).split())


def _value(text):
    return bool(len(text) <= 30 and VALUE.fullmatch(text))


def _column_candidates(table):
    cells = table["cells"]
    headers = [cell for cell in cells if cell["row"] == 1]
    header_rows = max((cell.get("row_span", 1) for cell in headers), default=1)
    body = [cell for cell in cells if cell["row"] > header_rows and cell["column"] > 1]
    votes, totals = Counter(), Counter()
    for cell in body:
        text = _clean(cell["text"])
        raw = _clean(cell.get("ocr_raw", {}).get("text", text))
        if text:
            totals[cell["column"]] += 1
        elif raw:
            totals[cell["column"]] += 1
        if _value(text) or _value(raw) or POWER_HINT.match(raw):
            votes[cell["column"]] += 1

    numeric_columns = {col for col, count in votes.items()
                       if count >= 2 and count >= totals[col] * .54}
    return [cell for cell in body if cell["column"] in numeric_columns and cell.get("row_span", 1) == 1]


def _crop(image, box, dpi):
    left, top, right, bottom = box
    margin = max(1, round(dpi * .008))
    left, top, right, bottom = left + margin, top + margin, right - margin, bottom - margin
    if right <= left or bottom <= top or (right - left) * (bottom - top) > 150_000:
        return None
    patch = np.array(image.crop((left, top, right, bottom)))
    ink = patch < 160

    # Trim residual straight ruling only near the cell perimeter.
    band = max(1, round(dpi * .018))
    for indexes in (slice(0, band), slice(-band, None)):
        rows = np.flatnonzero(ink[indexes].mean(axis=1) > .68)
        cols = np.flatnonzero(ink[:, indexes].mean(axis=0) > .80)
        start_y = 0 if indexes.start == 0 else max(0, patch.shape[0] - band)
        start_x = 0 if indexes.start == 0 else max(0, patch.shape[1] - band)
        patch[rows + start_y, :] = 255
        patch[:, cols + start_x] = 255

    ys, xs = np.nonzero(patch < 160)
    if len(ys) < 8 or ys.max() - ys.min() + 1 < max(5, round(dpi * .04)):
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    return Image.fromarray(patch[y0:y1, x0:x1]), [left + x0, top + y0, left + x1, top + y1]


def _power_parts(image):
    """Find a small multiplication dot and raised exponent in the cell pixels."""
    mask = np.asarray(image) < 165
    occupied = mask.any(axis=0)
    runs = []
    for present, columns in groupby(range(image.width), key=lambda x: occupied[x]):
        if present:
            xs = list(columns)
            ys, _ = np.nonzero(mask[:, xs])
            runs.append((xs[0], int(ys.min()), xs[-1] + 1, int(ys.max()) + 1))
    if len(runs) < 5:
        return None
    baseline = max(run[3] for run in runs)
    height = max(y1 - y0 for _, y0, _, y1 in runs)
    first = len(runs)
    while first and runs[first - 1][3] < baseline - height * .22:
        first -= 1
    if first < 4 or not 1 <= len(runs) - first <= 5:
        return None
    mark, base, raised = runs[first - 3], runs[first - 2:first], runs[first:]
    x0, y0, x1, y1 = mark
    if (y1 - y0 > height * .36 or x1 - x0 > (y1 - y0) * 1.6
            or not baseline - height * .78 < (y0 + y1) / 2 < baseline - height * .23
            or any(bottom - top < height * .7 for _, top, _, bottom in base)):
        return None

    def isolate(section):
        left, top, right, bottom = zip(*section)
        return image.crop((min(left), min(top), max(right), max(bottom)))

    return [isolate(runs[:first - 3]), isolate(base), isolate(raised)]


def _resize(image, binary, target_height=50):
    if binary:
        image = image.point(lambda n: 0 if n < 165 else 255)
    scale = target_height / max(18, image.height)
    size = max(1, round(image.width * scale)), max(1, round(image.height * scale))
    return image.resize(size, Image.Resampling.BICUBIC)


def _single_result(words):
    if len(words) != 1:
        return {"text": " ".join(word["text"] for word in words),
                "confidence": max((round(word["confidence"], 2) for word in words), default=None),
                "single_token": False}
    word = words[0]
    return {"text": _clean(word["text"]), "confidence": round(word["confidence"], 2), "single_token": True}


def _read(image, dpi, lang, mode):
    try:
        return recognize(image, dpi, lang, mode, whitelist=CHARACTERS, timeout=15)
    except (RuntimeError, TimeoutExpired):
        # Full-page OCR is already available if this optional retry fails.
        return []


def _strip_reads(images, dpi, lang, *, binary):
    results = {}
    iterator = iter(enumerate(images))
    while batch := list(islice(iterator, STRIP_LENGTH)):
        prepared = [(key, _resize(image, binary)) for key, image in batch]
        prepared = [(key, image) for key, image in prepared if image.width <= MAX_CROP_WIDTH]
        if not prepared:
            continue
        width = max(image.width for _, image in prepared) + 36
        height = max(image.height for _, image in prepared) + 30
        canvas = Image.new("L", (width, height * len(prepared)), 255)
        for index, (_, patch) in enumerate(prepared):
            canvas.paste(patch, ((width - patch.width) // 2, index * height + (height - patch.height) // 2))
        words = defaultdict(list)
        for word in _read(canvas, dpi, lang, 6):
            top, bottom = word["bbox"][1::2]
            if top // height == (bottom - 1) // height < len(prepared):
                words[top // height].append(word)
        for index, (key, _) in enumerate(prepared):
            results[key] = _single_result(words[index])
    return results


def _attempt(method, parts, power):
    if not all(part["single_token"] for part in parts):
        return {"method": method, "text": " | ".join(part["text"] for part in parts), "confidence": None,
                "valid": False}
    confidence = min(part["confidence"] for part in parts)
    if not power:
        text = parts[0]["text"]
        return {"method": method, "text": text, "confidence": confidence, "valid": _value(text)}
    mantissa, base, exponent = (part["text"] for part in parts)
    valid = bool(re.fullmatch(DECIMAL, mantissa) and base == "10" and re.fullmatch(r"[+-]?[0-9]{1,3}", exponent))
    text = f"{mantissa} × 10^{exponent}" if valid else " | ".join(part["text"] for part in parts)
    return {"method": method, "text": text, "confidence": confidence, "valid": valid}


def _confident_group(attempts):
    readings = defaultdict(list)
    for attempt in attempts:
        if attempt["valid"]:
            readings[attempt["text"].casefold()].append(attempt)
    agreed = [group for group in readings.values() if len(group) >= 2
              and max(attempt["confidence"] for attempt in group) >= CONFIDENT]
    return max(agreed[0], key=lambda attempt: attempt["confidence"]) if len(agreed) == 1 else None


def _crop_parts(raw, patch):
    if POWER_HINT.match(raw):
        parts = _power_parts(patch)
        if parts:
            return parts, True
    return [patch], False


def refine_numeric_cells(image, tables, dpi, lang):
    """Use repeatable crop reads for values while retaining raw text and failed attempts."""
    candidates, images = [], []
    for table in tables:
        for cell in _column_candidates(table):
            if len(candidates) >= MAX_CELLS_PER_PAGE:
                break
            raw = cell.get("ocr_raw") or {"text": cell["text"], "confidence": cell.get("ocr_confidence")}
            source = _clean(raw["text"])
            # Preserve annotations and placeholders in otherwise numeric columns.
            if source and (not HAS_DIGIT.search(source) or source.startswith("[")):
                continue
            crop = _crop(image, cell["bbox"], dpi)
            if crop is None:
                continue
            patch, bounds = crop
            pieces, power = _crop_parts(source, patch)
            first = len(images)
            images.extend(pieces)
            candidates.append((cell, raw, bounds, pieces, range(first, len(images)), power))
    if not candidates:
        return

    readouts = [(name, _strip_reads(images, dpi, lang, binary=binary))
                for name, binary in (("grayscale_strip", False), ("thresholded_strip", True))]
    retries = 0
    unrecognized = {"text": "", "confidence": None, "single_token": False}
    for cell, raw, bounds, pieces, indexes, power in candidates:
        attempts = [_attempt(method, [reads.get(index, unrecognized) for index in indexes], power)
                    for method, reads in readouts]
        selected = _confident_group(attempts)
        if not selected and retries < MAX_INDIVIDUAL_RETRIES:
            retries += 1
            for binary in (False, True):
                scaled = [_resize(piece, binary, target_height=32 if binary else 50) for piece in pieces]
                parts = [_single_result(_read(ImageOps.expand(piece, border=14, fill=255), dpi, lang, 8)
                                        if piece.width <= MAX_CROP_WIDTH else []) for piece in scaled]
                attempts.append(_attempt("thresholded_word" if binary else "grayscale_word", parts, power))
            selected = _confident_group(attempts)
        source = _clean(raw["text"])
        primary_confidence = raw.get("confidence") or 0
        contradicted = any(attempt["valid"] and attempt["confidence"] >= CONFIDENT
                           and attempt["text"].casefold() != source.casefold() for attempt in attempts)
        trusted_primary = (bool(cell["text"]) and _value(source)
                           and primary_confidence >= PRIMARY_CONFIDENT and not contradicted)
        if selected:
            cell.update(text=selected["text"], status="ocr_text", text_method="numeric_crop_agreement",
                        ocr_confidence=selected["confidence"])
        elif not trusted_primary:
            cell.update(text="", status="numeric_unresolved", text_method="numeric_crop_disagreement")
        cell["ocr_raw"] = raw
        cell["numeric_ocr"] = {"status": "agreed" if selected else "primary_only" if trusted_primary else "unresolved",
                               "bbox": bounds, "attempts": attempts}
