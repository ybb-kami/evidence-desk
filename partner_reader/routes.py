"""Local bilingual reader HTTP routes for the scan-paper workbench."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import fitz

ASSETS = Path(__file__).parent


def read_page(data, page_number):
    with fitz.open(stream=data, filetype="pdf") as pdf:
        if not 1 <= page_number <= len(pdf):
            raise ValueError("页码超出范围。")
        page = pdf[page_number - 1]
        segments = []
        for block in page.get_text(
            "dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES, sort=True
        )["blocks"]:
            if block["type"] != 0:
                continue
            text, protected = "", []
            for line in block["lines"]:
                if text:
                    text += "\n"
                for span in line["spans"]:
                    start = len(text)
                    text += span["text"]
                    if (span["flags"] & 8 and span["text"].isascii()) or re.search(
                        r"math|symbol|cm(?:mi|sy|ex|tt)", span["font"], re.IGNORECASE
                    ):
                        protected.append([start, len(text)])
            if text.strip():
                segments.append(
                    {
                        "block": block["number"] + 1,
                        "text": text,
                        "protected": protected,
                        "bbox": list(fitz.Rect(block["bbox"]) * page.rotation_matrix),
                    }
                )
        result = {
            "page": page_number,
            "pages": len(pdf),
            "label": page.get_label() or str(page_number),
            "segments": segments,
        }
        if segments and (pdf.metadata or {}).get("title") == "Local English OCR text-layer derivative":
            result["source"] = "ocr"
        return result


def scan_segments(page):
    formulas = [
        fitz.Rect(r["bbox_pt"]) for r in page["regions"] if r["type"] == "formula"
    ]
    paragraphs = {}
    for line in page["lines"]:
        box = fitz.Rect(line["bbox_pt"])
        group = line["line_id"].rsplit(".", 1)[0]
        segment = paragraphs.setdefault(
            group,
            {
                "block": len(paragraphs) + 1,
                "text": "",
                "protected": [],
                "bbox": list(box),
            },
        )
        if segment["text"]:
            segment["text"] += "\n"
        start = len(segment["text"])
        segment["text"] += line["text"]
        if any((box & math).get_area() >= 0.5 * box.get_area() for math in formulas):
            segment["protected"].append([start, len(segment["text"])])
        segment["bbox"] = list(fitz.Rect(segment["bbox"]) | box)
    return list(paragraphs.values())


def handle(handler, post, path, *, bad):
    """Use the active app's HTTP error class; never import app a second time."""
    assets = {
        "/translation": ("reader.html", "text/html"),
        "/bilingual/export.js": ("export.js", "text/javascript"),
        "/bilingual/reader.js": ("reader.js", "text/javascript"),
    }
    if not post and path in assets:
        filename, mime = assets[path]
        handler.payload(200, (ASSETS / filename).read_bytes(), mime + "; charset=utf-8")
    elif not post and (
        match := re.fullmatch(r"/api/bilingual/(page|crop)/([a-f0-9]{64})", path)
    ):
        params = parse_qs(urlsplit(handler.path).query)
        number = params.get("page", ["1"])[0]
        if not re.fullmatch(r"[1-9][0-9]{0,4}", number):
            raise bad(400, "页码无效。")
        if not handler.server.library.records([match[2]]):
            raise bad(404, "文献不存在于活动文库。")
        data = handler.server.library.pdf(match[2])
        if data is None:
            raise bad(404, "文献不存在。")
        try:
            result = read_page(data, int(number))
        except ValueError as exc:
            raise bad(400, str(exc)) from None
        if match[1] == "page":
            handler.payload(200, result)
        else:
            selected = params.get("block", [""])[0]
            block = next(
                (b for b in result["segments"] if str(b["block"]) == selected), None
            )
            if not block:
                raise bad(404, "找不到该原文段。")
            with fitz.open(stream=data, filetype="pdf") as pdf:
                page = pdf[int(number) - 1]
                clip = (fitz.Rect(block["bbox"]) + (-5, -5, 5, 5)) & page.rect
                if clip.is_empty:
                    raise bad(404, "该段无法裁切。")
                scale = min(2.0, 1400 / max(clip.width, clip.height))
                image = page.get_pixmap(
                    matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False
                )
                handler.payload(200, image.tobytes("png"), "image/png")
    elif not post and path == "/api/bilingual/models":
        from . import local

        handler.payload(200, {"directions": local.available()})
    elif post and path == "/api/bilingual/translate":
        from . import local

        req = handler.json_request()
        direction, text = req.get("direction"), req.get("text")
        if (
            set(req) != {"direction", "text"}
            or direction not in ("en-zh", "zh-en")
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 2000
        ):
            raise bad(400, "翻译方向或文本长度无效。")
        try:
            if direction not in local.available():
                raise RuntimeError("local model unavailable")
            translated = local.translate(direction, text)
        except local.Busy:
            raise bad(409, "本机已在翻译另一个片段，请稍后重试。") from None
        except (ImportError, RuntimeError, ValueError, OSError) as error:
            raise bad(503, "本机翻译模型不可用。请参阅本地模型配置说明；不会自动下载或联网。") from error
        handler.payload(200, {"text": translated})
    else:
        return False
    return True
