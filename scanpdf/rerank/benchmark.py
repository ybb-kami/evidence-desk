"""Compare the same RRF passages with and without Qwen3 reranking."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

from .client import MAX_CANDIDATES, Reranker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import find_evidence, parse_pdf
from rag_search import ranked_passages
from semantic import DenseIndex


CASES = Path(__file__).with_name("dev-cases.json")
POOL_SIZE = 8


def location(row):
    return row["page"], row["block"], row.get("part", 1)


def first_rank(rows, gold):
    return next((i for i, row in enumerate(rows, 1) if location(row) in gold), None)


def summary(rows, key):
    ranks = [r[key] for r in rows]
    return {
        "top1": ranks.count(1),
        "top3": sum(rank is not None and rank <= 3 for rank in ranks),
        "mrr": round(sum(1 / rank for rank in ranks if rank is not None) / len(ranks), 4),
    }


def compare(papers, e5, port, *, timeout=120, cache=None, pool_limit=POOL_SIZE, cases=CASES):
    case_bytes = cases.read_bytes()
    spec = json.loads(case_bytes)
    docs = {}
    dense = DenseIndex(e5, cache_dir=cache)
    for name, source in spec["sources"].items():
        pdf = (papers / name).read_bytes()
        if hashlib.sha256(pdf).hexdigest() != source["sha256"]:
            raise ValueError(f"Source PDF hash mismatch: {name}")
        doc = parse_pdf(pdf, name)
        docs[name] = doc

    ranker = Reranker(port=port, timeout=timeout)
    prepared = {}
    rows = []
    for case in spec["cases"]:
        question = case["question"]
        gold = set(map(tuple, case["gold"]))
        doc = docs[case["file"]]
        if not gold <= {location(row) for row in doc["chunks"]}:
            raise ValueError(f"Cannot locate gold blocks in {case['id']}")
        if case["file"] not in prepared:
            began = perf_counter()
            dense.search([doc], "method", limit=1)
            prepared[case["file"]] = perf_counter() - began
        t0 = perf_counter()
        pool, metadata = ranked_passages(
            doc, question, find_evidence([doc], question), dense,
            limit=max(POOL_SIZE, pool_limit), recall_bm25=False,
        )
        pool = pool[:pool_limit]
        if metadata["dense"] != "ready" or "multilingual-e5-small" not in metadata["methods"]:
            raise RuntimeError("Baseline dense search did not run.")
        t1 = perf_counter()
        ranked = ranker.rerank(question, pool)
        t2 = perf_counter()
        result = {
            **case,
            "methods": metadata["methods"],
            "rrf_rank": first_rank(pool, gold),
            "reranked_rank": first_rank(ranked, gold),
            "rrf_seconds": round(t1 - t0, 4),
            "rerank_extra_seconds": round(t2 - t1, 4),
            "pool": [location(row) for row in pool],
            "reranked_pool": [[location(row), row["rerank_score"]] for row in ranked],
        }
        print(json.dumps(result, ensure_ascii=False), flush=True)
        rows.append(result)
    groups = {}
    for group in dict.fromkeys(row["group"] for row in rows):
        members = [row for row in rows if row["group"] == group]
        groups[group] = {
            "cases": len(members), "rrf": summary(members, "rrf_rank"),
            "reranked": summary(members, "reranked_rank"),
            "median_rerank_seconds": median([row["rerank_extra_seconds"] for row in members]),
        }
    return {
        "case_file_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "documents": spec["sources"],
        "pool_limit": pool_limit,
        "warmup_seconds": round(sum(prepared.values()), 3),
        "results": rows,
        "groups": groups,
        "rrf": summary(rows, "rrf_rank"),
        "reranked": summary(rows, "reranked_rank"),
        "median_rerank_seconds": median([r["rerank_extra_seconds"] for r in rows]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", required=True, type=Path)
    parser.add_argument("--e5", required=True, type=Path)
    parser.add_argument("--port", default=39357, type=int)
    parser.add_argument("--timeout", default=120, type=float)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--cases", default=CASES, type=Path)
    parser.add_argument("--pool-limit", type=int, default=POOL_SIZE)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a new output file.")
    if not 1 <= args.pool_limit <= MAX_CANDIDATES:
        parser.error(f"Use a pool size between 1 and {MAX_CANDIDATES}.")
    result = compare(
        args.papers, args.e5, args.port, timeout=args.timeout, cache=args.cache,
        pool_limit=args.pool_limit, cases=args.cases,
    )
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result["groups"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
