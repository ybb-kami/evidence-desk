"""Exercise Evidence Desk's HTTP import, version selection and citation graph on local contest PDFs."""
import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
V7, V1 = "1706.03762v7.pdf", "1706.03762v1.pdf"
SCAN = "1706.03762v1_img.pdf"
BOOK = "UnderstandingDeepLearning_02_09_26_C.pdf"


@contextmanager
def workbench():
    with tempfile.TemporaryDirectory(prefix="evidence-corpus-") as data:
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scanpdf" / "workbench.py"), "--port", "0", "--data", data],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            banner = proc.stdout.readline()
            port = urlsplit(banner.strip()).port
            if not port:
                raise RuntimeError("Workbench did not report a local HTTP port")
            yield port
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            proc.stdout.close()


def call(port, path, body=None, mime="application/json"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
        connection.request("GET" if body is None else "POST", path, body=body,
                          headers={"Content-Type": mime} if body is not None else {})
        response = connection.getresponse()
        result = json.loads(response.read())
        if response.status != 200:
            raise ValueError(f"{path}: HTTP {response.status} {result.get('error')}")
        return result
    finally:
        connection.close()


def recognize_scan(port, source):
    identity = source["sha256"]
    info = call(port, f"/api/scan/{identity}?page=1")
    assert info["pending_pages"] == list(range(1, source["pages"] + 1))
    pages = []
    for page in info["pending_pages"]:
        recognized = call(port, "/api/scan", {"id": identity, "page": page})
        evidence = recognized["page"]
        assert evidence["physical_page"] == page and evidence["lines"], page
        pages.append({"physical_page": page, "regions": len(evidence["regions"])})
    assert not recognized["pending_pages"]
    hits = call(port, "/api/search", {"ids": [identity], "query": "NVIDIA P100 GPUs"})["hits"]
    assert any(h["page"] == 7 and h.get("source") == "ocr" for h in hits)
    return {"pages": pages, "indexed_blocks": recognized["doc"]["indexed_blocks"],
            "gpu_evidence_page": 7}


def run(folder, with_ocr=False):
    import pymupdf
    paths = {p.name: p for p in folder.glob("*.pdf")}
    if not all(name in paths for name in (V7, V1, BOOK)):
        raise ValueError("Expected Attention v7, v1 and the 541-page UDL book in the PDF directory")
    imports, failures, sources = {}, {}, {}
    version_test = None
    with workbench() as port:
        for name in [V7, V1, *sorted(set(paths) - {V7, V1})]:
            data = paths[name].read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            sources[digest] = paths[name]
            alias = {V7: "new-version.pdf", V1: "old-version.pdf"}.get(name, name)
            try:
                response = call(port, "/api/import?name=" + quote(alias), data, "application/pdf")
            except ValueError as exc:
                failures[name] = str(exc)
                continue
            doc = response["doc"]
            with pymupdf.open(paths[name]) as pdf:
                assert doc["id"] == digest and doc["pages"] == len(pdf)
            imports[name] = {"sha256": digest, "pages": doc["pages"], "chunks": doc["indexed_blocks"],
                             "textless_pages": doc["textless_pages"]}
            if name == V1 and V7 in imports:
                older, newer = doc["id"], imports[V7]["sha256"]
                hint = next(h for h in response["duplicates"] if h["id"] == newer)
                assert (hint["new_version"], hint["existing_version"]) == (1, 7)
                assert call(port, "/api/supersede", {"keep_id": newer, "archive_id": older})["archived_doc"]["id"] == older
                assert call(port, "/api/restore", {"id": older})["archived"] is False
                version_test = {"score": hint["score"], "v7_retained_and_v1_restored": True}
        graph = call(port, "/api/graph")
        review = call(port, "/api/local-review")
        nodes, edges = graph["nodes"], graph["edges"]
        assert len(nodes) >= 20 and edges
        assert abs(sum(n["pagerank"] for n in nodes) - 1) < 0.0001
        checked = []
        for edge in edges:
            with pymupdf.open(sources[edge["source_document"]]) as doc:
                blocks = doc[edge["page"] - 1].get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS, sort=True)
            block = next(b[4] for b in blocks if b[5] + 1 == edge["block"])
            assert " ".join(edge["text"].split()) in " ".join(block.split()), edge
            checked.append({"from": sources[edge["source_document"]].name, "to": edge["target"],
                            "page": edge["page"], "block": edge["block"], "method": edge["method"]})
        ocr = recognize_scan(port, imports[SCAN]) if with_ocr else None
        assert review["edges"] and all(e["source_document"] in sources for e in review["edges"])
    return {"files_seen": len(paths), "imported": imports, "import_failures": failures,
            "version_check": version_test, "ocr": ocr, "graph": {"nodes": len(nodes), "edges": len(edges),
            "unresolved_references": graph["unresolved"], "missing_bibliographies": len(graph["no_bibliography_detected"]),
            "rank_sum": sum(n["pagerank"] for n in nodes), "anchors_checked": checked},
            "review_edge_excerpts": len(review["edges"]), "future_mentions": len(review["future_mentions"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--ocr", action="store_true", help="OCR the full scan after importing the corpus")
    args = parser.parse_args()
    observed = run(args.pdf_dir, args.ocr)
    print(json.dumps(observed, ensure_ascii=False, indent=2))
    if observed["import_failures"]:
        raise SystemExit(1)
