"""Replay genuine archived responses through the current shared evidence verifier."""

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys
from unittest.mock import patch

from compare_instruct import CASES, digest, section


ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).with_name("comparison-results.json")


def outcome(result):
    value = {"status": result["status"]}
    if result.get("reason"):
        value["reason"] = result["reason"]
    if result.get("claims"):
        value["answer"] = result["claims"][0]["text"]
    return value


def replay(papers):
    sys.path[:0] = [str(ROOT / "scanpdf"), str(ROOT)]
    import claim_support
    from condition_support import Assessment
    from engine import parse_pdf
    from local_generation import LocalGenerator

    fixture_bytes = CASES.read_bytes()
    raw_bytes = RESULTS.read_bytes()
    fixture = json.loads(fixture_bytes)
    archived = json.loads(raw_bytes)
    documents = {}
    for name, source in fixture["sources"].items():
        data = (papers / name).read_bytes()
        if digest(data) != source["sha256"]:
            raise ValueError(f"PDF hash mismatch: {name}")
        doc = parse_pdf(data, name)
        documents[name] = {tuple(section(chunk)): dict(chunk, id=doc["id"], name=doc["name"])
                           for chunk in doc["chunks"]}

    report = {"cases_sha256": digest(fixture_bytes), "archived_results_sha256": digest(raw_bytes),
              "archived_revision": fixture["frozen_revision"], "arms": {}}
    for arm, history in archived["arms"].items():
        if history["fixture_sha256"] != report["cases_sha256"]:
            raise ValueError(f"Case manifest hash mismatch: {arm}")
        model = LocalGenerator("http://127.0.0.1:1/v1/chat/completions", history["model"])
        rows = []
        for case, row in zip(fixture["cases"], history["results"], strict=True):
            if any(row[key] != expected for key, expected in case.items()):
                raise ValueError(f"Case record mismatch: {arm}/{case['id']}")
            hits = [documents[case["pdf"]][tuple(anchor)] for anchor in case["hits"]]

            def request(payload, *, timeout=75):
                fingerprint = digest(json.dumps({k: v for k, v in payload.items() if k != "model"},
                                                sort_keys=True).encode())
                if fingerprint != row["input_sha256"]:
                    raise AssertionError(f"Request changed: {arm}/{case['id']}")
                return {"choices": [{"finish_reason": row["finish_reason"],
                                     "message": {"content": row["raw_model_output"]}}]}

            states = {"archived": outcome(row["result"])}
            for enabled in (False, True):
                guard = nullcontext() if enabled else patch.object(claim_support, "assess_conditions", return_value=Assessment())
                with patch.object(model, "_request", side_effect=request) as called, guard:
                    result = model.answer(case["question"], hits, english=case["english"])
                called.assert_called_once()
                states["current" if enabled else "without_condition_guard"] = outcome(result)
            rows.append({"id": case["id"], "request_sha256": row["input_sha256"], **states})
        report["arms"][arm] = rows
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.papers), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
