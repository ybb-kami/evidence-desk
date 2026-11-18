"""Bounded research workflow for user-selected PDF versions. All actions are read-only."""
import re
import threading
import time

from app import Bad, ID

DESCRIPTIONS = {
    "answer_paper": ("检索所选论文并核对回答", "Answer a question about one selected paper using its passages or tables."),
    "compare_papers": ("逐篇核对并比较", "Compare evidence across selected papers; separate incompatible settings."),
    "citation_graph": ("计算库内引用与未来工作线索", "Map citations, core papers and future directions in the selected collection."),
    "scan_coverage": ("检查扫描页识别进度", "Inspect available OCR pages and report missing coverage."),
    "publication_metadata": ("查询 JOSS 出版方题录", "Read publisher metadata for the one JOSS DOI explicitly approved in this request."),
}
GRAPH = re.compile(r"图谱|引用网|引用关系|基石|核心文献|未来工作|future work|core papers?|citation graph|citation network|pagerank", re.I)
CONTENT = re.compile(r"比较|对比|差别|差异|多少|何种|结果|指标|contrast|compare|difference|how many|what (?:was|is)|which (?:method|metric)", re.I)
COVERAGE = re.compile(r"(扫描|识别|OCR).{0,8}(覆盖|进度|哪些页|范围)|OCR coverage|unread pages", re.I)
DOI = re.compile(r"(?<![\w./])10\.21105/joss\.[0-9]{5,6}(?![\w/])", re.I)
WORKERS = threading.BoundedSemaphore(2)


def validate(store, req):
    if set(req) != {"ids", "question", "model_planner", "publisher_consent"}:
        raise Bad(400, "研究任务的字段无效。")
    ids, question = req["ids"], req["question"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 6
            or any(not isinstance(docid, str) or not ID.fullmatch(docid) for docid in ids)
            or len(set(ids)) != len(ids) or not isinstance(question, str)
            or not 1 <= len(question.strip()) <= 500
            or type(req["model_planner"]) is not bool or type(req["publisher_consent"]) is not bool):
        raise Bad(400, "请选择 1 至 6 篇不同的论文，并输入 1 至 500 字的问题。")
    dois = {value.lower() for value in DOI.findall(question)}
    if req["publisher_consent"] and len(dois) != 1:
        raise Bad(400, "出版方查询需要在问题中填写一个明确的 JOSS DOI。")
    selected = {doc["id"]: doc for doc in store.records(ids)}
    if set(selected) != set(ids):
        raise Bad(404, "所选论文已移出活动文库。")
    return [selected[i] for i in ids], next(iter(dois)) if req["publisher_consent"] else None


def choices(docs, question, doi):
    primary = "answer_paper" if len(docs) == 1 else "compare_papers"
    required = []
    if GRAPH.search(question):
        required.append("citation_graph")
    if COVERAGE.search(question):
        required.append("scan_coverage")
    if doi:
        required.append("publication_metadata")
    if not required or CONTENT.search(question):
        required.insert(0, primary)
    offered = [primary, "citation_graph", "scan_coverage"]
    if doi:
        offered.append("publication_metadata")
    return required, [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name][1],
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}} for name in offered]


def source(doc, hit, *, quote=None, start=None, end=None):
    page, block, part = hit.get("page"), hit.get("block"), hit.get("part", 1)
    if hit.get("id", hit.get("doc")) != doc["id"] or type(page) is not int or type(block) is not int:
        return None
    raw = next((c["text"] for c in doc["chunks"] if
                (c["page"], c["block"], c.get("part", 1)) == (page, block, part)), None)
    if not raw:
        return None
    text = quote or hit.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    if type(start) is int and type(end) is int and 0 <= start < end <= len(raw) and raw[start:end] == text:
        pass
    else:
        start = raw.find(text)
        if start < 0:
            pattern = r"\s+".join(re.escape(word) for word in text.split())
            match = re.search(pattern, raw)
            if not match:
                return None
            start, end = match.span()
        else:
            end = start + len(text)
    # Keep the link and the displayed excerpt on the same exact span.
    end = min(start + 650, end)
    return dict(id=doc["id"], name=doc["name"], page=page, block=block, part=part,
                start=start, end=end, text=raw[start:end])


