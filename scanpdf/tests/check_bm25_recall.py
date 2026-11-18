"""Replay the development and independent-document BM25 recall probes on real E5."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

SCANPDF = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCANPDF), str(SCANPDF.parent)]
from engine import find_evidence, parse_pdf
from hybrid_retrieval import english_query, retrieve, retrieve_candidates
from rag_search import ranked_passages
from semantic import DenseIndex


HOLDOUT = "language-model-holdout.pdf"
HOLDOUT_SOURCE = {
    "sha256": "0bddf9a608d62da449a6413d86cbf6e8b206212595efe0e0a14da0d79d5f4976",
    "url": (
        "https://raw.githubusercontent.com/tkrsh/Deep-Learning-Papers/"
        "40de8d74df9658bf6b775836a3a89379915f3638/A%20Neural%20Probabilistic%20Language%20Model.pdf"
    ),
}
HOLDOUT_CASES = [
    (
        "long", "In an extraction report with labels for test, tuning, and fit, how many "
        "Brown corpus words were used to choose the regularization parameters rather than "
        "how many remained for final evaluation?", [[11, 6, 1]],
    ),
    (
        "oov", "Pseudolexeme cryptomorph intellovex glossary aside, what neural language model "
        "technique fights the curse of dimensionality with continuous word representations?",
        [[1, 6, 1], [3, 6, 1], [5, 2, 1]],
    ),
    ("chinese", "用于布朗语料库验证和模型选择的部分有多少词？", [[11, 6, 1]]),
    ("no-number", "What was the Brown corpus perplexity after training on 987664 words?", []),
    (
        "word-overlap", "What prevented exchanging all model parameters over the local network, "
        "rather than how many free parameters the architecture has?", [[8, 6, 1]],
    ),
]


class FixedE5:
    """Give both arms the same actual E5 ranking for this question."""
    def __init__(self, hits):
        self.hits = hits

    def search(self, docs, question, *, limit):
        return self.hits[:limit]


def anchor(hit):
    return [hit["page"], hit["block"], hit.get("part", 1)]


def trial(doc, case, index):
    q, gold = case["question"], case["gold"]
    if not all(location in [anchor(hit) for hit in doc["chunks"]] for location in gold):
        raise ValueError(f"Gold anchor mismatch: {case['id']}")
    began = perf_counter()
    fixed = FixedE5(index.search([doc], q, limit=16))
    encoded = perf_counter()
    translation = english_query(q)
    translated = perf_counter()
    original = find_evidence([doc], q)
    retrieve(doc, q, limit=1)
    sparse_candidates = retrieve_candidates(doc, q)
    if not gold and sparse_candidates:
        raise ValueError(f"Unexpected sparse candidate for unsupported number: {case['id']}")
    results = {}
    for name, recall in (("base", False), ("recall", True)):
        timings = []
        for _ in range(5):
            t0 = perf_counter()
            hits, metadata = ranked_passages(doc, q, original, fixed, limit=8, recall_bm25=recall)
            timings.append(perf_counter() - t0)
        positions = [anchor(hit) for hit in hits]
        results[name] = {
            "methods": metadata["methods"],
            "first_gold_rank": next((i for i, p in enumerate(positions, 1) if p in gold), None),
            "pool": positions,
            "median_seconds": round(median(timings), 6),
        }
    return {
        **case, **results,
        "e5_seconds": round(encoded - began, 4),
        "translation": translation,
        "translation_seconds": round(translated - encoded, 4),
        "new_sparse": [anchor(hit) for hit in sparse_candidates],
    }


def evaluate(papers, model, cache):
    dev_file = SCANPDF / "rerank" / "dev-cases.json"
    dev = json.loads(dev_file.read_text(encoding="utf-8"))
    docs = {}
    for name, spec in {**dev["sources"], HOLDOUT: HOLDOUT_SOURCE}.items():
        data = (papers / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ValueError(f"PDF hash mismatch: {name}")
        docs[name] = parse_pdf(data, name)
    index = DenseIndex(model, cache_dir=cache)
    holdout = [
        {"id": uid, "file": HOLDOUT, "question": query, "gold": gold}
        for uid, query, gold in HOLDOUT_CASES
    ]
    results = {}
    for name, cases in (("development", dev["cases"]), ("independent", holdout)):
        rows = []
        for case in cases:
            record = trial(docs[case["file"]], case, index)
            rows.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        record = {"cases": rows}
        for arm in ("base", "recall"):
            ranks = [row[arm]["first_gold_rank"] for row in rows]
            record[arm] = {
                **{f"top{k}": sum(rank is not None and rank <= k for rank in ranks) for k in (1, 3, 8)},
                "median_seconds": round(median(row[arm]["median_seconds"] for row in rows), 6),
            }
        results[name] = record
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", required=True, type=Path)
    parser.add_argument("--e5", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a new output file.")
    results = evaluate(args.papers, args.e5, args.cache)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
