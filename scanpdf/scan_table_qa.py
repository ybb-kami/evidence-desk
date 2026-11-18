"""Ground scan table questions in cached OCR cells and original page geometry."""
from collections import defaultdict
from decimal import Decimal
from itertools import groupby
from math import ceil
import re
from threading import Lock
import unicodedata


CAPTION = re.compile(r"^\s*(?:table\s*|表(?:格)?\s*)([0-9]+(?:\.[0-9]+)*)\s*(?:[：:、]|\.(?![0-9]))\s*", re.I)
TABLE = re.compile(r"\btable\s*(?:no\.?\s*|#\s*)?([0-9]+(?:\.[0-9]+)*)\b|第?\s*表(?:格)?\s*([0-9]+(?:\.[0-9]+)*|[一二三四五六七八九十两]+)|第\s*([0-9一二三四五六七八九十两]+)\s*张?\s*表")
PAGE = re.compile(r"\b(?:physical\s+)?page\s*([0-9]+)\b|第?\s*([0-9一二三四五六七八九十两]+)\s*页")
ROW = re.compile(r"\brow\s*([0-9]+)\b|第?\s*([0-9一二三四五六七八九十两]+)\s*行")
COLUMN = re.compile(r"\b(?:column|col\.?)\s*([0-9]+)\b|第?\s*([0-9一二三四五六七八九十两]+)\s*列")
ASK = re.compile(r"\b(?:value|values|score|scores|how much|how many|what|compare|difference)\b|多少|数值|得分|分数|分别|相差|什么|比较|哪[个一]|是[几多]")
MULTIPLE = re.compile(r"\b(?:both|each|respectively|all (?:directions|tasks|languages))\b|分别|各是|各有|各为|都是多少|两个(?:方向|任务)|两[个种](?:语言|方向|任务)")
DIFFERENCE = re.compile(r"\b(?:difference|gap|differs?|subtract|minus|by how much|how much (?:higher|lower|larger|smaller|more|less))\b|相差|差值|之差|差多少|提高了多少|高出多少|[多少]了多少")
COMPARE = re.compile(r"\b(?:compare|vs|versus)\b|对比|相比|比较")
UNSUPPORTED = re.compile(r"\b(?:highest|lowest|maximum|minimum|average|mean|median|sum|ratio|how many times|percentage increase|rank|top)\b|最高|最低|最优|最大|最小|平均|中位数|总和|多少倍|排名")
REFERENCE = re.compile(r"\s*[\[|]\s*(?:[0-9]{1,3}|[a-z][a-z+]{1,10}[0-9]{2,4})\s*[\]|]\s*$", re.I)
DECIMAL = r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
DASHES = str.maketrans("‐‑–—−", "-----")
UNIT = r"%|flops|ms|s|sec(?:onds?)?|kb|mb|gb|tb|[kmbt]|thousand|million|billion|trillion"
NATIVE_CANDIDATE_LIMIT = 6
NATIVE_GRID_LOCK = Lock()
QUERY_TERMS = {"a", "an", "and", "are", "did", "for", "from", "how", "in", "is", "many", "much", "of", "on",
               "score", "scores", "the", "to", "value", "values", "was", "were", "what", "which", "with"}
LANGS = {
    "en": ("english", "英语", "英文", "英"), "de": ("german", "德语", "德文", "德"),
    "fr": ("french", "法语", "法文", "法"), "es": ("spanish", "西班牙语", "西语", "西"),
    "it": ("italian", "意大利语", "意语", "意"), "zh": ("chinese", "中文", "汉语", "中"),
    "ja": ("japanese", "日语", "日文", "日"), "ko": ("korean", "韩语", "韩文", "韩"),
    "ru": ("russian", "俄语", "俄文", "俄"), "pt": ("portuguese", "葡萄牙语", "葡语", "葡"),
    "ar": ("arabic", "阿拉伯语", "阿语", "阿"),
    "ro": ("romanian", "罗马尼亚语"), "cs": ("czech", "捷克语"), "nl": ("dutch", "荷兰语"),
    "fi": ("finnish", "芬兰语"), "sv": ("swedish", "瑞典语"), "pl": ("polish", "波兰语"),
    "tr": ("turkish", "土耳其语"), "el": ("greek", "希腊语"), "uk": ("ukrainian", "乌克兰语"),
    "hi": ("hindi", "印地语"), "vi": ("vietnamese", "越南语"), "th": ("thai", "泰语"),
    "id": ("indonesian", "印度尼西亚语", "印尼语"),
}
LANG_NAMES = {name: key for key, names in LANGS.items() for name in names}
ENGLISH_LANGUAGES = "|".join(names[0] for names in LANGS.values())
LANG_CODES = re.compile(r"(?<![a-z0-9])([a-z]{2})\s*(?:-|→|->|\bto\b)\s*([a-z]{2})(?![a-z0-9])")
LANG_ENGLISH = re.compile(rf"\b({ENGLISH_LANGUAGES})\s*(?:-\s*to\s*-|to|into|→|->|-)\s*({ENGLISH_LANGUAGES})\b")
LANG_DIRECTION_START = re.compile(rf"\b(?:{ENGLISH_LANGUAGES})\s*(?:-\s*to\s*-|\bto\b|\binto\b|→|->)")
LANG_ENGLISH_NEXT = re.compile(rf"\s*(?:,\s*(?:and\s+)?|and\s+|or\s+|as well as\s+)({ENGLISH_LANGUAGES})\b"
                               r"(?!\s*(?:-\s*to\s*-|to\b|into\b|→|->))")
