"""Small, source-anchored answers for bylines, training hardware and references."""
import re
import unicodedata

from byline import author_number
from evidence_context import sentences
from graph import _normalize_title


_DEVICE_WORD = r"(?:GPUs?|TPUs?|NPUs?|CPUs?|accelerators?|hardware|graphics cards?|comput(?:e|ing) (?:devices?|equipment|setup)|cards?)"
_DEVICE = re.compile(rf"(?<![a-z0-9]){_DEVICE_WORD}(?![a-z0-9])|显卡|加速器|硬件|计算设备|哪种卡|几[块张]", re.I)
_DEVICE_FIELD = re.compile(
    rf"\b(?:what|which)\s+(?:(?:was|were|is|are)\s+(?:(?:the|their|its)\s+)?)?"
    rf"(?:(?:kind|type|model|count|number)(?:\s+and\s+(?:type|model|count|number))?\s+of\s+)?"
    rf"(?:(?:training|physical|computational)\s+)*{_DEVICE_WORD}\b|"
    rf"\b(?:how many|number of)\s+(?:[A-Z0-9][A-Z0-9_-]*\s+){{0,3}}{_DEVICE_WORD}\b|"
    rf"\b{_DEVICE_WORD}\s+(?:type|model(?=\s+(?:did|was|were|used|for)|[?.;,]|$)|count)\b|"
    r"(?:什么|哪种|哪款|哪些|多少[个块张台]?|几[块张个台])\s*(?:(?:训练|型号的|类型的)\s*)?"
    r"(?:GPUs?|TPUs?|NPUs?|CPUs?|显卡|加速器|硬件|计算设备|卡)|"
    r"(?:GPU|TPU|NPU|CPU|显卡|加速器|硬件|计算设备)[^。！？；]{0,8}(?:型号|数量|几[块张个台]|多少[块张个台])",
    re.I,
)
_HARDWARE_FOLLOWON = re.compile(
    r"(?:how many(?: (?:were used|did they use|did the authors use|were there))?|"
    r"what (?:was|were|is|are) (?:the |their |its )?(?:type|types|model|models|kind|count|number)|"
    r"(?:具体)?(?:它们的?|其)?(?:又)?(?:是)?什么(?:型号|类型)|(?:具体)?(?:型号|类型|数量)是(?:什么|多少)|"
    r"(?:总共|一共|配了|有|用了)?(?:几[块张个台]|多少[块张个台]))", re.I,
)
_FOLLOWON_BREAK = re.compile(r"(?<=[?？;；])\s*(?=(?:还|又|另外|此外)[^?？;；]{0,24}(?:什么|哪[种个些]|多少|如何))")
_TRAIN = re.compile(r"\b(?:train\w*|pre[- ]?train\w*|fine[- ]?tun\w*|fit(?:ting|ted)?)\b|训练|微调|拟合", re.I)
_OTHER_TASK = re.compile(r"\b(?:infer(?:ence|ring)|decoding|deployment|evaluat(?:ion|e[sd]?|ing)|benchmarking|throughput|"
                         r"(?:GPU|TPU)[ -]?hours?|GPU memory|VRAM|(?:how long|wall.clock|time taken)|batch size|"
                         r"optimizer|learning rate|serial number)\b|推理|部署|解码|评测|显存|吞吐|耗时|优化器|学习率|批量大小|序列号", re.I)
_COMPARE = re.compile(r"\b(?:compare|compared (?:with|to)|versus|vs\.?|difference)\b|相差|差值|作对比|对比之下", re.I)
_CO_ASK = re.compile(r"\b(?:and|as well as)\s+(?:(?:what|which|the)\s+)?(?:datasets?|corpora|corpus|training data|authors?)\b"
                     r"|(?:以及|或者|和|及)(?:什么|哪个|哪些|所用(?:的)?)?(?:训练数据|数据集|语料|作者)", re.I)
_OTHER_WORK = re.compile(r"\b(?:et al|previous work|prior work|prior studies|earlier studies|other authors|"
                         r"published baselines?|reference implementation|comparison baselines?)\b|对比论文|先前研究|其他作者|已发表的基线", re.I)
_VARIANTS = {name: rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])" for name, pattern in {
    "base": r"base|基础(?:版(?:本)?|型|模型)|标准版", "big": r"big|大版|大型版",
    "large": r"large|大号模型", "small": r"small|小型(?:版|模型)",
    "tiny": r"tiny|微型(?:版|模型)", "medium": r"medium|中型(?:版|模型)",
    "baseline": r"baseline|基线",
}.items()}
_STAGES = {"pretraining": r"\bpre[- ]?train\w*\b|预训练", "finetuning": r"\bfine[- ]?tun\w*\b|微调",
           "from_scratch": r"\bfrom scratch\b|从零训练|从头训练"}
