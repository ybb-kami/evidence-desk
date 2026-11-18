"""Fetch the fixed public mirror of the contest inputs; existing files must match."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote
from urllib.request import urlopen


BASE = ("https://raw.githubusercontent.com/CuiMiles/ResearchTrace/"
        "52d11950da4f3da1dcb830d6212665eb89c0394f/evals/pdfs/" + quote("科研小助手") + "/")
MANIFEST_SHA = "a4fac4d247a36a22765ba3714e628bff9011518a791a91cc30c5b0fed105e63f"
CORE = {"1706.03762v1.pdf", "1706.03762v7.pdf", "1706.03762v1_img.pdf",
        "UnderstandingDeepLearning_02_09_26_C.pdf"}


SUPPLEMENT_URL = ("https://raw.githubusercontent.com/NLPcourse/Representation/"
                  "6c063e520f4496834d7a944fade39bfa6d2c5cd4/Alternative%20Reading%20List/"
                  "language%20models%20are%20unsupervised%20multitask%20learners.pdf")
SUPPLEMENT_SHA = "d9d852e2894556e73f53cb22b7c605a9643d6f0b19bf604b429ed6192fa24f4e"


def retrieve(url, target, size, expected_hash):
    if target.exists():
        with target.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").hexdigest() != expected_hash:
                raise ValueError(f"Existing file differs: {target}")
    else:
        with tempfile.NamedTemporaryFile(dir=target.parent) as staged:
            with urlopen(url, timeout=90) as response:
                remaining = size
                while remaining > 0:
                    chunk = response.read(min(remaining, 256 * 1024))
                    if not chunk:
                        raise ValueError(f"Truncated download: {target.name}")
                    staged.write(chunk)
                    remaining -= len(chunk)
                if response.read(1):
                    raise ValueError(f"Unexpected file length: {target.name}")
            staged.seek(0)
            if hashlib.file_digest(staged, "sha256").hexdigest() != expected_hash:
                raise ValueError(f"File SHA-256 mismatch: {target.name}")
            os.link(staged.name, target)


def fetch(destination, full):
    with urlopen(BASE + "manifest.json", timeout=30) as response:
        raw = response.read(128_000)
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError("Mirror manifest SHA-256 mismatch")
    rows = [r for r in json.loads(raw)["files"] if r["role"] == "test_input"
            and (full or r["name"] in CORE)]
    destination.mkdir(parents=True, exist_ok=True)
    for row in rows:
        name = row["name"]
        if Path(name).name != name or not name.endswith(".pdf"):
            raise ValueError("Unexpected input name in mirror manifest")
        target = destination / name
        retrieve(BASE + "pdf/" + quote(name), target, row["bytes"], row["sha256"])
        print("verified", name, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--all", action="store_true", help="Get all 39 PDFs (default: four core inputs)")
    parser.add_argument("--gpt2-only", action="store_true", help="Get the separately sourced GPT-2 course mirror only")
    args = parser.parse_args()
    if args.gpt2_only:
        if args.all:
            parser.error("--all and --gpt2-only select different collections")
        args.directory.mkdir(parents=True, exist_ok=True)
        target = args.directory / "GPT2-course.pdf"
        retrieve(SUPPLEMENT_URL, target, 582775, SUPPLEMENT_SHA)
        print("verified supplemental course copy", target, flush=True)
    else:
        fetch(args.directory, args.all)
