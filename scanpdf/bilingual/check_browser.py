"""Exercise the real bilingual reader using a temporary library and supplied contest PDFs."""

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import urllib.parse as qp
import urllib.request as u

import fitz
from playwright.sync_api import expect, sync_playwright


def add(base, name, data):
    req = u.Request(
        base + "/api/import?" + qp.urlencode({"name": name}),
        data=data,
        headers={"Content-Type": "application/pdf"},
    )
    return json.load(u.urlopen(req))["doc"]["id"]


def open_doc(page, base, docid, pnum):
    page.goto(base + "/translation?doc=" + docid + "&page=" + str(pnum))
    page.locator("article").first.wait_for(timeout=20000)


def verify_page_jump(page, base, docid):
    open_doc(page, base, docid, 1)
    field = page.locator("#page")
    field.fill("2")
    field.press("Enter")
    proof = page.locator("#original")
    expect(proof).to_have_attribute("href", f"/pdf/{docid}#page=2")
    passage = page.locator('.source-card[data-anchor="p2-b1"]').filter(visible=True)
    expect(passage).to_contain_text("The result is correct.")
    field.fill("999")
    field.press("Enter")
    expect(field).to_have_value("2")
    expect(page.locator("#status")).to_have_text("请输入 1 至 2 的物理页码，当前显示第 2 页。")
    expect(proof).to_have_attribute("href", f"/pdf/{docid}#page=2")
    expect(passage).to_be_visible()
    open_doc(page, base, docid, 999)
    expect(field).to_have_value("1")
    expect(page.locator("#status")).to_have_text("请输入 1 至 2 的物理页码，当前显示第 1 页。")


def select(page, part):
    source = page.locator(".source").filter(has_text=part).first
    card = source.locator("..")
    source.evaluate(
        """(el, excerpt) => {
 const start = el.textContent.indexOf(excerpt);
 const range = document.createRange();
 range.setStart(el.firstChild, start);
 range.setEnd(el.firstChild, start + excerpt.length);
 window.getSelection().removeAllRanges();
 window.getSelection().addRange(range);
}""",
        part,
    )
    card.locator(".run-segment").click()
    expect(page.locator("#status")).to_contain_text("本次已处理", timeout=90000)
    anchor = card.get_attribute("data-anchor")
    return page.locator(
        f'.translation-card[data-anchor="{anchor}"] .translated'
    ).inner_text()