_TRAINING_REPORT = re.compile(
    r"\b(?:we (?:initially |also )?(?:pre[- ]?)?train(?:ed)?|we fine[- ]?tun(?:e|ed)|we fit(?:ted)?|"
    r"(?:was|were|is|are) (?:pre[- ]?)?trained|(?:was|were|is|are) (?:fine[- ]?tuned|fitted)|"
    r"(?:pre[- ]?training|fine[- ]?tuning|training) (?:used|uses|ran|was (?:run|performed|conducted))|"
    r"(?:for|during) (?:model )?(?:pre[- ]?training|fine[- ]?tuning|training)[^;.!?]{0,60}\bwe (?:used|use))\b",
    re.I,
)
_LINKED_VARIANT = re.compile(r"^For (?:our|the) (base|big|large|small|tiny|medium|baseline) (?:models?|variants?)\b", re.I)
_SHARED_REPORT = re.compile(r"\b(?:we (?:pre[- ]?)?train(?:ed)? our models|our models (?:were|are) trained)\b", re.I)
_LINKED_DURATION = re.compile(r"^\s*,?\s*(?:this|it)\s+(?:took|used|ran)\b", re.I)
_UNCERTAIN_REPORT = re.compile(r"\b(?:not|never|rather than|could|would|should|might|instead of|up to|at least|"
                               r"at most|between|each (?:machine|node|server|host)|per[- ](?:node|machine|host|server))\b", re.I)
_CONNECTOR = re.compile(r"\b(?:on|using|with|across|utiliz\w*|used|uses)\b", re.I)
_NUMBERS = {name: n for n, name in enumerate(("one two three four five six seven eight nine ten eleven twelve "
                                              "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty").split(), 1)}
_COUNT = r"(?:[1-9]\d{0,5}(?:,\d{3})*|" + "|".join(_NUMBERS) + r")"
_SKU = r"(?:[A-Z]{1,8}[- ]?\d{2,5}[A-Z0-9.+/-]*|(?:RTX|GTX|Xeon|EPYC|TPU\s+v)\s*\d{1,5}[A-Z0-9.+/-]*)"
_MODEL = rf"(?:(?-i:[A-Z])[A-Za-z0-9.+/-]*\s+){{0,3}}{_SKU}(?:\s+(?:Ti|SXM\d*|NVL|\d+\s*[GM]i?B))?"
_UNIT = r"(?P<device>GPUs?|TPUs?|NPUs?|CPUs?|accelerators?)\b"
_COUNTED = re.compile(rf"(?<![\w.,])(?P<count>{_COUNT})\s+(?:[×x]\s*)?(?:(?P<model>{_MODEL})\s+)?{_UNIT}", re.I)
_NAMED = re.compile(rf"\b(?P<model>{_MODEL})\s+{_UNIT}", re.I)
_RANGE = re.compile(rf"(?<!\w){_COUNT}\s*(?:[-–—~×*]|\bto\b)\s*(?={_COUNT}\b)", re.I)
_ARXIV = re.compile(r"(?:\barxiv\s*[:/]\s*|(?<![a-z0-9_.])(?=(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})v))"
                    r"(\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v([1-9]\d{0,5}))?(?![a-z0-9_.])", re.I)
_VERSION = re.compile(r"(?<![a-z0-9_.-])(?:v([1-9]\d?)(?![a-z0-9_.])|"
                      r"(?i:(?:version|revision)\s+v?)\s*([1-9]\d{0,5})(?![a-z0-9_.])|"
                      r"(?i:v)?([1-9]\d{0,5})\s*(?:版本|版(?!型)))")
_TITLE = re.compile(r"《([^》]+)》|[“\"]([^”\"]+)[”\"]")
_CONDITION = re.compile(r"\b(?i:FP16|BF16|FP32|INT8|mixed.precision|single.node|multi.node)\b|混合精度|单节点|多节点|"
                        r"(?<![a-zA-Z0-9])([A-Z][A-Za-z0-9_.-]*(?:[ -][A-Z0-9][A-Za-z0-9_.-]*){0,2})\s*"
                        r"(?:dataset|benchmark|split|condition|setting|configuration|数据集|基准|条件|配置)")


