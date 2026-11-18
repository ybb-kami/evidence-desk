"""Local workbench routes for crop-linked, unverified formula candidates."""

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import Lock
from urllib.parse import parse_qs

from PIL import Image
import pymupdf

import app
from tools.formula_candidates import _WEIGHTS

_HERE = Path(__file__).resolve().parent
_INFERENCE = Lock()
_CONFIG = "设置 EVIDENCE_FORMULA_PYTHON 和 EVIDENCE_FORMULA_WEIGHTS 后重启工作台。"
_INSTALL = (
    "用 Python 3.12 建立独立虚拟环境并安装 rapid-latex-ocr==0.0.9、"
    "onnxruntime==1.32.0 和 requests。下载 image_resizer.onnx、encoder.onnx、"
    "decoder.onnx 和 tokenizer.json 到同一个本地目录。"
)
_RELEASE = "https://github.com/RapidAI/RapidLaTeXOCR/releases/tag/v0.0.0"


def _configuration():
    executable = Path(os.environ.get("EVIDENCE_FORMULA_PYTHON", ""))
    weights = Path(os.environ.get("EVIDENCE_FORMULA_WEIGHTS", ""))
    if (executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK)
            and weights.is_absolute() and all((weights / name).is_file() for name in _WEIGHTS.values())):
        return executable, weights
    return None


def _scope(req, required_region=False):
    allowed = {"id", "page", "region"} if required_region else {"id", "page"}
    if set(req) != allowed:
        raise app.Bad(400, "请选择文献、物理页和公式区域。" if required_region else "请选择文献和物理页。")
    docid, page = req["id"], req["page"]
    region = req.get("region")
    if (not isinstance(docid, str) or not app.ID.fullmatch(docid)
            or type(page) is not int or page < 1
            or (required_region and (not isinstance(region, str)
                    or not re.fullmatch(rf"p{page}-r[1-9][0-9]{{0,5}}", region)))):
        raise app.Bad(400, "文献、物理页或公式区域无效。")
    return docid, page, region


def _query(url, required_region=False):
    values = parse_qs(url.query, keep_blank_values=True)
    if not values and not required_region:
        return None
    if any(len(group) != 1 for group in values.values()):
        raise app.Bad(400, "每项参数只能出现一次。")
    raw = {key: value[0] for key, value in values.items()}
    if not re.fullmatch(r"[0-9]{1,6}", raw.get("page", "")):
        raise app.Bad(400, "物理页码无效。")
    raw["page"] = int(raw["page"])
    return raw


def _context(store, req, required_region=False):
    docid, number, regionid = _scope(req, required_region)
    info = store.page_info(docid, number)
    page = info["page"]
    regions = [region for region in (page or {}).get("regions", []) if region["type"] == "formula"]
    if required_region:
        region = next((value for value in regions if value["id"] == regionid), None)
        if region is None:
            raise app.Bad(404, "本页没有这个公式区域。")
        return info["doc"], page, region
    return info["doc"], page, regions


def _crop(store, docid, page, region):
    source = store.pdf(docid)
    if hashlib.sha256(source).hexdigest() != docid:
        raise app.Bad(409, "原 PDF 校验失败，请检查本地文献。")
    dpi = page["render"]["dpi"]
    x0, y0, x1, y1 = region["bbox"]
    if not (0 <= x0 < x1 <= page["width_px"] and 0 <= y0 < y1 <= page["height_px"]
            and (x1 - x0) * (y1 - y0) <= 1_000_000):
        raise app.Bad(422, "此公式的裁剪范围无效或过大。")
    with pymupdf.open(stream=source, filetype="pdf") as document:
        pix = document[page["physical_page"] - 1].get_pixmap(
            dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False
        )
    if (pix.width, pix.height) != (page["width_px"], page["height_px"]):
        raise app.Bad(409, "原图尺寸与 OCR 记录不一致。")
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    output = BytesIO()
    image.crop(region["bbox"]).save(output, "PNG")
    return output.getvalue()


def _infer(config, doc, page, region, crop):
    if not _INFERENCE.acquire(blocking=False):
        raise app.Bad(409, "本机正在识别另一条公式，请稍后重试。")
    try:
        with tempfile.TemporaryDirectory(prefix="formula-review-") as directory:
            root = Path(directory)
            name = "crop.png"
            (root / name).write_bytes(crop)
            scan = {
                "source": {"filename": doc["name"], "sha256": doc["id"], "page_count": doc["pages"]},
                "render": page["render"],
                "pages": [{
                    "physical_page": page["physical_page"], "lines": page["lines"],
                    "regions": [{**region, "crop": name}],
                }],
            }
            request_file = root / "scan.json"
            request_file.write_text(json.dumps(scan, ensure_ascii=False), encoding="utf-8")
            output = root / "candidate.json"
            command = [str(config[0]), str(_HERE / "tools" / "formula_candidates.py"),
                       str(request_file), "--crops", str(root), "--weights", str(config[1]),
                       "--output", str(output)]
            try:
                subprocess.run(command, check=True, timeout=90,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                result = json.loads(output.read_text(encoding="utf-8"))
            except subprocess.TimeoutExpired:
                raise app.Bad(504, "公式模型超时。请稍后再试。") from None
            except (subprocess.CalledProcessError, OSError, ValueError):
                raise app.Bad(502, "公式模型未能完成推理。请检查本地环境、权重和安装指引。") from None
        candidate, = result["candidates"]
        candidate.pop("evidence_crop", None)
        return {"source": result["source"], "model": result["model"], "candidate": candidate}
    finally:
        _INFERENCE.release()


def handle(handler, post, url):
    if url.path == "/formula-review" and not post:
        return handler.payload(200, (_HERE / "formula-review.html").read_bytes(),
                               "text/html; charset=utf-8")
    if url.path == "/formula-review/image" and not post:
        store = handler.server.library
        req = _query(url, True)
        doc, page, region = _context(store, req, True)
        return handler.payload(200, _crop(store, doc["id"], page, region), "image/png")
    if url.path != "/api/formula-review":
        raise app.Bad(405, "此路径只支持读取。")
    config = _configuration()
    store = handler.server.library
    if post:
        req = handler.json_request()
        doc, page, region = _context(store, req, True)
        if not config:
            raise app.Bad(503, f"本机公式模型未配置。{_CONFIG}")
        result = _infer(config, doc, page, region, _crop(store, doc["id"], page, region))
        return handler.payload(200, result)

    req = _query(url)
    body = {"configured": bool(config),
            "setup": {"instruction": _INSTALL, "configuration": _CONFIG, "release": _RELEASE}}
    if req:
        doc, page, regions = _context(store, req)
        body.update(doc=doc, page_scanned=page is not None, regions=regions)
    return handler.payload(200, body)
