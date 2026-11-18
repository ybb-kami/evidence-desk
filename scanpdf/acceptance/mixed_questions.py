"""Replay independent mixed table/prose questions against the actual HTTP workbench."""

import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench import Handler, ScanLibrary
from local_generation import LocalGenerator
from passage_answer import PassageReader


MISTRAL = "2310.06825v1.pdf"
BLOOM = "2211.05100v4.pdf"
ATTENTION = "1706.03762v1.pdf"
LAYERS = "In Table 1, how many n_layers does Mistral 7B have"
CASES = [
    (MISTRAL, LAYERS + ", and what does GQA stand for in the paper?", "complete", "grouped-query attention"),
    (MISTRAL, LAYERS + ", and what random seed did the authors use for pretraining?", "partial", "随机种子"),
    (MISTRAL, LAYERS + ", and why did the authors choose that number of n_layers?", "partial", "why did"),
    (MISTRAL, "表1中 Mistral 7B 的 n_layers 是多少？训练随机种子是多少？", "partial", "随机种子"),
    (MISTRAL, LAYERS + ", and what license was Mistral 7B released under?", "partial", "Apache 2.0"),
    (BLOOM, "For BLOOM-7.1B in Table 3, how many layers and attention heads, and what was its hidden dimension?",
     "native_table", "4096"),
    (ATTENTION, "How many English-to-German BLEU points did Transformer (big) get, and what was the previous"
                " state-of-the-art and by how much did the model improve?", "table_calculation", "2.04"),
    (ATTENTION, "How many GPUs did the authors use for training? What random seed did the authors use for training?",
     "partial", "8 NVIDIA P100 GPUs"),
]


def fetch(base, path, body=None):
    request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def verify(base, doc, question, expected, supporting):
    result = fetch(base, "/api/followup", {"id": doc, "question": question, "anchor": None})
    assert result.get("completeness", result["answer_method"]) == expected, result
    cells = result.get("table_cells", [])
    for cell in cells:
        params = {"id": doc} | {key: cell[key] for key in ("page", "region", "row", "column")}
        original = fetch(base, "/api/source?" + urlencode(params))
        assert original["text"] == cell["text"] and original["passage"]["precision"] == "cell", original
        box, = original["passage"]["boxes"]
        assert all(abs(a - b) <= .001 for a, b in zip(box, cell["bbox_pt"])), original
    if supporting == "8 NVIDIA P100 GPUs":
        first, second = result["fields"]
        assert first["method"] == "source_fields" and "8 块 NVIDIA P100 GPU" in first["answer"], result
        assert second["answer"] is None and "随机种子" in second["gap"], result
        assert any(supporting in " ".join(h["text"].split()) for h in result["hits"]), result
    elif expected in {"complete", "partial"}:
        first, second = result["fields"]
        assert first["method"] == "native_table" and [cell["value"] for cell in cells] == ["32"], result
        assert cells[0]["table"] == 1 and cells[0]["page"] == 2, result
        if expected == "complete":
            assert supporting in second["answer"] and second["method"] == "abbreviation", result
            hit = next(h for h in result["hits"] if h["page"] == 1 and "start" in h
                       and supporting in h["text"][h["start"]:h["end"]])
            params = {key: hit[key] for key in ("id", "page", "block", "part", "start", "end")}
            original = fetch(base, "/api/source?" + urlencode(params))
            assert original["text"][hit["start"]:hit["end"]] == "grouped-query attention (GQA)", original
            assert original["selection"]["boxes"], original
        else:
            assert second["answer"] is None and second["gap"], result
            if supporting == "Apache 2.0":
                assert any(h["page"] == 2 and supporting in h["text"] for h in result["hits"]), result
            else:
                assert supporting in second["gap"], result
    elif expected == "native_table":
        assert {cell["value"] for cell in cells if cell["page"] == 21} == {"30", "32", supporting}, result
    else:
        assert all(part in result["answer"] for part in ("28.4", "26.36", supporting)), result
    return {"source_sha256": doc, "question": question, "expected": expected, "result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as folder, ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.library = ScanLibrary(Path(folder))
        server.library.local_generator = LocalGenerator(url="", model="")
        server.library.passage_reader = PassageReader(model_path="")
        server.enable_model = False
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            docs = {name: server.library.add((args.pdf_dir / name).read_bytes(), name)["doc"]["id"]
                    for name in {file for file, _, _, _ in CASES}}
            results = [verify(base, docs[file], question, expected, supporting)
                       for file, question, expected, supporting in CASES]
        finally:
            server.shutdown()
            thread.join()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"{len(results)} verified mixed questions and pure table regressions")


if __name__ == "__main__":
    main()
