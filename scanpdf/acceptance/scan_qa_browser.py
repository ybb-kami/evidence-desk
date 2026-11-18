"""Verify an OCR table answer and the actual cell highlighted after clicking its citation."""
import argparse
import hashlib
import json
from pathlib import Path

from corpus import call, workbench

SCAN_SHA = "64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d"


def run(source, browser):
    data = source.read_bytes()
    assert hashlib.sha256(data).hexdigest() == SCAN_SHA
    with workbench() as port:
        doc = call(port, "/api/import?name=scan.pdf", data, "application/pdf")["doc"]
        recognized = call(port, "/api/scan", {"id": doc["id"], "page": 8})["page"]
        table = next(region for region in recognized["regions"] if region["type"] == "table")
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/followup")
        page.select_option("#doc", doc["id"])
        page.fill("#question", "扫描版表2中big模型的英德、英法BLEU各是多少？")
        with page.expect_response(lambda r: r.url.endswith("/api/followup")) as response:
            page.click("#send")
        body = response.value.json()
        assert response.value.ok and body.get("answer_method") == "scan_table", body
        cells = body["table_cells"]
        assert {cell["value"] for cell in cells} == {"28.4", "41.0"}, cells
        for cell in cells:
            link = page.locator(f'#history a[href*="row={cell["row"]}&column={cell["column"]}"]')
            with page.expect_popup() as popup:
                link.click()
            scan = popup.value
            scan.locator("#table-cell").wait_for()
            assert scan.locator("#table-cell").input_value() == f'{cell["row"]},{cell["column"]}'
            assert scan.locator("#page").input_value() == "8"
            assert scan.locator("#mark").is_visible()
            assert cell["value"] in scan.locator("#detail").inner_text()
            linked = next(c for c in table["cells"] if (c["row"], c["column"]) == (cell["row"], cell["column"]))
            assert linked["bbox_pt"] == cell["bbox_pt"]
            rect = scan.locator("#mark").evaluate(
                "(n) => [parseFloat(n.style.left),parseFloat(n.style.top),parseFloat(n.style.width),parseFloat(n.style.height)]"
            )
            x0, y0, x1, y1 = cell["bbox_pt"]
            expected = [100*x0/recognized["width_pt"], 100*y0/recognized["height_pt"],
                        100*(x1-x0)/recognized["width_pt"], 100*(y1-y0)/recognized["height_pt"]]
            assert all(abs(a-b) < .001 for a,b in zip(expected, rect)), (rect, expected)
            scan.close()
        page.close()
    return {"source_sha256": SCAN_SHA, "answer": body["answer"], "physical_page": 8,
            "cells": cells, "transport": "Chromium UI, actual OCR, cell highlight checked"}


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan", type=Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    options = parser.parse_args()
    with sync_playwright() as playwright:
        with playwright.chromium.launch(executable_path=options.chromium) as browser:
            print(json.dumps(run(options.scan, browser), ensure_ascii=False, indent=2))
