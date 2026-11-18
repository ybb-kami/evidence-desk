"""Replay the nine official questions through a temporary local workbench."""

import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
import http.client
import json
from pathlib import Path
import re
from urllib.parse import quote

from corpus import call, workbench
from official_questions import (
    ATTENTION, BERT, BERT_REQUIREMENTS, QUESTIONS, Requirement,
    attention_requirements, normalized, read_source,
)


GPT1 = "language_understanding_paper.pdf"
GPT2 = "language-models.pdf"
GPT3 = "2005.14165v4.pdf"
GPT_QUESTIONS = (
    "对比GPT-1、GPT-2和GPT-3的论文，说明这些模型的参数的变化。",
    "GPT-2 相比于 GPT-1，在 Transformer 的 Layer Normalization（LN）位置上做了什么关键的调整？",
    "从 GPT-1 到 GPT-3，模型的最大上下文 Token 长度（Context Window）分别递增到了多少？",
)
GPT_REQUIREMENTS = (
    {
        GPT1: ("GPT-1 参数总量，原文未明确找到", ()),
        GPT2: ("GPT-2 参数表中最大模型的参数规模", ()),
        GPT3: ("GPT-3 的 1750 亿参数", (
            Requirement("GPT-3 的 1750 亿参数", 1, 11, (r"train GPT-3, an autoregressive language model with 175 billion parameters",)),
            Requirement("GPT-3 的 1750 亿参数", 8, 12, (r"to 175 billion parameters, with the last being the model we call GPT-3",)),
        )),
    },
    {
        GPT1: ("GPT-1 归一化和子层的先后次序，原文未明确找到", ()),
        GPT2: ("GPT-2 预归一化与额外末端 LayerNorm 的直接原文", ()),
    },
    {
        GPT1: ("GPT-1 的 512 token 训练序列，只作为部分证据", (
            Requirement("GPT-1 的 512 token 训练序列", 5, 5, (
                r"We train for 100 epochs on minibatches of 64 randomly sampled, contiguous sequences of 512 tokens",
            )),
        )),
        GPT2: ("GPT-2 的上下文长度原文", ()),
        GPT3: ("GPT-3 的 2048 token 上下文窗口", (
            Requirement("GPT-3 的 2048 token 上下文窗口", 8, 13, (r"All models use a context window of nctx\s*=\s*2048 tokens",)),
            Requirement("GPT-3 的 2048 token 上下文窗口", 10, 3, (r"maximum amount allowed by the model.s context window, which is nctx\s*=\s*2048",)),
        )),
    },
)
GPT2_SHA256 = "d9d852e2894556e73f53cb22b7c605a9643d6f0b19bf604b429ed6192fa24f4e"
GPT1_MODEL = Requirement(
    "GPT-1 的论文架构参数与 GPT-2 报告所称同等级小模型相符", 5, 5,
    (r"12-layer decoder-only transformer", r"768 dimensional states"),
)
GPT1_NORMALIZATION = Requirement(
    "GPT-1 的模型说明只明确广泛使用归一化", 5, 5,
    (r"largely follows the original transformer work", r"layernorm.*is used extensively"),
)
GPT1_FIGURE = Requirement(
    "GPT-1 图 1 左图显示残差相加后 Layer Norm", 4, 1,
    (r"Figure 1: \(left\) Transformer architecture and training objectives used in this work",),
)
GPT2_SUPPLEMENT = (
    (
        Requirement("GPT-2 表 2 的参数列标题", 4, 9, (r"^Parameters Layers dmodel",)),
        Requirement("GPT-2 表 2 原报四组参数规模，包括最大模型 1542M", 4, 10,
                    (r"^117M 12 768 345M 24 1024 762M 36 1280 1542M 48 1600",)),
        Requirement("GPT-2 表 2 的标题与四组规模语境", 4, 11,
                    (r"Table 2.*Architecture hyperparameters for the 4 model sizes",)),
        Requirement("GPT-2 报告将最小模型与原 GPT 对应、最大模型称为 GPT-2", 4, 15,
                    (r"smallest model is equivalent to the original GPT",
                     r"largest model, which we call GPT-2")),
    ),
    (
        Requirement("GPT-2 将架构变更与原始 OpenAI GPT 建立来源联系", 4, 8,
                    (r"model largely follows the details of the OpenAI GPT model",)),
        Requirement("GPT-2 把归一化移到各子块输入并增加末端归一化", 4, 12,
                    (r"Layer normalization.*was moved to the input of each sub-block",
                     r"additional layer normalization was added after the final self-?attention block")),
    ),
    (
        Requirement("GPT-2 明确上下文长度由 512 增加至 1024 token", 4, 13,
                    (r"increase the context size from 512 to 1024 tokens",)),
    ),
)


