"""Read a grouped BLEU table; adapted from partner commit c18c3a0.

Pure local inputs: document index, question, and original PDF bytes.
"""

import hashlib
from decimal import Decimal
import math
import re
import unicodedata

import fitz
from engine import MAX_BYTES

DPI = 240
MAX_PIXELS = 25_000_000
TASKS = {
    "EN-DE": r"\b(?:en\s*-\s*de|english(?:\s*-\s*|\s+)to(?:\s*-\s*|\s+)german)\b|英德|英语(?:译|翻译)?(?:到|至|转)德语|英语到德语",
    "EN-FR": r"\b(?:en\s*-\s*fr|english(?:\s*-\s*|\s+)to(?:\s*-\s*|\s+)french)\b|英法|英语(?:译|翻译)?(?:到|至|转)法语|英语到法语",
}
CAPTION = re.compile(r"^table\s+\d+\s*[.:：]\s+.*\bbleu\b", re.I)
PRIOR = re.compile(r"previous(?:ly)?\b|state[- ]of[- ]the[- ]art|prior\b", re.I)
REFERENCE = re.compile(r"\s*[\[|]\s*\d{1,3}\s*[\]|]\s*$")


def _normal(text):
    return " ".join(unicodedata.normalize("NFKC", text).translate(str.maketrans("‐‑–", "---")).split())


def _hit(doc, excerpt):
    return dict(excerpt, id=doc["id"], name=doc["name"])


def _gap(hits, message):
    return {"answer": None, "hits": hits, "gap": message}


def _render(page, dpi):
    from PIL import Image
    if math.ceil(page.rect.width * dpi / 72) * math.ceil(page.rect.height * dpi / 72) > MAX_PIXELS:
        return None
    pixels = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    return Image.frombytes("L", (pixels.width, pixels.height), pixels.samples)


def _native_tables(page, image):
    from partner_tables import detect_tables
    words = []
    for x0, y0, x1, y1, text, block, line, _ in page.get_text("words", sort=True):
        box = fitz.Rect(x0, y0, x1, y1) * page.rotation_matrix
        words.append({
            "bbox": [round(position * DPI / 72) for position in box],
            "text": text, "confidence": 100, "line_id": f"{block}.{line}",
        })
    return detect_tables(image, words, DPI)


def _blank(image, box, dpi):
    # Trim the row boundaries, which may be printed rules or neighboring descenders.
    x0, y0, x1, y1 = map(int, box)
    margin = max(2, round(dpi * .015))
    if x1 - x0 <= 2 * margin or y1 - y0 <= 2 * margin:
        return False
    patch = image.crop((x0 + margin, y0 + margin, x1 - margin, y1 - margin))
    # Ignore sparse scan speckles. A missing/raster-only number has much more ink.
    dark = sum(patch.histogram()[:145])
    return dark <= max(3, round(dpi * .02))


def _score(cell, image, dpi):
    text = _normal(cell["text"])
    if not text:
        return (None, _blank(image, cell["bbox"], dpi))
    if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3})?", text):
        return (None, False)
    value = Decimal(text)
    return value, 0 <= value <= 100


def _column(table, task):
    cells = table["cells"]
    metric_headers = [cell for cell in cells if _normal(cell["text"]).upper() == "BLEU"]
    if len(metric_headers) != 1:
        return None
    header = metric_headers[0]
    span = header.get("column_span", 1)
    specific = [
        cell for cell in cells if cell["row"] == header["row"] + 1
        and header["column"] <= cell["column"] < header["column"] + span
        and re.fullmatch(TASKS[task], _normal(cell["text"]), re.I)
    ]
    labels = [
        cell for cell in cells if cell["column"] == 1 and cell["row"] <= header["row"] + 1
        and _normal(cell["text"]).lower() in {"model", "models", "system", "systems"}
    ]
    if len(specific) == 1 and len(labels) == 1:
        return header, specific[0], labels[0]
    return None