CHINESE_LANGUAGES = "|".join(sorted((s for s in LANG_NAMES if not s.isascii()), key=len, reverse=True))
LANG_CHINESE = re.compile(rf"({CHINESE_LANGUAGES})\s*(?:(?:翻?译|翻成)?[到成至转为]|翻?译|→|-)?\s*({CHINESE_LANGUAGES})")
LANG_CHINESE_START = re.compile(rf"(?:{CHINESE_LANGUAGES})\s*(?:翻?译[到成至转为]?|翻成|[到至转]|→)")
LANG_CHINESE_NEXT = re.compile(r"\s*(?:和|与|及|以及|、|或)\s*(" + "|".join(name for name in CHINESE_LANGUAGES.split("|") if len(name) > 1) + r")(?!\s*(?:翻?译|翻成|[到至转]|→))")
CONCEPTS = {
    "bleu": ("bleu", "bleu score", "bleu 分数", "bleu 得分"),
    "flops": ("flops", "training cost", "training costs", "training compute", "训练成本", "训练开销", "训练计算量", "训练算力", "训练耗费"),
    "f1": ("f1", "f1 score", "f1 measure", "f1 分数", "f1 得分", "f1值"),
    "accuracy": ("accuracy", "accuracies", "准确率", "正确率"),
    "precision": ("precision", "精确率", "查准率"),
    "recall": ("recall", "召回率", "查全率"),
    "meteor": ("meteor",), "rouge": ("rouge",), "glue": ("glue",),
    "superglue": ("superglue", "super glue", "sglue"), "squad": ("squad",),
    "chrf": ("chrf", "chrf++"), "perplexity": ("perplexity", "ppl", "困惑度"),
    "parameters": ("parameters", "parameter count", "params", "参数量", "参数规模", "参数个数"),
    "latency": ("latency", "延迟", "时延"), "throughput": ("throughput", "吞吐量", "吞吐率"),
    "memory": ("memory", "内存", "显存"),
    "complexity": ("complexity", "per layer complexity", "单层复杂度", "每层复杂度", "复杂度"),
    "path length": ("maximum path length", "max path length", "path length", "最大路径长度", "最长路径"),
    "sequential operations": ("sequential operations", "顺序操作", "串行操作"),
}
VARIANTS = {
    "base": ("base model", "baseline model", "base", "基础模型", "基础版本", "基础版", "基本版", "基础型"),
    "big": ("big model", "large model", "big", "large", "大型模型", "大模型", "大型版本", "大型版", "大版本", "大版"),
    "small": ("small model", "small", "小型模型", "小模型", "小型版", "小版"),
}
VARIANT_LEADS = {"a", "an", "and", "are", "between", "compare", "for", "from", "is", "of", "or", "the", "to", "versus", "vs", "was", "with"}


def _normal(text):
    text = unicodedata.normalize("NFKC", text).casefold().translate(DASHES)
    return " ".join(text.split())


def _words(text):
    return " ".join(re.findall(r"[a-z0-9]+|[^\W\d_a-z]+", _normal(text)))


def _find(phrase, query):
    if not phrase:
        return None
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", query)


def _number(value):
    digits = "一二三四五六七八九"
    value = value.replace("两", "二")
    if value.isascii():
        if "." in value:
            return ".".join(str(int(part)) for part in value.split("."))
        return int(value)
    if len(value) == 1 and value in digits:
        return digits.index(value) + 1
    tens = re.fullmatch(r"([一二三四五六七八九]?)十([一二三四五六七八九]?)", value)
    if tens:
        high, low = tens.groups()
        return (digits.index(high) + 1 if high else 1) * 10 + (digits.index(low) + 1 if low else 0)
    return None


def _directions(text):
    found = {f"{src}-{dst}" for src, dst in LANG_CODES.findall(text) if src in LANGS or dst in LANGS}
    for pattern, next_target in ((LANG_ENGLISH, LANG_ENGLISH_NEXT), (LANG_CHINESE, LANG_CHINESE_NEXT)):
        for match in pattern.finditer(text):
            src, dst = map(LANG_NAMES.__getitem__, match.groups())
            if src == dst:
                continue
            found.add(f"{src}-{dst}")
            stop = match.end()
            while following := next_target.match(text, stop):
                dst = LANG_NAMES[following[1]]
                if dst != src:
                    found.add(f"{src}-{dst}")
                stop = following.end()
    return found


def _header_directions(label):
    compact = re.fullmatch(r"([a-z]{2})([a-z]{2})[~\-]?", label)
    if compact and compact[1] in LANGS and compact[2] in LANGS and compact[1] != compact[2]:
        return {f"{compact[1]}-{compact[2]}"}
    return _directions(label)


def _concepts(text):
    return {key for key, aliases in CONCEPTS.items() if any(_find(alias, text) for alias in aliases)}


def _variants(text):
    return {key for key, aliases in VARIANTS.items() if any(_find(alias, text) for alias in aliases)}


def _scope(pattern, query):
    return {value for match in pattern.finditer(query)
            if (value := _number(next(x for x in match.groups() if x is not None))) is not None}


def _geometry_gap(line, region):
    l, t, r, b = line["bbox_pt"]
    xl, yt, xr, yb = region["bbox_pt"]
    overlap = min(r, xr) - max(l, xl)
    if overlap < .3 * min(r - l, xr - xl):
        return None
    if b <= yt + 2 and yt - b <= 76:
        return max(0, yt - b)
    if t >= yb - 2 and t - yb <= 42:
        return max(0, t - yb) + 5
    return None


