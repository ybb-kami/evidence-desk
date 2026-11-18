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
    expect(page.locator("#status")).to_contain_text("本次已翻译", timeout=90000)
    return card.locator(".translated").inner_text()


def verify(base, files, chromium):
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
        open_doc(page, base, chinese, 75)
        page.select_option("#direction", "zh-en")
        page.locator("article").first.wait_for()
        zh = select(page, "请参考示例的格式，完成最后的测试题。")
        print("ZH", zh)
        assert "test" in zh.lower()
        open_doc(page, base, english, 4)
        formula = "MultiHead(Q, K, V ) = Concat(head1, ..., headh)"
        assert select(page, formula).splitlines()[-1] == formula
        card = page.locator(".source").filter(has_text=formula).first.locator("..")
        card.locator("summary").click()
        expect(card.locator("details img")).to_be_visible()
        assert card.locator("img").evaluate(
            "(img) => img.decode().then(() => img.naturalWidth > 40)"
        )
        print("FORMULA and source PNG verified")
        open_doc(page, base, fixture, 1)
        page.locator("#pageRun").click()
        expect(page.locator("#status")).to_contain_text("本次已翻译", timeout=90000)
        t = page.locator(".translated").all_text_contents()
        print("FIXTURE", t, flush=True)
        assert t[1:] == ["print(value + 1)", "x = 2 + 3"]
        page.locator("#all").click()
        expect(page.locator("#status")).to_contain_text(
            "本次已翻译 4 段", timeout=90000
        )
        expect(page.locator("#page")).to_have_value("2")
        assert page.locator(".translated").inner_text() != "待翻译"
        page.locator("#page").fill("1")
        page.locator("#go").click()
        expect(page.locator(".translated").nth(1)).to_have_text("print(value + 1)")
        print("Two-page translation and returning to cached aligned rows verified")
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=pathlib.Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
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
            verify(proc.stdout.readline().strip(), args.samples, args.chromium)
        finally:
            proc.terminate()
            proc.wait(timeout=15)
