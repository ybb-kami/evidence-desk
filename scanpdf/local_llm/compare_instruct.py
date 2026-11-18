"""Score one loopback model on fixed passages using the frozen workbench answer path."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import sys
from time import perf_counter


CASES = Path(__file__).with_name("comparison-cases.json")
ARMS = {
    "1.7b": (39348, "ai-qwen3-1.7b-q4_K_M.gguf"),
    "4b-instruct-2507": (39350, "qwen3-4b-instruct-2507"),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def section(hit):
    return [hit["page"], hit["block"], hit.get("part", 1)]


def assess(case, result):
    expected = case["expected_anchor"]
    if expected is None:
        return {"refused": result["status"] == "insufficient_evidence",
                "unsafe_answer": result["status"] == "cited_draft"}
    claim = result.get("claims", [{}])[0]
    text = claim.get("text", "")
    span = " ".join(claim.get("span", "").split())
    matched = sum(bool(re.search(pattern, text, re.I)) for pattern in case["required_facts"])
    wrong_condition = any(re.search(pattern, text, re.I) for pattern in case["forbidden_answer"])
    language_ok = not re.search(r"[\u3400-\u9fff]", case["question"]) or bool(re.search(r"[\u3400-\u9fff]", text))
    correct = (result["status"] == "cited_draft" and language_ok and not wrong_condition
               and matched == len(case["required_facts"]))
    full_support = correct and section(claim) == expected and all(
        re.search(pattern, span, re.I) for pattern in case["quote_patterns"]
    )
    return {"correct": correct, "complete": full_support, "facts_matched": matched,
            "facts_required": len(case["required_facts"]), "language_ok": language_ok,
            "wrong_condition": wrong_condition}


def evaluate(root, papers, port, model):
    fixture = CASES.read_bytes()
    spec = json.loads(fixture)
    for filename, expected in spec["generation_sources"].items():
        if digest((root / filename).read_bytes()) != expected:
            raise ValueError(f"Use frozen {spec['frozen_revision']} files: {filename}")
    sys.path[:0] = [str(root / "scanpdf"), str(root)]
    from engine import parse_pdf
    from local_generation import LocalGenerator

    class RecordedGenerator(LocalGenerator):
        def _request(self, payload, *, timeout=75):
            shared = {k: v for k, v in payload.items() if k != "model"}
            self.input_sha256 = digest(json.dumps(shared, sort_keys=True).encode())
            self.receipt = super()._request(payload, timeout=timeout)
            return self.receipt

    generator = RecordedGenerator(f"http://127.0.0.1:{port}/v1/chat/completions", model)
    documents = {}
    for name, source in spec["sources"].items():
        data = (papers / name).read_bytes()
        if digest(data) != source["sha256"]:
            raise ValueError(f"PDF hash mismatch: {name}")
        doc = parse_pdf(data, name)
        documents[name] = {
            tuple(section(chunk)): dict(chunk, id=doc["id"], name=doc["name"])
            for chunk in doc["chunks"]
        }

    rows = []
    for case in spec["cases"]:
        hits = [documents[case["pdf"]][tuple(anchor)] for anchor in case["hits"]]
        generator.input_sha256, generator.receipt = None, {}
        start = perf_counter()
        result = generator.answer(case["question"], hits, english=case["english"])
        seconds = round(perf_counter() - start, 3)
        choice = next(iter(generator.receipt.get("choices", [])), {})
        row = {
            **case, **assess(case, result), "result": result,
            "input_sha256": generator.input_sha256,
            "raw_model_output": choice.get("message", {}).get("content"),
            "finish_reason": choice.get("finish_reason"), "seconds": seconds,
            "usage": generator.receipt.get("usage"),
            "timings": generator.receipt.get("timings"),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    answerable = [row for row in rows if row["expected_anchor"] is not None]
    negative = [row for row in rows if row["expected_anchor"] is None]
    summary = {
        "answerable": len(answerable), "negative": len(negative),
        "correct": sum(row["correct"] for row in answerable),
        "complete": sum(row["complete"] for row in answerable),
        "refused": sum(row["refused"] for row in negative),
        "unsafe_answers": sum(row["unsafe_answer"] for row in negative),
        "median_seconds": round(median(row["seconds"] for row in rows), 3),
        "maximum_seconds": max(row["seconds"] for row in rows),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return {"model": model, "fixture_sha256": digest(fixture), "frozen_revision": spec["frozen_revision"],
            "summary": summary, "results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--papers", required=True, type=Path)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--port", type=int)
    parser.add_argument("--model")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a new output path.")
    port, model = ARMS[args.arm]
    result = evaluate(args.source_tree, args.papers, args.port or port, args.model or model)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