def _tables(page):
    regions = [region for region in page["regions"] if region.get("type") == "table" and region.get("bbox_pt")]
    captions = defaultdict(list)
    for line in page["lines"]:
        match = CAPTION.match(_normal(line["text"]))
        if not match or not line.get("bbox_pt"):
            continue
        nearest = sorted((gap, i) for i, region in enumerate(regions) if (gap := _geometry_gap(line, region)) is not None)
        if nearest and (len(nearest) == 1 or nearest[1][0] - nearest[0][0] > 3):
            captions[nearest[0][1]].append((_number(match[1]), line))
    for i, region in enumerate(regions):
        number, caption = captions[i][0] if len(captions[i]) == 1 else (None, None)
        yield number, caption, region


def _header_grid(region):
    cells = region.get("cells", [])
    first_row = [cell for cell in cells if cell["row"] == 1 and cell.get("text")]
    if len(first_row) < 2:
        return 0, {}, {}
    depth = max(cell.get("row_span", 1) for cell in first_row)
    if depth == 1:
        second = {cell["column"]: _normal(cell["text"]) for cell in cells if cell["row"] == 2 and cell.get("text")}
        stub = second.get(1, "")
        other = [text for col, text in second.items() if col != 1]
        if (len(other) >= 2 and (not stub or stub.startswith("(")) and all(text.startswith("(") for text in other)
                or other and not any(cell["column"] == 1 for cell in first_row)
                and _words(stub) in {"dataset", "model", "method", "metric", "task", "system", "architecture"}
                and any(_numeric(text) is None for text in other)):
            depth = 2
    headers, rows = defaultdict(list), {}
    for cell in cells:
        row, col = cell["row"], cell["column"]
        if row <= depth:
            for column in range(col, col + cell.get("column_span", 1)):
                if cell.get("text"):
                    headers[column].append(cell)
        elif col == 1 and cell.get("text"):
            rows[row] = cell
    return depth, headers, rows


def _row_matches(rows, query, cells):
    matches = []
    for row, cell in rows.items():
        name = REFERENCE.sub("", _normal(cell["text"]))
        plain = _words(name)
        aliases = {plain}
        author = re.fullmatch(r"(.+?)\s+(?:et al\.|el al\.)\s*\([0-9]{4}\)", name)
        if author:
            aliases.update((_words(author[1]), _words(author[1]) + " et al"))
        variant = (re.fullmatch(r"(.+?)\s*\(([^()]+)\)", name)
                   or re.fullmatch(r"(.+?) (base(?: model)?|big|large|small)", plain))
        if variant:
            family = _words(variant[1])
            suffix = _words(variant[2])
            variants = next((forms for forms in VARIANTS.values() if suffix in forms), ())
            if not variants and not any(c.isdigit() for c in suffix):
                aliases.add(family)
            aliases.update(family + " " + alias for alias in variants)
            aliases.update(family + " 的 " + alias for alias in variants)
            if not any(_find(alias, query) for alias in aliases):
                for alias in variants:
                    qualifier = _find(alias, query)
                    if not qualifier:
                        continue
                    previous = query[:qualifier.start()].split()
                    if not previous or not previous[-1].isascii() or previous[-1] in VARIANT_LEADS:
                        matches.append((row, qualifier.span(), len(alias)))
        for alias in aliases:
            match = _find(alias, query)
            if match:
                matches.append((row, match.span(), len(alias)))
    best = [entry for entry in matches if not any(entry[0] != other[0] and entry[2] < other[2]
            and other[1][0] <= entry[1][0] and entry[1][1] <= other[1][1] for other in matches)]
    by_mention = defaultdict(set)
    for row, span, _ in best:
        by_mention[span].add(row)
    result = set()
    for candidates in by_mention.values():
        if len(candidates) > 1:
            qualified = {cell["row"] for cell in cells if cell["row"] in candidates and cell["column"] > 1
                         and len(_words(cell["text"])) > 2 and not re.search(r"[0-9]", cell["text"])
                         and _find(_words(cell["text"]), query)}
            if len(qualified) != 1:
                return [], True
            candidates = qualified
        result.update(candidates)
    return sorted(result), False


def _columns(headers, query, caption):
    wanted_tasks = _directions(query)
    plain = _words(query)
    concepts = _concepts(plain)
    caption_concepts = _concepts(_words(caption["text"])) if caption else set()
    header_concepts = set().union(*(_concepts(_words(cell["text"])) for path in headers.values() for cell in path))
    all_directions = set().union(*(_header_directions(_normal(cell["text"])) for path in headers.values() for cell in path))
    if all_directions and not wanted_tasks:
        if LANG_DIRECTION_START.search(query) or LANG_CHINESE_START.search(query):
            return [], {"unrecognized-direction"}
        wanted_tasks = {direction for direction in all_directions if _find(direction.replace("-", ""), plain)}
    if all_directions and not wanted_tasks:
        targets = {code for name, code in LANG_NAMES.items() if len(name) > 1 and _find(name, plain)}
        if targets:
            wanted_tasks = {direction for direction in all_directions if direction[3:] in targets}
            if {direction[3:] for direction in wanted_tasks} != targets:
                return [], targets
    matches = {}
    for col, path in headers.items():
        if col == 1:
            continue
        head = [_normal(cell["text"]) for cell in path]
        directions = set().union(*(_header_directions(text) for text in head))
        if wanted_tasks and not directions & wanted_tasks:
            continue
        labels = [_words(text) for text in head if not _header_directions(text)]
        column_concepts = set().union(*(_concepts(label) for label in labels))
        named = any(_find(label, plain) for label in labels)
        direction_only = not labels and bool(directions & wanted_tasks)
        if direction_only and not concepts:
            metric = re.search(r"\b(?:what (?:is|was)|report|give(?: me)?) (?:the )?([a-z][a-z0-9]*) (?:score )?(?:for|of)\b", plain)
            if metric and metric[1] not in {"score", "value", "result", "number", "reported"}:
                continue
        named = named or direction_only
        if named:
            column_concepts |= caption_concepts - header_concepts
        if concepts and not column_concepts & concepts:
            continue
        # A reused metric can have a different numbered dataset or split.
        datasets = re.findall(r"\b([a-z][a-z0-9]{1,14}) ([0-9]+) (?=(?:f1|bleu|accuracy)\b)", " ".join(labels))
        if any((requested := re.search(r"\b" + dataset + r" ([0-9]+)\b", plain)) and requested[1] != number
               for dataset, number in datasets):
            continue
        if column_concepts & concepts or named:
            matches[col] = (column_concepts, directions)
    concepts_found = set().union(*(value[0] for value in matches.values()))
    tasks_found = set().union(*(value[1] for value in matches.values()))
    if concepts - concepts_found or wanted_tasks - tasks_found:
        return [], wanted_tasks
    return sorted(matches), wanted_tasks


