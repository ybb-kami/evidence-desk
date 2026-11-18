"""Chromium acceptance with a Search fixture and a real JOSS publication."""

import argparse
from contextlib import closing, contextmanager
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import runpy
import select
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
DOI = "10.21105/joss.03021"
TERMS = "seaborn statistical data visualization"
OFFICIAL = "https://joss.theoj.org/papers/" + DOI
LOOKALIKE = "https://example.org/" + DOI
PDF_SHA256 = "163e1f3a1c502b0cc9005144811a69e03104cf009b75b028cba6dd6b725fef0e"
PATH = "/api/download/candidate"


def fixture_server(data):
    from discovery import deepseek_search

    def open_fixture(request):
        operation = request.full_url.rsplit("/", 1)[1]
        assert request.full_url == "https://api.deepseek.com/v1/tools/" + operation
        with (data / "fixture.ndjson").open("a", encoding="utf-8") as log:
            print(json.dumps({"operation": operation, "body": json.loads(request.data)}), file=log)
        if operation == "fetch":
            raise URLError("isolated fetch failure")
        assert operation == "search"
        response = io.BytesIO(json.dumps({"results": [
            {"url": OFFICIAL, "title": "Seaborn publication", "snippet": "Public title match", "date": None},
            {"url": LOOKALIKE, "title": "Unaffiliated result", "snippet": "Contains a DOI string", "date": None},
        ]}).encode())
        response.status = 200
        return response

    deepseek_search._open = open_fixture
    sys.argv = ["workbench.py", "--port", "0", "--data", str(data), "--enable-deepseek"]
    runpy.run_module("workbench", run_name="__main__")


@contextmanager
def serve(repo, data):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(repo), str(repo / "scanpdf"))),
               EVIDENCE_JOSS_CATALOG=str(data / "catalog"),
               EVIDENCE_BUDGET_DB=str(data / "isolated-budget.sqlite3"),
               DEEPSEEK_API_KEY="fixture-handoff-key", DEEPSEEK_SEARCH_USD_PER_CALL="0.001",
               DEEPSEEK_FETCH_USD_PER_CALL="0.001", PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--fixture-server", str(data)],
                            cwd=repo, env=env, stdout=subprocess.PIPE, text=True)
    try:
        if not select.select([proc.stdout], [], [], 25)[0]:
            raise RuntimeError("Workbench did not start")
        base = proc.stdout.readline().strip()
        assert urlsplit(base).hostname == "127.0.0.1" and urlsplit(base).port
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        proc.stdout.close()


def check(page, base, data, *, before):
    import pymupdf

    requests = []
    page.context.on("request", lambda event: requests.append((event.method, urlsplit(event.url).path)))
    page.goto(base + "/copilot")
    page.locator("#question").fill("Find the official publication for the given public title.")
    page.locator("#web-terms").fill(TERMS)
    page.locator("#fetch-first").check()
    with page.expect_response(lambda response: response.url.endswith("/api/copilot")) as stream:
        page.locator("#run").click()
    page.locator("#result").wait_for()
    events = [json.loads(line) for line in stream.value.text().splitlines()]
    final = events[-1]
    assert final["event"] == "final" and final["web"]["state"] == "partial" and final["reports"] == []
    assert not page.locator('#reports a[href^="/source?"]').count()
    assert page.get_by_role("link", name="Seaborn publication").get_attribute("href") == OFFICIAL
    links = page.locator('#reports a[href^="/download?"]')
    record = {"search_transport": "isolated_fixture", "search_state": final["web"]["state"],
              "web_items": len(final["web"]["items"]), "discovery_links": links.count()}
    if not before:
        assert links.count() == 1 and DOI in links.first.inner_text()
        with page.expect_popup() as popup:
            links.first.click()
        discovery = popup.value
        discovery.get_by_text("本机尚无此 DOI 的出版题录。").wait_for()
        assert ("POST", PATH) not in requests and ("POST", "/api/download/joss") not in requests
        assert discovery.request.get(base + "/api/library").json()["docs"] == []
        discovery.locator("#candidate-consent").check()
        with discovery.expect_response(lambda res: res.url.endswith(PATH) and res.request.method == "POST") as verified:
            discovery.get_by_role("button", name="核对 JOSS 题录").click()
        result = verified.value.json()
        assert verified.value.ok, result
        metadata = result["publication"]
        local_xml = (data / "catalog" / "joss.03021.xml").read_bytes()
        assert metadata["doi"] == DOI and sha256(local_xml).hexdigest() == metadata["metadata_sha256"]
        assert "seaborn" in metadata["title"].lower() and "creativecommons.org/licenses/by/4.0" in metadata["license"]
        discovery.get_by_role("link", name="转到出版方下载确认").wait_for()
        assert not discovery.locator("#acl-consent").is_checked()
        assert discovery.request.get(base + "/api/library").json()["docs"] == []
        discovery.locator("#acl-consent").check()
        with discovery.expect_response(lambda res: res.url.endswith("/api/download/joss") and res.request.method == "POST") as imported:
            discovery.get_by_role("button", name="下载所选 PDF 并导入").click()
        document = imported.value.json()
        assert imported.value.ok, document
        link = discovery.get_by_role("link", name="打开原件第 1 页")
        original_path = link.get_attribute("href")
        original = discovery.request.get(base + original_path)
        assert original.ok and sha256(original.body()).hexdigest() == PDF_SHA256 == document["doc"]["id"]
        assert document["source"]["sha256"] == PDF_SHA256 and document["source"]["doi"] == DOI
        assert original_path.endswith("#page=1")
        with pymupdf.open(stream=original.body(), filetype="pdf") as pdf:
            anchor = "Center for Neural Science, New York University"
            original_page = pdf[0]
            assert anchor in " ".join(original_page.get_text().split())
            scale = min(1.8, 1600 / max(original_page.rect.width, original_page.rect.height))
            png = original_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
            rendered = discovery.request.get(f"{base}/scan-image/{PDF_SHA256}?page=1")
            assert rendered.ok and rendered.body() == png
            record.update(metadata=metadata, publication_transport="real_official_repository",
                          original_link=original_path, physical_page=1, pages=len(pdf), page_anchor=anchor,
                          pdf_sha256=PDF_SHA256, received_bytes=len(original.body()),
                          page_image_sha256=sha256(png).hexdigest())
    else:
        assert not links.count()
    record["fixture_requests"] = [json.loads(line) for line in (data / "fixture.ndjson").read_text().splitlines()]
    assert record["fixture_requests"] == [
        {"operation": "search", "body": {"query": TERMS, "count": 5, "need_content": False}},
        {"operation": "fetch", "body": {"url": OFFICIAL}},
    ]
    with closing(sqlite3.connect(data / "isolated-budget.sqlite3")) as db:
        record["fixture_reservations"] = dict(db.execute("SELECT kind, COUNT(*) FROM paid_reservations GROUP BY kind"))
    assert record["fixture_reservations"] == {"fetch": 1, "search": 1}
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--before", action="store_true")
    parser.add_argument("--fixture-server", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.fixture_server:
        fixture_server(args.fixture_server)
    else:
        from playwright.sync_api import sync_playwright

        with TemporaryDirectory(prefix="evidence-discovery-handoff-") as folder:
            data = Path(folder)
            with serve(args.repo.resolve(), data) as address, sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium, args=["--no-sandbox"])
                page = browser.new_page()
                print(json.dumps(check(page, address, data, before=args.before), ensure_ascii=False, indent=2))
                browser.close()
