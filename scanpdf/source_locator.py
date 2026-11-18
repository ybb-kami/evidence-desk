"""Read-only coordinates for a saved native or OCR passage on its original PDF page."""

from collections import defaultdict
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlencode
import zlib

import pymupdf

import app
import engine


HERE = Path(__file__).resolve().parent
ROUTES = {"/source", "/source.js", "/api/source", "/source-image"}
MAX_GEOMETRY_CHARS = 200_000


def _query(url, *, passage):
    try:
        values = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=6)
    except ValueError:
        raise app.Bad(400, "引文定位参数无效。") from None
    required = {"id", "page"}
    anchor = required | {"block", "part"}
    cell = required | {"region", "row", "column"}
    allowed = (anchor, anchor | {"start", "end"}, cell) if passage else (required,)
    if set(values) not in allowed or any(len(group) != 1 for group in values.values()):
        raise app.Bad(400, "引文定位参数不完整或重复。")
    request = {key: group[0] for key, group in values.items()}
    if not app.ID.fullmatch(request["id"]):
        raise app.Bad(400, "文献标识无效。")
    for key in request.keys() - {"id", "region"}:
        value = request[key]
        if not re.fullmatch(r"[0-9]{1,6}", value) or (key != "start" and int(value) == 0):
            raise app.Bad(400, "页码、段落或字符位置无效。")
        request[key] = int(value)
    if "region" in request and not re.fullmatch(rf"p{request['page']}-r[1-9][0-9]{{0,5}}", request["region"]):
        raise app.Bad(400, "表格区域与物理页不匹配。")
    return request


def _document(store, docid):
    data = store.pdf(docid)
    if data is None:
        raise app.Bad(404, "本地文库中没有此版本的 PDF。")
    if hashlib.sha256(data).hexdigest() != docid:
        raise app.Bad(409, "原始 PDF 校验失败。")
    return pymupdf.open(stream=data, filetype="pdf")


def _page(pdf, number):
    if not 1 <= number <= len(pdf):
        raise app.Bad(404, "该版本中没有这个物理页。")
    return pdf[number - 1]


def render_image(page):
    """Render the same visible, rotated page frame used by the locator boxes."""
    width, height = page.rect.width, page.rect.height
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise app.Bad(422, "无效的 PDF 页面尺寸。")
    scale = min(1.8, 1600 / max(width, height))
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")


def _result(precision, boxes=()):
    return {"precision": precision, "boxes": list(boxes)}


def _rectangle(box, page, *, native=False):
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or not all(type(value) in (int, float) and math.isfinite(value) for value in box)):
        return None
    rect = pymupdf.Rect(box)
    if not rect.is_valid or rect.is_empty:
        return None
    if native:
        rect = rect * page.rotation_matrix
    rect = rect & page.rect
    if not rect.is_empty:
        return tuple(round(value, 4) for value in rect)
    return None


def _regions(units, start, stop, page, *, native=False):
    rows = {}
    for line, bbox in units[start:stop]:
        box = _rectangle(bbox, page, native=native)
        if box is None:
            continue
        rect = pymupdf.Rect(box)
        if line in rows:
            rows[line].include_rect(rect)
        else:
            rows[line] = rect
    return [[round(value, 4) for value in rect] for rect in rows.values()]


def _text_units(lines):
    pieces, units = [], []
    for line, tokens in enumerate(lines):
        if line:
            pieces.append("\n")
            units.append((line, None))
        for text, box in tokens:
            pieces.append(text)
            units.extend([(line, box)] * len(text))
    text = "".join(pieces)
    start = len(text) - len(text.lstrip())
    stop = len(text.rstrip())
    return text[start:stop], units[start:stop]


def _start(original, chunk):
    passage = chunk["text"]
    offset = chunk.get("offset")
    if type(offset) is int and offset >= 0 and original.startswith(passage, offset):
        return offset
    # Older records can lack offsets. An exact, unique occurrence is still usable.
    first = original.find(passage)
    return first if first >= 0 and original.find(passage, first + 1) < 0 else None


