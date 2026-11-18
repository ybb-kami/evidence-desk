"""Replay a sealed long-book holdout against a frozen workbench over loopback HTTP.

Provide the precommitted private manifest and original PDF. Each positive item
declares one or more physical pages, their printed labels, and required literal
evidence. Only whitespace is normalized. A located excerpt must match both the
original PDF block and the workbench source locator. Score final answer fields
and the specificity of refusals separately with a human adjudication.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import statistics
import subprocess
import sys
import tarfile
import tempfile
from threading import Thread
from time import perf_counter
from urllib.parse import urlencode, urlsplit

import pymupdf


TOP_K = (1, 3, 8)
FIELD_GRADES = {"correct", "partial", "wrong", "unanswered"}
REFUSAL_GRADES = {"targeted", "generic", "wrong", "silent"}


def layout(text):
    return " ".join(text.split())


def checked_cases(gold, pdf):
    cases = gold["cases"]
    if len({item["id"] for item in cases}) != len(cases):
        raise ValueError("Repeated case IDs in holdout")
    pages = {}
    for case in cases:
        if case["kind"] not in {"answerable", "unanswerable"}:
            raise ValueError(f"Unknown case kind in {case['id']}")
        if case["kind"] == "answerable" and not case["sources"]:
            raise ValueError(f"Answerable case {case['id']} lacks a source")
        if absence := case.get("absence_pattern"):
            pattern = re.compile(absence)
            if any(pattern.search(page.get_text()) for page in pdf):
                raise ValueError(f"Absence control occurs in the original PDF in {case['id']}")
        for source in case["sources"]:
            physical = source["physical_page"]
            if type(physical) is not int or not 1 <= physical <= len(pdf):
                raise ValueError(f"Invalid original physical page in {case['id']}")
            page = pdf[physical - 1]
            if page.get_label() != source["printed_page"]:
                raise ValueError(f"Unexpected printed page in {case['id']}")
            if physical not in pages:
                pages[physical] = {block[5] + 1: layout(block[4])
                                   for block in page.get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS)
                                   if block[6] == 0}
            if not source["atoms"] or not all(
                atom and any(atom in block for block in pages[physical].values()) for atom in source["atoms"]
            ):
                raise ValueError(f"Evidence atom missing from original PDF in {case['id']}")
    return cases, pages


def call(port, path, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    headers = {}
    if isinstance(body, bytes):
        headers["Content-Type"] = "application/pdf"
    elif body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body, ensure_ascii=False).encode()
    try:
        connection.request("GET" if body is None else "POST", path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        if response.status != 200:
            raise RuntimeError(f"{path.split('?')[0]} returned HTTP {response.status}")
        return json.loads(data)
    finally:
        connection.close()


@contextmanager
def workbench(repo, python, translation_models, translation_python):
    with tempfile.TemporaryDirectory(prefix="long-book-holdout-") as data:
        private = Path(data)
        inherited = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        env = {key: os.environ[key] for key in inherited if key in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1",
                   XDG_CACHE_HOME=str(private / "cache"),
                   EVIDENCE_DENSE_MODEL_DIR=str(private / "no-dense-model"),
                   EVIDENCE_TRANSLATION_MODELS=str(translation_models or private / "no-translation"))
        if translation_python:
            env["EVIDENCE_RAG_TRANSLATION_PYTHON"] = str(translation_python)
        command = [python, "scanpdf/workbench.py", "--port", "0", "--data", str(private / "library")]
        with (private / "stderr").open("w+") as stderr, subprocess.Popen(
            command, cwd=repo, env=env, stdout=subprocess.PIPE, stderr=stderr, text=True
        ) as proc:
            try:
                banner = Queue(maxsize=1)
                Thread(target=lambda: banner.put(proc.stdout.readline()), daemon=True).start()
                try:
                    url = urlsplit(banner.get(timeout=30).strip())
                except Empty as exc:
                    raise RuntimeError("Local workbench startup timed out") from exc
                if url.hostname != "127.0.0.1" or not url.port:
                    stderr.seek(0)
                    raise RuntimeError("Local workbench failed to start\n" + stderr.read(1000))
                yield url.port
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def location(hit):
    return {key: hit[key] for key in ("id", "page", "block")} | {"part": hit.get("part", 1)}


def exact_source(port, hit, page, original):
    loc = location(hit)
    text = hit.get("text")
    if not isinstance(text, str) or not layout(text) or layout(text) not in original.get(loc["block"], ""):
        return False
    try:
        receipt = call(port, "/api/source?" + urlencode(loc))
    except RuntimeError:
        return False
    return (receipt.get("doc", {}).get("id") == loc["id"]
            and all(receipt.get(key) == loc[key] for key in ("page", "block", "part"))
            and receipt.get("page_label") == page["printed_page"]
            and layout(text) in layout(receipt.get("text") or ""))


def evidence(port, case, response, doc_id, original):
    targets = {source["physical_page"]: source for source in case["sources"]}
    hits = response["hits"]
    page_ranks, located, matched = [], {}, {}
    for rank, hit in enumerate(hits[:max(TOP_K)], 1):
        page = hit.get("page")
        if hit.get("id") != doc_id or type(page) is not int or page not in targets:
            continue
        page_ranks.append(rank)
        if exact_source(port, hit, targets[page], original[page]):
            located[rank] = location(hit)
            matched[rank] = {atom for atom in targets[page]["atoms"] if atom in layout(hit["text"])}
    required = set().union(*(set(source["atoms"]) for source in targets.values()))
    by_rank = {}
    for k in TOP_K:
        covered = set().union(*(atoms for rank, atoms in matched.items() if rank <= k))
        by_rank[str(k)] = {"physical_page": any(rank <= k for rank in page_ranks),
                           "verbatim_located": any(rank <= k for rank in located),
                           "all_required_atoms": bool(required) and covered == required}
    claims = []
    for claim in (response.get("generation") or {}).get("claims", []):
        index, span = claim.get("hit_index"), claim.get("span")
        if type(index) is not int or index + 1 not in located or not isinstance(span, str):
            continue
        hit = hits[index]
        if span and span in hit["text"] and all(claim.get(key, 1 if key == "part" else None) == value
                                               for key, value in located[index + 1].items()):
            claims.append(index + 1)
    return {"top_k": by_rank, "resolved_claim_ranks": claims}


def requests(port, cases, pdf_bytes, doc, originals):
    if call(port, "/api/library").get("deepseek_enabled"):
        raise RuntimeError("This holdout requires the provider-disabled workbench")
    imported = call(port, "/api/import?" + urlencode({"name": doc["name"]}), pdf_bytes)["doc"]
    if imported["id"] != doc["sha256"] or imported["pages"] != doc["physical_pages"]:
        raise RuntimeError("Workbench imported a different PDF edition")
    rows = []
    for case in cases:
        payload = {"id": imported["id"], "question": case["question"], "anchor": None, "model_consent": False}
        started = perf_counter()
        response = call(port, "/api/followup", payload)
        duration = round(perf_counter() - started, 4)
        generation = response.get("generation") or {}
        if response.get("model_available") or generation.get("model_reply") in {"accepted", "declined"}:
            raise RuntimeError("Unexpected model availability or provider receipt in offline replay")
        rows.append({"id": case["id"], "group": case["group"], "language": case["language"],
                     "kind": case["kind"], "response": response, "seconds": duration,
                     "evidence": evidence(port, case, response, imported["id"], originals)})
    return {"document": doc, "indexed_blocks": imported["indexed_blocks"], "cases": rows}


def summarize(result, adjudication=None):
    """Add independently reviewed field grades without treating retrieved text as an answer."""
    partitions = {"all": []}
    for row in result["cases"]:
        partitions["all"].append(row)
        partitions.setdefault(row["group"], []).append(row)

    scores = {}
    for name, rows in partitions.items():
        positives = [row for row in rows if row["kind"] == "answerable"]
        negatives = [row for row in rows if row["kind"] == "unanswerable"]
        score = {"questions": len(rows), "answerable": len(positives), "unanswerable": len(negatives),
                 "nonempty_answers": sum(bool(row["response"].get("answer")) for row in rows),
                 "p50_seconds": round(statistics.median(row["seconds"] for row in rows), 4)}
        if positives:
            score["retrieval"] = {str(k): {
                metric: sum(row["evidence"]["top_k"][str(k)][metric] for row in positives)
                for metric in ("physical_page", "verbatim_located", "all_required_atoms")
            } for k in TOP_K}
            score["resolved_explicit_answer_citation"] = sum(
                bool(row["response"].get("answer") and row["evidence"]["resolved_claim_ranks"])
                for row in positives)
        if adjudication is not None:
            for selected, field, choices in (
                (positives, "field_verdicts", FIELD_GRADES), (negatives, "refusal_verdicts", REFUSAL_GRADES)
            ):
                ratings = [adjudication[row["id"]] for row in selected]
                if set(ratings) - choices:
                    raise ValueError(f"Unsupported {field}")
                score[field] = dict(Counter(ratings))
            score["correct_with_all_located_atoms_at_8"] = sum(
                adjudication[row["id"]] == "correct" and row["evidence"]["top_k"]["8"]["all_required_atoms"]
                for row in positives)
        scores[name] = score
    return {"scores": scores, "methods": dict(sorted(Counter(
        "+".join((row["response"].get("retrieval") or {}).get("methods") or []) or "not_reported"
        for row in result["cases"]
    ).items())), "dense": dict(sorted(Counter(
        (row["response"].get("retrieval") or {}).get("dense", "not_reported") for row in result["cases"]
    ).items()))}


def write_private(path, payload):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Git repository to archive")
    parser.add_argument("--revision", required=True, help="Frozen product commit")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True, help="Private source and answer manifest")
    parser.add_argument("--gold-sha256", required=True, help="Precommitted manifest SHA-256")
    parser.add_argument("--out", type=Path, required=True, help="New private file for raw responses")
    parser.add_argument("--server-python", default=sys.executable)
    parser.add_argument("--translation-models", type=Path)
    parser.add_argument("--translation-python", type=Path)
    args = parser.parse_args()

    gold_bytes, pdf_bytes = args.gold.read_bytes(), args.pdf.read_bytes()
    if sha256(gold_bytes).hexdigest() != args.gold_sha256:
        parser.error("The selected gold differs from the precommitment")
    gold = json.loads(gold_bytes)
    if sha256(pdf_bytes).hexdigest() != gold["document"]["sha256"]:
        parser.error("The selected PDF differs from the precommitment")
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        if len(pdf) != gold["document"]["physical_pages"]:
            parser.error("The selected PDF has an unexpected physical page count")
        cases, originals = checked_cases(gold, pdf)

    revision = subprocess.check_output(
        ["git", "-C", str(args.repo), "rev-parse", "--verify", f"{args.revision}^{{commit}}"], text=True
    ).strip()
    with tempfile.TemporaryDirectory(prefix="long-book-code-") as build:
        archive = Path(build, "source.tar")
        repo = Path(build, "checkout")
        subprocess.run(["git", "-C", str(args.repo), "archive", "-o", str(archive), revision], check=True)
        with tarfile.open(archive) as source:
            source.extractall(repo, filter="data")
        with workbench(repo, args.server_python, args.translation_models, args.translation_python) as port:
            result = requests(port, cases, pdf_bytes, gold["document"], originals)
    result.update(revision=revision, gold_sha256=args.gold_sha256, endpoint="POST /api/followup",
                  server_python=args.server_python,
                  configuration={"dense": "disabled", "model_consent": False, "passage_reader": "disabled",
                                 "translation_models": str(args.translation_models) if args.translation_models else None,
                                 "translation_python": str(args.translation_python) if args.translation_python else None})
    result["summary"] = summarize(result)
    write_private(args.out, result)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
