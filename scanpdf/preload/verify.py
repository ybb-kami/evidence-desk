"""Replay trusted-directory preload and physical page anchors against the pinned local corpus."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, TemporaryFile
import threading
from time import monotonic
from urllib.parse import quote, urlencode, urlsplit

import pymupdf

from test_longdoc import QUERIES, normalize, request, rss
from ..acceptance.corpus import ROOT, call
from ..acceptance.fetch_inputs import MANIFEST_SHA
from ..acceptance.numeric_scan import GOLD, table_overlap
from .load import BOOK, DEMO_FILES, SCAN, V1, V7


def _cli(data, folder, *items, expected=0):
    command = [sys.executable, "-m", "scanpdf.preload", "--data", str(data), "--pdf-dir", str(folder), *items]
    started = monotonic()
    with TemporaryFile() as output, TemporaryFile() as errors:
        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=errors)
        _, status, memory = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
        output.seek(0)
        errors.seek(0)
        report = json.load(output)
        assert process.returncode == expected, (report, errors.read().decode(errors="replace"))
    return report, {"elapsed_seconds": round(monotonic() - started, 4), "peak_rss_kib": memory.ru_maxrss}


@contextmanager
def _service(data):
    command = [sys.executable, str(ROOT / "scanpdf/workbench.py"), "--port", "0", "--data", str(data)]
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as process:
        try:
            port = urlsplit(process.stdout.readline().strip()).port
            assert port, "Workbench did not report a loopback port"
            yield port, process.pid
        finally:
            process.terminate()
            process.wait(timeout=15)


def _manifest(folder, path):
    raw = path.read_bytes()
    assert sha256(raw).hexdigest() == MANIFEST_SHA
    rows = {row["name"]: row for row in json.loads(raw)["files"] if row["role"] == "test_input"}
    names = {path.name for path in folder.iterdir() if path.suffix.lower() == ".pdf"}
    assert names == set(rows) and len(rows) == 39
    return rows


def _check_first(first, expected):
    assert first["before"]["active_docs"] == first["before"]["archived_docs"] == 0
    assert first["states"] == {"imported": len(expected)}
    assert first["after"]["pdf_bytes"] == sum(row["bytes"] for row in expected.values())
    assert all(row["id"] == expected[row["file"]]["sha256"] and row["size_bytes"] == expected[row["file"]]["bytes"]
               for row in first["files"])
    by_name = {row["file"]: row for row in first["files"]}
    assert next(hint for hint in by_name[V1]["duplicate_hints"] if hint["id"] == by_name[V7]["id"])["existing_version"] == 7
    assert by_name[SCAN]["pending_textless_pages"] == list(range(1, 16))
    assert by_name[BOOK]["physical_pages"] == 541
    return by_name


def _read_anchors(port, original, docid):
    records = []
    with pymupdf.open(original) as pdf:
        assert len(pdf) == 541
        for category, phrase, physical, label in QUERIES:
            response = call(port, "/api/search", {"ids": [docid], "query": phrase})
            hit = next(row for row in response["hits"] if row["page"] == physical and normalize(phrase) in normalize(row["text"]))
            assert hit["id"] == docid and hit["page_label"] == label
            query = urlencode({"id":docid, "page":physical, "block":hit["block"], "part":hit["part"]})
            locator = call(port, "/api/source?" + query)
            source_page = pdf[physical - 1]
            assert source_page.get_label() == label
            blocks = source_page.get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS, sort=True)
            original_text = next(row[4] for row in blocks if row[5] + 1 == hit["block"])
            assert normalize(hit["text"]) in normalize(original_text)
            assert locator["pdf_url"] == f"/pdf/{docid}#page={physical}"
            assert locator["page"] == physical and locator["page_label"] == label and locator["source"] == "native"
            assert locator["text"] == hit["text"] and locator["passage"]["boxes"]
            picture, elapsed = request(port, "GET", locator["image_url"])
            assert picture.startswith(b"\x89PNG\r\n\x1a\n")
            records.append({"topic":category, "physical_page":physical, "page_label":label, "block":hit["block"],
                            "part":hit["part"], "original_page":locator["pdf_url"], "image_sha256":sha256(picture).hexdigest(),
                            "source_boxes":locator["passage"]["boxes"], "render_seconds":round(elapsed,4)})
    return records


def _scan_page_eight(port, original, docid):
    incoming = call(port, f"/api/scan/{docid}?page=8")
    assert incoming["page"] is None and incoming["pending_pages"] == list(range(1, 16))
    before = monotonic()
    result = call(port, "/api/scan", {"id":docid, "page":8})
    scan_seconds = monotonic() - before
    page = result["page"]
    assert page["physical_page"] == 8 and 8 not in result["pending_pages"]
    source, _ = request(port, "GET", f"/pdf/{docid}")
    assert sha256(source).hexdigest() == docid
    with pymupdf.open(original) as pdf:
        expected_pixels = pdf[7].get_pixmap(dpi=page["render"]["dpi"], alpha=False)
    assert (expected_pixels.width, expected_pixels.height) == (page["width_px"], page["height_px"])
    image, _ = request(port, "GET", f"/scan-image/{docid}?page=8")
    assert image.startswith(b"\x89PNG\r\n\x1a\n")

    gold = next(case for case in json.loads(GOLD.read_text())["tables"] if case["input"] == SCAN and case["page"] == 8)
    tables = [region for region in page["regions"] if region["type"] == "table"]
    region = max(tables, key=lambda record: table_overlap(record["bbox_pt"], gold["bbox"]))
    assert region["status"] == "structure_recognized"
    cells = region["cells"]
    bleu, = [cell for cell in cells if cell["text"].strip() == "BLEU"]
    group_columns = range(bleu["column"], bleu["column"] + bleu.get("column_span", 1))
    labels = {cell["text"].strip():cell["column"] for cell in cells
              if cell["row"] == bleu["row"] + 1 and cell["column"] in group_columns
              and cell["text"].strip() in {"EN-DE", "EN-FR"}}
    assert set(labels) == {"EN-DE", "EN-FR"} and labels["EN-DE"] != labels["EN-FR"]
    observations = []
    for name, number in [("EN-DE", "28.4"), ("EN-FR", "41.0")]:
        point = next(row["point"] for row in gold["checks"] if row["text"] == number)
        x, y = point
        cell, = [cell for cell in cells if cell["bbox_pt"][0] <= x < cell["bbox_pt"][2]
                 and cell["bbox_pt"][1] <= y < cell["bbox_pt"][3]]
        assert cell["column"] == labels[name] and cell["text"].strip() == number, (name, cell)
        observations.append({"column_name":name, "column":cell["column"], "text":number,
                             "row":cell["row"], "bbox_pt":cell["bbox_pt"], "reference_point_pt":point})
    assert observations[0]["row"] == observations[1]["row"]
    return {"physical_page":8, "source":f"/pdf/{docid}#page=8", "table":region["id"],
            "numeric_cells":observations, "scan_seconds": round(scan_seconds,4),
            "image_sha256":sha256(image).hexdigest(), "pending_after":result["pending_pages"]}


def _fixture_pdf(text):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((70,75), text)
        return pdf.tobytes()


def _concurrent(port, pid, book, initial):
    pdf_a = _fixture_pdf("Local upload A: spectral calibration, apparatus 509.")
    pdf_b = _fixture_pdf("Local upload B: measured vibration of instrument 317.")
    jobs = [
        ("read_original_541_page_book", "GET", f"/pdf/{book}", None, None),
        ("render_book_physical_page_541", "GET", f"/source-image?id={book}&page=541", None, None),
        ("upload_user_document_a", "POST", "/api/import?name=user-a.pdf", pdf_a, "application/pdf"),
        ("upload_user_document_b", "POST", "/api/import?name=user-b.pdf", pdf_b, "application/pdf"),
    ]
    barrier = threading.Barrier(len(jobs))

    def perform(job):
        label, method, path, body, media = job
        barrier.wait(timeout=5)
        response, elapsed = request(port, method, path, body, media)
        if method == "POST":
            assert response["doc"]["id"] == sha256(body).hexdigest() and not response["existing"]
        elif label.startswith("read"):
            assert sha256(response).hexdigest() == book
        else:
            assert response.startswith(b"\x89PNG\r\n\x1a\n")
        return {"operation":label, "elapsed_seconds":round(elapsed,4)}

    before = rss(pid)
    started = monotonic()
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        observations = list(pool.map(perform, jobs))
    elapsed = monotonic() - started
    peak = rss(pid)
    final_ids = {doc["id"] for doc in call(port, "/api/library")["docs"]}
    new = {sha256(data).hexdigest() for data in (pdf_a,pdf_b)}
    assert final_ids == initial | new
    for identity, body in ((sha256(pdf_a).hexdigest(), pdf_a), (sha256(pdf_b).hexdigest(), pdf_b)):
        stored, _ = request(port, "GET", f"/pdf/{identity}")
        assert stored == body
    return {"maximum_clients":len(jobs), "elapsed_seconds":round(elapsed,4),
            "before_server_peak_rss_kib":before, "after_server_peak_rss_kib":peak,
            "originals_retained":len(initial), "new_uploads":len(new), "operations":observations}, final_ids


def _fault_retry(root, folder):
    fixture_dir = root / "fault-pdf"
    fixture_dir.mkdir()
    failing = fixture_dir / V1
    failing.write_bytes(b"partial file transfer")
    shutil.copyfile(folder / V7, fixture_dir / V7)
    shutil.copyfile(folder / "N19-1423.pdf", fixture_dir / "N19-1423.pdf")
    input_dir = root / "fault-library"
    first, _ = _cli(input_dir, fixture_dir, "--all", expected=1)
    assert first["states"] == {"imported":2, "rejected":1}
    shutil.copyfile(folder / V1, failing)
    repaired, _ = _cli(input_dir, fixture_dir, "--all")
    assert repaired["states"] == {"existing_active":2, "imported":1}
    assert {row["id"] for row in first["files"] if "id" in row} <= {row["id"] for row in repaired["files"]}
    return {"first":first["states"], "repaired":repaired["states"]}


def run(root, folder, manifest):
    expected = _manifest(folder, manifest)
    library = root / "full-library"
    first, cold = _cli(library, folder, "--all")
    by_name = _check_first(first, expected)
    second, warm = _cli(library, folder, "--all")
    assert second["states"] == {"existing_active":39} and first["after"] == second["after"]
    assert [(row["file"], row["id"]) for row in second["files"]] == [(row["file"], row["id"]) for row in first["files"]]
    demo, demo_perf = _cli(root / "demo-library", folder, "--demo")
    assert demo["states"] == {"imported":len(DEMO_FILES)} and tuple(row["file"] for row in demo["files"]) == DEMO_FILES
    original_ids = {row["id"] for row in by_name.values()}
    old, new = by_name[V1]["id"], by_name[V7]["id"]
    with _service(library) as (port, pid):
        assert {doc["id"] for doc in call(port, "/api/library")["docs"]} == original_ids
        baseline_rss = rss(pid)
        concurrent, extended = _concurrent(port, pid, by_name[BOOK]["id"], original_ids)
        book = _read_anchors(port, folder / BOOK, by_name[BOOK]["id"])
        scan = _scan_page_eight(port, folder / SCAN, by_name[SCAN]["id"])
        assert call(port, "/api/supersede", {"keep_id":new, "archive_id":old})["archived_doc"]["id"] == old
        alias_dir = root / "aliases"
        alias_dir.mkdir()
        shutil.copyfile(folder / V1, alias_dir / "old alias.pdf")
        aliases, _ = _cli(library, alias_dir, "--file", "old alias.pdf")
        alias, = aliases["files"]
        assert alias["id"] == old and alias["state"] == "existing_archived" and alias["library_name"] == V1
        assert {doc["id"] for doc in call(port, "/api/library")["docs"]} == extended - {old}
        assert {doc["id"] for doc in call(port, "/api/archive")["docs"]} == {old}
        assert call(port, "/api/restore", {"id":old})["archived"] is False
        assert {doc["id"] for doc in call(port, "/api/library")["docs"]} == extended
        available = aliases["after"]["max_next_upload_original_bytes"]
        assert 0 < available < aliases["after"]["single_upload_pdf_limit_bytes"]
        too_large = _fixture_pdf("Strong enough PDF signature for the capacity check").ljust(available + 1, b" ")
        denied, _ = request(port, "POST", "/api/import?name=" + quote("beyond-budget.pdf"), too_large, "application/pdf", expected=400)
        assert "容量" in denied["error"] and {doc["id"] for doc in call(port, "/api/library")["docs"]} == extended
        full_rss = rss(pid)
    return {"manifest_sha256":MANIFEST_SHA, "first":{"states":first["states"], "capacity":first["after"], **cold},
            "rerun":{"states":second["states"], **warm}, "demo":{"states":demo["states"], "capacity":demo["after"], **demo_perf},
            "fault_recovery":_fault_retry(root, folder), "book_anchors":book, "scanned_page":scan, "concurrency":concurrent,
            "version_choice":{"v7":new,"v1":old,"archived_alias_kept_name":alias["library_name"],"v1_restored":True},
            "upload_boundary":{"remaining_original_bytes":available,"one_byte_over_rejected":True},
            "workbench_baseline_rss_kib":baseline_rss,"workbench_peak_rss_kib":full_rss}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_directory", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="evidence-preload-verify-") as folder:
        print(json.dumps(run(Path(folder), args.pdf_directory.resolve(), args.manifest.resolve()), ensure_ascii=False, indent=2))
