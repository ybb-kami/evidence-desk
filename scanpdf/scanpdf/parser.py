"""Parse scanned pages in the visible, rotated PDF coordinate frame."""
from collections import defaultdict
import hashlib
import math
from pathlib import Path
import re

import pymupdf
from PIL import Image, ImageStat

from .formulas import detect_formulas
from .math_cells import review_math_cells
from .numeric import refine_numeric_cells
from .ocr import recognize
from .tables import detect_tables


PAGE_FRAME = "rotated_visible_page_top_left"


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
            if cell["text"] or cell["status"] == "numeric_unresolved":
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
                            status="ocr_text", text_method="cell_crop_tesseract",
                            ocr_confidence=round(min(word["confidence"] for word in recovered), 2))


def _render_page(page, dpi):
    rect = page.rect
    if math.ceil(rect.width * dpi / 72) * math.ceil(rect.height * dpi / 72) > 25_000_000:
        raise ValueError(f"Physical page {page.number + 1} exceeds 25 megapixels; use a lower DPI")
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _page_geometry(page, number, image):
    return {"physical_page": number, "width_px": image.width, "height_px": image.height,
            "width_pt": page.rect.width, "height_pt": page.rect.height, "rotation": page.rotation,
            "cropbox": list(page.cropbox)}


def _locate(record, rect, dpi, field="bbox"):
    record[field] = [int(value) for value in record[field]]
    record[field + "_pt"] = [round(min(rect.height if i % 2 else rect.width, value * 72 / dpi), 4)
                              for i, value in enumerate(record[field])]


def _region_crop(record, image, crops, digest):
    filename = f"{digest[:16]}-{record['id']}.png"
    with (crops / filename).open("xb") as out:
        image.crop(record["bbox"]).save(out, "PNG")
    record["crop"] = filename


def _figure_annotations(page, number, image, lines, tables, dpi, crops, digest):
    from .figures import detect_figures

    figures, unresolved = detect_figures(image, lines, dpi, tables)
    for index, item in enumerate(figures, 1):
        item.update(id=f"p{number}-f{index}", type="figure", status="detected_only",
                    physical_page=number, source_sha256=digest)
        _locate(item, page.rect, dpi)
        _locate(item, page.rect, dpi, "caption_bbox")
        if crops:
            _region_crop(item, image, crops, digest)
    for index, item in enumerate(unresolved, 1):
        item.update(id=f"p{number}-fc{index}", type="figure_caption", status="caption_only",
                    physical_page=number, source_sha256=digest)
        _locate(item, page.rect, dpi, "caption_bbox")
    return {"figures": figures, "unresolved_captions": unresolved}


def _parse_page(page, number, dpi, lang, crops, digest, figure_lines):
    rect = page.rect
    image = _render_page(page, dpi)
    words = recognize(image, dpi, lang, 3)
    if not words and ImageStat.Stat(image).stddev[0] > 5:
        words = recognize(image, dpi, lang, 11)
    lines = _lines(words)
    tables = detect_tables(image, words, dpi)
    regions = tables + detect_formulas(image, words, dpi, tables)
    refine_numeric_cells(image, tables, dpi, lang)
    _read_missing_cells(image, tables, dpi, lang)
    review_math_cells(tables)
    regions.sort(key=lambda reg: (reg["bbox"][1], reg["bbox"][0]))
    annotations = _figure_annotations(page, number, image, lines if figure_lines is None else figure_lines,
                                      tables, dpi, crops, digest)

    for line in lines:
        _locate(line, rect, dpi)
        for word in line["words"]:
            _locate(word, rect, dpi)
    for index, region in enumerate(regions, 1):
        region["id"] = f"p{number}-r{index}"
        _locate(region, rect, dpi)
        for cell in region.get("cells", []):
            _locate(cell, rect, dpi)
            if "numeric_ocr" in cell:
                _locate(cell["numeric_ocr"], rect, dpi)
        if crops:
            _region_crop(region, image, crops, digest)
    return {**_page_geometry(page, number, image), "ocr_status": "words_found" if words else "no_words",
            "lines": lines, "regions": regions, **annotations}


def parse_pdf(source, *, pages=None, dpi=240, lang="eng", crops=None, figure_ocr=None):
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
            "render": {"dpi": dpi, "lang": lang, "frame": PAGE_FRAME},
            "pages": [_parse_page(document[n - 1], n, dpi, lang, crop_dir, digest, (figure_ocr or {}).get(n))
                      for n in selected],
        }


def refresh_cached_figures(source, cached):
    """Locate figures against the original raster and the cached OCR line anchors."""
    number = cached["physical_page"]
    render = cached["render"]
    dpi = render["dpi"]
    if render["frame"] != PAGE_FRAME or not 72 <= dpi <= 600:
        raise ValueError("无效的扫描页坐标系。")
    path = Path(source)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with pymupdf.open(path) as pdf:
        page = pdf[number - 1]
        image = _render_page(page, dpi)
        geometry = _page_geometry(page, number, image)
        if any(cached.get(key) != value for key, value in geometry.items()):
            raise ValueError("更新后的区域坐标与原页不一致。")
        tables = [region for region in cached["regions"] if region["type"] == "table"]
        fields = _figure_annotations(page, number, image, cached["lines"], tables, dpi, None, digest)
    return {"source": {"sha256": digest}, "render": render, "pages": [cached | fields]}