def source_records(folder, gpt2):
    sources = {name: read_source(folder / name) for name in (*ATTENTION.values(), BERT, GPT1, GPT3)}
    if gpt2:
        sources[GPT2] = read_source(gpt2)
        if sources[GPT2][0]["id"] != GPT2_SHA256:
            raise ValueError("GPT-2 input does not match the pinned public course copy SHA-256")
    return sources


def check_hits(papers, sources):
    lookup = {doc["id"]: (name, doc, blocks) for name, (doc, blocks) in sources.items()}
    checked = []
    for paper in papers:
        for rank, hit in enumerate(paper["hits"], 1):
            canonical, doc, blocks = lookup[paper["id"]]
            anchor = hit["page"], hit["block"]
            quote_text = normalized(hit["text"])
            raw = normalized(blocks.get(anchor, ""))
            result = {
                "paper": canonical, "rank": rank, "page": anchor[0], "block": anchor[1],
                "verbatim": hit["id"] == doc["id"] and hit["name"] == doc["name"]
                and bool(quote_text) and quote_text in raw,
                "excerpt": hit["text"],
            }
            checked.append(result)
    return checked


def check_requirement(name, hits, sources, requirements):
    anchors = []
    if name in sources:
        for requirement in requirements:
            original = sources[name][1].get((requirement.page, requirement.block), "")
            if not requirement.matches(original):
                raise ValueError(f"Changed source {name} p{requirement.page}:{requirement.block}: {requirement.label}")
            for hit in hits:
                if (hit["paper"] == name and hit["verbatim"]
                    and (hit["page"], hit["block"]) == (requirement.page, requirement.block)
                    and requirement.matches(hit["excerpt"])):
                    anchors.append({k: hit[k] for k in ("page", "block", "rank")})
    return anchors


def requirements(identifier, names, hits, sources):
    if identifier <= 6:
        version = next(v for v, n in ATTENTION.items() if n == names[0])
        primary = attention_requirements(version)[identifier - 1]
        if identifier == 4:
            primary = (*primary,
                       Requirement("表 2 的 Transformer (big) 行", 8, 11,
                                   (r"^Transformer \(big\)\s+28\.4",)),
                       Requirement("表 2 的 EN-DE 栏", 8, 3, (r"^EN-DE\s+EN-FR",)))
        by_file = {names[0]: primary}
        if identifier == 6:
            by_file[BERT] = BERT_REQUIREMENTS
        return [
            {"criterion": item.label, "paper": name, "required": item.required,
             "expected": [{"page": item.page, "block": item.block}],
             "matches": check_requirement(name, hits, sources, (item,))}
            for name, items in by_file.items() for item in items
        ]

    results = []
    for name, (label, candidates) in GPT_REQUIREMENTS[identifier - 7].items():
        if name == GPT2 and name in sources:
            for item in GPT2_SUPPLEMENT[identifier - 7]:
                results.append({
                    "criterion": item.label, "paper": name, "required": True,
                    "expected": [{"page": item.page, "block": item.block}],
                    "source_available": True,
                    "matches": check_requirement(name, hits, sources, (item,)),
                })
            continue
        if name == GPT1 and GPT2 in sources:
            if identifier == 7:
                label, candidates = GPT1_MODEL.label, (GPT1_MODEL,)
            elif identifier == 8:
                label, candidates = GPT1_NORMALIZATION.label, (GPT1_NORMALIZATION,)
                results.append({
                    "criterion": GPT1_FIGURE.label, "paper": name, "required": True,
                    "expected": [{"page": GPT1_FIGURE.page, "block": GPT1_FIGURE.block}],
                    "source_available": True, "visual_gold_checked_manually": True,
                    "matches": check_requirement(name, hits, sources, (GPT1_FIGURE,)),
                })
        results.append({
            "criterion": label, "paper": name, "required": True,
            "expected": [{"page": item.page, "block": item.block} for item in candidates],
            "source_available": name in sources,
            "matches": check_requirement(name, hits, sources, candidates),
        })
    return results


