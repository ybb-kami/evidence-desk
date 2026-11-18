"""Compare independent, source-bound readings of explicitly selected PDFs."""

from decimal import Decimal
import re
import unicodedata

from app import Bad, ID
from engine import find_evidence


MAX_PAPERS = 6
GPT_TITLES = {
    "improving language understanding by generative pre-training": 1,
    "language models are unsupervised multitask learners": 2,
    "language models are few-shot learners": 3,
}
GPT_MENTION = re.compile(r"(?<![a-z0-9_])GPT\s*[-–]?\s*([123])(?![a-z0-9_])", re.I)
VERSION = re.compile(r"(?<![a-z0-9_])v([1-9][0-9]{0,5})(?![a-z0-9_]|\.[0-9])", re.I)
ARXIV_VERSION = re.compile(r"(?<![a-z0-9_])([0-9]{4}\.[0-9]{4,5})v([1-9][0-9]{0,5})(?![a-z0-9_]|\.[0-9])", re.I)
INHERITANCE = re.compile(r"承袭|继承|沿用|基于|源自|继承自|丢弃|舍弃|省去|\binherit|\bdescend|\bderived from|"
                         r"\bbased on|\bdescendant|\bomitted|\bdiscard", re.I)
DIFFERENCE = re.compile(r"差值|相差|差多少|高出多少|提高了多少|\bdifference\b|how much (?:higher|lower)", re.I)
PROTOCOL = re.compile(r"\b(?:on|for|using|under)\s+(?:(?:the|official)\s+){0,2}"
                      r"([\w.-]+(?:\s+[\w.-]+){0,3})\s+(?:dataset|benchmark|test set|test split|"
                      r"validation set|validation split|development set|evaluation protocol)\b|"
                      r"([A-Za-z0-9][\w.-]{1,40})\s*(?:数据集|测试集|验证集|评测协议)", re.I)
SCALAR = re.compile(r"([+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)\s*([%\w /.-]*)")


