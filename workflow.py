"""Read-only evidence workflow: plan, locate, verify original bytes, summarize."""
from collections import defaultdict
import hashlib

import fitz

from compare_evidence import compare_evidence
from engine import MAX_BYTES, MAX_PAGES, _windows
from evidence_questions import locate_question
from partner_evidence import PROVENANCE, candidates as partner_candidates

MAX_WORKFLOW_PAPERS = 8
PLAN = (
    ("plan", "固定本次查询及用户明确所选、以 SHA-256 标识的文献"),
    ("retrieve", "用本机词法和固定概念表分篇寻找候选段落"),
    ("verify", "重读 PDF 原件并比较 SHA-256、物理页、文本块、片段偏移和逐字文本"),
    ("summarize", "按文献整理已核摘录、无命中和失败；没有已核摘录时拒绝给提要"),
)
LIMITS = (
    "这是确定性的只读本地流程，不调用模型，不执行文献里的文字或外部指令。",
    "核验仅指候选文本与当前解析器从同一 PDF 页块重新提取的文本逐字相同；不是视觉校勘、事实真伪或语义支持判定。",
    "对多篇文献分别展示；无命中不证明文献没有该内容，也不能据此推断文献之间的关系。",
)


def _passage_key(hit):
    return (hit.get("page"), hit.get("block"), hit.get("part"), hit.get("offset"))


def _valid_address(hit):
    page, block, part, offset = _passage_key(hit)
    return all(type(i) is int for i in (page,block,part,offset)) and (
        1 <= page <= MAX_PAGES and block >= 1 and part >= 1 and offset >= 0
        and isinstance(hit.get("text"), str) and bool(hit["text"]))


def _locate(docs, query, get_pdf):
    by_doc = {doc["id"]: {} for doc in docs}
    concepts = {}
    metrics = {}

    def append(doc_id, hit, groups):
        # Keep inconsistent text for the same address as a separate verification
        # candidate, rather than allowing one cache record to overwrite another.
        key = (*_passage_key(hit), hit.get("text"))
        current = by_doc[doc_id].setdefault(key, {"hit":hit,"concepts":[]})
        for group in groups:
            if group not in current["concepts"]:
                current["concepts"].append(group)

    questions = locate_question(docs, query)
    for concept in questions["concepts"]:
        concepts[concept["id"]] = concept["label"]
    for paper in questions["papers"]:
        for hit in paper["hits"]:
            append(paper["id"],hit,hit["concept_ids"])
    if len(docs) > 1:
        matrix = compare_evidence(docs, query)
        for row in matrix["rows"]:
            concepts[row["id"]] = row["label"]
            for cell in row["cells"]:
                for hit in cell["hits"]:
                    append(cell["id"],hit,[row["id"]])
    for doc_id,key,label,hit in partner_candidates(docs,query,get_pdf):
        concepts.setdefault(key,label)
        append(doc_id,hit,[key])
    if "bleu" in query.casefold():
        from partner_metric import answer_metric
        for doc in docs:
            try:
                data = get_pdf(doc["id"])
            except (OSError, KeyError, ValueError):
                continue
            if not isinstance(data,bytes) or hashlib.sha256(data).hexdigest()!=doc["id"]:
                continue
            result = answer_metric(doc, query, data)
            if result and result.get("comparison") and result.get("hits"):
                metrics[doc["id"]]=result
            for hit in (result or {}).get("hits", ()):
                concepts["metric_table"] = "BLEU 分组表与正文出处（非自动语义证明）"
                append(doc["id"],hit,["metric_table"])
    return by_doc, concepts, metrics


def _verification_failure(hit, reason):
    page, block, part, offset = _passage_key(hit)
    return {"reason": reason, **{k:v if type(v) is int else None
                                 for k,v in zip(("page","block","part","offset"),(page,block,part,offset))}}


