"""Compare literal architecture descriptions across explicitly selected PDFs."""
import re
import unicodedata

from app import Bad, ID
from engine import find_evidence


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


def compare(docs, question):
    papers = [dict(id=d["id"], name=d["name"], hits=[]) for d in docs]
    titles = re.findall(r"《([^》]+)》", question)
    selected_titles = {normalized((d.get("bibliographic_identity") or {}).get("title") or "").casefold() for d in docs}
    if titles and not all(normalized(t).casefold() in selected_titles for t in titles):
        return dict(papers=papers, answer=None, gap="所选文献与问题中的题名不匹配，请核对选择。")
    if re.search(r"encoder|decoder|编码器|解码器|承袭", question, re.I):
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
        else:
            paper["hits"] = find_evidence([doc], question)[:5]
    if topic:
        from model_series import comparison
        answer = comparison(docs, papers, topic[0], normalized, evidence)
        if answer:
            return dict(papers=papers, answer=answer, gap=None)
    requested = {int(n) for n in re.findall(r"GPT\s*[-–]?\s*(\d+)", question, re.I)}
    covers_three = requested == {1, 2, 3} or (
        {1, 3} <= requested and re.search(r"到|至|through", question, re.I))
    gap = ("题目涉及三代模型，当前仅选择两篇文献，缺少一份原始论文。" if covers_three and len(docs) < 3 else "")
    return dict(papers=papers, answer=None, gap=gap + "请逐篇核对摘录中的模型、指标和实验条件。")


def route(handler):
    req = handler.json_request()
    ids, query = req.get("ids"), req.get("query")
    if (set(req) != {"ids", "query"} or not isinstance(query, str) or not 1 <= len(query.strip()) <= 500
            or not isinstance(ids, list) or not 2 <= len(ids) <= 3
            or any(not isinstance(i, str) or not ID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids)):
        raise Bad(400, "请选择两到三份不同文献，并输入 1 至 500 字的问题。")
    docs = {d["id"]: d for d in handler.server.library.records(ids)}
    if len(docs) != len(ids):
        raise Bad(404, "所选文献已移出活动文库。")
    return handler.payload(200, compare([docs[i] for i in ids], query))
