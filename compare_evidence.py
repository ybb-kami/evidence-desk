"""Bounded source matrix across selected local papers; no comparison verdict."""
import re
import unicodedata

from engine import MAX_RESULTS, find_evidence

MAX_PAPERS, MAX_CONCEPTS, MAX_CELL_HITS = 8, 8, 3
ASCII_EDGE_L = r"(?<![a-z0-9])"
ASCII_EDGE_R = r"(?![a-z0-9])"


def _word(pattern):
    return ASCII_EDGE_L + "(?:" + pattern + ")" + ASCII_EDGE_R


def _clean(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    # Match prose across soft PDF wrapping and common visible hyphens.
    text = re.sub(r"(?<=[a-z])[-\u00ad]\s*\n\s*(?=[a-z])", "", text)
    return " ".join(re.sub(r"[-‐‑–−_]", " ", text).split())


CONCEPTS = (
    ("encoder", "Encoder / 编码器",
     _word(r"encoders?") + r"|编码器", ("encoder", "encoders", "编码器"),
     _word(r"encoders?") + r"|编码器"),
    ("decoder", "Decoder / 解码器",
     _word(r"decoders?") + r"|解码器", ("decoder", "decoders", "解码器"),
     _word(r"decoders?") + r"|解码器"),
    ("layer_norm", "Layer normalization / 层归一化",
     _word(r"layer\s*norm(?:ali[sz]ation)?|(?:pre|post)\s*(?:ln|normali[sz]ation)|ln") + r"|层归一化|层标准化",
     ("LN", "layer normalization", "layer norm", "pre-LN / post-LN / pre-normalization", "层归一化"),
     _word(r"layer\s*norm(?:ali[sz]ation)?|(?:pre|post)\s*(?:ln|normali[sz]ation)") +
     r"|层归一化|层标准化|" + _word(r"ln") + r"\s*(?:位置|层)|"
     + _word(r"(?:residual|sublayer|layer|normali[sz]ation)\s+ln|ln\s+(?:layer|normali[sz]ation)")),
    ("context_window", "Context window / 上下文窗口",
     _word(r"(?:context|sequence)\s*(?:windows?|lengths?|size)|maximum\s+(?:input|sequence)\s+length") + r"|上下文(?:窗口|长度)|序列长度|最大输入长度",
     ("context window/length/size", "sequence length", "maximum input length", "上下文窗口"),
     _word(r"(?:context|sequence)\s*(?:windows?|lengths?|size)|maximum\s+(?:input|sequence)\s+length|"
           r"max(?:imum)?\s+(?:of\s+)?\d[\d, ]*\s+tokens?|(?:sequences?|inputs?)\s+(?:of\s+)?(?:up to\s+)?\d[\d, ]*\s+tokens?") +
     r"|上下文(?:窗口|长度)|序列长度|最大输入长度"),
    ("parameter_count", "Parameter count / 参数量",
     _word(r"parameter\s+counts?|(?:number|count)\s+of\s+(?:trainable\s+)?parameters|model size") + r"|参数量|参数(?:个数|数量|规模)|模型大小",
     ("parameter count", "number of parameters", "million/billion parameters", "参数量"),
     _word(r"parameter\s+counts?|(?:number|count)\s+of\s+(?:trainable\s+)?parameters|"
           r"\d[\d.,]*\s*(?:[mbk]\b|million|billion|thousand)?\s+(?:total\s+|trainable\s+)?(?:parameters?|params)|"
           r"parameters?\s*[:=]\s*\d[\d.,]*\s*(?:[mbk]|million|billion|thousand)?") +
     r"|参数量|参数(?:个数|数量|规模)"),
)
_SEPARATOR = re.compile(r"[。！？，,、;；!?\n]+|\b(?:and|versus|vs\.?)\b|以及|和|与", re.I)


def _plans(query):
    normalized = _clean(query)
    plans = []
    for key, label, detect, aliases, pattern in CONCEPTS:
        if re.search(detect, normalized):
            plans.append((key, label, aliases, re.compile(pattern), None))
    uncovered = [part.strip(" \t,，") for part in _SEPARATOR.split(query)]
    uncovered = [part for part in uncovered if part and not any(re.search(c[2], _clean(part)) for c in CONCEPTS)]
    if not plans and not uncovered:
        uncovered = [query]
    for clause in list(dict.fromkeys(uncovered)):
        plans.append(("literal_" + str(len(plans)), clause, (clause,), None, clause))
    return plans[:MAX_CONCEPTS], max(0,len(plans)-MAX_CONCEPTS)


def _candidate_rank(key, match, passage):
    # Only rank how easy a term is to inspect; this is not claim validation.
    if key in ("encoder", "decoder"):
        local = passage[max(0, match.start() - 50):match.end() + 50]
        # Prefer likely definitional/structural context over an isolated label.
        hint = bool(re.search(r"\b(?:consists|uses?|architecture|layers?|stack|transformer|self attention)\b", local))
        return (int(hint),)
    if key == "context_window":
        nearby = passage[max(0, match.start()-35):match.end()+90]
        token_count = re.search(r"\b\d[\d,]*\s+tokens?\b", nearby)
        return (int(bool(token_count)), int(bool(re.search(r"[0-9]",nearby))))
    return (int(bool(re.search(r"[0-9]", passage))),)


def _pick(doc, key, matcher, norm_chunks):
    hits = []
    for chunk, normalized in norm_chunks:
        found = matcher.search(normalized)
        if found:
            hits.append((_candidate_rank(key,found,normalized),chunk))
    hits.sort(key=lambda pair:(tuple(-n for n in pair[0]),pair[1]["page"],pair[1]["block"],pair[1].get("part",1)))
    results, seen = [], set()
    labels = doc.get("page_labels", [])
    available = len({(chunk["page"],chunk["block"]) for _,chunk in hits})
    for _, chunk in hits:
        page, block = chunk["page"], chunk["block"]
        if (page, block) in seen:
            continue
        seen.add((page,block))
        results.append({**chunk,"id":doc["id"],"name":doc["name"],
                        "page_label":labels[page-1] if page <= len(labels) else str(page)})
        if len(results) == MAX_CELL_HITS:
            break
    return results, available


def compare_evidence(docs, query):
    if not 2 <= len(docs) <= MAX_PAPERS or len({d["id"] for d in docs}) != len(docs):
        raise ValueError("请选择 2 到 8 篇不同的活动文献。")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError("请输入 1 到 500 字的术语。")
    plans, omitted = _plans(query.strip())
    normalized = [[(c,_clean(c["text"])) for c in doc["chunks"]] for doc in docs]
    rows = []
    for key,label,aliases,matcher,literal in plans:
        cells = []
        for doc,chunks in zip(docs,normalized):
            if matcher:
                result, total = _pick(doc,key,matcher,chunks)
                lower_bound = False
            else:
                lexical = find_evidence([doc],literal)
                total = len(lexical)
                result = lexical[:MAX_CELL_HITS]
                lower_bound = total == MAX_RESULTS
            cells.append({"id":doc["id"],"status":"candidate_evidence" if result else "no_match","hits":result,
                          "match_scope":"lexical_only_subject_unresolved",
                          "candidate_count":total,"hidden_count":total-len(result),
                          "candidate_count_is_lower_bound":lower_bound})
        rows.append({"id":key,"label":label,"terms":list(aliases),"cells":cells})
    return {
        "query":query.strip(),
        "papers":[{"id":d["id"],"name":d["name"],"arxiv":d.get("arxiv")} for d in docs],
        "rows":rows,
        "omitted_term_count":omitted,
        "notes":[
            "每个单元只表示词项命中，指涉对象未辨别；显示最多三条原文。无命中不代表文献没有该内容。",
            "同词可能指不同对象。候选段落不证明继承、替代、参数规模或指标可比；请逐页核对对象、版本、模型配置和范围。",
            "LN、pre-LN、post-LN 只用作原文定位，词面命中不证明归一化在子层之前或之后。",
        ] + ([f"术语行已截断：最多显示 {MAX_CONCEPTS} 项，另有 {omitted} 项未检索。请分批重试。"] if omitted else []),
    }