def _contains(cell, row, col):
    return (cell["row"] <= row < cell["row"] + cell.get("row_span", 1)
            and cell["column"] <= col < cell["column"] + cell.get("column_span", 1))


def _cell_refs(cells, headers, rows, selected_rows, selected_cols):
    selections = {}
    for row in selected_rows:
        for col in selected_cols:
            matches = [cell for cell in cells if _contains(cell, row, col)]
            if len(matches) != 1 or matches[0].get("row_span", 1) != 1:
                return []
            cell = matches[0]
            covered = range(cell["column"], cell["column"] + cell.get("column_span", 1))
            levels = list(dict.fromkeys(header["text"] for covered_col in covered for header in headers.get(covered_col, [])))
            label = " · ".join(" ".join(text.split()) for text in levels) or f"第 {col} 列"
            selections[cell["row"], cell["column"]] = (cell, rows[row], label)
    return list(selections.values())


def _numeric(text):
    text = re.sub(r"(?<=10)([⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)", lambda m: "^" + m[1].translate(SUPERSCRIPT), text)
    text = _normal(text).lstrip("= ")
    direct = re.fullmatch(rf"({DECIMAL})\s*({UNIT})?", text)
    scientific = re.fullmatch(rf"({DECIMAL})\s*(?:[·×x*]\s*10\s*\^\s*|e\s*)([+-]?[0-9]{{1,3}})\s*({UNIT})?", text)
    if direct:
        scalar, unit = direct.groups()
        suffix = unit if unit == "%" else unit.upper() if unit in {"k", "m", "b", "t"} else f" {unit}" if unit else ""
        return Decimal(scalar.replace(",", "")), scalar + suffix, unit
    if scientific:
        mantissa, exponent, unit = scientific.groups()
        return Decimal(mantissa.replace(",", "")).scaleb(int(exponent)), f"{mantissa} × 10^{int(exponent)}" + (f" {unit}" if unit else ""), unit
    return None


def _ink_runs(image):
    import numpy as np

    ink = np.asarray(image) < 165
    runs = []
    occupied = ink.any(axis=0)
    for dark, columns in groupby(range(ink.shape[1]), key=lambda x: occupied[x]):
        if dark:
            xs = list(columns)
            ys, _ = np.nonzero(ink[:, xs])
            runs.append((xs[0], int(ys.min()), xs[-1] + 1, int(ys.max()) + 1))
    return runs


def _digit_crop(image, runs, dpi, mode, zoom):
    from PIL import Image, ImageOps
    from scanpdf.ocr import recognize

    left, top, right, bottom = zip(*runs)
    sample = image.crop((min(left), min(top), max(right), max(bottom)))
    if sample.width > 160 or sample.height > 100:
        return ""
    sample = sample.resize((sample.width * zoom, sample.height * zoom), Image.Resampling.BICUBIC)
    words = recognize(ImageOps.expand(sample, border=14, fill=255), dpi, "eng", mode)
    return "".join(word["text"] for word in words) if words and min(word["confidence"] for word in words) >= 75 else ""


def _read_exponent(image, expected, dpi):
    runs = _ink_runs(image)
    if len(runs) < 5:
        return None
    bottom = max(yb for _, _, _, yb in runs)
    height = max(yb - yt for _, yt, _, yb in runs)
    first = len(runs)
    while first > 0 and runs[first - 1][3] < bottom - height * .22:
        first -= 1
    if not 1 <= len(runs) - first <= 5 or first < 4:
        return None
    mark, base, exponent = runs[first - 3], runs[first - 2:first], runs[first:]
    x0, y0, x1, y1 = mark
    # A centered dot is small in both dimensions. Exclude baseline dots and hyphens.
    if (y1 - y0 > height * .36 or x1 - x0 > (y1 - y0) * 1.6
            or not bottom - height * .78 < (y0 + y1) / 2 < bottom - height * .23
            or any(yb - yt < height * .7 for _, yt, _, yb in base)):
        return None
    if _digit_crop(image, runs[:first - 3], dpi, 7, 3) != expected or _digit_crop(image, base, dpi, 7, 3) != "10":
        return None
    first_read = _digit_crop(image, exponent, dpi, 7, 3)
    if re.fullmatch(r"[+-]?[0-9]{1,3}", first_read) and first_read == _digit_crop(image, exponent, dpi, 8, 4):
        return _numeric(f"{expected} · 10^{first_read}")
    return None


