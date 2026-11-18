"""Verify official JOSS metadata, publisher download, and an original PDF page."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import pymupdf
from playwright.sync_api import sync_playwright

from corpus import ROOT, call, workbench

sys.path.insert(0, str(ROOT / "scanpdf"))
from discovery import joss


def run(args):
    with TemporaryDirectory(prefix="evidence-joss-catalog-") as folder:
        metadata = [joss.cache_record(doi, Path(folder)) for doi in args.seed]
        previous = os.environ.get("EVIDENCE_JOSS_CATALOG")
        os.environ["EVIDENCE_JOSS_CATALOG"] = folder
        try:
            with workbench() as port, sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium, args=["--no-sandbox"])
                try:
                    return check_ui(port, browser, args, metadata)
                finally:
                    browser.close()
        finally:
            if previous is None:
                os.environ.pop("EVIDENCE_JOSS_CATALOG", None)
            else:
                os.environ["EVIDENCE_JOSS_CATALOG"] = previous


def check_ui(port, browser, args, metadata):
    base = f"http://127.0.0.1:{port}"
    page = browser.new_page()
    page.goto(base + "/download")
    page.select_option("#search-provider", "joss")
    page.fill("#query", args.query)
    with page.expect_response(lambda r: r.url.endswith("/api/download/joss/search")) as found:
        page.get_by_role("button", name="查找题录").click()
    catalog = found.value.json()
    assert found.value.status == 200 and catalog["cached"] and catalog["catalog_count"] == len(metadata), catalog
    item = next(record for record in catalog["items"] if record["doi"] == args.expected_doi)
    assert item in metadata and item["license"] == joss.LICENSE, item
    match = page.locator("#matches li", has_text=args.expected_doi)
    match.get_by_role("button", name="选择开放 PDF").click()
    assert call(port, "/api/library")["docs"] == []
    assert not page.is_checked("#acl-consent")
    page.check("#acl-consent")
    with page.expect_response(lambda r: r.url.endswith("/api/download/joss") and r.request.method == "POST") as response:
        page.get_by_role("button", name="下载所选 PDF 并导入").click()
    imported = response.value.json()
    assert response.value.status == 200, imported
    digest = imported["doc"]["id"]
    assert imported["source"]["sha256"] == digest
    assert imported["source"]["doi"] == item["doi"] and not imported["existing"]
    assert call(port, "/api/library")["docs"] == [imported["doc"]]
    assert call(port, "/api/download/joss")["receipts"] == [imported["source"] | {"archived": False}]

    expected_link = f"/pdf/{digest}#page=1"
    source_link = page.get_by_role("link", name="打开原件第 1 页", exact=True)
    source_link.wait_for()
    assert source_link.get_attribute("href") == expected_link
    original = page.request.get(base + expected_link)
    assert original.status == 200 and original.headers["content-type"] == "application/pdf"
    assert sha256(original.body()).hexdigest() == digest
    with pymupdf.open(stream=original.body(), filetype="pdf") as pdf:
        assert len(pdf) == imported["doc"]["pages"]
        assert 1 <= args.page <= len(pdf)
        requested_page = pdf[args.page - 1]
        anchor = " ".join(args.anchor.split()).casefold()
        assert anchor in " ".join(requested_page.get_text().split()).casefold(), args.anchor
        scale = min(1.8, 1600 / max(requested_page.rect.width, requested_page.rect.height))
        image = requested_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    image_url = f"/scan-image/{digest}?page={args.page}"
    rendered = page.request.get(base + image_url)
    assert rendered.status == 200 and rendered.headers["content-type"] == "image/png"
    assert image == rendered.body()
    return {"title_query": args.query, "catalog_size": catalog["catalog_count"], "catalog_lookup": "local cached metadata",
            "catalog_upstream_http_status": 200, "metadata": item, "download_upstream_http_status": 200,
            "source": imported["source"], "original_bytes": len(original.body()), "pages": imported["doc"]["pages"],
            "original_link": expected_link, "verified_page": args.page, "anchor": args.anchor,
            "verified_page_image_sha256": sha256(image).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", nargs="+", required=True, help="Public JOSS DOIs used to prepare the local catalog")
    parser.add_argument("--query", required=True)
    parser.add_argument("--expected-doi", required=True)
    parser.add_argument("--anchor", required=True, help="An independently checked text anchor on the requested physical page")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))