def report(doc, result):
    citations = []
    claims = result.get("generation", {}).get("claims", [])
    for claim in claims:
        item = source(doc, claim, quote=claim.get("span"), start=claim.get("answer_start"), end=claim.get("answer_end"))
        if item:
            citations.append(item)
    if result.get("answer_method") == "local_extractive":
        for candidate in result.get("reader", {}).get("candidates", []):
            if candidate["answer"].strip() == result["answer"]:
                item = source(doc, candidate, quote=candidate["answer"], start=candidate["answer_start"], end=candidate["answer_end"])
                if item:
                    citations.append(item)
                    break
    for hit in result.get("hits", [])[:5]:
        item = source(doc, hit)
        if item and item not in citations:
            citations.append(item)
    cells = []
    for cell in result.get("table_cells", []):
        if (cell.get("id", doc["id"]) == doc["id"] and cell.get("source") in ("native", "ocr")
                and type(cell.get("page")) is int and 1 <= cell["page"] <= doc["pages"]
                and type(cell.get("row")) is int and type(cell.get("column")) is int and cell.get("bbox_pt")):
            cells.append(dict(id=doc["id"], **{k: cell[k] for k in ("page","region","row","column","value","source")}))
    answer = result.get("answer") if citations or cells else None
    if claims and not any(source(doc, c, quote=c.get("span"), start=c.get("answer_start"), end=c.get("answer_end")) for c in claims):
        answer = None
    return {"name": doc["name"], "answer": answer, "method": result.get("answer_method"),
            "gap": result.get("gap") or (None if answer else "所选原文未提供可核对的回答。"),
            "sources": citations, "cells": cells, "generation": result.get("generation", {}).get("status")}