def _recover_numeric(store, docid, page, cell):
    # Look again only at a plausible, garbled scientific number. The raster must
    # independently show a mantissa, a centered dot, base ten and a raised suffix.
    from subprocess import TimeoutExpired

    text = _normal(cell["text"])
    match = re.fullmatch(r"([0-9]{1,3}(?:\.[0-9]{1,5})?)\s*[·×x*.\-]\s*10[0-9?!.°'%\s-]{1,8}", text)
    dpi = page.get("render", {}).get("dpi")
    if not match or not dpi or not cell.get("bbox_pt"):
        return None
    import pymupdf
    from PIL import Image

    l, t, r, b = cell["bbox_pt"]
    scale = dpi / 72
    if ceil((r - l) * scale) * ceil((b - t) * scale) > 300_000:
        return None
    # One extra pixel of margin removes grid lines at cell edges.
    margin = 2 / scale
    try:
        with pymupdf.open(stream=store.pdf(docid), filetype="pdf") as pdf:
            pix = pdf[page["physical_page"] - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False,
                                                              clip=pymupdf.Rect(l + margin, t + margin, r - margin, b - margin))
            if not pix.width or not pix.height:
                return None
            image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        return _read_exponent(image, match[1], dpi)
    except (OSError, RuntimeError, ValueError, TimeoutExpired):
        return None


def _overlap(left, right):
    lx, ly, rx, ry = left
    x0, y0, x1, y1 = right
    return max(0, min(ry, y1) - max(ly, y0)) * max(0, min(rx, x1) - max(lx, x0))


def _anchor(doc, page, box=None, line=None):
    if line is None:
        if not box:
            return None, None
        lines = []
        for candidate in page["lines"]:
            boxes = [word["bbox_pt"] for word in candidate.get("words", []) if word.get("bbox_pt")]
            if not boxes and candidate.get("bbox_pt"):
                boxes = [candidate["bbox_pt"]]
            covered = sum(_overlap(word_box, box) for word_box in boxes
                          if _overlap(word_box, box) >= .57 * _overlap(word_box, word_box))
            if covered:
                lines.append((covered, candidate))
        if not lines:
            return None, None
        line = max(lines, key=lambda pair: pair[0])[1]
    source = page.get("source", "ocr")
    block = line.get("block")
    if block is None:
        paragraphs = dict.fromkeys(candidate["line_id"].rsplit(".", 1)[0] for candidate in page["lines"])
        blocks = {key: n for n, key in enumerate(paragraphs, 1)}
        block = blocks[line["line_id"].rsplit(".", 1)[0]]
    chunks = [chunk for chunk in doc["chunks"] if chunk["page"] == page["physical_page"] and chunk["block"] == block
              and chunk.get("source", "native") == source and (_normal(line["text"]) in _normal(chunk["text"])
                  or source == "native" and _words(line["text"]) in _words(chunk["text"]))]
    if not chunks:
        return None, line["line_id"]
    return dict(chunks[0], id=doc["id"], name=doc["name"]), line["line_id"]


def _same_anchor(anchor, hit):
    if not hit:
        return False
    keys = ("page", "block", "part")
    return tuple(hit[key] for key in keys) == tuple(anchor[key] for key in keys)


def _gap(message, hits=(), cells=()):
    return {"answer": None, "gap": message, "hits": list(hits), "table_cells": list(cells)}


def _response(store, doc, question, page, number, region, cells, hits, anchor):
    evidence, values = [], []
    cell_hits = {}
    context_hits = {(hit["page"], hit["block"], hit["part"]): hit for hit in hits}
    source = page.get("source", "ocr")
    for cell, row, column_label in cells:
        hit, line = _anchor(doc, page, box=cell.get("bbox_pt"))
        if anchor is not None and not _same_anchor(anchor, hit):
            return _gap("所选证据段落没有覆盖这些单元格，请回到全文查询。")
        if hit:
            cell_hits[hit["page"], hit["block"], hit["part"]] = hit
        row_hit, _ = _anchor(doc, page, box=row.get("bbox_pt"))
        if row_hit and (anchor is None or _same_anchor(anchor, row_hit)):
            context_hits[row_hit["page"], row_hit["block"], row_hit["part"]] = row_hit
        raw = cell["text"]
        value = _numeric(cell.get("number_text", raw)) if cell.get("status") in {"ocr_text", "native_text"} and cell.get("bbox_pt") else None
        method = "cached_ocr" if source == "ocr" else "native_pdf"
        if source == "ocr" and cell.get("text_method") == "numeric_crop_agreement":
            method = "crop_ocr"
        if source == "ocr" and value is None and cell.get("status") == "ocr_text":
            value = _recover_numeric(store, doc["id"], page, cell)
            method = "crop_ocr" if value else "unresolved"
        concepts = _concepts(_words(column_label))
        if value and concepts & {"bleu", "f1", "accuracy", "precision", "recall"} and not 0 <= value[0] <= 100:
            value = None
        if value is None:
            method = "unresolved"
        units_match = re.search(r"\((flops|%|ms|s|kb|mb|gb|tb)\)", column_label, re.I)
        unit = units_match[1] if units_match else None
        if value:
            scalar, display, written_unit = value
            if unit and not written_unit:
                display += " " + unit
            values.append((scalar, display, written_unit or unit, column_label))
        entry = {"page": page["physical_page"], "region": region["id"], "table": number,
                 "row": cell["row"], "column": cell["column"], "column_span": cell.get("column_span", 1),
                 "row_label": REFERENCE.sub("", " ".join(row["text"].split())), "column_label": column_label,
                 "text": raw, "value": values[-1][1] if value else None, "method": method,
                 "bbox_pt": cell.get("bbox_pt"), "line_id": line, "region_status": region["status"], "source": source}
        for key in ("ocr_raw", "ocr_confidence", "numeric_ocr"):
            if key in cell:
                entry[key] = cell[key]
        if hit:
            entry.update(block=hit["block"], part=hit["part"])
        evidence.append(entry)
    hits = list((cell_hits | context_hits).values())[:8]
    if len(values) != len(evidence):
        return _gap("目标单元格为空、未读出或数值格式无法确认，请核对原页。", hits, evidence)
    prefix = f"表 {number}" if number is not None else "识别表格"
    prefix += f"（物理页 {page['physical_page']}）"
    claims = [f"{item['row_label']} · {item['column_label']} = {item['value']}" for item in evidence]
    answer = prefix + "中，" + "。".join(claims) + "。"
    if DIFFERENCE.search(question):
        if len(values) != 2 or (values[0][2] or "").lower() != (values[1][2] or "").lower() or values[0][3] != values[1][3]:
            return _gap("计算差值需要同一列中两个格式与单位一致的数值。", hits, evidence)
        difference = abs(values[0][0] - values[1][0])
        unit = values[0][2]
        if unit == "%":
            unit = "个百分点"
        if not unit and "bleu" in _concepts(_words(values[0][3])):
            unit = "BLEU"
        answer += f"两项相差 {difference:f}" + (f" {unit}" if unit else "") + "。"
    return {"answer": answer, "gap": None, "hits": hits, "table_cells": evidence}