def _verify_record(doc_id, candidates, get_pdf):
    if not candidates:
        return [], [], "not_read_no_candidates"
    try:
        data = get_pdf(doc_id)
    except Exception:
        return [], [_verification_failure(c["hit"],"source_unavailable") for c in candidates], "unavailable"
    if not isinstance(data,bytes) or not data or len(data) > MAX_BYTES:
        return [], [_verification_failure(c["hit"],"source_unavailable") for c in candidates], "unavailable"
    if hashlib.sha256(data).hexdigest() != doc_id:
        return [], [_verification_failure(c["hit"],"source_hash_mismatch") for c in candidates], "hash_mismatch"
    verified, rejected = [], []
    targets = defaultdict(list)
    for candidate in candidates:
        hit = candidate["hit"]
        if not _valid_address(hit):
            rejected.append(_verification_failure(hit,"invalid_index_anchor"))
        else:
            targets[hit["page"]].append(candidate)
    try:
        with fitz.open(stream=data,filetype="pdf") as source:
            if source.needs_pass or not source.is_pdf or not 1 <= len(source) <= MAX_PAGES:
                raise ValueError("unsupported PDF")
            for page_num, page_candidates in targets.items():
                if page_num > len(source):
                    rejected.extend(_verification_failure(c["hit"],"page_not_found") for c in page_candidates)
                    continue
                try:
                    page = source[page_num - 1]
                    blocks = {int(item[5])+1:item[4].strip()
                              for item in page.get_text("blocks",flags=fitz.TEXTFLAGS_BLOCKS,sort=True)
                              if item[6] == 0}
                    label = page.get_label() or str(page_num)
                except Exception:
                    rejected.extend(_verification_failure(c["hit"],"page_unreadable") for c in page_candidates)
                    continue
                wanted_blocks = {c["hit"]["block"] for c in page_candidates}
                verified_windows = {block:list(_windows(blocks[block])) for block in wanted_blocks if block in blocks}
                for candidate in page_candidates:
                    hit = candidate["hit"]
                    windows = verified_windows.get(hit["block"])
                    if not windows or hit["part"] > len(windows):
                        rejected.append(_verification_failure(hit,"block_or_fragment_not_found"))
                        continue
                    text, offset = windows[hit["part"] - 1]
                    if offset != hit["offset"] or text != hit["text"]:
                        rejected.append(_verification_failure(hit,"text_or_offset_mismatch"))
                        continue
                    verified.append({"page":page_num,"page_label":label,"block":hit["block"],"part":hit["part"],
                                     "offset":offset,"text":text,"concept_ids":candidate["concepts"]})
    except Exception:
        return [], [_verification_failure(c["hit"],"source_unreadable") for c in candidates], "hash_matched_unreadable"
    return verified, rejected, "hash_matched"


def run_workflow(docs, query, get_pdf):
    if not isinstance(query,str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError("请输入 1 到 500 字的检索问题。")
    if not 1 <= len(docs) <= MAX_WORKFLOW_PAPERS or len({d["id"] for d in docs}) != len(docs):
        raise ValueError("请选择 1 到 8 篇不同的活动文献。")
    query = query.strip()
    by_doc, concepts, metrics = _locate(docs, query, get_pdf)
    papers = []
    for doc in docs:
        ident = doc["id"]
        candidates = list(by_doc[ident].values())
        verified, failures, source_status = _verify_record(ident,candidates,get_pdf)
        found = "verified_evidence" if verified else "verification_failed" if candidates else "no_match"
        papers.append({"id":ident,"name":doc["name"],"status":found,"source_status":source_status,
                       "candidate_count":len(candidates),"evidence":verified,"failures":failures})
        metric=metrics.get(ident)
        if metric and not failures and verified and source_status=="hash_matched":
            keys={(*_passage_key(e),e["text"]) for e in verified}
            if all((*_passage_key(h),h["text"]) in keys for h in metric["hits"]):
                papers[-1]["table_calculation"]={"text":metric["answer"],**metric["comparison"]}
    n = sum(len(p["evidence"]) for p in papers)
    count = sum(p["candidate_count"] for p in papers)
    failures = sum(len(p["failures"]) for p in papers)
    matched=sum(bool(p["evidence"]) for p in papers)
    coverage="all" if matched==len(papers) else "partial" if matched else "none"
    message = (f"{matched}/{len(papers)} 篇找到并核对摘录；问题本身尚未判定。提要仅收录通过原件逐字核验的摘录。请核对每处原页以及问题与引文的关系。"
               if n else "证据不足：本次没有通过原件核验的摘录，不提供结论或证据提要。")
    methods={key for p in papers for e in p["evidence"] for key in e["concept_ids"]}
    partner = {key:value for key,value in PROVENANCE["features"].items() if key in methods}
    return {
        "source_methods":[{**PROVENANCE,"features":partner}] if partner else [],
        "schema":"evidence-desk-workflow-v1","query":query,
        "status":"verified_evidence" if n else "insufficient_evidence",
        "coverage":coverage,"papers_with_evidence":matched,"papers_selected":len(papers),"claim_support":"not_assessed",
        "summary":message,"concepts":concepts,"papers":papers,"notes":list(LIMITS),
        "steps":[
            {"id":PLAN[0][0],"label":PLAN[0][1],"status":"completed","paper_count":len(papers)},
            {"id":PLAN[1][0],"label":PLAN[1][1],"status":"completed","candidate_count":count},
            {"id":PLAN[2][0],"label":PLAN[2][1],"status":"completed" if count else "skipped_no_candidates",
             "verified_count":n,"rejected_count":failures},
            {"id":PLAN[3][0],"label":PLAN[3][1],"status":"completed" if n else "refused_no_verified_evidence"},
        ],
    }
