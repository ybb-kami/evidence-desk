"""Replay actual figure detection, retained OCR anchors and version-aware scan links."""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.parse import quote, urlencode

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import workbench
from source_locator import ocr_paragraphs

from corpus import call
from figure_regions import ATTENTION, FLASH, LANDMARKS, check_source, raster_page


@contextmanager
def serve(store):
    with ThreadingHTTPServer(("127.0.0.1", 0), workbench.Handler) as server:
        server.library = store
        server.enable_model = False
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


def configured_page(browser, port, docid, number):
    page = browser.new_page()
    page.goto(f"http://127.0.0.1:{port}/scan?" + urlencode({"id": docid, "page": number}))
    return page


def upload(port, source):
    return call(port, "/api/import?name=" + quote(source.name), source.read_bytes(), "application/pdf")["doc"]


def scan_info(port, docid, number):
    return call(port, f"/api/scan/{docid}?page={number}")


def trigger(page, button):
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/scan"),
                              timeout=100_000) as response:
        page.locator(button).click()
    result = response.value.json()
    assert response.value.ok, result
    page.wait_for_function("document.querySelector('#preview').naturalWidth > 0")
    return result


def coordinate_check(page, region, frame, field="bbox_pt"):
    mark = page.locator("#mark")
    mark.wait_for(state="visible")
    actual = mark.evaluate("(node) => ['left','top','width','height'].map(k=>parseFloat(node.style[k]))")
    x0, y0, x1, y1 = region[field]
    w, h = frame["width_pt"], frame["height_pt"]
    expected = [100*x0/w, 100*y0/h, 100*(x1-x0)/w, 100*(y1-y0)/h]
    assert all(abs(a-b) < .001 for a,b in zip(actual, expected)), (actual, expected)
    return actual


def caption_check(item, frame, docid):
    assert item["physical_page"] == frame["physical_page"] and item["source_sha256"] == docid
    lines = {line["line_id"]: line for line in frame["lines"]}
    asserted = [lines[number] for number in item["caption_lines"]]
    assert item["caption"] == "\n".join(line["text"] for line in asserted), item
    expected_bbox = [min(line["bbox_pt"][0] for line in asserted), min(line["bbox_pt"][1] for line in asserted),
                     max(line["bbox_pt"][2] for line in asserted), max(line["bbox_pt"][3] for line in asserted)]
    assert item["caption_bbox_pt"] == expected_bbox


def verify_detected(page, frame, docid, label, reference):
    figure, = frame["figures"]
    assert figure["id"] == f"p{frame['physical_page']}-f1"
    assert figure["type"] == "figure" and figure["status"] == "detected_only" and figure["label"] == label
    assert all(point in pymupdf.Rect(figure["bbox_pt"]) for point in LANDMARKS[reference]), figure
    caption_check(figure, frame, docid)
    assert not (pymupdf.Rect(figure["bbox_pt"]) & pymupdf.Rect(figure["caption_bbox_pt"])).get_area()
    page.get_by_role("button", name=f"图 {label} · 图版", exact=True).click()
    assert "已定位图框 · OCR 图注" in page.locator("#detail").inner_text()
    coordinate_check(page, figure, frame)
    page.locator("#figure-location").click()
    assert "图注框坐标" in page.locator("#detail").inner_text()
    coordinate_check(page, figure, frame, "caption_bbox_pt")
    page.locator("#figure-location").click()
    coordinate_check(page, figure, frame)
    return figure


def no_version_fallback(browser, port, target):
    page = browser.new_page()
    fetched = []
    page.on("request", lambda request: fetched.append(request.url))
    page.goto(f"http://127.0.0.1:{port}/scan?id={target}&page=3")
    page.get_by_text("指定的文献版本不在活动文库。", exact=True).wait_for()
    assert page.locator("#doc").input_value() == "" and not page.locator("#preview").is_visible()
    assert not any("/api/scan/" in url or "/scan-image/" in url for url in fetched), fetched
    page.close()