def _compare(table, headers, question, image, dpi):
    _, task, label = headers
    cells = {(cell["row"], cell["column"]): cell for cell in table["cells"]}
    models = [cell for cell in table["cells"] if cell["column"] == label["column"] and cell["row"] > task["row"]]
    model_names = {cell["row"]: REFERENCE.sub("", _normal(cell["text"])) for cell in models}
    targets = [
        cell for cell in models if model_names[cell["row"]] and re.search(
            r"(?<![A-Za-z0-9])" + re.escape(model_names[cell["row"]]) + r"(?![A-Za-z0-9])",
            question, re.I)
    ]
    if len(targets) != 1:
        return None
    target = targets[0]
    # Sibling configurations of the queried model are this paper's experiments.
    family = re.sub(r"\s*\([^()]+\)\s*$", "", model_names[target["row"]])
    prior, target_score = [], None
    for model in models:
        row = model["row"]
        name = model_names[row]
        cell = cells.get((row, task["column"]))
        if cell is None or cell.get("column_span", 1) != 1 or not name:
            return None
        if row == target["row"]:
            target_score, valid = _score(cell, image, dpi)
            if not valid or target_score is None:
                return None
        elif re.fullmatch(re.escape(family) + r"\s*\([^()]+\)", name, re.I) and not REFERENCE.search(model["text"]):
            continue
        elif REFERENCE.search(model["text"]):
            score, valid = _score(cell, image, dpi)
            if not valid:
                return None
            if score is not None:
                prior.append((score, name, model, cell))
        else:
            # A row with unclear provenance might change which comparator is best.
            return None
    if target_score is None or not prior:
        return None
    maximum = max(score for score, _, _, _ in prior)
    leaders = [entry for entry in prior if entry[0] == maximum]
    return model_names[target["row"]], target_score, maximum, leaders, target, cells[target["row"], task["column"]]


def _evidence(doc, page, dpi, selected):
    page_chunks = [row for row in doc["chunks"] if row["page"] == page.number + 1]
    boxes = [fitz.Rect(cell["bbox"]) * (72 / dpi) for cell in selected]
    matched = {
        block[5] + 1
        for block in page.get_text("blocks", flags=fitz.TEXTFLAGS_BLOCKS, sort=True)
        if block[6] == 0 and any(
            ((fitz.Rect(block[:4]) * page.rotation_matrix) & box).get_area() > 1 for box in boxes
        )
    }
    return [_hit(doc, chunk) for chunk in page_chunks if chunk["block"] in matched]


def _cell_anchor(role, cell, number, dpi):
    return {"role":role,"page":number,"row":cell["row"],"column":cell["column"],
            "text":cell["text"],"bbox_pt":[round(v*72/dpi,4) for v in cell["bbox"]],
            "frame":"rotated_visible_page_top_left"}


def _comparison_prose(doc, task, target):
    """A task-specific prose score, when present, corroborates the table."""
    passages = []
    qualifier = re.search(r"\(([a-z]+)\)$", target, re.I)
    alias = rf"\bour\s+{re.escape(qualifier[1])}\s+model\b" if qualifier else None
    for chunk in doc["chunks"]:
        text = _normal(chunk["text"])
        direction = re.search(TASKS[task], text, re.I)
        if direction is None:
            continue
        excerpt = text[direction.start():]
        next_task = re.search("|".join(pattern for code, pattern in TASKS.items() if code != task), excerpt, re.I)
        if next_task:
            excerpt = excerpt[:next_task.start()]
        score = re.search(r"\bBLEU\s+score\s+of\s+([0-9]+(?:\.[0-9]+)?)\b", excerpt[:350], re.I)
        if not score:
            continue
        lead = excerpt[:score.start()]
        if target.lower() in lead.lower() or (alias and re.search(alias, lead, re.I)):
            passages.append((Decimal(score[1]), _hit(doc, chunk)))
    return passages


