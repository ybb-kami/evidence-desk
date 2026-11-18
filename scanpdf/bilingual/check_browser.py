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


def select(page, part):
    source = page.locator(".source").filter(has_text=part).first
    card = source.locator("..")
    source.evaluate(
        """(el, excerpt) => {
 const k=el.textContent.indexOf(excerpt),r=document.createRange();r.setStart(el.firstChild,k);r.setEnd(el.firstChild,k+excerpt.length);
 window.getSelection().removeAllRanges();window.getSelection().addRange(r);el.dispatchEvent(new MouseEvent("mouseup",{bubbles:true}));}""",
        part,
    )
    card.locator("button").click()
    expect(page.locator("#status")).to_contain_text("本次已处理", timeout=90000)
    return card.locator(".translated").inner_text()


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
        assert "模型" in en
        verify_export(
            page,
            browser,
            "The dominant sequence transduction models are based on complex recurrent or\nconvolutional neural networks in an encoder-decoder conﬁguration.",
            en.split("」\n", 1)[1],
            1,
            "选区",
            example_dir,
        )
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
        page.locator("article button").first.click()
        expect(page.locator(".translated").first).to_have_text("Run the test.")
        verify_export(page, browser, "Run the test.", "Run the test.", 1, "整段")
        print(
            "Direction scoping, glossary invalidation and literal preservation verified"
        )
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