def _query_shape(question):
    query = _normal(question)
    table_numbers, page_numbers = _scope(TABLE, query), _scope(PAGE, query)
    row_numbers, col_numbers = _scope(ROW, query), _scope(COLUMN, query)
    explicit = bool(table_numbers or (row_numbers and col_numbers)
                    or (page_numbers and (_concepts(_words(query)) or re.search(r"\btable\b|表格|单元格", query))))
    query_without_scope = query
    for token in (TABLE, PAGE, ROW, COLUMN):
        query_without_scope = token.sub(" ", query_without_scope)
    return query_without_scope, table_numbers, page_numbers, row_numbers, col_numbers, explicit


def _table_answers(store, doc, anchor, shape, pages):
    query, table_numbers, page_numbers, row_numbers, col_numbers, explicit = shape
    if any(len(numbers) > 1 for numbers in (table_numbers, page_numbers, row_numbers, col_numbers)):
        return _gap("请指定同一张表或一个行列坐标。") if explicit else None
    plain = _words(query)
    results, inspected = [], []
    ambiguity = False
    for page in pages:
        if not page:
            continue
        for number, caption, region in _tables(page):
            if table_numbers and number not in table_numbers:
                continue
            caption_hit, _ = _anchor(doc, page, line=caption) if caption else (None, None)
            if caption_hit and (anchor is None or _same_anchor(anchor, caption_hit)):
                inspected.append(caption_hit)
            if region["status"] not in {"structure_recognized", "partial_structure"}:
                continue
            depth, headers, labels = _header_grid(region)
            if not depth:
                continue
            cells = region["cells"]
            selected_rows, rows_ambiguous = _row_matches(labels, plain, cells)
            columns, directions = _columns(headers, query, caption)
            if row_numbers:
                if not selected_rows:
                    for row in row_numbers - labels.keys():
                        if row > depth and any(cell["row"] == row for cell in cells):
                            stub = next((cell for cell in cells if cell["column"] == 1 and _contains(cell, row, 1)
                                         and cell["text"]), None)
                            labels[row] = stub or {"text": f"第 {row} 行"}
                selected_rows = sorted(row_numbers & (set(selected_rows) if selected_rows else labels.keys()))
                rows_ambiguous = False
            if col_numbers:
                named_columns = columns if columns or directions or _concepts(plain) else {
                    col for cell in cells for col in range(cell["column"], cell["column"] + cell.get("column_span", 1))}
                columns = sorted(col_numbers & set(named_columns))
            if rows_ambiguous and columns:
                ambiguity = True
            if not selected_rows or not columns:
                continue
            requested_variants = _variants(plain)
            matched_variants = set().union(*(_variants(_words(labels[row]["text"])) for row in selected_rows))
            if requested_variants & matched_variants and requested_variants - matched_variants:
                ambiguity = True
                continue
            operation = plain
            for header in {cell["text"] for col in columns for cell in headers.get(col, [])}:
                operation = operation.replace(_words(header), " ")
            for key in _concepts(plain) & {"path length"}:
                for alias in CONCEPTS[key]:
                    operation = operation.replace(alias, " ")
            if UNSUPPORTED.search(operation):
                ambiguity = True
                continue
            selected = _cell_refs(cells, headers, labels, selected_rows, columns)
            multi_metric = len(_concepts(plain)) > 1
            if not selected or len(selected) > 8 or (len(selected) > len(selected_rows) and not (len(directions) > 1 or multi_metric or MULTIPLE.search(query) or DIFFERENCE.search(query))):
                ambiguity = True
                continue
            if len(selected) < 2 and COMPARE.search(query):
                ambiguity = True
                continue
            results.append((page, number, region, selected, [caption_hit] if caption_hit and (anchor is None or _same_anchor(anchor, caption_hit)) else []))
    if len(results) == 1 and not ambiguity:
        return _response(store, doc, query, *results[0], anchor)
    if not results and not ambiguity and not explicit:
        return None
    if ambiguity or len(results) > 1:
        return _gap("无法确认唯一的表格行列或计算范围。请补充表号、行标签、列名或语言方向。", inspected[:8])
    return _gap("所选范围内未找到可核对的表格行列。请检查表号、行标签和列名。", inspected[:8])