def answer_metric(doc, question, pdf_bytes):
    """Answer a selected-document BLEU comparison or return an evidence gap."""
    normalized = _normal(question)
    tasks = [task for task, pattern in TASKS.items() if re.search(pattern, normalized, re.I)]
    if not re.search(r"\bBLEU\b", normalized, re.I) or not re.search(
        r"state[- ]of[- ]the[- ]art|previous|improv|prior|gain|当时|提升|提高|最佳|差值", normalized, re.I
    ):
        return None
    if len(tasks) != 1:
        return _gap([], "请明确一个语言方向，以便比较同一任务列的 BLEU。")
    task = tasks[0]
    captions = [
        _hit(doc, chunk) for chunk in doc["chunks"] if CAPTION.search(_normal(chunk["text"]))
        and PRIOR.search(_normal(chunk["text"])) and re.search(TASKS[task], _normal(chunk["text"]), re.I)
    ]
    if not captions:
        return _gap([], "未找到标明任务与既有模型的 BLEU 比较表；扫描件可先识别对应页面。")

    candidates = []
    pages = sorted({caption["page"] for caption in captions})
    data = pdf_bytes
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES or len(pages) > 8:
        return _gap(captions[:8], "未能确定唯一的 BLEU 比较表，请核对原页。")
    if hashlib.sha256(data).hexdigest() != doc["id"]:
        return _gap([], "原件字节哈希与所选文献不符，未采用任何数值。")
    try:
        import partner_tables
    except ImportError:
        return _gap(captions, "本机未安装可选的分组表格解析依赖，请核对原页。")
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for number in pages:
            if not 1 <= number <= len(pdf):
                continue
            page = pdf[number - 1]
            native_textless = number in doc.get("native_textless", doc.get("textless_pages", []))
            if native_textless:
                continue
            dpi = DPI
            image = _render(page, dpi)
            if image is None:
                continue
            tables = _native_tables(page, image)
            for table in tables:
                if table.get("type") != "table" or table.get("status") != "structure_recognized":
                    continue
                headers = _column(table, task)
                if not headers:
                    continue
                comparison = _compare(table, headers, normalized, image, dpi)
                if comparison:
                    model, score, maximum, leaders, target, target_cell = comparison
                    selected = [*headers, target, target_cell, *(cell for _, _, row, value in leaders for cell in (row, value))]
                    evidence = _evidence(doc, page, dpi, selected)
                    if evidence:
                        cells = [_cell_anchor(role, cell, number, dpi) for role, cell in zip(
                            ("metric_header","task_header","model_header","target_model","target_score"), selected[:5])]
                        for _, _, row, value in leaders:
                            cells.extend(_cell_anchor(role, cell, number, dpi) for role, cell in
                                         (("baseline_model",row),("baseline_score",value)))
                        if any(_normal(page.get_text(clip=fitz.Rect(cell["bbox_pt"])*page.derotation_matrix))
                               != _normal(cell["text"]) for cell in cells):
                            continue
                        candidates.append((model, score, maximum, leaders, number, evidence, cells))
    if len(candidates) != 1:
        return _gap(captions, "无法确认唯一的模型行、BLEU 任务列和既有最高分；请核对原表。")
    model, score, maximum, leaders, page, evidence, cells = candidates[0]
    prose = _comparison_prose(doc, task, model)
    hits = list({
        (hit["page"], hit["block"], hit["part"], hit["text"]): hit
        for hit in [*[caption for caption in captions if caption["page"] == page], *evidence, *(hit for _, hit in prose)]
    }.values())
    if any(value != score for value, _ in prose):
        return _gap(hits, "正文与表格对该任务报告的 BLEU 不一致，请核对原文。")
    names = "、".join(name for _, name, _, _ in leaders)
    difference = score - maximum
    if difference == 0:
        change = "与该最高分持平"
    elif difference > 0:
        change = f"提升 {difference:f} BLEU"
    else:
        change = f"低 {abs(difference):f} BLEU"
    return {
        "answer": f"{task} 的 BLEU 列中，{model} 为 {score:f}；表中既有模型最高为 {names}（{maximum:f}）。"
                  f"{score:f} − {maximum:f} = {difference:f}，{change}。",
        "hits": hits,
        "gap": None,
        "comparison": {"task":task,"page":page,"model":model,"score":format(score,"f"),
                       "baseline_models":[name for _,name,_,_ in leaders],"baseline":format(maximum,"f"),
                       "difference":format(difference,"f"),"source_document":doc["id"],"cells":cells},
    }