def _fine(original, units, chunk, span, page, precision, *, native=False):
    start = _start(original, chunk)
    if start is None:
        return None, None
    stop = start + len(chunk["text"])
    boxes = _regions(units, start, stop, page, native=native)
    selection = None
    if span:
        selected = _regions(units, start + span[0], start + span[1], page, native=native)
        if selected:
            selection = _result(precision, selected)
    return _result(precision, boxes) if boxes else None, selection


def _native(page, chunk, span):
    textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_BLOCKS)
    blocks = [row for row in textpage.extractBLOCKS() if row[6] == 0]
    source = next((row for row in blocks if row[5] + 1 == chunk["block"]), None)
    if source is None or chunk["text"] not in source[4]:
        return _result("page"), None
    bbox = _rectangle(source[:4], page, native=True)
    fallback = _result("block", [bbox]) if bbox else _result("page")
    if sum(len(row[4]) for row in blocks) > MAX_GEOMETRY_CHARS:
        return fallback, None
    raw = next((entry for entry in textpage.extractRAWDICT()["blocks"]
                if entry["type"] == 0 and entry["number"] + 1 == chunk["block"]), None)
    if raw is None:
        return fallback, None
    text, units = _text_units(
        ((char["c"], char["bbox"] if not char["c"].isspace() else None)
         for segment in line["spans"] for char in segment["chars"])
        for line in raw["lines"]
    )
    if text != source[4].strip():
        return fallback, None
    passage, selection = _fine(text, units, chunk, span, page, "glyph", native=True)
    return passage or fallback, selection


def ocr_paragraphs(scan):
    """Keep the same paragraph insertion order as workbench.index_page."""
    groups = defaultdict(list)
    for line in scan["lines"]:
        groups[line["line_id"].rsplit(".", 1)[0]].append(line)
    return list(groups.values())


def ocr_index_cells(scan):
    """Keep the original indexed crop order and the text and rectangle it cited."""
    if "indexed_crop_cells" in scan:
        saved = scan["indexed_crop_cells"]
        if not isinstance(saved, list):
            return []
        return [cell if isinstance(cell, dict) and isinstance(cell.get("text"), str)
                else {"text": "", "bbox_pt": None} for cell in saved]
    return [{"text": cell["text"], "bbox_pt": cell.get("bbox_pt")}
            for region in scan["regions"] for cell in region.get("cells", ())
            if cell.get("text_method") == "cell_crop_tesseract"]


