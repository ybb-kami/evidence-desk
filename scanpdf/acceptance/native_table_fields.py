"""Exercise multiple native table fields through the workbench and source APIs."""

import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import workbench


def unlabeled_matrix():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=480, height=420)
        page.insert_text((45, 45), "Bench A assembly profile", fontsize=14)
        rows = (("Component", "Specification"), ("Controller", "MICA-9"), ("Actuator", "Linear drive"),
                ("Power source", "18 V"), ("Protection", "Thermal relay"))
        for y, (left, right) in zip((94, 124, 154, 184, 214), rows):
            page.insert_text((55, y), left)
            page.insert_text((268, y), right)
        return pdf.tobytes()


def run_case(server_url, doc, case):
    payload = {"id": doc["id"], "question": case["q"], "anchor": None}
    request = Request(server_url + "/api/followup", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    if case["want"] is None:
        assert not result["answer"] and result["gap"], (case, result)
        return dict(case, doc_sha256=doc["id"], gap=result["gap"])
    assert result["answer"] and result["answer_method"] == "native_table" and not result["gap"], (case, result)
    cells = result["table_cells"]
    assert len(cells) == len(case["want"]), (case, cells)
    assert all(any(needle.casefold() in cell["value"].casefold() for cell in cells) for needle in case["want"]), (case, cells)
    locations = []
    for cell in cells:
        assert cell["page"] == case["page"], (case, cell)
        params = {"id": doc["id"]} | {key: cell[key] for key in ("page", "region", "row", "column")}
        with urlopen(server_url + "/api/source?" + urlencode(params), timeout=30) as response:
            location = json.load(response)
        assert location["text"] == cell["text"] and location["passage"]["precision"] == "cell", (cell, location)
        box, = location["passage"]["boxes"]
        assert all(abs(a - b) <= .001 for a, b in zip(box, cell["bbox_pt"])), (cell, location)
        locations.append({"text": location["text"], "bbox_pt": box, **params})
    return dict(case, doc_sha256=doc["id"], answer=result["answer"], cells=cells, locations=locations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cases = json.loads((HERE / "native_table_fields_gold.json").read_text())
    with TemporaryDirectory() as folder, ThreadingHTTPServer(("127.0.0.1", 0), workbench.Handler) as server:
        server.library = workbench.ScanLibrary(Path(folder))
        server.enable_model = False
        docs = {}
        for name in {case["doc"] for case in cases}:
            payload = unlabeled_matrix() if name == "unlabeled.pdf" else (args.pdf_dir / name).read_bytes()
            docs[name] = server.library.add(payload, name)["doc"]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            outcomes = [run_case(base, docs[case["doc"]], case) for case in cases]
        finally:
            server.shutdown()
            thread.join()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2) + "\n")
    print(len(outcomes), "verified requests and", sum(len(item.get("locations", [])) for item in outcomes), "verified original cells")


if __name__ == "__main__":
    main()