def translate_raw(base, text):
    request = u.Request(
        base + "/api/bilingual/translate",
        data=json.dumps(
            {"direction": "en-zh", "text": text.replace("\n", " ").replace("ﬁ", "fi")}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(u.urlopen(request))["text"]


def measured_position(pane):
    return pane.evaluate("""pane => {
      const cards = Array.from(pane.querySelectorAll('.passage'));
      const top = pane.scrollTop;
      const index = cards.findLastIndex(card => card.offsetTop <= top);
      const card = cards[index];
      const end = cards[index+1] ? cards[index+1].offsetTop - 1 : pane.scrollHeight - pane.clientHeight;
      return {anchor:card.dataset.anchor, top, within:top-card.offsetTop,
        progress:end > card.offsetTop ? (top-card.offsetTop) / (end-card.offsetTop) : 0};
    }""")


def verify_alignment(page, base, docid):
    open_doc(page, base, docid, 1)
    source = page.locator("#source-pane")
    target = page.locator("#translation-pane")
    blocks = source.locator(".passage")
    passage = blocks.nth(1)
    marker = passage.get_attribute("data-anchor")
    assert len(passage.locator("p").text_content()) > 4000
    assert passage.evaluate(
        "(el) => el.offsetHeight > 3 * el.closest('.reader-pane').clientHeight"
    )
    other = target.locator(f'.passage[data-anchor="{marker}"]')
    passage.get_by_role("button", name="定位译文").click()
    expect(other).to_be_focused()
    assert measured_position(source)["within"] == measured_position(target)["within"] == 0

    source.hover()
    page.mouse.wheel(0, 430)
    page.wait_for_function("""() => {
        const pane = document.getElementById('source-pane');
        return pane.scrollTop > pane.querySelectorAll('.passage')[1].offsetTop + 300;
    }""")
    a, b = measured_position(source), measured_position(target)
    assert a["anchor"] == b["anchor"] == marker, (a, b)
    assert 0.05 < a["progress"] < 0.95 and abs(a["progress"] - b["progress"]) < 0.03, (a, b)

    target.hover()
    page.mouse.wheel(0, 130)
    last = blocks.last.get_attribute("data-anchor")
    expect(source.locator(".current-passage")).to_have_attribute("data-anchor", last)
    assert measured_position(source)["anchor"] == measured_position(target)["anchor"] == last
    target.focus()
    page.keyboard.press("Home")
    first = blocks.first.get_attribute("data-anchor")
    expect(source.locator(".current-passage")).to_have_attribute("data-anchor", first)
    page.wait_for_function(
        "() => [...document.querySelectorAll('.reader-pane')].every(pane => pane.scrollTop === 0)"
    )

    # Translating changes the target passage from one line to many. Its index, not
    # its old scroll height, must stay aligned.
    passage.locator(".run-segment").click()
    expect(other.locator(".translated")).to_contain_text("标签平滑", timeout=90000)
    translated = other.locator(".translated").text_content()
    assert len(translated) > 1000
    other.get_by_role("button", name="定位原文").click()
    expect(passage).to_be_focused()
    assert measured_position(source)["anchor"] == measured_position(target)["anchor"] == marker

    page.set_viewport_size({"width": 390, "height": 780})
    stacked = page.locator("#stacked")
    pair = stacked.locator(".stacked-pair").filter(
        has=page.locator(f'[data-anchor="{marker}"]')
    )
    expect(stacked).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    assert pair.locator("article").evaluate_all(
        "(cards) => cards[0].dataset.anchor === cards[1].dataset.anchor"
    )
    pair.get_by_role("button", name="定位译文").click()
    expect(pair.locator(".translation-card")).to_be_focused()
    assert pair.locator(".translated").text_content() == translated
    expect(page.locator("#export")).to_be_enabled()
    page.set_viewport_size({"width": 1280, "height": 720})
    expect(other).to_be_visible()
    assert measured_position(source)["anchor"] == measured_position(target)["anchor"] == marker
    print(
        "Desktop mouse/keyboard, long-to-short alignment, reflow, and compact paired layout verified",
        flush=True,
    )


def verify_export(
    page, browser, expected, translated, page_no, scope, example_dir=None
):
    with page.expect_download() as download:
        page.locator("#export").click()
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "bilingual.html"
        download.value.save_as(path)
        offline = browser.new_context(
            offline=True, viewport={"width": 390, "height": 780}
        )
        try:
            opened = offline.new_page()
            url = opened.evaluate(
                "(html) => URL.createObjectURL(new Blob([html], {type:'text/html;charset=utf-8'}))",
                path.read_text(encoding="utf-8"),
            )
            opened.goto(url)
            item = opened.locator("article").filter(has_text=expected).first
            expect(item.locator("h2")).to_contain_text(f"物理第 {page_no} 页")
            expect(item.locator("h2")).to_contain_text(scope)
            expect(item.locator("section").first.locator("p")).to_have_text(expected)
            expect(item.locator("section").last.locator("p")).to_have_text(translated)
            assert opened.locator("script").count() == 0
            assert opened.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert (
                opened.locator("nav a").first.get_attribute("href").startswith("#page-")
            )
        finally:
            offline.close()
        if example_dir:
            example_dir.mkdir(parents=True, exist_ok=True)
            example = example_dir / f"p{page_no}-{download.value.suggested_filename}"
            with example.open("xb") as output:
                output.write(path.read_bytes())


def verify(base, files, chromium, example_dir=None):
    english = add(base, "1706.03762v1.pdf", (files / "1706.03762v1.pdf").read_bytes())
    zhname = "2501.12948v2.pdf"
    chinese = add(base, zhname, (files / zhname).read_bytes())
    with fitz.open() as doc:
        p = doc.new_page()
        p.insert_text((20, 40), "Run the test.")
        p.insert_text((20, 80), "print(value + 1)", fontname="cour")
        p.insert_text((20, 120), "x = 2 + 3")
        doc.new_page().insert_text((20, 40), "The result is correct.")
        fixture = add(base, "Code-and-math-test.pdf", doc.tobytes())
    held_out = [
        (
            "A short ablation removes a convolutional neural network from the image model.",
            ("卷积神经网络",),
        ),
        (
            "The decoder uses scaled dot-product attention to compare queries and keys.",
            ("缩放点积注意力",),
        ),
        (
            "To reduce overconfidence, we add LABEL SMOOTHING only during training.",
            ("标签平滑",),
        ),
        ("The editor is smoothing a paper label before storage.", ()),
        (
            "Convolutional neural networks and label smoothing appear in separate ablation runs.",
            ("卷积神经网络", "标签平滑"),
        ),
    ]
    with fitz.open() as doc:
        long_page = doc.new_page(width=620, height=1600)
        long_page.insert_text((25, 30), "Start of the page.")
        long_text = (
            "Label smoothing adjusts the training targets, and the validation score remains stable. "
            * 65
        )
        assert (
            long_page.insert_textbox(fitz.Rect(25, 90, 590, 1350), long_text, fontsize=10)
            > 0
        )
        long_page.insert_text((25, 1490), "End of the page.")
        terms_page = doc.new_page(width=620)
        for index, (text, _) in enumerate(held_out, 1):
            terms_page.insert_text((25, 55 * index), text, fontsize=10)
        alignment_fixture = add(base, "Alignment-and-terms.pdf", doc.tobytes())
    print(
        "directions",
        u.urlopen(base + "/api/bilingual/models").read().decode(),
        flush=True,
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        open_doc(page, base, english, 1)
        en = select(
            page,
            "The dominant sequence transduction models are based on complex recurrent or\nconvolutional neural networks in an encoder-decoder conﬁguration.",
        )
        print("EN", en)
        assert "模型" in en and "卷积神经网络" in en
        verify_export(
            page,
            browser,
            "The dominant sequence transduction models are based on complex recurrent or\nconvolutional neural networks in an encoder-decoder conﬁguration.",
            en.split("」\n", 1)[1],
            1,
            "选区",
            example_dir,
        )
        known_cases = [
            (1, "The dominant sequence transduction models are based on complex recurrent or\nconvolutional neural networks in an encoder-decoder conﬁguration.", "卷积神经网络"),
            (3, "3.2.1\nScaled Dot-Product Attention", "缩放点积注意力"),
            (4, "3.2.2\nMulti-Head Attention", "多头注意力"),
            (7, "During training, we employed label smoothing of value ", "标签平滑"),
        ]
        for number, text, term in known_cases:
            open_doc(page, base, english, number)
            after = select(page, text).split("」\n", 1)[1]
            before = translate_raw(base, text.strip())
            print("TERM", repr(text), "| before", before, "| after", after, flush=True)
            assert term in after and "[TERM" not in after
        for text,terms in held_out:
            open_doc(page, base, alignment_fixture, 2)
            after = select(page, text).split("」\n", 1)[1]
            before = translate_raw(base, text)
            print("HELD OUT", repr(text), "| before", before, "| after", after, flush=True)
            if terms:
                assert all(term in after for term in terms) and "[TERM" not in after
            else:
                assert after == before and "标签平滑" not in after
        open_doc(page, base, chinese, 75)
        page.select_option("#direction", "zh-en")
        page.locator("article").first.wait_for()
        zh = select(page, "请参考示例的格式，完成最后的测试题。")
        print("ZH", zh)
        assert "test" in zh.lower()
        verify_export(
            page,
            browser,
            "请参考示例的格式，完成最后的测试题。",
            zh.split("」\n", 1)[1],
            75,
            "选区",
            example_dir,
        )
        open_doc(page, base, english, 4)
        formula = "MultiHead(Q, K, V ) = Concat(head1, ..., headh)"
        assert select(page, formula).splitlines()[-1] == formula
        card = page.locator(".source").filter(has_text=formula).first.locator("..")
        card.locator("summary").click()
        expect(card.locator("details img")).to_be_visible()
        assert card.locator("img").evaluate(
            "(img) => img.decode().then(() => img.naturalWidth > 40)"
        )
        verify_export(page, browser, formula, formula, 4, "选区", example_dir)
        print(
            "Both translation directions, formula export, standalone offline rendering and source PNG verified"
        )
        open_doc(page, base, fixture, 1)
        page.locator("#pageRun").click()
        expect(page.locator("#status")).to_contain_text("本次已处理", timeout=90000)
        t = page.locator(".translated").all_text_contents()
        print("FIXTURE", t, flush=True)
        assert t[1:] == ["print(value + 1)", "x = 2 + 3"]
        page.locator("#all").click()
        expect(page.locator("#status")).to_contain_text(
            "本次已处理 4 段", timeout=90000
        )
        expect(page.locator("#page")).to_have_value("2")
        assert page.locator(".translated").inner_text() != "待翻译"
        page.locator("#page").fill("1")
        page.locator("#go").click()
        expect(page.locator(".translated").nth(1)).to_have_text("print(value + 1)")
        print("Two-page translation and returning to cached aligned rows verified")
        verify_export(page, browser, "print(value + 1)", "print(value + 1)", 1, "整段")
        page.select_option("#direction", "zh-en")
        expect(page.locator("#export")).to_be_disabled()
        page.select_option("#direction", "en-zh")
        expect(page.locator("#export")).to_be_enabled()
        page.get_by_text("设置需保留原文的术语").click()
        page.locator("#terms").fill("Run the test")
        page.locator("#applyTerms").click()
        expect(page.locator("#export")).to_be_disabled()
        expect(page.locator(".translated").first).to_have_text("待翻译")
        page.locator(".run-segment").first.click()
        expect(page.locator(".translated").first).to_have_text("Run the test.")
        verify_export(page, browser, "Run the test.", "Run the test.", 1, "整段")
        print(
            "Direction scoping, glossary invalidation and literal preservation verified"
        )
        verify_page_jump(page, base, fixture)
        verify_alignment(page, base, alignment_fixture)
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=pathlib.Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument(
        "--example-dir",
        type=pathlib.Path,
        help="Save verified official-paper HTML downloads here",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as library:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(pathlib.Path(__file__).resolve().parents[1] / "workbench.py"),
                "--port",
                "0",
                "--data",
                library,
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            verify(
                proc.stdout.readline().strip(),
                args.samples,
                args.chromium,
                args.example_dir,
            )
        finally:
            proc.terminate()
            proc.wait(timeout=15)
