"""Check false numerical premises against an actual scanned table and its source image."""

import argparse
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path

from corpus import call, workbench


CASES = (
    ("扫描表2的big英德BLEU为什么是284？", "28.4", "284"),
    ("Why does Table 2 report 284 BLEU for Transformer (big) in English-to-German?", "28.4", "284"),
    ("扫描表2英德方向大模型的 BLEU 被识别为２８４，能核对原表吗？", "28.4", "284"),
    ("Was the English-to-German BLEU for Transformer (big) in Table 2 284?", "28.4", "284"),
    ("Why does scanned Table 2 report 273 as the English-to-German BLEU for Transformer (base model)?", "27.3", "273"),
    ("扫描表2第12行第2列的值为什么是284？", "28.4", "284"),
    ("Was the English-to-German BLEU for Transformer (big) in Table 2 28.40?", "28.4", None),
    ("Why does scan Table 2 report 284 BLEU for Transformer (big)?", None, None),
    ("Why does scan Table 2 report 284 BLEU for Transformer in English-to-German?", None, None),
    ("Why does scan Table 2 report 380 BLEU for ByteNet in English-to-French?", None, None),
    ("Why does scan Table 2 report 284 BLEU for Transformer (big) in English-to-Portuguese?", None, None),
    ("Why does scan Table 2 report 284% BLEU for Transformer (big) in English-to-German?", None, None),
    ("扫描表2的big英德BLEU为什么是28.4？", None, None),
)


def browser_source(port, browser, sha, cell, original):
    page = browser.new_page()
    try:
        page.goto(f"http://127.0.0.1:{port}/followup")
        page.select_option("#doc", sha)
        page.fill("#question", CASES[0][0])
        with page.expect_response(lambda response: response.url.endswith("/api/followup")):
            page.click("#send")
        href = f'row={cell["row"]}&column={cell["column"]}'
        with page.expect_popup() as shown:
            page.locator(f'#history a[href*="{href}"]').click()
        source = shown.value
        source.locator("#mark").wait_for()
        assert source.locator("#table-cell").input_value() == f'{cell["row"]},{cell["column"]}'
        assert source.locator("#page").input_value() == str(cell["page"])
        assert cell["value"] in source.locator("#detail").inner_text()
        rect = source.locator("#mark").evaluate(
            "n => ['left', 'top', 'width', 'height'].map(key => parseFloat(n.style[key]))"
        )
        x0, y0, x1, y1 = cell["bbox_pt"]
        width, height = original["width_pt"], original["height_pt"]
        expected = [100*x0/width, 100*y0/height, 100*(x1-x0)/width, 100*(y1-y0)/height]
        assert all(abs(actual - wanted) < .001 for actual, wanted in zip(rect, expected)), (rect, expected)
        return {"region": cell["region"], "highlight_percent": rect}
    finally:
        page.context.close()


def run(source, browser=None):
    data = source.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    with workbench() as port:
        doc = call(port, "/api/import?name=source-scan.pdf", data, "application/pdf")["doc"]
        assert doc["id"] == sha
        original = call(port, "/api/scan", {"id": sha, "page": 8})["page"]
        table = next(region for region in original["regions"] if region["type"] == "table")
        results = []
        for question, value, stated in CASES:
            result = call(port, "/api/followup", {"id": sha, "question": question, "anchor": None})
            if value is None:
                assert not result["answer"] and result["gap"] and "premise_check" not in result, result
            else:
                assert result["answer_method"] == "scan_table" and not result["gap"], result
                cell, = result["table_cells"]
                assert (cell["source"], cell["page"], cell["table"], cell["value"]) == ("ocr", 8, 2, value), cell
                matched = next(c for c in table["cells"] if (c["row"], c["column"]) == (cell["row"], cell["column"]))
                assert (cell["region"], cell["bbox_pt"], cell["text"]) == (table["id"], matched["bbox_pt"], matched["text"])
                assert any(hit["page"] == 8 and "Table 2:" in hit["text"] for hit in result["hits"][:5]), result
                if stated:
                    assert result["premise_check"] == {"status": "contradicted", "stated": stated, "reported": value}
                else:
                    assert "premise_check" not in result
            results.append({"question": question, "answer": result["answer"], "gap": result["gap"],
                            "premise_check": result.get("premise_check"), "table_cells": result.get("table_cells", []),
                            "top_evidence": [[hit["page"], hit["block"]] for hit in result["hits"][:5]]})
        connection = HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("GET", "/pdf/" + sha)
            response = connection.getresponse()
            assert response.status == 200 and response.read() == data
        finally:
            connection.close()
        browser_check = browser_source(port, browser, sha, results[0]["table_cells"][0], original) if browser else None
    return {"source_sha256": sha, "original_bytes_verified": True, "browser": browser_check, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan", type=Path)
    parser.add_argument("--chromium", help="Optional Chromium executable for visible source highlight verification")
    args = parser.parse_args()
    if args.chromium:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            with playwright.chromium.launch(executable_path=args.chromium) as browser:
                result = run(args.scan, browser)
    else:
        result = run(args.scan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
