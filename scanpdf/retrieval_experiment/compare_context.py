"""Compare excerpt selectors on the same original retrieval candidates."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from subprocess import check_output
import sys
from time import perf_counter
from types import ModuleType
from unittest.mock import patch

import evidence_context
from hybrid_retrieval import english_query
from retrieval_experiment import run as replay


REPOSITORY = Path(__file__).resolve().parents[2]
BASELINE = "b69cca345f604e552d6000aa56d4fd0014f8ec9b"


def _old_selector():
    name = "_frozen_evidence_context"
    source = check_output(["git", "show", f"{BASELINE}:scanpdf/evidence_context.py"], cwd=REPOSITORY)
    module = ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(source, f"{BASELINE}:evidence_context.py", "exec"), module.__dict__)
        return module.select_excerpts, sha256(source).hexdigest()
    finally:
        del sys.modules[name]


def _assessment(evaluate, select, question, hits):
    elapsed = None

    def measured(*args, **kwargs):
        nonlocal elapsed
        start = perf_counter()
        values = select(*args, **kwargs)
        elapsed = round((perf_counter() - start) * 1000, 3)
        return values

    with patch.object(replay, "select_excerpts", measured):
        return evaluate(question, hits) | {"selection_ms": elapsed}


def _summarize(cases):
    result = {}
    for arm in replay.ARMS:
        before = [dict(row, **{arm: row[arm]["before"] | {"additional_ms": row[arm]["additional_ms"]}})
                  for row in cases]
        result[arm] = {
            label: replay.summarize(rows, arm) | {
                "selection_ms_median": median(row[arm]["selection_ms"] for row in rows),
            }
            for label, rows in (("before", before), ("after", cases))
        }
        transitions = [(old[arm]["complete_input"], new[arm]["complete_input"]) for old, new in zip(before, cases)]
        result[arm]["paired_complete_input"] = {
            "gained": transitions.count((False, True)), "lost": transitions.count((True, False)),
        }
    return result


def compare(pdfs, *, expect_translation, translated_query=False, include_cases=False):
    previous, previous_sha = _old_selector()
    current_sha = sha256(Path(evidence_context.__file__).read_bytes()).hexdigest()
    evaluate = replay.evaluate

    def paired(question, hits):
        if translated_query:
            text = question["question"]
            question = dict(question, question=english_query(text) or text)
        before = _assessment(evaluate, previous, question, hits)
        after = _assessment(evaluate, evidence_context.select_excerpts, question, hits)
        return after | {"before": before}

    with patch.object(replay, "evaluate", paired):
        result = replay.run(pdfs, expect_translation=expect_translation)
    cases = result.pop("cases")
    partitions = {
        "all": cases,
        "same_page_multiblock": [row for row in cases if row["gold_blocks"] > 1 and len(row["gold_pages"]) == 1],
        "zh": [row for row in cases if row["language"] == "zh"],
        "en": [row for row in cases if row["language"] == "en"],
    }
    result["summary"] = {name: _summarize(rows) for name, rows in partitions.items()}
    result["selector_query"] = "available_english_translation" if translated_query else "original"
    result["selector_versions"] = {
        "before_git_revision": BASELINE,
        "before_sha256": previous_sha,
        "after_worktree_head": check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip(),
        "after_sha256": current_sha,
    }
    if include_cases:
        result["cases"] = cases
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdfs", type=Path, required=True)
    parser.add_argument("--expect-translation", action="store_true")
    parser.add_argument("--translated-query", action="store_true",
                        help="Supply available English translation to both selectors as the workbench does")
    parser.add_argument("--include-cases", action="store_true")
    options = parser.parse_args()
    print(json.dumps(compare(options.pdfs, expect_translation=options.expect_translation,
                             translated_query=options.translated_query,
                             include_cases=options.include_cases), ensure_ascii=False, indent=2))
