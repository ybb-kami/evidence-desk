"""Check source, bilingual reading and scoped followup against two versions with the same name."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import expect, sync_playwright
import pymupdf

from corpus import V1, V7, call, workbench


def run(pdf_dir, browser, width=1200):
    data = (pdf_dir / V1).read_bytes()
    identity = sha256(data).hexdigest()
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        physical = 7
        block = next(row[5] + 1 for row in pdf[physical - 1].get_text("blocks") if "P100" in row[4])
    scope = {"id": identity, "page": physical, "block": block, "part": 1}

    with workbench() as port:
        base = f"http://127.0.0.1:{port}"
        # Identical display names deliberately make file selection by name unreliable.
        v1 = call(port, "/api/import?name=paper.pdf", data, "application/pdf")["doc"]
        v7 = call(port, "/api/import?name=paper.pdf", (pdf_dir / V7).read_bytes(), "application/pdf")["doc"]
        assert identity == v1["id"] != v7["id"]
        evidence = call(port, "/api/source?" + urlencode(scope))
        start = evidence["text"].index("P100")
        source_url = base + "/source?" + urlencode(dict(scope, start=start, end=start + 4))
        page = browser.new_page(viewport={"width": width, "height": 840})
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        try:
            page.goto(source_url)
            expect(page.locator("#excerpt mark")).to_have_text("P100")
            page.locator("#context summary").click()
            links = page.locator("#neighbors li a")
            expect(links).to_have_count(len(evidence["neighbors"]))
            assert links.count() > 0
            links.first.click()
            neighbor = evidence["neighbors"][0]
            neighbor_query = dict(scope, block=neighbor["block"], part=neighbor["part"])
            expected = call(port, "/api/source?" + urlencode(neighbor_query))
            expect(page.locator("#identity")).to_contain_text(identity)
            expect(page.locator("#excerpt")).to_have_text(expected["text"])
            page.goto(source_url)
            page.locator("#bilingual").click()
            expect(page.locator("#doc")).to_have_value(identity)
            expect(page.locator(".source-card.current-passage")).to_have_attribute("data-anchor", f"p{physical}-b{block}")
            expect(page.locator(".source-card.current-passage")).to_contain_text("P100")
            bilingual_url = page.url
            page.goto(source_url)
            page.locator("#ask-here").click()
            expect(page.locator("#doc")).to_have_value(identity)
            expect(page.locator("#scope")).to_contain_text(f"物理页 {physical} · 文本块 {block}")
            ask_url = page.url
            page.locator("#question").fill("训练使用多少块 GPU？")
            with page.expect_response(lambda response: response.url.endswith("/api/followup")) as received:
                page.locator("#send").click()
            response = received.value
            assert response.ok and response.request.post_data_json["anchor"] == [physical, block, 1]
            hits = response.json()["hits"]
            assert hits and all((row["id"], row["page"], row["block"]) == (identity, physical, block) for row in hits)

            call(port, "/api/archive", {"id": identity})
            page.goto(source_url)
            expect(page.locator("#excerpt mark")).to_have_text("P100")
            for url in (ask_url, bilingual_url):
                page.goto(url)
                expect(page.get_by_role("status")).to_contain_text("不在活动文库")
                expect(page.locator("#doc")).to_have_value("")
            page.goto(ask_url.replace(f"block={block}", "block=99999").replace(identity, v7["id"]))
            expect(page.locator("#scope")).to_contain_text("未找到")
            expect(page.locator("#send")).to_be_disabled()
            assert not failures, failures
        finally:
            page.close()
    return {"viewport_width": width, "versions": [identity, v7["id"]], "identical_display_name": "paper.pdf",
            "physical_page": physical, "block": block, "quoted_span": "P100",
            "neighbor_reopened": neighbor_query, "scoped_followup_hits": len(hits),
            "bilingual_block_focused": True, "archived_source_readable": True,
            "unavailable_version_fallbacks": 0, "invalid_block_submission_disabled": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    options = parser.parse_args()
    with sync_playwright() as playwright, playwright.chromium.launch(executable_path=options.chromium) as browser:
        print(json.dumps([run(options.pdf_dir, browser, width) for width in (1200, 390)], ensure_ascii=False, indent=2))
