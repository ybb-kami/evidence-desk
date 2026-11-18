"""Replay frozen, source-verified questions with equal retrieval and excerpt budgets."""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from engine import find_evidence, parse_pdf
from evidence_context import MAX_SOURCE_CHARS, MAX_SOURCES, select_excerpts, terms
from hybrid_retrieval import english_query
from rag_search import ranked_passages
from semantic import DenseIndex, MODEL, REVISION


HERE = Path(__file__).resolve().parent
FIXTURE_DIGEST = "3995db3c539311931c1e4e9481c549ed1847a85416666d51081a2b81be2fccb2"
BLOCK_LIMIT = 8
KEEP = 6
PAGE_LIMIT = 2
ARMS = ("baseline", "adjacent", "page_fusion")


def elapsed(start):
    return round((perf_counter() - start) * 1000, 3)


def key(row):
    return row["page"], row["block"], row.get("part", 1)


def matched(span, rows):
    return any(key(row)[:2] == key(span)[:2] and span["text"] in row["text"] for row in rows)


def load(pdf_dir):
    fixture = (HERE / "questions.json").read_bytes()
    if sha256(fixture).hexdigest() != FIXTURE_DIGEST:
        raise ValueError("The frozen questions or annotations have changed")
    manifest = json.loads(fixture)
    docs = {}
    for name, spec in manifest["papers"].items():
        data = (pdf_dir / spec["file"]).read_bytes()
        if sha256(data).hexdigest() != spec["sha256"]:
            raise ValueError(f"Original PDF hashes differ for {name}")
        docs[name] = parse_pdf(data, spec["file"])
    for item in manifest["questions"]:
        locations = {key(row): row["text"] for row in docs[item["paper"]]["chunks"]}
        for anchor in [*item["support"], *item["wrong_condition"]]:
            if anchor["text"] not in locations.get(key(anchor), ""):
                raise ValueError(f"The original span for {item['id']} is missing at {key(anchor)}")
    return docs, manifest["questions"]


def _expanded_row(doc, chunk):
    page = chunk["page"]
    labels = doc.get("page_labels", ())
    return dict(chunk, id=doc["id"], name=doc["name"],
                page_label=labels[page - 1] if 0 < page <= len(labels) else str(page),
                added_by="page_experiment")


def _seed_pages(hits, mode):
    if mode == "adjacent":
        strongest = {}
        for hit in hits[:KEEP]:
            strongest.setdefault(hit["page"], hit)
        return list(strongest.values())[:PAGE_LIMIT]
    grouped = defaultdict(list)
    for rank, hit in enumerate(hits, start=1):
        grouped[hit["page"]].append((rank, hit))
    ranked = sorted(grouped.values(), key=lambda group: (
        -sum(1 / (20 + rank) for rank, _ in group[:2]), group[0][0]))
    return [group[0][1] for group in ranked[:PAGE_LIMIT]]


def expansion(doc, hits, query, translated, *, mode):
    """Reserve at most two tail slots for unseen blocks on initially retrieved pages."""
    already = {key(row)[:2] for row in hits}
    desired_terms = terms(query) | terms(translated)
    additions = []
    for seed in _seed_pages(hits, mode):
        candidates = []
        for chunk in doc["chunks"]:
            if (chunk["page"] != seed["page"] or key(chunk)[:2] in already
                    or chunk.get("arxiv_stamp") or len(chunk["text"]) < 80):
                continue
            distance = abs(chunk["block"] - seed["block"])
            if mode == "adjacent" and distance > 2:
                continue
            chunk_terms = terms(chunk["text"])
            if len(chunk_terms) < 8:
                continue
            overlap = len(desired_terms & chunk_terms)
            if mode == "page_fusion" and overlap == 0:
                continue
            merit = (distance, -overlap) if mode == "adjacent" else (-overlap, distance)
            candidates.append((merit, key(chunk), chunk))
        if candidates:
            best = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]
            additions.append(_expanded_row(doc, best))
    return (hits[:KEEP] + additions + hits[KEEP:])[:BLOCK_LIMIT]


def evaluate(question, hits):
    gold = question["support"]
    gold_pages = {span["page"] for span in gold}
    pages = list(dict.fromkeys(hit["page"] for hit in hits))[:5]
    sources = []
    for index, start, raw in select_excerpts(hits, query=question["question"]).values():
        source = hits[index]
        sources.append(dict(source, text=raw, excerpt_start=start))
    candidate_coverage = [matched(span, hits) for span in gold]
    context_coverage = [matched(span, sources) for span in gold]
    negatives = [matched(span, sources) for span in question["wrong_condition"]]
    context_chars = sum(len(row["text"]) for row in sources)
    assert len(hits) <= BLOCK_LIMIT and len(sources) <= MAX_SOURCES and context_chars <= MAX_SOURCE_CHARS
    return {
        "candidate_support": candidate_coverage,
        "complete_candidates": all(candidate_coverage),
        "input_support": context_coverage,
        "complete_input": all(context_coverage),
        "pages5": pages,
        "gold_in_pages5": gold_pages.issubset(pages),
        "off_page_excerpts": sum(row["page"] not in gold_pages for row in sources),
        "wrong_condition_matches": negatives,
        "candidate_chars": sum(len(row["text"]) for row in hits),
        "candidate_anchors": [key(row) for row in hits],
        "added_anchors": [key(row) for row in hits if row.get("added_by")],
        "excerpt_chars": context_chars,
        "excerpt_count": len(sources),
        "excerpt_anchors": [(*key(row), row["excerpt_start"], row["excerpt_start"] + len(row["text"]))
                            for row in sources],
    }


