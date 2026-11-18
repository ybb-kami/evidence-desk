"""Resolve model comparisons from a successor paper and its cited predecessor."""
import re


def comparison(docs, papers, label, normalize, cite):
    """Return a conclusion only when selected documents support the relationship."""
    texts = [[(chunk, normalize(chunk["text"])) for chunk in doc["chunks"]] for doc in docs]

    def find(i, pattern, page=None):
        for chunk, text in texts[i]:
            if page is None or chunk["page"] == page:
                match = re.search(pattern, text, re.I)
                if match:
                    return chunk, match
        return None, None

    for i, doc in enumerate(docs):
        bridge, names = find(i, r"smallest model is equivalent to the original ([\w-]+),.*?"
                                r"our largest model, which we call ([\w-]+),")
        if not bridge:
            continue
        page = bridge["page"]
        prior_name, current_name = names.groups()
        link, citation = find(i, r"model largely follows the details of the (?:OpenAI )?"
                                 + re.escape(prior_name) + r" model \(([\w-]+) et al\., (\d{4})\)", page)
        if not link:
            continue
        for j, prior in enumerate(docs):
            if j == i:
                continue
            title = normalize((prior.get("bibliographic_identity") or {}).get("title") or "")
            if not title:
                continue
            ref, _ = find(i, re.escape(citation[1]) + r".*?" + re.escape(title) + r".*?" + citation[2])
            spec, dims = find(j, r"we trained a (\d+)-layer decoder-only transformer.*?\((\d+) dimensional states")
            if not ref or not spec:
                continue
            others = [k for k in range(len(docs)) if k not in (i, j)]
            k = others[0] if len(others) == 1 else None
            answer, extra = None, []
            prior_hits = [(spec, "前代模型的规格")]
            later_hits = []
            if label == "Layer Normalization 位置":
                change, _ = find(i, r"Layer normalization.{0,70}was moved to the input of each sub-block", page)
                final, _ = find(i, r"additional layer normalization was added after the final self-?attention block", page)
                diagram, _ = find(j, r"Figure \d+: \(left\) Transformer architecture and training objectives")
                if change and final and diagram:
                    prior_hits.insert(0, (diagram, "前代架构图：沿图中箭头核对归一化位置"))
                    extra = [(change, "新架构的归一化位置"), (final, "末端归一化")]
                    answer = (f"{current_name} 将 Layer Normalization 移到每个子块输入端（Pre-LN），"
                              f"并在最后一个自注意力块后增加归一化。前代 {prior_name} 的结构顺序可在架构图中核对。")
            elif k is not None and label == "参数规模":
                header, _ = find(i, r"^Parameters Layers dmodel$", page)
                caption, count = find(i, r"Table (\d+)\. Architecture hyperparameters for the (\d+) model sizes", page)
                table, _ = find(i, r"^(?:\d+(?:\.\d+)?M \d+ \d+\s*){2,}$", page)
                last, size = find(k, r"we train ([\w-]+), [^.,;]{1,100} with (\d+(?:\.\d+)?) "
                                     r"(billion|million|trillion) parameters")
                if header and caption and table and last:
                    rows = re.findall(r"(\d+(?:\.\d+)?)M (\d+) (\d+)", normalize(table["text"]))
                    rows.sort(key=lambda t: float(t[0]))
                    referenced_table = re.search(r"Table\s+"+count[1]+r"\b",normalize(bridge["text"]), re.I)
                    if len(rows) == int(count[2]) and referenced_table and rows[0][1:] == dims.groups():
                        extra = [(header, "参数表列头"), (table, "参数规模和层数"),
                                 (caption, "表题"), (bridge, "最小与最大模型的对应关系")]
                        later_hits = [(last, "后续模型的总参数量")]
                        answer = (f"{prior_name}（第一代）约 {rows[0][0]}M，依据后继论文表中与其等价的最小模型；"
                                  f"{current_name} 最大模型 {rows[-1][0]}M；{size[1]} 为 {size[2]} {size[3]}。")
            elif k is not None and label == "上下文或训练序列长度":
                training, seq = find(j, r"train(?:ed)? [^.]{0,140}sequences of ([\d,]+) tokens")
                change, sizes = find(i, r"increase the context size from ([\d,]+) to ([\d,]+) tokens", page)
                window, limit = find(k, r"all models use a context window of nctx\s*=\s*([\d,]+) tokens")
                identity, name = find(k, r"we train ([\w-]+), an autoregressive language model")
                if training and change and window and identity and seq[1] == sizes[1]:
                    prior_hits = [(training, "前代训练序列长度")]
                    extra = [(change, "扩展前后的上下文窗口")]
                    later_hits = [(window, "后续模型的上下文窗口"), (identity, "后续模型名称")]
                    answer = (f"上下文窗口依次为 {prior_name}（第一代）{sizes[1]}、{current_name} {sizes[2]}、"
                              f"{name[1]} {limit[1]} tokens。前代训练序列长度由其原文给出，"
                              f"{current_name} 另明确写出窗口从 {sizes[1]} 扩至 {sizes[2]}。")
            if answer:
                def hits(index, items):
                    seen = set()
                    result = []
                    for chunk, role in items:
                        key = chunk["page"], chunk["block"], chunk["part"]
                        if key not in seen:
                            seen.add(key)
                            result.append(cite(docs[index], chunk, role))
                    return result
                papers[j]["hits"] = hits(j, prior_hits)
                papers[i]["hits"] = hits(i, [*extra, (link,"前代模型指引"), (ref,"前代论文引用")])
                if k is not None and later_hits:
                    papers[k]["hits"] = hits(k, later_hits)
                return answer
    return None
