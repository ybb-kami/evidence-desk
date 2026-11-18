"""Reproduce two-source E5 comparisons through the real loopback desktop/mobile UI."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

from acceptance.corpus import call, workbench
from .test_compare import controlled_pdfs


V1, V7, BERT = "1706.03762v1.pdf", "1706.03762v7.pdf", "N19-1423.pdf"


def _origin(port):
    return f"http://127.0.0.1:{port}"


def _review(page, left, right, *, model=True):
    page.locator("#before").select_option(left)
    page.locator("#after").select_option(right)
    page.locator("#semantic").set_checked(model)
    started = perf_counter()
    with page.expect_response(lambda response: response.url.endswith("/api/version-evidence"), timeout=30000) as received:
        page.locator("#submit").press("Enter")
    assert received.value.ok, received.value.status
    expect(page.locator("#results")).to_be_visible()
    response = received.value.json()
    assert (response["before"]["id"], response["after"]["id"]) == (left, right)
    assert page.evaluate("document.documentElement.scrollWidth === document.documentElement.clientWidth")
    return response, round((perf_counter() - started) * 1000)


def _check_anchors(page, row, selector):
    checked = []
    for n, side in enumerate(("left", "right")):
        anchor = row[side]
        pane = page.locator(selector).locator(".pane").nth(n)
        link = pane.get_by_role("link", name="定位原文")
        assert link.get_attribute("href") == anchor["source_url"]
        assert pane.get_by_role("link", name="打开原 PDF 页").get_attribute("href") == anchor["pdf_url"]
        with page.expect_popup() as opened:
            link.click()
        popup = opened.value
        try:
            expect(popup.locator("#identity")).to_have_text(f"SHA-256 {anchor['id']}")
            expect(popup.locator("#anchor")).to_contain_text(f"物理页 {anchor['page']}")
            popup.wait_for_function("() => document.querySelector('#image').naturalWidth > 0")
            expect(popup.locator("#overlay .region.selected").first).to_be_visible()
            assert popup.locator("#excerpt mark").text_content() == anchor["text"]
            checked.append({"page": anchor["page"], "block": anchor["block"], "part": anchor["part"]})
        finally:
            popup.close()
    return checked


def _metrics(response, browser_ms=None, checked=()):
    return {
        "identity": response["identity"]["status"], "state": response["semantic"]["state"],
        "reason": response["semantic"]["reason"], "title_cosine": response["title"]["cosine"],
        "abstract_cosine": response["abstract"]["cosine"],
        "e5_ms": response["semantic"]["elapsed_ms"], "browser_ms": browser_ms,
        "matches": [{"change": row["change"], "cosine": row["cosine"], "numbers": row.get("number_tokens")}
                    for row in response["passages"] if row["left"] and row["right"]],
        "source_popups": list(checked),
    }


def run(folder, chromium):
    cases = {}
    with TemporaryDirectory(prefix="evidence-versions-fixtures-") as fixtures:
        fixture_dir = Path(fixtures)
        files = {name: folder / name for name in [V1, V7, BERT]}
        for n, data in enumerate(controlled_pdfs(), 1):
            name = f"controlled-{n}.pdf"
            files[name] = fixture_dir / name
            files[name].write_bytes(data)
        ids = {name: sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
        controlled = ids["controlled-1.pdf"], ids["controlled-2.pdf"]
        with workbench() as port, sync_playwright() as playwright:
            origin = _origin(port)
            browser = playwright.chromium.launch(executable_path=chromium, headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            peer = browser.new_page(viewport={"width": 390, "height": 844})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            peer.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/")
            with page.expect_file_chooser() as chooser:
                page.locator("#choose").press("Enter")
            chooser.value.set_files([str(path) for path in files.values()])
            expect(page.locator(f'#version-before option[value="{ids[V7]}"]')).to_be_attached(timeout=30000)
            page.locator("#version-before").select_option(ids[V1])
            page.locator("#version-after").select_option(ids[V7])
            page.get_by_role("link", name="核对两个版本的摘要与原文段落").click()
            params = parse_qs(urlsplit(page.url).query)
            assert params == {"before": [ids[V1]], "after": [ids[V7]]}
            expect(page.locator("#before")).to_have_value(ids[V1])
            expect(page.locator("#after")).to_have_value(ids[V7])

            result, elapsed = _review(page, ids[V1], ids[V7])
            assert result["identity"]["status"] == "shared_identifier"
            assert result["identity"]["left"]["arxiv"]["version"] == 1
            assert result["identity"]["right"]["arxiv"]["version"] == 7
            assert result["semantic"]["state"] == "ready", result["semantic"]
            assert result["abstract"]["cosine"] >= .90
            matches = [row["change"] for row in result["passages"]]
            assert "same_wording" in matches and "similar_candidate" in matches
            cases["arxiv_v1_v7"] = _metrics(result, elapsed, _check_anchors(page, result["abstract"], "#front article:nth-child(2)"))

            negative, elapsed = _review(page, ids[V1], ids[BERT])
            assert negative["identity"]["status"] == "unverified"
            assert negative["semantic"]["state"] == "ready"
            assert 0 < negative["abstract"]["cosine"] < result["abstract"]["cosine"]
            cases["attention_bert"] = _metrics(negative, elapsed, _check_anchors(
                page, negative["abstract"], "#front article:nth-child(2)"))

            assert call(port, "/api/archive", {"id": ids[V1]})["archived"] is True
            peer.goto(origin + "/")
            peer.locator("#version-before").select_option(ids[V1])
            peer.locator("#version-after").select_option(ids[V7])
            peer.locator("#version-evidence").press("Enter")
            expect(peer.locator("#before")).to_have_value(ids[V1])
            expect(peer.locator("#before option:checked")).to_contain_text("已归档")
            disabled, elapsed = _review(peer, ids[V1], ids[V7], model=False)
            assert disabled["semantic"]["state"] == "disabled"
            assert disabled["abstract"]["cosine"] is None
            cases["archived_model_off_mobile"] = _metrics(disabled, elapsed, _check_anchors(
                peer, disabled["title"], "#front article:nth-child(1)"))
            assert [row["id"] for row in call(port, "/api/archive")["docs"]] == [ids[V1]]

            revised, elapsed = _review(peer, *controlled)
            assert revised["identity"]["status"] == "unverified"
            assert revised["semantic"]["state"] == "ready"
            assert revised["title"]["change"] == "changed_wording"
            phrases = [row for row in revised["passages"] if row["change"] == "similar_candidate"]
            exact = next(row for row in revised["passages"] if row["change"] == "same_wording")
            assert len(phrases) == 2 and exact["left"]["block"] > exact["right"]["block"]
            numbers = next(row for row in phrases if row.get("number_tokens"))
            assert numbers["number_tokens"] == {"left": ["20"], "right": ["30"]}
            row_selector = f"#passages article:nth-child({revised['passages'].index(numbers) + 1})"
            cases["renamed_reordered_no_id_mobile"] = _metrics(revised, elapsed, _check_anchors(peer, numbers, row_selector))

            peer.get_by_role("link", name="返回文库").click()
            assert peer.url == origin + "/"
            assert not errors, errors
            browser.close()

        with patch.dict("os.environ", {"EVIDENCE_DENSE_MODEL_DIR": str(fixture_dir / "missing-model")}), workbench() as port:
            for name in ("controlled-1.pdf", "controlled-2.pdf"):
                call(port, "/api/import?name=" + name, files[name].read_bytes(), "application/pdf")
            fallback = call(port, "/api/version-evidence", {"before_id": controlled[0], "after_id": controlled[1], "semantic": True})
            assert fallback["semantic"]["state"] == "unavailable"
            assert fallback["semantic"]["reason"] == "model_missing"
            assert fallback["title"]["left"] and fallback["title"]["right"]
            assert all(row["cosine"] is None for row in fallback["passages"])
            cases["model_missing"] = _metrics(fallback)
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdfs", required=True, type=Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    print(json.dumps(run(args.pdfs, args.chromium), ensure_ascii=False, indent=2))
