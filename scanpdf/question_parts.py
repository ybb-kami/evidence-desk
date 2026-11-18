"""Identify independently answerable clauses and refer back to their source anchors."""
import re

# Split at a second question, never at an "and" inside a term, author name,
# table cell, mathematical expression or unqualified yes/no comparison.
NEXT = r"(?:how\b|what\b|which\b|when\b|where\b|who\b|why\b|did\b|does\b|were\b|was\b|请问|哪[个些项种]|采用|使用|测量|报告|实验|作者|样本|校准|验证|测试|训练|其[中余]|该[组项]|[\u3400-\u9fff]{1,14}(?:多少|如何|是什么|是哪|用什么))"
BOUNDARY = re.compile(r"(?:[?？;；]+\s*(?:and\s+|以及|另外|同时|并且)?\s*|,?\s+and\s+|[，,]\s*(?:以及|另外|并且|同时))(?="+NEXT+r")",re.I)
ASK = re.compile(r"\b(?:how|what|which|when|where|who|why|did|does|was|were)\b|多少|如何|什么|哪个|哪些|哪种|是否|几[个种名组篇次]",re.I)


def split_question(question):
    text=question.strip()
    sections=[s.strip(" ,，;；?？") for s in BOUNDARY.split(text)]
    if not 2 <= len(sections) <= 3 or any(len(s)<6 or not ASK.search(s) for s in sections):
        return [text]
    return sections


def describe_gap(question, hits, reason=None):
    # Identify the missing evidence without asserting that the entire paper is silent.
    amount=len(hits)
    scope=(f"已检索的 {amount} 段原文" if amount else "当前可检索的文献范围")
    if reason=="ambiguous_measurement":
        return f"{scope}中有多个条件及不同读数。请指定所问的组别、阶段或单位后再核对。"
    if reason=="different_condition":
        return f"检索到的原句描述了其他实验条件。{scope}未给出与「{question.rstrip('？?')}」相符的读数。"
    fields = (
        (r"serial number|序列号", "设备序列号", r"serial number|序列号"),
        (r"random seed|随机种子", "随机种子", r"random seed|随机种子"),
        (r"confidence interval|置信区间", "置信区间", r"confidence interval|置信区间|95% CI"),
        (r"standard deviation|标准差", "标准差", r"standard deviation|标准差|\bSD\b"),
        (r"batch size|批量大小", "批量大小", r"batch size|批量大小"),
        (r"licen[cs]e|许可协议", "许可协议", r"licen[cs]e|许可协议"),
    )
    for ask, label, reported in fields:
        if re.search(ask,question,re.I) and not any(re.search(reported,h["text"],re.I) for h in hits):
            return f"{scope}中没有明确报告{label}的原文，无法据此给出「{question.rstrip('？?')}」的答案。"
    return f"{scope}中未找到能直接回答「{question.rstrip('？?')}」的原句。"


def combine(parts):
    hits, indices, claims, cells, fields = [], {}, [], [], []
    supported = 0
    # Put the actual support for each field first; then interleave independent
    # retrieval lists instead of hiding every source for the second question.
    def add(hit):
        key = (hit["id"],hit["page"],hit["block"],hit.get("part",1))
        if key not in indices:
            indices[key] = len(hits)
            hits.append(hit)
    for _, result in parts:
        cited = result.get("generation",{}).get("claims",[])
        for claim in cited:
            rows = result.get("hits",[])
            index = claim.get("hit_index")
            if type(index) is int and 0 <= index < len(rows):
                add(rows[index])
        if not cited and result.get("answer") and result.get("hits"):
            add(result["hits"][0])
    rows = [result.get("hits",[]) for _,result in parts]
    for rank in range(max(map(len,rows),default=0)):
        for ranked in rows:
            if rank < len(ranked) and len(hits)<12:
                add(ranked[rank])
    for question, result in parts:
        field_claims=[]
        for claim in result.get("generation",{}).get("claims",[]):
            key=(claim["id"],claim["page"],claim["block"],claim.get("part",1))
            if key in indices:
                field_claims.append(dict(claim,hit_index=indices[key]))
        claims.extend(field_claims)
        cells.extend(result.get("table_cells", []))
        answer=result.get("answer")
        if answer:
            supported+=1
        fields.append(dict(question=question,answer=answer,
                           gap=None if answer else result.get("gap") or describe_gap(
                               question, result.get("hits", []), result.get("generation", {}).get("reason")),
                           method=result.get("answer_method"),claims=field_claims,
                           cells=result.get("table_cells",[])))
    status="complete" if supported==len(parts) else ("partial" if supported else "insufficient_evidence")
    return dict(hits=hits, fields=fields, answer="\n".join(f"{f['question']} — {f['answer']}" for f in fields if f["answer"]) or None,
                answer_method="fieldwise", completeness=status,
                gap=None if status=="complete" else " ".join(f["gap"] for f in fields if f["gap"]),
                generation={"status":status,"claims":claims}, **({"table_cells":cells} if cells else {}))
