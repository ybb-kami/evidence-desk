"""Local evidence lookup within a user-selected paper or a cited passage."""
import re
import unicodedata

from app import Bad, ID
import engine
from byline import author_number
from paper_facts import extract_facts


def beta_values(text):
    normalized = unicodedata.normalize("NFKC", text)
    pairs = re.findall(r"(?<!\w)(?:β|beta)\s*[_-]?\s*([12])\s*=\s*"
                       r"(0?\.\d+|[01](?:\.\d+)?)(?!\w|\.\d|\s*[%×*/^+–−-])",
                       normalized, re.IGNORECASE)
    values = [{value for key, value in pairs if key == number} for number in ("1", "2")]
    if all(len(group) == 1 for group in values):
        return [next(iter(group)) for group in values]
    return None


def adam_pair_question(query, anchor):
    normalized = unicodedata.normalize("NFKC", query)
    topic = re.search(r"\bAdam\b", normalized, re.I) or (
        anchor is not None and re.search(r"\bAdam\b", anchor["text"], re.I))
    return (topic and re.search(r"β|beta|贝塔", normalized, re.I)
            and re.search(r"两个|两项|分别|(?:β|beta)\s*1.*(?:β|beta)\s*2", normalized, re.I))


def adam_evidence(doc):
    sources, matched = [], []
    for chunk in doc["chunks"]:
        if not re.search(r"\bAdam\b", chunk["text"], re.I):
            continue
        hit = dict(chunk, id=doc["id"], name=doc["name"])
        sources.append(hit)
        # Avoid joining numbers from a separate sentence about another method.
        for sentence in re.split(r"(?<=[.!?。！？])\s+(?=[A-Z\u3400-\u9fff])", chunk["text"]):
            if re.search(r"\bAdam\b", sentence, re.I):
                values = beta_values(sentence)
                if values:
                    matched.append((tuple(values), hit))
    distinct = {pair for pair, _ in matched}
    if len(distinct) == 1:
        first, second = distinct.pop()
        return {"hits": [matched[0][1]], "answer": f"β₁ = {first}，β₂ = {second}"}
    if distinct:
        examples = {}
        for pair, hit in matched:
            examples.setdefault(pair, hit)
        return {"hits": list(examples.values())[:8], "answer": None,
                "gap": "所选范围中存在多组 Adam 参数值，请按实验设置核对原页。"}
    return {"hits": sources[:8], "answer": None,
            "gap": "所选范围未读出两个带明确标记的 Adam β 参数值，请核对原页。"}


def lookup(store, request, *, dense=None):
    docid, question, anchor = (request.get(key) for key in ("id", "question", "anchor"))
    if (set(request) != {"id", "question", "anchor"} or not isinstance(docid, str)
            or not ID.fullmatch(docid) or not isinstance(question, str) or not 1 <= len(question.strip()) <= 500):
        raise Bad(400, "请选择一份文献并输入 1 至 500 字的问题。")
    docs = store.records([docid])
    if not docs:
        raise Bad(404, "文献已移出活动文库。")
    doc = docs[0]
    if "author_blocks" not in doc and author_number(question) is not None:
        doc = engine.with_author_blocks(doc, store.pdf(docid))
    chunk = None
    if anchor is not None:
        if (not isinstance(anchor, list) or len(anchor) != 3
                or any(type(n) is not int or n < 1 for n in anchor)):
            raise Bad(400, "证据段落位置无效。")
        chunk = next((c for c in doc["chunks"] if [c["page"], c["block"], c["part"]] == anchor), None)
        if chunk is None:
            raise Bad(404, "未找到所选证据段落。")
    from scan_table_qa import answer_native_table, answer_scan_table
    metric = None
    if anchor is None:
        from metric_qa import answer_metric
        metric = answer_metric(store, doc, question)
        if metric and metric.get("answer"):
            return dict(metric, answer_method="table_calculation")
    table = answer_scan_table(store, doc, question, chunk)
    if table and table.get("answer"):
        return dict(table, answer_method="scan_table")
    native_table = answer_native_table(store, doc, question, chunk)
    if native_table and native_table.get("answer"):
        return dict(native_table, answer_method="native_table")
    if anchor is not None:
        doc = dict(doc, chunks=[chunk])
    query = re.sub(r"^(?:其中|这段(?:话)?中|该段中)\s*", "", question.strip())
    if adam_pair_question(query, chunk):
        return dict(adam_evidence(doc), answer_method="source_fields")
    hits = engine.find_evidence([doc], query)
    fact = extract_facts(doc, query, hits)
    if fact is not None:
        return dict(fact, answer_method="source_fields")
    from rag_search import ranked_passages
    ranked, retrieval = ranked_passages(doc, query, hits, dense)
    limited = table or native_table or metric
    if limited:
        results = {**limited, "retrieval": retrieval}
        known = {(h["page"], h["block"], h.get("part", 1)) for h in results["hits"]}
        results["hits"] = (results["hits"] + [h for h in ranked
            if (h["page"], h["block"], h.get("part", 1)) not in known])[:8]
        return results
    fact = extract_facts(doc, query, ranked)
    if fact is not None:
        return dict(fact, answer_method="source_fields", retrieval=retrieval)
    return {"hits": ranked, "answer": None, "retrieval": retrieval,
            "gap": ("已找到相关段落，请核对其中的条件和数值。" if ranked else
                    "所选范围未找到足以回答这个问题的原文。")}

def answer_question(store, request):
    """Local retrieval runs first; optional local models receive only the selected evidence."""
    result = lookup(store, request, dense=store.dense)
    if result.get("answer") or not result["hits"] or "table_cells" in result or result.get("answer_method"):
        return result
    from passage_answer import assess_spans
    question = request["question"]
    candidates, status, english = store.passage_reader.candidates(question, result["hits"])
    checked, direct = assess_spans(question, english, candidates, result["hits"]) if candidates else ([], None)
    result["reader"] = {"status": status, "candidates": checked}
    draft = store.local_generator.answer(question, result["hits"], checked)
    result["generation"] = draft
    if draft["status"] == "cited_draft":
        claim = draft["claims"][0]
        result.update(answer=claim["text"], answer_method="local_cited_draft", gap=None)
    elif direct and draft["status"] in {"disabled", "busy", "unavailable", "misconfigured"}:
        result.update(answer=direct["answer"].strip(), answer_method="local_extractive", gap=None)
    elif draft["status"] == "insufficient_evidence":
        result["gap"] = "在已检索的原文中，本机模型没有找到问题所需的直接依据。"
    return result