def byline_answer(doc, number, hits):
    if not number or len(hits) != 1:
        return None
    hit = hits[0]
    lines = hit['text'].strip().splitlines()
    if not 2 <= len(lines) <= 4 or '@' not in lines[-1]:
        return None
    name = lines[0].rstrip('∗*†‡0123456789¹²³⁴⁵⁶⁷⁸⁹ ')
    affiliation = '、'.join(lines[1:-1])
    answer = f'第 {number} 位署名作者为 {name}。'
    if affiliation:
        answer += f'署名机构为 {affiliation}。'
    else:
        answer += '该署名块未列出机构。'
    start = hit["text"].find(lines[0])
    end = hit["text"].find(lines[-1], start)
    evidence = [dict(hit, start=start, end=end) if 0 <= start < end else hit]
    for mark in ('†', '‡'):
        if mark not in lines[0][len(name):]:
            continue
        pattern = re.compile(r'^' + mark + r'\s*Work (?:performed|done) while at ([^\n.]{2,100})\.[ \t]*$', re.I | re.M)
        notes = [(chunk, pattern.search(chunk['text'])) for chunk in doc['chunks'] if chunk['page'] == hit['page']]
        matches = [(chunk,m) for chunk,m in notes if m]
        if len(matches) == 1:
            chunk, note = matches[0]
            org = note[1]
            answer += f'作者的 {mark} 脚注说明，这项工作是在 {org} 完成的。'
            evidence.append(dict(chunk, id=doc['id'], name=doc['name'], start=note.start(), end=note.end()))
    return {'answer':answer, 'hits':evidence}