def _cached_ocr(store, docid, number):
    if not hasattr(store, "db"):
        return None
    uri = Path(store.db).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_pages'").fetchone():
            return None
        row = db.execute("SELECT result FROM scan_pages WHERE id=? AND page=?", (docid, number)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(zlib.decompress(row[0]))
    except (zlib.error, ValueError, TypeError):
        return None


def _ocr_frame(scan, page):
    width, height = scan.get("width_pt"), scan.get("height_pt")
    return (scan.get("physical_page") == page.number + 1 and scan.get("rotation") == page.rotation
            and type(width) in (int, float) and type(height) in (int, float)
            and math.isclose(width, page.rect.width, abs_tol=0.001)
            and math.isclose(height, page.rect.height, abs_tol=0.001)
            and scan.get("cropbox") == list(page.cropbox)
            and (scan.get("render") or {}).get("frame") == "rotated_visible_page_top_left")


def _ocr_words(line):
    for index, word in enumerate(line.get("words", ())):
        if index:
            yield " ", None
        yield word["text"], word.get("bbox_pt")


def _ocr_paragraph(page, chunk, span, lines):
    original = "\n".join(line["text"] for line in lines).strip()
    if chunk["text"] not in original:
        return _result("page"), None
    text, units = _text_units(_ocr_words(line) for line in lines)
    if text == original:
        passage, selection = _fine(text, units, chunk, span, page, "word")
        if passage:
            return passage, selection
    _, units = _text_units([(line["text"], line.get("bbox_pt"))] for line in lines)
    passage, _ = _fine(original, units, chunk, None, page, "line")
    if passage:
        return passage, None
    boxes = [box for line in lines if (box := _rectangle(line.get("bbox_pt"), page))]
    return _result("block", boxes) if boxes else _result("page"), None


def _ocr_cell(page, chunk, group, cells, document):
    # Match the full indexed cell and require a unique original candidate
    # before using its rectangle.
    expected = [(part["text"], part.get("offset")) for part in document["chunks"]
                if part["page"] == chunk["page"] and part["block"] == chunk["block"]]
    if not 0 <= group < len(cells) or not expected:
        return _result("page"), None
    matches = [index for index, cell in enumerate(cells) if list(engine._windows(cell["text"])) == expected]
    if matches == [group] and (box := _rectangle(cells[group].get("bbox_pt"), page)):
        return _result("cell", [box]), None
    return _result("page"), None


def _ocr(store, document, page, chunk, span):
    scan = _cached_ocr(store, document["id"], page.number + 1)
    if not isinstance(scan, dict) or not _ocr_frame(scan, page):
        return _result("page"), None
    paragraphs = ocr_paragraphs(scan)
    group = chunk["block"] - 1
    if group < len(paragraphs):
        return _ocr_paragraph(page, chunk, span, paragraphs[group])
    return _ocr_cell(page, chunk, group - len(paragraphs), ocr_index_cells(scan), document)


def _passage_view(store, doc, page, request):
    block, part = request["block"], request["part"]
    matches = [row for row in doc["chunks"] if (row["page"], row["block"], row.get("part", 1))
               == (page.number + 1, block, part)]
    if len(matches) != 1:
        raise app.Bad(404, "本页没有这个可核对的引文段落。")
    chunk = matches[0]
    span = (request["start"], request["end"]) if "start" in request else None
    if span and not 0 <= span[0] < span[1] <= len(chunk["text"]):
        raise app.Bad(400, "证据字符范围超出该段原文。")
    source = "ocr" if chunk.get("source") == "ocr" else "native"
    if source == "ocr":
        passage, selection = _ocr(store, doc, page, chunk, span)
    else:
        passage, selection = _native(page, chunk, span)
    return {
        "block": block, "part": part, "text": chunk["text"], "source": source, "passage": passage,
        "selection": ({"start": span[0], "end": span[1], **(selection or _result("text"))}
                      if span else None),
    }


def _cell_view(page, request):
    if __package__:
        from .scan_table_qa import native_cell_box
    else:
        from scan_table_qa import native_cell_box
    region, row, column = (request[key] for key in ("region", "row", "column"))
    cell = native_cell_box(page, region, row, column)
    if not cell or not (box := _rectangle(cell.get("bbox_pt"), page)):
        raise app.Bad(404, "本页没有这个可核对的原生表格单元格。")
    return {"region": region, "table": cell.get("table"), "row": row, "column": column,
            "text": cell["text"], "source": "native", "passage": _result("cell", [box]),
            "selection": None}


def locate(store, request):
    docid, number = request["id"], request["page"]
    docs = store.records([docid], archived=None)
    if not docs:
        raise app.Bad(404, "本地文库中没有此版本的文献。")
    doc = docs[0]
    with _document(store, docid) as pdf:
        page = _page(pdf, number)
        evidence = _cell_view(page, request) if "region" in request else _passage_view(store, doc, page, request)
        return {
            "doc": {"id": docid, "name": doc["name"]},
            "page": number, "page_label": page.get_label() or str(number),
            "width": page.rect.width, "height": page.rect.height, "rotation": page.rotation,
            "image_url": "/source-image?" + urlencode({"id": docid, "page": number}),
            "pdf_url": f"/pdf/{docid}#page={number}",
            **evidence,
        }


def handle(handler, post, url):
    if post:
        raise app.Bad(405, "引文定位只支持读取。")
    if url.path == "/source":
        return handler.payload(200, (HERE / "source.html").read_bytes(), "text/html; charset=utf-8")
    if url.path == "/source.js":
        return handler.payload(200, (HERE / "source.js").read_bytes(), "application/javascript; charset=utf-8")
    if handler.headers.get("Sec-Fetch-Site") == "cross-site":
        raise app.Bad(403, "不能跨站读取本地引文或原页。")
    store = handler.server.library
    if url.path == "/api/source":
        return handler.payload(200, locate(store, _query(url, passage=True)))
    if url.path == "/source-image":
        request = _query(url, passage=False)
        with _document(store, request["id"]) as pdf:
            return handler.payload(200, render_image(_page(pdf, request["page"])), "image/png")
    raise app.Bad(404, "找不到引文定位路径。")