def fresh_run(browser, official, flash, output):
    with TemporaryDirectory() as folder, serve(workbench.ScanLibrary(Path(folder))) as port:
        doc = upload(port, official)
        source = doc["id"]
        assert source == ATTENTION[1] and doc["textless_pages"] == list(range(1, 16))
        detected = {}
        for number, label, reference in ((3, "1", "attention_architecture"), (4, "2", "attention_two_panels")):
            page = configured_page(browser, port, source, number)
            result = trigger(page, "#scan")
            assert result["region_revision"] == 3
            frame = result["page"]
            detected[number] = verify_detected(page, frame, source, label, reference)
            if number == 3:
                formula, = frame["regions"]
                assert (formula["id"], formula["type"], formula["status"]) == ("p3-r1", "formula", "detected_only")
            assert scan_info(port, source, number)["page"] == frame
            page.screenshot(path=str(output / f"figure-page-{number}.png"), full_page=True)
            page.close()
        page = configured_page(browser, port, source, 8)
        table_page = trigger(page, "#scan")["page"]
        table, = table_page["regions"]
        assert table["id"] == "p8-r1" and table["status"] == "structure_recognized"
        assert not table_page["figures"] and not table_page["unresolved_captions"]
        page.close()
        page = configured_page(browser, port, source, 13)
        figure_page = trigger(page, "#scan")["page"]
        candidate, = figure_page["unresolved_captions"]
        assert not figure_page["figures"] and "bbox" not in candidate and "bbox_pt" not in candidate
        assert candidate["status"] == "caption_only" and candidate["id"] == "p13-fc1"
        caption_check(candidate, figure_page, source)
        page.get_by_role("button", name="图 3 · 仅图注", exact=True).click()
        assert "图框待确认" in page.locator("#detail").inner_text()
        assert not page.locator("#figure-location").count()
        coordinate_check(page, candidate, figure_page, "caption_bbox_pt")
        page.screenshot(path=str(output / "unresolved-caption.png"), full_page=True)
        page.close()
        independent = upload(port, flash)["id"]
        page = configured_page(browser, port, independent, 1)
        independent_page = trigger(page, "#scan")["page"]
        extra = verify_detected(page, independent_page, independent, "1", "flash_memory_attention")
        page.close()
        call(port, "/api/archive", {"id": source})
        no_version_fallback(browser, port, source)
        no_version_fallback(browser, port, "0"*64)
        return {"source_sha256": source, "figures": detected, "caption_only": candidate,
                "table_control": {"page": 8, "id": table["id"], "status": table["status"]},
                "independent_raster_page": extra, "flash_original_physical_page": 2,
                "archived_and_unknown_links_stay_closed": True}


def legacy_run(browser, official, legacy_root, output):
    previous_path = output / "revision-two-parser.json"
    subprocess.run([sys.executable, "-m", "scanpdf", str(official), "--pages", "3,9",
                    "--output", str(previous_path)], check=True,
                   env={**os.environ, "PYTHONPATH": str(legacy_root.resolve())}, cwd=output, timeout=150)
    previous = json.loads(previous_path.read_text())
    with TemporaryDirectory() as folder:
        store = workbench.ScanLibrary(Path(folder))
        docid = store.add(official.read_bytes(), official.name)["doc"]["id"]
        assert docid == previous["source"]["sha256"]
        for old in previous["pages"]:
            with patch.object(workbench.ScanLibrary, "REGION_REVISION", 2), patch.object(
                    workbench, "recognize_pdf", return_value={**previous, "pages": [deepcopy(old)]}):
                assert store.recognize(docid, old["physical_page"])["region_revision"] == 2
        original = store.records([docid])
        count = 0
        with serve(store) as port:
            for legacy in previous["pages"]:
                number = legacy["physical_page"]
                original_info = scan_info(port, docid, number)
                assert original_info["region_update_available"] and "figures" not in original_info["page"]
                locator = "/api/source?" + urlencode({"id": docid, "page": number, "block": 1, "part": 1})
                before = call(port, locator)
                originals = {}
                if number == 9:
                    paragraphs = len(ocr_paragraphs(legacy))
                    for chunk in original[0]["chunks"]:
                        if chunk["page"] != number or chunk["block"] <= paragraphs:
                            continue
                        path = "/api/source?" + urlencode({"id": docid, **{k: chunk[k] for k in ("page", "block", "part")}})
                        previous_cell = call(port, path)
                        if previous_cell["passage"]["precision"] == "cell":
                            originals[path] = previous_cell
                page = configured_page(browser, port, docid, number)
                updated = trigger(page, "#update-regions")
                result = updated["page"]
                assert updated["region_revision"] == 3
                assert result["regions"] == legacy["regions"] and result["lines"] == legacy["lines"]
                assert result["render"] == previous["render"]
                assert updated == call(port, "/api/scan", {"id": docid, "page": number, "region_revision": 2})
                assert before == call(port, locator) and before["passage"]["boxes"]
                if number == 3:
                    verify_detected(page, result, docid, "1", "attention_architecture")
                else:
                    assert originals and all(call(port, path) == old for path, old in originals.items())
                    count += len(originals)
                page.close()
        reopened = workbench.ScanLibrary(Path(folder))
        assert reopened.records([docid]) == original and reopened.page_info(docid, 3)["page"]["figures"]
        return {"source_sha256": docid, "pages": [3,9], "from_revision": 2, "to_revision": 3,
                "unchanged_existing_regions_and_ocr": True, "confirmed_crop_locators_after_refresh": count,
                "stable_after_restart_and_repeat": True}


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--legacy-parser-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    official = check_source(args.pdf_dir, ATTENTION)
    flash = raster_page(check_source(args.pdf_dir, FLASH), 2, output / "flash-image-page-2.pdf")
    with sync_playwright() as playwright, playwright.chromium.launch(executable_path=args.chromium) as browser:
        proof = {"fresh_http": fresh_run(browser, official, flash, output),
                 "refresh_http": legacy_run(browser, official, args.legacy_parser_dir, output)}
    (output / "results.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
    print("3 figure frames, 1 caption-only frame, 1 table-only control, 2 cache refreshes and 2 rejected version links")


if __name__ == "__main__":
    main()
