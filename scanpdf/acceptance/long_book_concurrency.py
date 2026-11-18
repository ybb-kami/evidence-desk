"""Probe four parallel HTTP workbench clients on an original 541-page book and one paper."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
from time import perf_counter
from urllib.parse import urlencode, urlsplit

import pymupdf

from workbench_soak import ROOT, call, capture, check, quantiles_ms, rss_kib, utc_now, workbench


BOOK = "UnderstandingDeepLearning_02_09_26_C.pdf"
PAPER = "1706.03762v1.pdf"
INPUTS = {
    BOOK: (541, "f8237d393163900fa8e43210e680a3f987b45ccac7750b372e156fae3df0bf32"),
    PAPER: (15, "0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476"),
}
# Each simultaneous client has a separate document scope and a source excerpt prechecked
# against the PDF itself. The two reader clients check physical navigation on other pages.
CASES = {
    "book-index": (BOOK, 541, "527", 3, "Wasserstein GAN, 281–286, 300"),
    "paper-question": (PAPER, 2, "2", 12,
                       "Encoder:\nThe encoder is composed of a stack of N = 6 identical layers."),
    "book-reader": (BOOK, 271, "257", 7, "This is known as diagonal enhancement."),
    "paper-reader": (PAPER, 3, "3", 4, "mapping a query and a set of key-value pairs to an output,"),
}
INDEX_QUESTION = 'What printed pages does the back-of-book index list for "Wasserstein GAN"?'
PAPER_QUESTION = "How many layers are in the encoder stack?"
SOURCE_FILES = ("app.py", "engine.py", "reader.py", "scanpdf/workbench.py", "scanpdf/followup.py",
                "scanpdf/book_index.py", "scanpdf/direct_measurement.py", "scanpdf/claim_support.py",
                "scanpdf/source_locator.py", "scanpdf/rag_search.py", "scanpdf/acceptance/long_book_concurrency.py")


def normalized(text):
    return " ".join(text.split())


def originals(folder):
    files = {name: (folder / name).read_bytes() for name in INPUTS}
    gold = {}
    for name, (pages, digest) in INPUTS.items():
        check(sha256(files[name]).hexdigest() == digest, f"Different public PDF edition: {name}")
        with pymupdf.open(stream=files[name], filetype="pdf") as doc:
            check(len(doc) == pages, f"Unexpected number of physical pages: {name}")
            for role, (file, number, label, block, quote) in CASES.items():
                if file != name:
                    continue
                page = doc[number - 1]
                check((page.get_label() or str(number)) == label, f"Unexpected PDF page label: {role}")
                blocks = [row for row in page.get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS)
                          if row[5] + 1 == block]
                check(len(blocks) == 1 and normalized(quote) in normalized(blocks[0][4]),
                      f"Expected quote absent in original PDF block: {role}")
                gold[role] = {"box": tuple(blocks[0][:4]), "size": (page.rect.width, page.rect.height)}
    return files, gold


def anchor_key(anchor):
    return tuple(anchor[key] for key in ("id", "page", "block", "part"))


def scoped_read(port, role):
    name, page, label, block, quote = CASES[role]
    docid = INPUTS[name][1]
    expected = (docid, page, block, 1)
    report = {"doc_id": docid, "physical_page": page, "page_label": label, "block": block}
    if role in ("book-index", "paper-question"):
        question = INDEX_QUESTION if role == "book-index" else PAPER_QUESTION
        answer = call(port, "/api/followup", dict(id=docid, question=question, anchor=None))
        hits = answer["hits"]
        check(hits and all(hit["id"] == docid for hit in hits), "Follow-up returned another PDF's hits")
        check(answer["model_available"] is False and not answer["gap"], "The offline follow-up is unresolved")
        if role == "book-index":
            entry = answer["book_index"]
            check(answer["answer_method"] == "book_index" and entry["printed_pages"] == ["281–286", "300"],
                  "The index answer or printed page references changed")
            check((entry["physical_page"], entry["page_label"]) == (page, label), "Index page types were conflated")
            anchor = entry["anchor"]
            report["printed_page_references"] = entry["printed_pages"]
        else:
            check(answer["answer_method"] == "direct_measurement" and answer["answer"] == "6",
                  "The encoder-layer question did not produce the observed source measurement")
            claim, = answer["generation"]["claims"]
            check(claim["span"] == quote, "The number was cited from a different source sentence")
            anchor = {key: claim[key] for key in ("id", "page", "block", "part")}
            anchor.update(start=claim["answer_start"], end=claim["answer_end"])
        check(anchor_key(anchor) == expected, "Follow-up citation anchor changed")
        hit, = [hit for hit in hits if anchor_key(hit) == expected]
        check(hit["text"][anchor["start"]:anchor["end"]] == quote, "Follow-up quote does not match its source offsets")
        report.update(question=question, answer=answer["answer"], answer_method=answer["answer_method"],
                      returned_hits=len(hits))
        return anchor, report

    reader = call(port, f"/api/reader/{docid}")
    check(reader["doc"]["id"] == docid and len(reader["pages"]) == INPUTS[name][0], "Mixed reader identity or page count")
    samples = ((1, "i"), (page, label), (541, "527")) if name == BOOK else ((1, "1"), (page, label), (15, "15"))
    check(all((reader["pages"][n - 1]["page"], reader["pages"][n - 1]["label"]) == (n, expected_label)
              for n, expected_label in samples), "Wrong reader physical-page navigation or PDF label")
    texts = [row["text"] for row in reader["pages"][page - 1]["blocks"] if row["id"] == f"p{page}-b{block}"]
    check(len(texts) == 1 and quote in texts[0], "Wrong reader paragraph on sampled page")
    # /api/source offsets are relative to one indexed part. These two prechecked
    # excerpts occur in the first part, whose starting offset is zero.
    begin = texts[0].index(quote)
    report["physical_pages_checked"] = [n for n, _ in samples]
    return dict(id=docid, page=page, block=block, part=1, start=begin, end=begin + len(quote)), report


def locate(port, role, anchor, gold):
    name, page, label, block, quote = CASES[role]
    path = "/api/source?" + urlencode(anchor)
    result = call(port, path)
    check(result["doc"]["id"] == anchor["id"] and (result["page"], result["block"], result["part"]) == (page, block, 1),
          "Opened the wrong PDF, physical page or block")
    check(result["page_label"] == label and (result["width"], result["height"]) == gold["size"],
          "The source page label or render dimensions differ from the original")
    check(result["pdf_url"] == f"/pdf/{anchor['id']}#page={page}", "Original-PDF page link changed")
    pick = result["selection"]
    check(pick and pick["precision"] == "glyph" and pick["boxes"] and result["source"] == "native",
          "The quote was not backed by native PDF glyphs")
    check((pick["start"], pick["end"]) == (anchor["start"], anchor["end"])
          and result["text"][pick["start"]:pick["end"]] == quote, "Source selection did not recover the exact quote")
    x0, y0, x1, y1 = gold["box"]
    check(all(x0 - 1 <= left < right <= x1 + 1 and y0 - 1 <= top < bottom <= y1 + 1
              for left, top, right, bottom in pick["boxes"]), "Highlighted glyphs fall outside the original source block")
    return {"source_url": path, "pdf_url": result["pdf_url"], "quote": quote, "boxes": pick["boxes"],
            "pdf_sha256": INPUTS[name][1]}


def open_original(port, evidence):
    url = urlsplit(evidence["pdf_url"])
    raw = call(port, url.path, raw=True)
    check(sha256(raw).hexdigest() == evidence["pdf_sha256"], "Original PDF download hash differs")
    return {"physical_page_fragment": url.fragment, "bytes": len(raw), "sha256_verified": True}


def wrong_document(port, anchor):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        connection.request("GET", "/api/source?" + urlencode(dict(anchor, id=INPUTS[PAPER][1])))
        response = connection.getresponse()
        payload = json.loads(response.read())
        check(response.status == 404 and set(payload) == {"error"}, "The paper accepted physical page 541 from the book")
        return {"wrong_document_http": response.status, "error": payload["error"]}
    finally:
        connection.close()


def client(port, role, gold, barrier):
    operations = {}
    barrier.wait(timeout=30)
    first = capture("read", lambda: scoped_read(port, role), operations)
    if first:
        anchor, proof = first
        operations["read"]["evidence"] = proof
    barrier.wait(timeout=30)
    found = capture("locate", lambda: locate(port, role, anchor, gold), operations) if first else None
    if found:
        operations["locate"]["evidence"] = found
    barrier.wait(timeout=30)
    opened = capture("open_pdf", lambda: open_original(port, found), operations) if found else None
    if opened:
        operations["open_pdf"]["evidence"] = opened
    if role == "book-index":
        rejected = capture("reject_wrong_pdf", lambda: wrong_document(port, anchor), operations) if first else None
        if rejected:
            operations["reject_wrong_pdf"]["evidence"] = rejected
        operations.setdefault("reject_wrong_pdf", {"state": "skipped", "reason": "prerequisite failed"})
    for name in ("locate", "open_pdf"):
        operations.setdefault(name, {"state": "skipped", "reason": "prerequisite failed"})
    return {"client": role, "operations": operations}


def run(folder, rounds):
    files, gold = originals(folder)
    sources = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_FILES}
    report = {"tested_at_utc": utc_now(), "head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "parallel_clients": len(CASES),
        "mode": "temporary loopback workbench, external models disabled", "inputs": {}, "rounds": [],
        "sources_sha256": sources,
        "scope": "One disposable library with four HTTP clients. Timings include response verification and exclude import."}
    with tempfile.TemporaryDirectory() as tmp, workbench(ROOT, sys.executable, Path(tmp) / "server.log") as (proc, port):
        initial = call(port, "/api/library")
        check(not initial["docs"] and initial["deepseek_enabled"] is False, "Expected an empty offline workbench")
        for name, data in files.items():
            start = perf_counter()
            imported = call(port, "/api/import?" + urlencode({"name": name}), data, "application/pdf")["doc"]
            check((imported["pages"], imported["id"]) == INPUTS[name], "HTTP import changed the original PDF identity")
            report["inputs"][name] = {"physical_pages": imported["pages"], "sha256": imported["id"],
                                      "indexed_blocks": imported["indexed_blocks"], "bytes": len(data),
                                      "import_ms": round(1000 * (perf_counter() - start), 2)}
        with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
            for number in range(1, rounds + 1):
                start, barrier = perf_counter(), Barrier(len(CASES))
                jobs = [pool.submit(client, port, role, gold[role], barrier) for role in CASES]
                observed = [job.result() for job in jobs]
                report["rounds"].append({"round": number, "wall_ms": round(1000 * (perf_counter() - start), 2),
                                         "clients": observed, "server_memory_kib": rss_kib(proc.pid)})
        final = call(port, "/api/library")
        check({doc["id"] for doc in final["docs"]} == {digest for _, digest in INPUTS.values()}, "Unexpected library mutation")
    check(all(sha256((ROOT / path).read_bytes()).hexdigest() == digest for path, digest in sources.items()),
          "Workbench source changed during timing; repeat the sample")
    grouped = {}
    for row in report["rounds"]:
        for item in row["clients"]:
            for operation, outcome in item["operations"].items():
                grouped.setdefault(f"{item['client']}/{operation}", []).append(outcome)
    report["summary"] = {key: {"counts": dict(Counter(x["state"] for x in values)),
                               "passed_latency": quantiles_ms([x["ms"] for x in values if x["state"] == "passed"])}
                         for key, values in grouped.items()}
    report["operation_counts"] = dict(Counter(x["state"] for values in grouped.values() for x in values))
    report["passed"] = all(x["state"] == "passed" for values in grouped.values() for x in values)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path, help="Directory with the SHA-256-pinned public PDFs")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10:
        parser.error("Choose between 1 and 10 rounds")
    result = run(args.samples, args.rounds)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
