"""Parse scanned pages in the visible, rotated PDF coordinate frame."""
from collections import defaultdict
import hashlib
import math
from pathlib import Path
import re

import pymupdf
from PIL import Image, ImageStat

from .formulas import detect_formulas
from .ocr import recognize
from .tables import detect_tables


def select_pages(spec: str | None, count: int) -> list[int]:
    if spec is None:
        return list(range(1, count + 1))
    selected = set()
    for token in spec.split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token.strip())
        if not match:
            raise ValueError(f"Invalid page selection: {token!r}")
        start, stop = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= stop <= count:
            raise ValueError(f"Pages must be in ascending ranges within 1..{count}: {token}")
        selected.update(range(start, stop + 1))
    return sorted(selected)


def _lines(words):
    groups = defaultdict(list)
    for word in words:
        groups[word["line_id"]].append(word)
    result = []
    for key, group in groups.items():
        boxes = list(zip(*(word["bbox"] for word in group)))
        result.append({
            "line_id": key, "words": group, "text": " ".join(w["text"] for w in group),
            "bbox": [min(boxes[0]), min(boxes[1]), max(boxes[2]), max(boxes[3])],
        })
    return result


def _read_missing_cells(image, tables, dpi, lang):
    # Full-page segmentation often discards text inside a ruled grid. Retry
    # only unresolved cells with visible ink, with borders trimmed away.
    for table in tables:
        for cell in table["cells"]:
            if cell["text"]:
                continue
            l, t, r, b = cell["bbox"]
            pad = max(2, round(dpi * 0.025))
            if r - l <= 2 * pad or b - t <= 2 * pad:
                continue
            roi = image.crop((l + pad, t + pad, r - pad, b - pad))
            if ImageStat.Stat(roi).stddev[0] < 5:
                continue
            recovered = recognize(roi, dpi, lang, 6)
            if recovered:
                cell.update(text="\n".join(line["text"] for line in _lines(recovered)),
                            status="ocr_text", text_method="cell_crop_tesseract")


def _parse_page(page, number, dpi, lang, crops, digest):
    rect = page.rect
    if math.ceil(rect.width * dpi / 72) * math.ceil(rect.height * dpi / 72) > 25_000_000:
        raise ValueError(f"Physical page {number} exceeds 25 megapixels; use a lower DPI")
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    del pix
    words = recognize(image, dpi, lang, 3)
    if not words and ImageStat.Stat(image).stddev[0] > 5:
        words = recognize(image, dpi, lang, 11)
    lines = _lines(words)
    tables = detect_tables(image, words, dpi)
    regions = tables + detect_formulas(image, words, dpi, tables)
    _read_missing_cells(image, tables, dpi, lang)
    regions.sort(key=lambda reg: (reg["bbox"][1], reg["bbox"][0]))

    def locate(record):
        record["bbox"] = [int(v) for v in record["bbox"]]
        record["bbox_pt"] = [
            round(min(rect.height if i % 2 else rect.width, v * 72 / dpi), 4)
            for i, v in enumerate(record["bbox"])
        ]

    for line in lines:
        locate(line)
        for word in line["words"]:
            locate(word)
    for index, region in enumerate(regions, 1):
        region["id"] = f"p{number}-r{index}"
        locate(region)
        for cell in region.get("cells", []):
            locate(cell)
        if crops:
            filename = f"{digest[:16]}-{region['id']}.png"
            with (crops / filename).open("xb") as out:
                image.crop(region["bbox"]).save(out, "PNG")
            region["crop"] = filename
    return {
        "physical_page": number, "width_px": image.width, "height_px": image.height,
        "width_pt": rect.width, "height_pt": rect.height, "rotation": page.rotation,
        "cropbox": list(page.cropbox), "ocr_status": "words_found" if words else "no_words",
        "lines": lines, "regions": regions,
    }


def parse_pdf(source, *, pages=None, dpi=240, lang="eng", crops=None):
    """Return a JSON-compatible result. 'pages' uses 1-based intervals, e.g. '1,3-5'."""
    if not 72 <= dpi <= 600:
        raise ValueError("DPI must be between 72 and 600")
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", lang):
        raise ValueError("Expected a Tesseract language name or combination such as chi_sim+eng")
    path = Path(source)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with pymupdf.open(path) as document:
        if not document.is_pdf or document.needs_pass or not document.page_count:
            raise ValueError("A nonempty, unlocked PDF is required")
        selected = select_pages(pages, document.page_count)
        crop_dir = Path(crops) if crops is not None else None
        if crop_dir is not None:
            crop_dir.mkdir(parents=True, exist_ok=True)
        return {
            "schema_version": 1, "source": {"filename": path.name, "sha256": digest,
                                             "page_count": document.page_count},
            "render": {"dpi": dpi, "lang": lang, "frame": "rotated_visible_page_top_left"},
            "pages": [_parse_page(document[n - 1], n, dpi, lang, crop_dir, digest)
                      for n in selected],
        }