def summarize(rows, arm):
    runs = [row[arm] for row in rows]
    if not runs:
        return {}
    excerpts = sum(run["excerpt_count"] for run in runs)
    off_page = sum(run["off_page_excerpts"] for run in runs)
    changes = {
        "gained_input": [row["id"] for row in rows if row[arm]["complete_input"] and not row["baseline"]["complete_input"]],
        "lost_input": [row["id"] for row in rows if row["baseline"]["complete_input"] and not row[arm]["complete_input"]],
    }
    return dict(
        questions=len(rows), complete_input=sum(run["complete_input"] for run in runs),
        input_supports=sum(sum(run["input_support"]) for run in runs),
        complete_candidates=sum(run["complete_candidates"] for run in runs),
        candidate_supports=sum(sum(run["candidate_support"]) for run in runs),
        gold_in_pages5=sum(run["gold_in_pages5"] for run in runs),
        wrong_condition=sum(sum(run["wrong_condition_matches"]) for run in runs),
        off_page_excerpts=off_page, excerpts=excerpts,
        off_page_fraction=round(off_page / max(1, excerpts), 4),
        candidate_chars_median=statistics.median(run["candidate_chars"] for run in runs),
        excerpt_chars_median=statistics.median(run["excerpt_chars"] for run in runs),
        excerpt_chars_max=max(run["excerpt_chars"] for run in runs),
        additional_ms_median=statistics.median(run["additional_ms"] for run in runs),
        **changes,
    )


def run(pdf_dir, *, expect_translation):
    docs, questions = load(pdf_dir)
    warmup = {}
    rows = []
    with TemporaryDirectory(prefix="evidence-retrieval-vectors-") as cache:
        dense = DenseIndex(cache_dir=cache)
        for name, doc in docs.items():
            start = perf_counter()
            title = next(item["question"] for item in questions if item["paper"] == name)
            assert dense.search([doc], title, limit=1), name
            warmup[name] = elapsed(start)
            print(f"Prepared local E5 vectors for {name} in {warmup[name]} ms", file=sys.stderr, flush=True)
        for item in questions:
            doc = docs[item["paper"]]
            query = item["question"]
            start = perf_counter()
            hits, status = ranked_passages(doc, query, find_evidence([doc], query), dense, limit=BLOCK_LIMIT)
            base_ms = elapsed(start)
            if status["dense"] != "ready" or "multilingual-e5-small" not in status["methods"]:
                raise ValueError(f"Real E5 must participate in each question: {item['id']} {status}")
            translation = english_query(query)
            if expect_translation and item["language"] == "zh" and not translation:
                raise ValueError(f"The local Chinese-to-English query expansion was unavailable: {item['id']}")
            row = {"id": item["id"], "paper": item["paper"], "language": item["language"],
                   "gold_pages": sorted({span["page"] for span in item["support"]}),
                   "gold_blocks": len({key(span)[:2] for span in item["support"]}),
                   "initial_retrieval_ms": base_ms, "methods": status["methods"],
                   "translation_used": bool(translation)}
            row["baseline"] = dict(evaluate(item, hits), additional_ms=0)
            for arm in ARMS[1:]:
                start = perf_counter()
                altered = expansion(doc, hits, query, translation, mode=arm)
                extra_ms = elapsed(start)
                row[arm] = dict(evaluate(item, altered), additional_ms=extra_ms)
            rows.append(row)
    splits = {"all": rows, "same_page_multiblock": [row for row in rows if row["gold_blocks"] > 1 and len(row["gold_pages"]) == 1],
              "zh": [row for row in rows if row["language"] == "zh"],
              "en": [row for row in rows if row["language"] == "en"]}
    return {
        "questions_sha256": FIXTURE_DIGEST, "dense_model": MODEL, "dense_revision": REVISION,
        "scope": {"candidate_blocks": BLOCK_LIMIT, "context_excerpts": MAX_SOURCES,
                  "context_chars": MAX_SOURCE_CHARS, "gold_supports": sum(len(q["support"]) for q in questions),
                  "distractor_spans": sum(len(q["wrong_condition"]) for q in questions)},
        "index_preparation_ms": warmup,
        "initial_retrieval_ms_median": statistics.median(row["initial_retrieval_ms"] for row in rows),
        "summary": {name: {arm: summarize(group, arm) for arm in ARMS} for name, group in splits.items()},
        "cases": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdfs", type=Path, required=True)
    parser.add_argument("--expect-translation", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.pdfs, expect_translation=args.expect_translation), ensure_ascii=False, indent=2))