def answer_status(identifier, answer, complete, criteria):
    if answer is None:
        return "missing"
    if not complete:
        return "unverified"
    clean = normalized(answer)
    if identifier == 1:
        passed = (re.search(r"第\s*6\s*位署名作者", clean)
                  and all(term in clean for term in ("Aidan N. Gomez", "University of Toronto"))
                  and ("Google Brain" not in clean or (
                      "†" in clean and "完成" in clean
                      and any(item["matches"] for item in criteria if not item["required"])
                  )))
    elif identifier == 2:
        passed = re.search(r"β1\s*=\s*0\.9\b", clean) and re.search(r"β2\s*=\s*0\.98\b", clean)
    elif identifier == 3:
        passed = re.search(r"(?<!\d)8\s*[块张个]?(?:\s+)?NVIDIA P100(?:\s+)?GPUs?", clean, re.I)
    elif identifier == 4:
        passed = all(term in answer for term in ("EN-DE", "Transformer (big)", "28.4", "ConvS2S Ensemble", "26.36", "2.04"))
    elif identifier == 5:
        passed = all(term in clean for term in (
            "Jimmy Lei Ba", "Jamie Ryan Kiros", "Geoffrey E Hinton", "2016",
        ))
    elif identifier == 6:
        passed = all(term in answer for term in ("BERT", "双向", "编码器", "省去", "独立解码器"))
    elif identifier == 7:
        passed = (re.search(r"GPT(?:-1)?[^(]*(?:\(第一代\))?\s*约\s*117M", clean)
                  and re.search(r"GPT-2 最大模型 1542M", clean)
                  and re.search(r"GPT-3 为 175 billion", clean)
                  and "等价" in clean)
    elif identifier == 8:
        passed = all(term in clean for term in (
            "GPT-2", "子块输入", "Pre-LN", "最后一个自注意力块后", "归一化", "前代", "架构图",
        ))
    elif identifier == 9:
        passed = (re.search(r"GPT(?:-1)?\(第一代\)512.*GPT-2 1024.*GPT-3 2048 tokens", clean)
                  and "训练序列长度" in clean)
    else:
        return "unverified"
    return "supported" if passed else "unverified"


def assert_displayed_evidence(container, hits):
    anchors = container.locator('a[href^="/pdf/"]')
    assert anchors.count() == len(hits)
    assert [anchors.nth(i).get_attribute("href") for i in range(len(hits))] == [
        f"/pdf/{hit['id']}#page={hit['page']}" for hit in hits
    ]
    assert [normalized(s) for s in container.locator("blockquote").all_text_contents()] == [
        normalized(hit["text"]) for hit in hits
    ]


def browser_request(browser, port, endpoint, values):
    page = browser.new_page()
    try:
        single = endpoint == "/api/followup"
        url = "/followup" if single else "/architecture"
        page.goto(f"http://127.0.0.1:{port}{url}")
        if single:
            page.select_option("#doc", values["id"])
            page.fill("#question", values["question"])
            submit = "#send"
        else:
            for digest in values["ids"]:
                page.check(f'input[value="{digest}"]')
            page.fill("#query", values["query"])
            submit = '#form button'
        with page.expect_response(lambda response: response.url.endswith(endpoint)) as pending:
            page.click(submit)
        response = pending.value
        if not response.ok:
            body = response.json()
            raise RuntimeError(f"{endpoint}: HTTP {response.status} {body.get('error','')} · {values.get('question',values.get('query',''))}")
        result = response.json()
        observed = response.request.post_data_json
        if single:
            turn = page.locator("#history section").last
            turn.wait_for()
            heading = turn.locator("h2").inner_text()
            assert heading == values["question"] and observed == {**values, "model_consent": False}
            assert (turn.locator("strong").text_content() if result.get("answer") else None) == result.get("answer")
            assert_displayed_evidence(turn, result["hits"])
        else:
            assert observed["query"] == values["query"] and set(observed["ids"]) == set(values["ids"])
            summary = page.locator("#summary")
            assert summary.inner_text() == (result["answer"] or result["gap"])
            for i, paper in enumerate(result["papers"]):
                article = page.locator(".papers article").nth(i)
                assert article.locator("h2").inner_text() == paper["name"]
                assert_displayed_evidence(article, paper["hits"])
        return result
    finally:
        page.close()


