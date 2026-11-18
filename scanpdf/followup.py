"""Local evidence lookup within a user-selected paper or a cited passage."""
import re
import unicodedata

from app import Bad, ID
import engine
from byline import author_number
from paper_facts import extract_facts, hardware_clauses, hardware_parts


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
    query = re.sub(r"^(?:其中|这段(?:话)?中|该段中)\s*", "", question.strip())
    from book_index import answer as book_index_answer
    from book_exercises import locate_exercise
    index_entry = book_index_answer(doc, question, chunk)
    if index_entry is not None:
        if index_entry.get("index_status") == "unresolved":
            from rag_search import ranked_passages
            scoped = dict(doc, chunks=[chunk]) if chunk is not None else doc
            candidates, retrieval = ranked_passages(scoped, query, engine.find_evidence([scoped], query), dense)
            index_entry.update(hits=candidates, retrieval=retrieval)
        return index_entry
    exercise = locate_exercise(doc if chunk is None else dict(doc, chunks=[chunk]), question,
                               load_pdf=lambda: store.pdf(doc["id"]))
    if exercise is not None:
        return exercise
    from abbreviations import answer as abbreviation_answer
    definition = abbreviation_answer(doc, question, chunk)
    if definition is not None:
        return definition
    from training_scale import read_scale
    scale = read_scale(doc, question, chunk)
    if scale is not None:
        return scale
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

def _answer_one(store, request, result, *, on_delta=None, generator=None):
    if result.get("exercise", {}).get("read_statement"):
        original = result["hits"]
        # Preserve character offsets while hiding text from neighboring exercises.
        scoped = [dict(h, text=" " * h["start"] + h["text"][h["start"]:h["end"]]) for h in original]
        read = _read_passages(store, request, {"hits": scoped, "answer": None},
                              on_delta=on_delta, generator=generator)
        read.update(hits=original, exercise=result["exercise"])
        if not read.get("answer"):
            read["gap"] = result["gap"] + " " + read.get("gap", "")
        return read
    return _read_passages(store, request, result, on_delta=on_delta, generator=generator)


def _read_passages(store, request, result, *, on_delta=None, generator=None):
    if result.get("answer_method"):
        return result
    if not result["hits"] and not result.get("answer"):
        from question_parts import describe_gap
        result["gap"] = describe_gap(request["question"], [])
        return result
    if result.get("answer") or "table_cells" in result:
        return result
    from passage_answer import assess_spans
    question = request["question"]
    from direct_measurement import extract
    from hybrid_retrieval import english_query
    from claim_support import check
    from question_parts import describe_gap
    english = english_query(question) if re.search(r"[㐀-鿿]", question) else question
    measurement, reason = extract(question, english, result["hits"])
    if reason:
        result.update(gap=describe_gap(question,result["hits"],reason), generation={"status":"insufficient_evidence","reason":reason})
        return result
    if measurement:
        result.update(answer=measurement["text"],answer_method="direct_measurement",gap=None,
                      generation={"status":"source_measurement","claims":[measurement]})
        return result
    candidates, status, translated = store.passage_reader.candidates(question, result["hits"])
    english = translated or english
    checked, direct = assess_spans(question, english, candidates, result["hits"]) if candidates else ([], None)
    result["reader"] = {"status": status, "candidates": checked}
    if direct:
        claim = dict(direct, text=direct["answer"].strip(), quote=direct["context"], span=direct["answer"])
        if not check(question, english, result["hits"], claim):
            result.update(answer=claim["text"],answer_method="local_extractive",gap=None,
                          generation={"status":"source_extraction","claims":[claim]})
            return result
    options = {"english": english} if english else {}
    if on_delta:
        options["on_delta"] = on_delta
    model = generator if generator is not None else store.local_generator
    draft = model.answer(question, result["hits"], checked, **options)
    result["generation"] = draft
    if draft["status"] == "cited_draft":
        claim = draft["claims"][0]
        result.update(answer=claim["text"], answer_method=getattr(model, "answer_method", "local_cited_draft"), gap=None)
    elif draft["status"] == "insufficient_evidence":
        reasons = {"different_unit":"回答的数值单位与所引原文不一致。", "field_missing":"所引原句没有说明问题所问的字段。",
                   "different_condition":"原句对应的实验条件与问题不符。",
                   "ambiguous_measurement":"多个条件的原文报告不同读数，请指定实验条件后再核对。"}
        result["gap"] = reasons.get(draft.get("reason"), describe_gap(question,result["hits"]))
    elif not result.get("answer"):
        result["gap"]=describe_gap(question,result["hits"])
    return result


def answer_question(store, request, *, on_delta=None, generator=None):
    """Split explicitly coordinated questions while preserving every field's source."""
    from question_parts import combine, split_question
    full = lookup(store, request, dense=store.dense)
    parts = hardware_clauses(split_question(request["question"]))
    from direct_coverage import covers_parts
    if (len(parts) == 1 or full.get("exercise", {}).get("read_statement")
            or full.get("exercise", {}).get("statement_fields")
            or (full.get("training_hardware") and hardware_parts(parts)) or covers_parts(full, parts)):
        return _answer_one(store,request,full,on_delta=on_delta,generator=generator)
    results=[]
    for part in parts:
        scoped=dict(request,question=part)
        # Each field obtains its own original observations and conditions.
        raw=lookup(store,scoped,dense=store.dense)
        results.append((part,_answer_one(store,scoped,raw,generator=generator)))
    return combine(results)
