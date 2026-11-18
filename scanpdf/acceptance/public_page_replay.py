"""Replay public single-PDF questions through the frozen Evidence Desk workbench."""
import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
import os
import time
from pathlib import Path
from urllib.parse import quote

TRACE = "52d11950da4f3da1dcb830d6212665eb89c0394f"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("desk", "trace", "pdfs", "csv"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--expect-sha", help="Require this Evidence Desk commit before starting")
    args = parser.parse_args()
    if os.environ.get("EVIDENCE_LOCAL_QA_URL") or os.environ.get("EVIDENCE_QA_READER"):
        parser.error("Unset optional answer-generation and reader variables for this retrieval-only evaluation.")
    actual = subprocess.check_output(["git", "-C", args.desk, "rev-parse", "HEAD"], text=True).strip()
    if args.expect_sha and actual != args.expect_sha:
        raise ValueError(f"Expected {args.expect_sha}, got {actual}")
    print(f"Evidence Desk {actual}; ResearchTrace {TRACE}", flush=True)
    sys.path.insert(0, str(args.desk.resolve() / "scanpdf" / "acceptance"))
    from corpus import call, recognize_scan, workbench

    def read(path):
        return subprocess.check_output(["git", "-C", args.trace, "show", f"{TRACE}:{path}"])

    raw = read("evals/retrieval_upgrade_80.json")
    peer = json.loads(read("evidence/review-upgrade/retrieval-nemotron-all-rerank.json"))
    assert hashlib.sha256(raw).hexdigest() == peer["gold_sha256"]
    questions = [q for q in json.loads(raw)["questions"] if q["answerable"] and len(q["version_ids"]) == 1]
    scopes = json.loads(read("evals/review_upgrade_scopes.json"))
    docs = {v["version_id"]: v for v in scopes["core"] + scopes["expansion"]}
    runs = {c["id"]: c for c in peer["cases"]}
    rows = []
    with workbench() as port:
        selected = sorted({q["version_ids"][0] for q in questions}, key=lambda v: docs[v]["name"])
        for version in selected:
            doc = docs[version]
            name = doc["name"]
            data = (args.pdfs / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == doc["sha256"]
            record = call(port, "/api/import?name=" + quote(name), data, "application/pdf")["doc"]
            assert record["id"] == doc["sha256"] and record["pages"] == doc["page_count"]
            if name == "1706.03762v1_img.pdf":
                recognize_scan(port, {"sha256": record["id"], "pages": record["pages"]})
            print(f"Prepared {name}", flush=True)
        for q in questions:
            doc = docs[q["version_ids"][0]]
            request = {"id": doc["sha256"], "anchor": None, "question": q["question"]}
            response = call(port, "/api/followup", request)
            cold_pages = [h["page"] for h in response["hits"][:5]]
            initial_status = response.get("retrieval", {}).get("dense", "structured")
            started = time.monotonic()
            while response.get("retrieval", {}).get("dense") in {"index_preparing", "busy"}:
                if time.monotonic() - started >= 240:
                    raise TimeoutError(f"{q['id']} semantic index did not become ready")
                time.sleep(3)
                response = call(port, "/api/followup", request)
            gold = {g["page_index"] + 1 for g in q["gold"]}
            assert gold and all(g["version_id"] == doc["version_id"] for g in q["gold"])
            hits = response["hits"][:5]
            assert all(hit["id"] == doc["sha256"] for hit in hits)
            ours = [hit["page"] for hit in hits]
            theirs = []
            for block in runs[q["id"]]["reranked"][:5]:
                parse_id, page, _ = block.rsplit(":", 2)
                assert parse_id == doc["active_parse_id"]
                theirs.append(int(page) + 1)
            rows.append({
                "id": q["id"], "group": q["group"], "category": q["category"], "language": q["language"],
                "pdf": doc["name"], "question": q["question"], "gold_pages": sorted(gold),
                "desk_first_five_pages": ours, "trace_first_five_pages": theirs,
                "desk_page_hit": int(gold.issubset(ours)), "trace_page_hit": int(gold.issubset(theirs)),
                "desk_cold_pages": cold_pages, "desk_cold_hit": int(gold.issubset(cold_pages)),
                "desk_initial_state": initial_status, "desk_final_state": response.get("retrieval", {}).get("dense","structured"),
                "desk_answer_method": response.get("answer_method", ""), "desk_answer_field": response.get("answer") or "",
                "desk_commit": actual, "peer_commit": TRACE,
            })
    with args.csv.open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for grouping in ("group", "language", "category"):
        summary = {}
        for row in rows:
            counts = summary.setdefault(row[grouping], Counter())
            counts.update(n=1, desk=row["desk_page_hit"], trace=row["trace_page_hit"])
        print(grouping, json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