def verify_pdf_route(port, digest):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("GET", f"/pdf/{digest}")
        response = connection.getresponse()
        if response.status != 200 or response.headers.get_content_type() != "application/pdf":
            raise ValueError(f"Original PDF link failed for {digest}: HTTP {response.status}")
        pdf = response.read()
        if hashlib.sha256(pdf).hexdigest() != digest:
            raise ValueError(f"Original PDF bytes do not match the imported ID {digest}")
    finally:
        connection.close()


def run(folder, gpt2, browser, show_hits):
    sources = source_records(folder, gpt2)
    cases = []
    with workbench() as port:
        for name, (doc, _) in sources.items():
            path = gpt2 if name == GPT2 else folder / name
            recorded = call(port, "/api/import?name=" + quote(doc["name"]),
                            path.read_bytes(), "application/pdf")["doc"]
            assert recorded["id"] == doc["id"]

        queries = []
        if not gpt2:
            queries.extend((i, [name], query) for name in ATTENTION.values()
                           for i, query in enumerate(QUESTIONS[:5], 1))
            queries.extend((6, [name, BERT], QUESTIONS[5]) for name in ATTENTION.values())
        gpt_chain = [GPT1, GPT2, GPT3] if gpt2 else [GPT1, GPT3]
        for i, query in enumerate(GPT_QUESTIONS, 7):
            # Without GPT-2, use the two available GPT papers and keep the missing
            # direct GPT-2 source visible in the result for all three questions.
            names = [GPT1, GPT2] if i == 8 and gpt2 else gpt_chain
            queries.append((i, names, query))
        for number, names, query in queries:
            ids = [sources[name][0]["id"] for name in names]
            single = number <= 5
            endpoint = "/api/followup" if single else "/api/architecture"
            values = ({"id": ids[0], "question": query, "anchor": None} if single
                      else {"ids": ids, "query": query})
            response = (browser_request(browser, port, endpoint, values) if browser
                        else call(port, endpoint, values))
            papers = [{"id": ids[0], "hits": response["hits"]}] if single else response["papers"]
            hits = check_hits(papers, sources)
            criteria = requirements(number, names, hits, sources)
            required = [item for item in criteria if item["required"]]
            found = sum(bool(item["matches"]) for item in required)
            all_supported = found == len(required) and all(item["verbatim"] for item in hits)
            if number == 9 and not gpt2:
                # GPT-1 gives training sequence length. The supplement has the
                # original GPT-2 comparison explicitly stating the 512 baseline.
                all_supported = False
            result = {
                "number": number, "question": query, "selected": names, "endpoint": endpoint,
                "evidence": "complete" if all_supported else "partial" if found else "missing",
                "answer": response.get("answer"), "answer_status": answer_status(number, response.get("answer"), all_supported, criteria),
                "gap": response.get("gap"), "criteria": criteria,
                "hits": hits if show_hits else [{k: v for k, v in hit.items() if k != "excerpt"} for hit in hits],
            }
            cases.append(result)
        for name in {hit["paper"] for case in cases for hit in case["hits"]}:
            verify_pdf_route(port, sources[name][0]["id"])

    return {
        "transport": "browser UI → local HTTP" if browser else "local HTTP",
        "gpt2_original_present": GPT2 in sources,
        "sources": {name: {"file": doc["name"], "sha256": doc["id"]} for name, (doc, _) in sources.items()},
        "evidence_summary": dict(Counter(case["evidence"] for case in cases)),
        "answer_summary": dict(Counter(case["answer_status"] for case in cases)),
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--gpt2", type=Path, help="Run the three GPT questions with the pinned course copy")
    parser.add_argument("--chromium", type=Path, help="Drive actual UI in Playwright using this Chromium binary")
    parser.add_argument("--show-hits", action="store_true")
    args = parser.parse_args()
    if args.chromium:
        from playwright.sync_api import sync_playwright
        context = sync_playwright()
    else:
        context = nullcontext()
    with context as playwright:
        browser = (playwright.chromium.launch(executable_path=str(args.chromium), args=["--no-sandbox"])
                   if playwright else None)
        try:
            report = run(args.pdf_dir, args.gpt2, browser, args.show_hits)
        finally:
            if browser:
                browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
