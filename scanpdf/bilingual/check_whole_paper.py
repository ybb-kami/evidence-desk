"""Translate an entire supplied PDF in Chromium, then audit the actual downloaded HTML."""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

import fitz
from check_browser import add, open_doc
from playwright.sync_api import expect, sync_playwright


def perform(base, pdf, output, chromium):
    data = pdf.read_bytes()
    with fitz.open(stream=data, filetype="pdf") as original:
        page_count = len(original)
        visible_blocks = {
            i: {
                b[5] + 1: "".join(b[4].split())
                for b in page.get_text("blocks")
                if b[6] == 0 and b[4].strip()
            }
            for i, page in enumerate(original, 1)
        }
    docid = add(base, pdf.name, data)
    assert docid == hashlib.sha256(data).hexdigest()
    directions = json.load(urllib.request.urlopen(base + "/api/bilingual/models"))[
        "directions"
    ]
    assert "en-zh" in directions
    source = {}
    references = set()
    for number in range(1, page_count + 1):
        record = json.load(
            urllib.request.urlopen(f"{base}/api/bilingual/page/{docid}?page={number}")
        )
        source[number] = {r["block"]: r["text"] for r in record["segments"]}
        assert visible_blocks[number] == {
            block: "".join(text.split()) for block, text in source[number].items()
        }
        references.update(
            (number, r["block"]) for r in record["segments"] if r.get("reference")
        )
    count = sum(map(len, source.values()))
    print("Source pages and blocks", page_count, count, flush=True)
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chromium, args=["--no-sandbox"]
        )
        page = browser.new_page()
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda req: (
                requests.append(req.post_data)
                if req.url.endswith("/api/bilingual/translate")
                else None
            ),
        )
        open_doc(page, base, docid, 1)
        started = time.monotonic()
        page.locator("#all").click()
        last = ""
        while not page.locator("#all").is_enabled():
            page.wait_for_timeout(2000)
            msg = page.locator("#status").inner_text()
            if msg != last:
                print(round(time.monotonic() - started, 1), msg, flush=True)
                last = msg
        inference = time.monotonic() - started
        expect(page.locator("#status")).to_have_text(
            f"本次已处理 {count} 段。", timeout=1000
        )
        expect(page.locator("#page")).to_have_value(str(page_count))
        with page.expect_download(timeout=180000) as download:
            page.locator("#export").click()
        with tempfile.TemporaryDirectory() as folder:
            html = pathlib.Path(folder) / "actual-download.html"
            download.value.save_as(html)
            contents = html.read_bytes()
        downloaded = time.monotonic() - started
        offline = browser.new_context(
            offline=True, viewport={"width": 390, "height": 780}
        )
        reader = offline.new_page()
        url = reader.evaluate(
            "(html) => URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}))",
            contents.decode(),
        )
        reader.goto(url)
        actual = reader.locator(
            "article"
        ).evaluate_all("""nodes => nodes.map(n => [n.querySelector('h2').textContent,
            n.querySelector('p[lang=en]').textContent,n.querySelector('p[lang=zh]').textContent,n.dataset.treatment])""")
        assert reader.locator("nav a").count() == sum(
            bool(values) for values in source.values()
        )
        assert reader.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        offline.close()
        browser.close()
    stats = {
        number: {
            "segments": len(blocks),
            "identical": 0,
            "with_han": 0,
            "machine": 0,
            "reference": 0,
            "preserved": 0,
            "table": 0,
        }
        for number, blocks in source.items()
    }
    seen = set()
    for header, orig, tran, treatment in actual:
        match = re.fullmatch(r"物理第 (\d+) 页 · 块 (\d+) · 整段", header)
        assert match, header
        num, block = map(int, match.groups())
        assert (num, block) not in seen
        seen.add((num, block))
        assert orig == source[num][block], (num, block)
        assert tran.strip(), (num, block)
        assert treatment in {"machine", "preserved", "reference", "table"}
        assert (treatment == "reference") == ((num, block) in references)
        if treatment in {"reference", "preserved", "table"}:
            assert orig == tran, (num, block)
        stats[num][treatment] += 1
        stats[num]["identical"] += orig == tran
        stats[num]["with_han"] += bool(re.search(r"[\u3400-\u9fff]", tran))
    assert len(seen) == count
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as target:
        target.write(contents)
    metrics = {
        "source_sha256": docid,
        "export_sha256": hashlib.sha256(contents).hexdigest(),
        "rows": count,
        "translate_seconds": round(inference, 2),
        "through_download_seconds": round(downloaded, 2),
        "local_model_requests": len(requests),
        "unique_model_inputs": len(set(requests)),
        "pages": stats,
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    with (
        tempfile.TemporaryDirectory() as folder,
        subprocess.Popen(
            [
                sys.executable,
                str(pathlib.Path(__file__).resolve().parents[1] / "workbench.py"),
                "--port",
                "0",
                "--data",
                folder,
            ],
            stdout=subprocess.PIPE,
            text=True,
        ) as server,
    ):
        try:
            perform(
                server.stdout.readline().strip(),
                args.pdf,
                args.output,
                args.chromium,
            )
        finally:
            server.terminate()
