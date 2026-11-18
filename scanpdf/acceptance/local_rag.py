"""Exercise an unseen-input QA flow via the local workbench; never call a cloud model."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import pymupdf

from corpus import call, workbench


QUESTIONS = [
    ("验证集收录了多少篇标注摘要？", 2, "37"),
    ("Which instrument recorded chamber pressure during calibration?", 3, "capacitive gauge"),
    ("What serial number was printed on that pressure gauge?", None, None),
]


def input_pdf():
    pages = [
        ("Field study of annotated abstracts", "We examined multilingual scientific abstracts and a calibration log. "
         "The training split contains 406 abstracts. Test data are evaluated separately."),
        ("Data splits", "The validation split contains 37 annotated abstracts. "
         "The test split includes 146 annotated abstracts. None of the test abstracts appeared in training."),
        ("Laboratory calibration", "A capacitive gauge recorded chamber pressure during calibration. "
         "The calibration log lists temperature and pressure measurements."),
    ]
    with pymupdf.open() as pdf:
        for title, text in pages:
            page = pdf.new_page()
            page.insert_text((68, 75), title, fontsize=17)
            page.insert_textbox(pymupdf.Rect(68, 122, 480, 450), text, fontsize=13)
        return pdf.tobytes(no_new_id=True)


def run(browser=None):
    data = input_pdf()
    rows = []
    with workbench() as port:
        imported = call(port, "/api/import?name=field-study.pdf", data, "application/pdf")
        digest = imported["doc"]["id"]
        assert digest == hashlib.sha256(data).hexdigest()
        for query, physical, expected in QUESTIONS:
            params = {"id": digest, "question": query, "anchor": None}
            began = time.monotonic()
            if browser:
                from official_http import browser_request
                answer = browser_request(browser, port, "/api/followup", params)
            else:
                answer = call(port, "/api/followup", params)
            source = answer.get("generation", {})
            claims = source.get("claims", [])
            supported = (any(c["page"] == physical and c["id"] == digest for c in claims)
                         and expected.casefold() in (answer.get("answer") or "").casefold()
                         if expected else source.get("status") == "insufficient_evidence" and not answer.get("answer"))
            for claim in claims:
                hit = answer["hits"][claim["hit_index"]]
                assert hit["text"][claim["answer_start"]:claim["answer_end"]] == claim["span"]
            rows.append({"question":query,"elapsed_seconds":round(time.monotonic()-began,2),
                         "pass":supported,"result":answer})
    return {"document_sha256":digest,"transport":"browser" if browser else "http","cases":rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", type=Path)
    args = parser.parse_args()
    if args.chromium:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p, p.chromium.launch(executable_path=str(args.chromium)) as browser:
            result = run(browser)
    else:
        result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(row["pass"] for row in result["cases"]):
        raise SystemExit(1)