def _fold(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _matches(text, patterns):
    return {name for name, pattern in patterns.items() if re.search(pattern, text, re.I)}


def _hardware_question(question):
    return (_DEVICE_FIELD.search(question) and _TRAIN.search(question)
            and not (_OTHER_TASK.search(question) or _COMPARE.search(question) or _CO_ASK.search(question)))


def hardware_parts(parts):
    """Retain a joined answer only for independently recognized device fields."""
    for part in parts:
        part = _fold(part).strip(" ?？.。")
        if (_OTHER_TASK.search(part) or _COMPARE.search(part) or _CO_ASK.search(part)
                or not (_DEVICE_FIELD.search(part) or _HARDWARE_FOLLOWON.fullmatch(part))):
            return False
    return True


def hardware_clauses(parts):
    """Keep a clearly introduced Chinese follow-up as a separate question."""
    if len(parts) == 1:
        clauses = [part.strip(" ?？;；") for part in _FOLLOWON_BREAK.split(parts[0])]
        if 2 <= len(clauses) <= 3 and _hardware_question(_fold(clauses[0])):
            return clauses
    return parts


def _scope_gap(doc, question):
    identity = doc.get("arxiv") or {}
    versions = {int(next(value for value in row if value)) for row in _VERSION.findall(question)}
    for paper_id, version in _ARXIV.findall(question):
        if identity.get("id") != paper_id.casefold() or (version and identity.get("version") != int(version)):
            return "所选原件的 arXiv 标识或版本与问题不匹配。"
    if versions and versions != {identity.get("version")}:
        return "无法从所选 PDF 原件确认问题指定的版本。请先选择对应原件。"
    titles = [first or second for first, second in _TITLE.findall(question)]
    titles = [_normalize_title(re.sub(r"\s+[vV]\d+$", "", title)) for title in titles]
    titles = {title for title in titles if len(title.split()) >= 3 or len(re.findall(r"[㐀-鿿]", title)) >= 6}
    if titles and titles != {_normalize_title((doc.get("bibliographic_identity") or {}).get("title") or "")}:
        return "所选 PDF 原件与问题指定的题名不匹配。"
    return None


def _device(candidate):
    count = candidate.groupdict().get("count")
    if count:
        count = _NUMBERS.get(count.casefold(), count.replace(",", ""))
    return (int(count) if count else None, candidate["model"], candidate["device"].upper().rstrip("S"))


def _hardware_readings(question, hits):
    variants = _matches(question, _VARIANTS)
    stages = _matches(question, _STAGES)
    conditions = {(_fold(match[1] or match[0]).casefold()) for match in _CONDITION.finditer(question)}
    requested_device = re.search(r"(?<![a-z0-9])(GPU|TPU|NPU|CPU)s?(?![a-z0-9])", question, re.I)
    requested_model = _NAMED.search(question)
    for hit in hits:
        raw = hit["text"]
        if not _DEVICE.search(raw) or hit.get("arxiv_stamp") or re.match(r"^\s*\[\d+\]", raw):
            continue
        spans = list(sentences(raw))
        for index, (start, end) in enumerate(spans):
            text = _fold(raw[start:end])
            report = _TRAINING_REPORT.search(text)
            if (not report or _OTHER_WORK.search(text) or _OTHER_TASK.search(text)
                    or _UNCERTAIN_REPORT.search(text) or _RANGE.search(text)):
                continue
            equipment = [(match, _device(match)) for match in (_COUNTED.finditer(text))]
            if not equipment:
                equipment = [(match, _device(match)) for match in _NAMED.finditer(text)]
            if len(equipment) != 1:
                continue
            match, (count, model, kind) = equipment[0]
            if (match.start() < report.end() or not _CONNECTOR.search(text[report.start():match.start()])
                    or requested_device and kind != requested_device[1].upper()
                    or requested_model and (not model or _fold(requested_model["model"]).casefold() not in model.casefold())):
                continue
            evidence_variants = _matches(text[:match.start()], _VARIANTS)
            if variants and not evidence_variants and _SHARED_REPORT.search(text) and index + 1 < len(spans):
                following = _fold(raw[slice(*spans[index + 1])])
                linked = _LINKED_VARIANT.match(following)
                if (linked and (_TRAIN.search(following) or _LINKED_DURATION.match(following[linked.end():]))
                        and not _UNCERTAIN_REPORT.search(following)):
                    evidence_variants = {linked[1].casefold()}
                    if variants == evidence_variants:
                        end = spans[index + 1][1]
                        text += " " + following
            if (variants and evidence_variants != variants or len(variants) > 1
                    or stages and _matches(text, _STAGES) != stages
                    or any(not re.search(r"(?<![\w-])" + re.escape(condition) + r"(?![\w-])", text.casefold())
                           for condition in conditions)):
                continue
            yield count, model, kind, dict(hit, start=start, end=end)


def _hardware_result(question, hits):
    readings = {}
    for count, model, kind, hit in _hardware_readings(question, hits):
        readings.setdefault((count, model.casefold() if model else None, kind), (model, hit))
    if len(readings) != 1:
        return None, [hit for _, hit in readings.values()][:4]
    (count, _, kind), (model, hit) = next(iter(readings.items()))
    if re.search(r"[㐀-鿿]", question):
        detail = " ".join(value for value in (f"{count} 块" if count is not None else "", model, kind) if value)
        answer = f"文中报告的训练硬件为 {detail}。"
        missing = ("数量" if count is None else "型号" if model is None else "")
        if missing:
            answer += f"该原句未报告{missing}。"
    else:
        device = (kind.lower() if kind == "ACCELERATOR" else kind) + ("s" if count != 1 else "")
        detail = " ".join(str(value) for value in (count, model, device) if value is not None)
        answer = f"The paper reports {detail} for training."
        if count is None or model is None:
            answer += f" The passage does not specify the {'device count' if count is None else 'model'}."
    return {"answer": answer, "hits": [hit]}, []


def training_hardware(question, hits):
    question = _fold(question)
    if _hardware_question(question) and not _OTHER_WORK.search(question):
        return _hardware_result(question, hits)[0]
    return None


def reference_answer(question, hits):
    title = re.search(r'[“"《]([^”"》]{3,150})[”"》]',question)
    if not title or not re.search(r'参考文献|references?',question,re.I):
        return None
    result = []
    for hit in hits:
        text = ' '.join(hit['text'].split())
        match = re.match(r'^\[\d+\]\s+(.{3,310}?)\.\s+' + re.escape(title[1]) + r'\.\s+(.+)$',text,re.I)
        if match:
            years = set(re.findall(r'\b(?:19|20)\d{2}\b', match[2]))
            if len(years) == 1:
                result.append((match[1], next(iter(years)), hit))
    if len(result) == 1:
        authors,year,hit = result[0]
        return {'answer':f'《{title[1]}》的作者为 {authors}，年份为 {year}。', 'hits':[hit]}
    return None


def extract_facts(doc, query, hits):
    question = _fold(query)
    if _hardware_question(question):
        gap = _scope_gap(doc, question)
        if not gap and _OTHER_WORK.search(question):
            gap = "该问题指定了对比研究。请选择对比研究的原件，以核对其训练硬件。"
        if gap:
            return {"answer": None, "hits": [], "gap": gap, "training_hardware": True}
        evidence = (dict(chunk, id=doc["id"], name=doc["name"]) for chunk in doc.get("chunks", ()))
        answer, conflicts = _hardware_result(question, evidence)
        if answer:
            return dict(answer, training_hardware=True)
        gap = ("原件在不同条件下报告了不同的训练硬件。请指定模型或训练阶段。" if conflicts
               else "所选原件范围内未找到明确对应所问条件的训练硬件原句。")
        return {"answer": None, "hits": conflicts, "gap": gap, "training_hardware": True}
    return byline_answer(doc,author_number(query),hits) or reference_answer(query,hits)
