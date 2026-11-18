"""Audit scoped comparison through the browser against local PDFs and original cells."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import quote

import pymupdf

from corpus import call, workbench
from official_http import assert_displayed_evidence, verify_pdf_route
from official_questions import normalized


def sample(value):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=420, height=480)
        page.insert_text((42, 68), "Table 1: Accuracy on the CrossBench test split.")
        for x in (40, 180, 280):
            page.draw_line((x, 84), (x, 174))
        for y in (84, 114, 144, 174):
            page.draw_line((40, y), (280, y))
        rows = (("System", "Accuracy (%)"), ("Shared baseline", value), ("Other system", "78.0%"))
        for y, (name, score) in zip((102, 132, 162), rows):
            page.insert_text((46, y), name)
            page.insert_text((195, y), score)
        return pdf.tobytes()


def check_original(data, hit):
    assert sha256(data).hexdigest() == hit["id"]
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        page = pdf[hit["page"] - 1]
        rows = page.get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS, sort=True)
        raw = next(row[4] for row in rows if row[5] + 1 == hit["block"] and row[6] == 0)
    assert normalized(hit["text"]) and normalized(hit["text"]) in normalized(raw)


def browser_comparison(browser, port, sources, ids, question):
    tab = browser.new_page()
    try:
        tab.goto(f"http://127.0.0.1:{port}/architecture")
        for docid in ids:
            tab.check(f'input[value="{docid}"]')
        tab.fill("#query", question)
        with tab.expect_response(lambda reply: reply.url.endswith("/api/architecture")) as response:
            tab.click("#form button")
        assert response.value.ok, response.value.text()
        submitted = response.value.request.post_data_json
        assert submitted["query"] == question and set(submitted["ids"]) == set(ids)
        result = response.value.json()
        assert [paper["id"] for paper in result["papers"]] == submitted["ids"]
        assert tab.locator("#summary").inner_text() == (result["answer"] or result["gap"])
        for index, paper in enumerate(result["papers"]):
            pane = tab.locator(".papers article").nth(index)
            assert pane.locator("h2").inner_text() == paper["name"]
            if paper.get("answer"):
                assert pane.locator(".paper-answer").inner_text() == paper["answer"]
            else:
                assert pane.locator(".gap").inner_text() == paper["gap"]
            assert_displayed_evidence(pane, paper["hits"])
            for hit in paper["hits"]:
                assert hit["id"] == paper["id"]
                check_original(sources[paper["id"]], hit)
            for cell in paper.get("table_cells", ()):
                link = pane.locator(f'a[href*="id={paper["id"]}"][href*="region={cell["region"]}&row={cell["row"]}&column={cell["column"]}"]')
                with tab.expect_popup() as opened:
                    link.click()
                locator = opened.value
                locator.locator("#overlay .region").wait_for()
                assert locator.locator("#excerpt").inner_text().replace(" ", "") == cell["value"].replace(" ", "")
                assert locator.locator("#pdf").get_attribute("href") == f'/pdf/{paper["id"]}#page={cell["page"]}'
                locator.close()
        return {"question": question, "answer": result["answer"], "gap": result["gap"], "papers": [
            {"name": paper["name"], "id": paper["id"], "answer": paper["answer"], "gap": paper["gap"],
             "anchors": [[hit["page"], hit["block"], hit["part"]] for hit in paper["hits"]],
             "cells": [[cell["page"], cell["row"], cell["column"], cell["value"]]
                       for cell in paper.get("table_cells", ())]}
            for paper in result["papers"]]}
    finally:
        tab.close()


def run(folder, browser):
    inputs = {name: (folder / name).read_bytes()
              for name in ("1706.03762v1.pdf", "1706.03762v7.pdf", "N19-1423.pdf")}
    inputs.update({"sample-alpha.pdf": sample("83.5%"), "sample-beta.pdf": sample("80.0%")})
    with workbench() as port:
        ids, sources = {}, {}
        for name, data in inputs.items():
            doc = call(port, "/api/import?name=" + quote(name), data, "application/pdf")["doc"]
            assert doc["id"] == sha256(data).hexdigest()
            ids[name] = doc["id"]
            sources[doc["id"]] = data
        cases = (
            (["1706.03762v1.pdf", "1706.03762v7.pdf"], "所选两篇论文的 Transformer (big) 在表 2 的英法 BLEU 各是多少？"),
            (["1706.03762v7.pdf", "N19-1423.pdf"], "这篇论文中提到的 Transformer 架构，在训练 Base 模型时使用了多少个 GPU？具体是什么型号？"),
            (["sample-alpha.pdf", "sample-beta.pdf"], "在表1中，Shared baseline 的准确率分别是多少？相差多少？"),
        )
        results = [browser_comparison(browser, port, sources, [ids[name] for name in names], question)
                   for names, question in cases]
        for docid in sources:
            verify_pdf_route(port, docid)
    versions = {paper["name"]: paper for paper in results[0]["papers"]}
    assert "41.0" in versions["1706.03762v1.pdf"]["answer"]
    assert "41.8" in versions["1706.03762v7.pdf"]["answer"]
    partial = {paper["name"]: paper for paper in results[1]["papers"]}
    assert "8 块 NVIDIA P100 GPU" in partial["1706.03762v7.pdf"]["answer"]
    assert partial["N19-1423.pdf"]["answer"] is None and partial["N19-1423.pdf"]["gap"]
    assert "3.5个百分点" in results[2]["answer"]
    return {"transport": "Chromium → local HTTP → PDF source locator", "cases": results}


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    with sync_playwright() as playwright:
        with playwright.chromium.launch(executable_path=args.chromium) as browser:
            print(json.dumps(run(args.pdf_dir, browser), ensure_ascii=False, indent=2))