def normalized(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", text, flags=re.I)
    return " ".join(text.split())


# An architecture identity statement can justify a role. A mere reference
# to an encoder elsewhere in the introduction cannot establish inheritance.
DERIVED = re.compile(r"\b([A-Za-z][\w-]{1,39})[’']s (?:model )?architecture is (?:a|an) "
                     r"(?:multi-?layer(?:ed)? )?bidirectional Transformer encoder "
                     r"based on (?:the )?original implementation", re.I)


def evidence(doc, chunk, label):
    return dict(chunk, id=doc["id"], name=doc["name"], evidence_label=label)


def architecture_roles(doc):
    hits, model = {}, None
    for chunk in doc["chunks"]:
        text = normalized(chunk["text"])
        if match := DERIVED.search(text):
            hits["derived"] = evidence(doc, chunk, "沿用的模型结构")
            model = match[1]
        if (re.match(r"Encoder\s*:", text, re.I) and re.search(r"stack of .*layers", text, re.I)
                and re.search(r"self[- ]attention", text, re.I)
                and re.search(r"feed[- ]forward", text, re.I)):
            hits["encoder"] = evidence(doc, chunk, "原架构的编码器")
        if (re.match(r"Decoder\s*:", text, re.I) and re.search(r"stack of .*layers", text, re.I)
                and re.search(r"attention over the output of the encoder", text, re.I)):
            hits["decoder"] = evidence(doc, chunk, "原架构的解码器")
    return hits, model


TOPICS = [
    (r"层归一化|Layer\s*Norm|归一化|\bLN\b", "Layer Normalization 位置",
     r"(?:layer normali[sz]ation|layernorm|pre[- ]normalization|post[- ]normalization).{0,120}"
     r"(?:beginning|end of|input|before|after|sub-block|sub-layer)|"
     r"(?:followed by|prior to|before|after).{0,75}(?:layer normali[sz]ation|layernorm)|"
     r"including the modified initialization, pre-normalization"),
    (r"上下文|context\s*(?:window|length)", "上下文或训练序列长度",
     r"(?:context (?:window|size|length)|sequences? of|spans? of|nctx\s*=).{0,60}?\d[\d,]{2,}"
     r"|\d[\d,]{2,}[ -]tokens?.{0,30}(?:context|sequences?)"),
    (r"参数|parameters?", "参数规模", r"\b\d[\d,.]*\s*(?:billion|million|trillion)\s+parameters?\b"
     r"|\b\d[\d,.]*[BMT]\s+parameters?\b"),
]


def metric_observation(label, text):
    if label == "上下文或训练序列长度":
        window = re.search(r"\b(?:context window of (?:nctx\s*=\s*)?|context window, which is nctx\s*=\s*)([\d,]{3,})\b", text, re.I)
        if window:
            return f"文中给出的上下文窗口为 {window[1]} tokens。"
        training = re.search(r"(?:we train|we trained) [^.]{0,130}sequences of ([\d,]{3,}) tokens", text, re.I)
        if training:
            return f"训练采样的连续序列为 {training[1]} tokens；此处没有明确最大上下文上限。"
    if label == "参数规模":
        model = re.search(r"\bwe train ([A-Za-z][A-Za-z0-9-]+), [^.,;]{1,100} with "
                          r"(\d+(?:\.\d+)?) (billion|million|trillion) parameters", text, re.I)
        if model:
            return f"{model[1]}：{model[2]} {model[3]} parameters。"
    return None


def _title(doc):
    return normalized((doc.get("bibliographic_identity") or {}).get("title") or "").casefold()


def _scope_gap(docs, question):
    versions = {(arxiv.get("id"), arxiv.get("version")) for doc in docs if (arxiv := doc.get("arxiv"))}
    for arxiv_id, version in ARXIV_VERSION.findall(question):
        if (arxiv_id, int(version)) not in versions:
            return f"所选文献缺少 arXiv:{arxiv_id}v{version}，请核对版本。"
    for match in re.finditer(r"《([^》]+)》(\s*v[1-9][0-9]{0,5}(?![a-z0-9_]|\.[0-9]))?", question, re.I):
        title = normalized(match[1]).casefold()
        internal = re.search(r"\s+v([1-9][0-9]{0,5})$", title, re.I)
        external = VERSION.search(match[2] or "")
        if internal and external and internal[1] != external[1]:
            return "同一题名标注了两个不同版本，请核对问题。"
        version = int((internal or external)[1]) if internal or external else None
        if internal:
            title = title[:internal.start()]
        matches = [doc for doc in docs if _title(doc) == title]
        if not matches:
            return "所选文献与问题中的题名不匹配，请核对选择。"
        if version and not any((doc.get("arxiv") or {}).get("version") == version for doc in matches):
            return f"所选文献缺少该题名的 v{version}，请核对版本。"
    standalone = {int(version) for version in VERSION.findall(question)}
    if (len(standalone) > 1 or re.search(r"版本|arXiv|\bversion|\brevision", question, re.I)) and (
            standalone - {version for _, version in versions}):
        return "所选文献缺少问题指定的 arXiv 版本，请核对选择。"
    return None


def _missing_gpt(docs, question):
    requested = {int(generation) for generation in GPT_MENTION.findall(question)}
    if {1, 3} <= requested and re.search(r"到|至|through|\bto\b", question, re.I):
        requested.add(2)
    if len(requested) < 2:
        return []
    selected = {GPT_TITLES.get(_title(doc)) for doc in docs}
    return sorted(requested - selected)


def _local_question(question):
    # An independent paper lookup should not ask its reader to compare documents.
    local = re.sub(r"《[^》]+》(?:\s*v[1-9][0-9]{0,5})?", " ", question, flags=re.I)
    local = re.sub(r"^(?:请\s*)?(?:(?:对比|比较|根据|结合)\s*)?(?:这?[两三四五六几多]篇|这些|所选|各篇|上述)"
                   r"(?:论文|文章|文献|研究)?(?:中|里)?[，、,:：\s]*", "", local)
    local = re.sub(r"^(?:请\s*)?(?:比较|对比|根据|结合)[\s与和及、，,:：]+", "", local)
    local = re.sub(r"^(?:please\s+)?(?:compare|contrast)[,:\s]+", "", local, flags=re.I)
    local = re.sub(r"各自|各篇|分别|\b(?:in|for|of|from) each (?:selected )?(?:paper|study|document)\b|"
                   r"\brespectively\b", "", local, flags=re.I)
    local = re.sub(r"\beach (?:selected )?(paper|study|document)\b", r"the \1", local, flags=re.I)
    local = re.split(r"[？?。；;，,]\s*(?=(?:两篇之间|二者|两个数值)?\s*(?:相差|差值|差多少|差了|提高了多少|"
                     r"what is the difference|how much (?:higher|lower)))", local, maxsplit=1, flags=re.I)[0]
    return local.strip() or question.strip()


def _valid_hits(doc, hits):
    originals = {(row["page"], row["block"], row.get("part", 1)): row for row in doc["chunks"]}
    checked, seen = [], set()
    for hit in hits:
        key = hit["page"], hit["block"], hit.get("part", 1)
        if hit.get("id") != doc["id"] or key not in originals or key in seen:
            continue
        text = normalized(hit["text"])
        if text and text in normalized(originals[key]["text"]):
            checked.append(dict(hit, text=originals[key]["text"], name=doc["name"]))
            seen.add(key)
    return checked


def _read_paper(store, doc, question, *, use_models):
    from followup import lookup

    request = {"id": doc["id"], "question": question, "anchor": None}
    try:
        result = lookup(store, request, dense=getattr(store, "dense", None) if use_models else None)
    except Bad as error:
        if error.code != 404:
            raise
        return {"hits": [], "answer": None, "gap": "该版本已移出活动文库。"}
    hits = _valid_hits(doc, result["hits"])
    cells = [cell for cell in result.get("table_cells", ()) if cell.get("region") and cell.get("bbox_pt")
             and cell.get("source") in {"native", "ocr"}
             and ("id" not in cell or cell["id"] == doc["id"])]
    answer = result.get("answer") if hits or cells else None
    method = result.get("answer_method") if answer else None
    passage_reader = getattr(store, "passage_reader", None) if use_models else None
    if passage_reader and hits and not answer and not result.get("table_cells"):
        from passage_answer import assess_spans

        spans, _, english = passage_reader.candidates(question, hits)
        if spans:
            _, direct = assess_spans(question, english, spans, hits)
            if direct:
                cited = hits[direct["hit_index"]]
                cited.update(start=direct["answer_start"], end=direct["answer_end"])
                hits = [cited, *(hit for hit in hits if hit is not cited)]
                answer, method = direct["answer"].strip(), "local_extractive"
    gap = None if answer else result.get("gap") or "原文证据不足，无法确认本篇的结论。"
    return {"hits": hits[:5], "answer": answer, "gap": gap, "answer_method": method, "table_cells": cells,
            "retrieval": result.get("retrieval")}


def _measured_value(paper):
    cells = paper.get("table_cells", ())
    if not paper["answer"] or len(cells) != 1:
        return None
    cell = cells[0]
    number = SCALAR.fullmatch(cell.get("value") or "")
    column = normalized(cell.get("column_label") or "").casefold()
    row = normalized(cell.get("row_label") or "").casefold()
    if (not number or not column or not row or cell.get("method") not in {"native_pdf", "cached_ocr"}
            or cell.get("table") is None):
        return None
    captions = set()
    pattern = re.compile(r"^\s*(?:table|表)\s*" + re.escape(str(cell["table"])) + r"\s*[:：.、]\s*(.*)$", re.I)
    for hit in paper["hits"]:
        if hit["page"] != cell["page"]:
            continue
        caption = pattern.match(normalized(hit["text"]))
        if caption and (protocol := PROTOCOL.search(caption[1])):
            name = next(value for value in protocol.groups() if value)
            if any(char.isupper() or char.isdigit() for char in name):
                captions.add(caption[1].casefold())
    if len(captions) != 1:
        return None
    unit = number[2].strip().casefold()
    if not unit and not re.search(r"\b(?:BLEU|F1|ROUGE|chrF)\b", column, re.I):
        return None
    return (captions.pop(), row, column, unit), Decimal(number[1].replace(",", "")), cell["value"]


def _difference(papers):
    if len(papers) != 2:
        return None
    first, second = map(_measured_value, papers)
    if not first or not second or first[0] != second[0]:
        return None
    delta = abs(second[1] - first[1])
    _, _, column, unit = first[0]
    suffix = "个百分点" if unit == "%" else " " + (unit or column)
    return f"两篇报告在同一表述的评测条件下分别给出 {first[2]} 和 {second[2]}，原表读数相差 {delta:f}{suffix}。"


def compare(docs, question, *, store=None):
    papers = [dict(id=d["id"], name=d["name"], arxiv=d.get("arxiv"), hits=[], answer=None, gap=None) for d in docs]
    if gap := _scope_gap(docs, question):
        return dict(papers=papers, answer=None, gap=gap)
    if (re.fullmatch(r"\s*(?:encoder|decoder|编码器|解码器)\s*[?？]?\s*", question, re.I)
            or (INHERITANCE.search(question) and re.search(r"encoder|decoder|编码器|解码器|承袭", question, re.I))):
        roles = [architecture_roles(d) for d in docs]
        for paper, (hits, _) in zip(papers, roles):
            paper["hits"] = list(hits.values())
        sources = [i for i, (hits, _) in enumerate(roles) if {"encoder", "decoder"} <= hits.keys()]
        successors = [i for i, (hits, _) in enumerate(roles) if "derived" in hits]
        if len(sources) == len(successors) == 1 and sources[0] != successors[0]:
            original, derived = sources[0], successors[0]
            # Resolve the author/year citation to a bibliography entry bearing
            # the selected source title, including when the source is a revision.
            title = normalized((docs[original].get("bibliographic_identity") or {}).get("title") or "").casefold()
            cited = normalized(roles[derived][0]["derived"]["text"])
            citations = re.findall(r"([A-Z][a-zA-Z'’-]+) et al\.\s*\((\d{4})\)", cited)
            reference = next((chunk for chunk in docs[derived]["chunks"]
                              if title and title in normalized(chunk["text"]).casefold()
                              and any(re.search(r"\b"+re.escape(name)+r"\b", chunk["text"])
                                      and year in chunk["text"] for name, year in citations)), None)
            if reference:
                papers[derived]["hits"].append(evidence(docs[derived], reference, "原架构的参考文献"))
                model = roles[derived][1]
                answer = (f"{model} 的主体沿用多层双向 Transformer 编码器。原架构的编码器包含自注意力和"
                          f"前馈网络，其独立解码器还会对编码器输出进行注意力计算。由两篇的架构描述可知，"
                          f"{model} 保留了编码器，省去了原来的独立解码器栈。")
                return dict(papers=papers, answer=answer, gap=None)
        return dict(papers=papers, answer=None, gap="现有摘录不足以确认原架构、继承关系和被省略的组件。")
    topic = next(((label, pattern) for query, label, pattern in TOPICS if re.search(query, question, re.I)), None)
    for doc, paper in zip(docs, papers):
        if topic:
            label, pattern = topic
            for chunk in doc["chunks"]:
                if re.search(pattern, normalized(chunk["text"]), re.I):
                    hit = evidence(doc, chunk, label)
                    observation = metric_observation(label, normalized(chunk["text"]))
                    if observation:
                        hit["observation"] = observation
                    paper["hits"].append(hit)
            paper["hits"].sort(key=lambda hit: not bool(hit.get("observation")))
            paper["hits"] = paper["hits"][:5]
        elif store is None:
            paper["hits"] = find_evidence([doc], question)[:5]
    missing = _missing_gpt(docs, question)
    if topic and not missing:
        from model_series import comparison
        answer = comparison(docs, papers, topic[0], normalized, evidence)
        if answer:
            return dict(papers=papers, answer=answer, gap=None)
    if missing:
        selected = "、".join(f"GPT-{version}" for version in missing)
        return dict(papers=papers, answer=None, gap=f"所选文献缺少 {selected} 原始论文，请补齐后再比较各代模型。")
    if store is None:
        return dict(papers=papers, answer=None, gap="请逐篇核对摘录中的模型、指标和实验条件。")
    local = _local_question(question)
    # Keep optional per-paper CPU inference to the normal two-to-three-paper workflow.
    use_models = len(docs) <= 3
    for doc, paper in zip(docs, papers):
        result = _read_paper(store, doc, local, use_models=use_models)
        locators = {(hit["page"], hit["block"], hit.get("part", 1)) for hit in result["hits"]}
        result["hits"].extend(hit for hit in paper["hits"]
                              if (hit["page"], hit["block"], hit.get("part", 1)) not in locators)
        result["hits"] = result["hits"][:5]
        paper.update(result)
    if DIFFERENCE.search(question):
        answer = _difference(papers)
        return dict(papers=papers, answer=answer, gap=None if answer else "可逐篇核对结论。现有原文不足以确认同一指标、单位和评测条件下的跨篇差值。")
    return dict(papers=papers, answer=None, gap="已按文献分别列出可核对的结论或证据缺口。")


def route(handler):
    req = handler.json_request()
    ids, query = req.get("ids"), req.get("query")
    if (set(req) != {"ids", "query"} or not isinstance(query, str) or not 1 <= len(query.strip()) <= 500
            or not isinstance(ids, list) or not 2 <= len(ids) <= MAX_PAPERS
            or any(not isinstance(i, str) or not ID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids)):
        raise Bad(400, f"请选择 2 至 {MAX_PAPERS} 篇不同文献，并输入 1 至 500 字的问题。更多文献可分批核对。")
    docs = {d["id"]: d for d in handler.server.library.records(ids)}
    if len(docs) != len(ids):
        raise Bad(404, "所选文献已移出活动文库。")
    return handler.payload(200, compare([docs[i] for i in ids], query, store=handler.server.library))
