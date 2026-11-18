"""Replay original print-page references and citation highlights in a real book."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from corpus import call, workbench


ANSWERS = (
    ("教材末页索引将reparameterization trick指向哪些印刷页？", ["339", "347"], "reparameterization trick, 339, 347"),
    ("教材末页索引将VGG指向哪一印刷页？", ["176"], "VGG, 176"),
    ('What printed pages does the back-of-book index list for "Wasserstein GAN"?', ["281–286", "300"],
     "Wasserstein GAN, 281–286, 300"),
    ("VGG loss 在书末索引中对应哪些印刷页？", ["293"], "VGG loss, 293"),
)


def cite(port, result, quote, physical_page=541, label="527"):
    item = result["book_index"]
    assert item["physical_page"] == physical_page and item["page_label"] == label, item
    hit, = result["hits"]
    assert hit["text"][hit["start"]:hit["end"]] == quote, hit
    raw = call(port, "/api/source?" + urlencode(item["anchor"]))
    selected = raw["selection"]
    assert selected["precision"] == "glyph" and selected["boxes"], selected
    assert raw["page"] == physical_page and raw["page_label"] == label, raw
    assert raw["text"][selected["start"]:selected["end"]] == quote, raw
    assert raw["doc"]["id"] == hit["id"], raw
    return dict(item, quote=quote, boxes=selected["boxes"], answer=result["answer"])


def browser_case(browser, port, sha):
    page = browser.new_page()
    try:
        page.goto(f"http://127.0.0.1:{port}/followup")
        page.select_option("#doc", sha)
        question, _, quote = ANSWERS[0]
        page.fill("#question", question)
        with page.expect_response(lambda response: response.url.endswith("/api/followup")):
            page.click("#send")
        with page.expect_popup() as clicked:
            page.locator('#history a[href^="/source?"][href*="start="]').click()
        cited = clicked.value
        cited.locator("#excerpt mark").wait_for()
        text = cited.locator("#excerpt mark").inner_text()
        position = cited.locator("#anchor").inner_text()
        assert text == quote and "物理页 541（页标签 527）" in position, (text, position)
        cited.locator("#overlay .selected").first.wait_for(timeout=15000)
        return {"browser_quote": text, "browser_position": position}
    finally:
        page.context.close()


def run(source, browser=None):
    data = source.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    with workbench() as port:
        doc = call(port, "/api/import?name=original-book.pdf", data, "application/pdf")["doc"]
        assert doc["id"] == sha and doc["pages"] == 541, doc

        def ask(question, anchor=None):
            return call(port, "/api/followup", {"id": sha, "question": question, "anchor": anchor})

        results = []
        for question, refs, quote in ANSWERS:
            result = ask(question)
            assert result["answer_method"] == "book_index" and not result["gap"], result
            result = cite(port, result, quote)
            assert result["printed_pages"] == refs, result
            results.append(dict(result, question=question))
        ambiguous_question = "书末索引将ViT指向哪些印刷页？"
        ambiguous = ask(ambiguous_question)
        assert not ambiguous["answer"] and "多个" in ambiguous["gap"] and len(ambiguous["hits"]) > 1, ambiguous
        selected = next(hit for hit in ambiguous["hits"] if hit["page"] == 540)
        scoped = ask(ambiguous_question, [selected[key] for key in ("page", "block", "part")])
        anchored = cite(port, scoped, "ViT, 229", 540, "526")
        assert anchored["printed_pages"] == ["229"], anchored
        unknown = ask("书末索引将VGG Zeta形变指向哪些印刷页？")
        assert not unknown["answer"] and unknown["gap"] and unknown["hits"] == [], unknown
        ordinary = ask("What is the reparameterization trick?")
        assert ordinary.get("answer_method") != "book_index" and "book_index" not in ordinary, ordinary
        original = call(port, "/api/search", {"ids": [sha], "query": "reparameterization trick"})["hits"]
        body = next(hit for hit in original if hit["page"] < 527)
        wrong_scope = ask(ANSWERS[0][0], [body[key] for key in ("page", "block", "part")])
        assert not wrong_scope["answer"] and "所选证据段落" in wrong_scope["gap"] and not wrong_scope["hits"], wrong_scope
        highlighted = browser_case(browser, port, sha) if browser else None
    return {"source_sha256": sha, "results": results, "anchored_disambiguation": anchored,
            "ambiguous_entry_count": len(ambiguous["hits"]), "unknown_has_gap": True,
            "topic_delegated": True, "body_anchor_has_gap": True, "browser": highlighted}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("--chromium", help="Optional Chromium executable to verify the citation popup")
    options = parser.parse_args()
    if options.chromium:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            with playwright.chromium.launch(executable_path=options.chromium) as browser:
                result = run(options.book, browser)
    else:
        result = run(options.book)
    print(json.dumps(result, indent=2, ensure_ascii=False))