def answer_scan_table(store, doc, question, anchor=None):
    """Look up numeric cells from cached OCR. None lets evidence lookup continue."""
    if anchor is not None and anchor.get("source") != "ocr":
        return None
    shape = _query_shape(question)
    query, table_numbers, page_numbers, _, _, explicit = shape
    if not (explicit or ASK.search(query) or _concepts(_words(query)) or len(_words(query).split()) > 1):
        return None
    info = getattr(store, "page_info", None)
    indexed = {chunk["page"] for chunk in doc["chunks"] if chunk.get("source") == "ocr"}
    scanned = set(doc.get("native_textless", doc.get("textless_pages", []))) | indexed
    if not info or not scanned or (page_numbers and not (page_numbers & scanned)):
        return None
    scope = indexed | (page_numbers & scanned)
    if anchor is not None:
        scope &= {anchor["page"]}
    if page_numbers:
        scope &= page_numbers
    if not scope and explicit:
        return _gap("尚未读取相关扫描页。请先识别可能包含表格的物理页。")
    if table_numbers and not page_numbers:
        caption_pages = _caption_pages(chunk for chunk in doc["chunks"] if chunk.get("source") == "ocr")
        matched = set().union(*(caption_pages.get(number, set()) for number in table_numbers))
        if matched:
            scope &= matched
    return _table_answers(store, doc, anchor, shape, (info(doc["id"], number)["page"] for number in sorted(scope)))


def _caption_pages(chunks):
    result = defaultdict(set)
    for chunk in chunks:
        for line in chunk["text"].splitlines():
            match = CAPTION.match(_normal(line))
            if match:
                result[_number(match[1])].add(chunk["page"])
    return result


def _native_candidates(chunks, shape, anchor):
    query, table_numbers, page_numbers, rows, cols, _ = shape
    if page_numbers or anchor is not None:
        requested = page_numbers or {anchor["page"]}
        return sorted(requested & {chunk["page"] for chunk in chunks})
    caption_pages = _caption_pages(chunks)
    pages_with_captions = set().union(*caption_pages.values())
    if table_numbers:
        return sorted(set().union(*(caption_pages.get(number, set()) for number in table_numbers)))
    if rows and cols and len(pages_with_captions) <= NATIVE_CANDIDATE_LIMIT:
        return sorted(pages_with_captions)
    if not pages_with_captions:
        return []
    words = _words(query)
    terms = {word for word in words.split() if len(word) > 1 and word not in QUERY_TERMS}
    for concept in _concepts(words):
        terms.update(CONCEPTS[concept][0].split())
    for direction in _directions(query):
        terms.update((direction.replace("-", ""), direction.replace("-", " ")))
    if len(terms) < 2:
        return []
    terms_by_page = defaultdict(set)
    for chunk in chunks:
        if chunk["page"] in pages_with_captions:
            excerpt = _words(chunk["text"])
            terms_by_page[chunk["page"]].update(term for term in terms if _find(term, excerpt))
    ordered = sorted(((len(matched), page) for page, matched in terms_by_page.items() if len(matched) >= 2),
                     key=lambda item: (-item[0], item[1]))
    return [page for _, page in ordered[:NATIVE_CANDIDATE_LIMIT]]


def _native_lines(page):
    import pymupdf

    rotation = page.rotation_matrix
    textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_BLOCKS)
    words = defaultdict(list)
    for left, top, right, bottom, text, block_n, line_n, _ in page.get_text("words", textpage=textpage):
        box = pymupdf.Rect(left, top, right, bottom) * rotation
        words[block_n, line_n].append({"text": text, "bbox_pt": list(box)})
    lines = []
    for block in page.get_text("dict", textpage=textpage)["blocks"]:
        if block["type"] != 0:
            continue
        block_n = block["number"]
        for line_n, line in enumerate(block["lines"]):
            spans = [dict(span, bbox_pt=list(pymupdf.Rect(span["bbox"]) * rotation)) for span in line["spans"]]
            lines.append({"line_id": f"{block_n + 1}.{line_n + 1}", "block": block_n + 1,
                          "text": "".join(span["text"] for span in spans),
                          "bbox_pt": list(pymupdf.Rect(line["bbox"]) * rotation),
                          "words": words[block_n, line_n], "spans": spans})
    return lines


def _native_power(raw, box, lines):
    import pymupdf

    if not re.fullmatch(rf"{DECIMAL}\s*[·×x*]\s*10\s*[0-9+\-]{{1,4}}", _normal(raw)):
        return None
    for line in lines:
        if not _overlap(line["bbox_pt"], box):
            continue
        spans = [span for span in line["spans"] if _overlap(span["bbox_pt"], box) >= .6 * _overlap(span["bbox_pt"], span["bbox_pt"])]
        if len(spans) < 2 or not spans[-1]["flags"] & pymupdf.TEXT_FONT_SUPERSCRIPT:
            continue
        prefix = "".join(span["text"] for span in spans[:-1]).strip()
        exponent = spans[-1]["text"].strip()
        combined = f"{prefix}^{exponent}"
        if _words(prefix + exponent) == _words(raw) and _numeric(combined):
            return combined
    return None


def _mupdf_grid(table, region_id):
    if not 2 <= table.col_count <= 30 or table.col_count * table.row_count > 2400:
        return None
    cells = []
    current = 0
    for values, row in zip(table.extract(), table.rows):
        if not any(value and value.strip() for value in values):
            continue
        current += 1
        for col, (text, bbox) in enumerate(zip(values, row.cells), 1):
            if not bbox:
                continue
            text = (text or "").strip()
            cells.append({"row": current, "column": col, "text": text, "bbox_pt": list(bbox),
                          "status": "native_text" if text else "empty_or_unread"})
    if current < 2 or sum(cell["row"] == 1 and bool(cell["text"]) for cell in cells) < 2:
        return None
    return {"id": region_id, "type": "table", "status": "structure_recognized", "bbox_pt": list(table.bbox),
            "cells": cells}