def execute(store, docs, req, doi, emit):
    question = req["question"].strip()
    required, offered = choices(docs, question, doi)
    emit("stage", {"label": "核对文献范围", "detail": f"文库已读出 {len(docs)} 份指定版本。"})
    planner, proposed = "not_requested", []
    if req["model_planner"]:
        emit("stage", {"label": "请求本机模型选择工具"})
        planner, proposed = store.local_generator.choose_tools(question, len(docs), offered)
    allowed = {tool["function"]["name"] for tool in offered}
    # Fixed scopes disallow model-chosen PDFs, file paths, URLs and writes.
    actions = list(dict.fromkeys([n for n in proposed if n in allowed] + required))
    emit("plan", {"planner": planner, "model": store.local_generator.model if planner == "model_tool_calls" else None,
                  "proposed": proposed, "required": required, "tools": actions})
    by_id = {d["id"]: d for d in docs}
    groups, public, receipts = [], [], []
    for action in actions:
        start = time.monotonic()
        emit("stage", {"label": DESCRIPTIONS[action][0], "tool": action})
        pages = 0
        state = "ok"
        if action == "answer_paper":
            from followup import answer_question
            doc = docs[0]
            data = answer_question(store, {"id": doc["id"], "question": question, "anchor": None},
                                   on_delta=lambda delta: emit("token", {"text": delta}))
            item = report(doc, data)
            groups.append(item)
            pages = len(item["sources"]) + len(item["cells"])
            if not item["answer"]:
                state = "gap"
        elif action == "compare_papers":
            from architecture import compare
            comparison = compare(docs, question, store=store)
            reports = [report(by_id[paper["id"]], paper) for paper in comparison["papers"]]
            groups.extend(reports)
            pages = sum(len(r["sources"]) + len(r["cells"]) for r in reports)
            if comparison["answer"] and all(r["sources"] or r["cells"] for r in reports):
                groups.append(dict(name="跨篇对照", answer=comparison["answer"], gap=None, sources=[], cells=[]))
            elif comparison.get("gap"):
                groups.append(dict(name="跨篇对照", answer=None, gap=comparison["gap"], sources=[], cells=[]))
                if any(not r["answer"] for r in reports):
                    state = "gap"
        elif action == "citation_graph":
            from synthesis import evidence_set
            graph = evidence_set(docs)
            rows = [s for s in graph["sources"] if s["category"] in {"引用","未来工作"}]
            kept = [s for hit in rows if (s := source(by_id[hit["doc"]], hit))]
            top = graph["nodes"][0] if graph["edges"] else None
            summary = f"所选文献构成 {graph['works']} 个作品节点、{graph['edges']} 条内部引文边。"
            if top:
                summary += f" 局部 PageRank 首位为 {top['title'] or top['name']}（{top['pagerank']:.3f}）。"
            summary += f" 找到 {len(graph['follow_ups'])} 处作者讨论未来工作的摘录。"
            groups.append(dict(name="所选文献的引文网络", answer=summary, sources=kept[:10], cells=[],
                               gap=None if top else "所选文献之间尚未抽到引文边，无法据此判断核心文献。"))
            pages = len(kept[:10])
        elif action == "scan_coverage":
            coverage = []
            for doc in docs:
                pending = doc["textless_pages"]
                if pending:
                    coverage.append(f"{doc['name']} 尚有 {len(pending)} 页缺少可检索文字；前几页为 " +
                                    "、".join(map(str, pending[:6])) + "。")
                else:
                    coverage.append(f"{doc['name']} 各物理页均有可检索文字。")
            groups.append(dict(name="扫描覆盖", answer=" ".join(coverage), gap=None, sources=[], cells=[],
                               scan=[{"id": d["id"], "page": d["textless_pages"][0]} for d in docs if d["textless_pages"]]))
        elif action == "publication_metadata":
            from discovery.joss import read_publication
            try:
                record, _ = read_publication(doi)
                public.append({key:record[key] for key in ("doi","title","authors","venue","year","url","metadata_sha256")})
            except (OSError, ValueError):
                state = "unavailable"
                groups.append(dict(name="JOSS 出版方", answer=None, gap="未取得可核验的题录，可稍后重试。", sources=[], cells=[]))
        receipts.append({"tool": action, "status": state, "anchors": pages, "seconds": round(time.monotonic()-start, 2)})
        emit("receipt", receipts[-1])
        if state == "gap" and "scan_coverage" not in actions and any(d["textless_pages"] for d in docs):
            actions.append("scan_coverage")
            emit("replan", {"tool":"scan_coverage","reason":"当前回答有证据缺口，所选文献仍有未索引页面。"})
    emit("final", {"reports":groups, "publications":public, "receipts":receipts, "planner":planner})


def handle(handler):
    req = handler.json_request()
    store = handler.server.library
    docs, doi = validate(store, req)
    if not WORKERS.acquire(blocking=False):
        raise Bad(429, "已有两项研究任务正在执行，请稍后重试。")
    try:
        import json
        handler.send_response(200)
        for key, value in {"Content-Type":"application/x-ndjson; charset=utf-8", "Cache-Control":"no-store",
                           "X-Content-Type-Options":"nosniff", "X-Accel-Buffering":"no", "Connection":"close"}.items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.close_connection = True

        def emit(kind, payload):
            handler.wfile.write((json.dumps({"event":kind, **payload}, ensure_ascii=False) + "\n").encode())
            handler.wfile.flush()

        try:
            execute(store, docs, req, doi, emit)
        except (ConnectionError, TimeoutError):
            return
        except Exception:
            emit("error", {"message":"任务中断，未能完成全部核对；可重试或缩小文献范围。"})
    finally:
        WORKERS.release()