def _native_dividers(page, region):
    import pymupdf

    base = region["cells"]
    # A detector can miss an inter-column gutter and concatenate numbers. Try a
    # clipped native grid only when a recurring two-number cell indicates this.
    merged = [cell for cell in base if cell["row"] > 1 and cell["column"] > 1
              and re.fullmatch(rf"{DECIMAL}\s+{DECIMAL}", _normal(cell["text"]))]
    if page.rotation or len(merged) < 2:
        return region
    clip = pymupdf.Rect(region["bbox_pt"]) + (-2, -2, 2, 2)
    with NATIVE_GRID_LOCK:
        tables = page.find_tables(clip=clip, strategy="text", min_words_vertical=2).tables
        alternative = _mupdf_grid(tables[0], region["id"]) if len(tables) == 1 else None
    if not alternative:
        return region
    candidates = alternative["cells"]
    base_labels = {_words(cell["text"]).replace(" ", "") for cell in base if cell["column"] == 1 and cell["row"] > 1 and cell["text"]}
    new_labels = {_words(cell["text"]).replace(" ", "") for cell in candidates if cell["column"] == 1 and cell["row"] > 1 and cell["text"]}

    def number_count(cells):
        return sum(_numeric(cell["text"]) is not None for cell in cells if cell["column"] > 1 and cell["row"] > 1)
    if (len(base_labels & new_labels) >= max(2, len(base_labels) - 1)
            and number_count(candidates) >= number_count(base) + len(merged)):
        return alternative
    return region


def _native_page(page):
    from metric_qa import DPI, _native_tables, _render

    image = _render(page, DPI)
    if image is None:
        return None
    regions = _native_tables(page, image)
    if not regions and not page.rotation:
        with NATIVE_GRID_LOCK:
            for table in page.find_tables(strategy="lines").tables:
                if region := _mupdf_grid(table, f"p{page.number + 1}-r{len(regions) + 1}"):
                    regions.append(region)
    if not regions:
        return None
    lines = _native_lines(page)
    for index, region in enumerate(regions, 1):
        if "id" not in region:
            region.update(id=f"p{page.number + 1}-r{index}", bbox_pt=[float(v) * 72 / DPI for v in region["bbox"]])
            for cell in region["cells"]:
                cell["bbox_pt"] = [float(v) * 72 / DPI for v in cell["bbox"]]
                if cell["text"]:
                    cell["status"] = "native_text"
                    if formatted := _native_power(cell["text"], cell["bbox_pt"], lines):
                        cell["number_text"] = formatted
            regions[index - 1] = _native_dividers(page, region)
    return {"physical_page": page.number + 1, "source": "native", "lines": lines, "regions": regions}


def native_cell_box(page, region, row, column):
    """Resolve a native source link against the same original-page table geometry."""
    physical_page = page.number + 1
    if (not isinstance(region, str) or not re.fullmatch(rf"p{physical_page}-r[1-9][0-9]{{0,2}}", region)
            or any(type(value) is not int or value < 1 for value in (row, column))):
        return None
    native = _native_page(page)
    if native is None:
        return None
    for number, _, table in _tables(native):
        if table["id"] != region or table["status"] not in {"structure_recognized", "partial_structure"}:
            continue
        matches = [cell for cell in table["cells"] if cell["row"] == row and cell["column"] == column]
        if len(matches) != 1 or not matches[0].get("bbox_pt"):
            return None
        cell = matches[0]
        return {"page": physical_page, "region": region, "table": number, "row": row, "column": column,
                "row_span": cell.get("row_span", 1), "column_span": cell.get("column_span", 1),
                "bbox_pt": cell["bbox_pt"], "text": cell["text"], "source": "native"}
    return None


def answer_native_table(store, doc, question, anchor=None):
    """Look up numeric cells in a bounded set of matching native-text pages."""
    if anchor is not None and anchor.get("source", "native") != "native":
        return None
    shape = _query_shape(question)
    _, table_numbers, page_numbers, _, _, explicit = shape
    if anchor is not None and not explicit:
        table_intent = re.search(r"\b(?:table|row|column|cell)\b|表格|单元格|(?:这|该|此|本)表", _normal(question))
        if not table_intent and not _caption_pages((anchor,)):
            return None
    native = [chunk for chunk in doc["chunks"] if chunk.get("source", "native") == "native"]
    if not native:
        return None
    if page_numbers and not any(chunk["page"] in page_numbers for chunk in native):
        return None
    pages = _native_candidates(native, shape, anchor)
    if not pages:
        if table_numbers and explicit:
            return _gap("文字层中未定位到该表题，请补充表格所在的物理页。")
        return None
    if len(pages) > NATIVE_CANDIDATE_LIMIT:
        return _gap("找到多处可能的表格，请指定物理页码。")
    if anchor is not None:
        pages = [number for number in pages if number == anchor["page"]]
    if not pages:
        return _gap("所选证据段落与表格物理页不一致。") if explicit else None
    data = store.pdf(doc["id"])
    if not isinstance(data, bytes) or not data:
        return _gap("当前范围内无法读取原始 PDF 表格。") if explicit else None
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        return _table_answers(store, doc, anchor, shape,
                              (_native_page(pdf[number - 1]) for number in pages if 1 <= number <= len(pdf)))
