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
        if target.exists():
            with target.open("rb") as existing:
                if hashlib.file_digest(existing, "sha256").hexdigest() != row["sha256"]:
                    raise ValueError(f"Existing file differs: {target}")
        else:
            with tempfile.NamedTemporaryFile(dir=destination) as staged:
                with urlopen(BASE + "pdf/" + quote(name), timeout=90) as response:
                    remaining = row["bytes"]
                    while remaining > 0:
                        chunk = response.read(min(remaining, 256 * 1024))
                        if not chunk:
                            raise ValueError(f"Truncated download: {name}")
                        staged.write(chunk)
                        remaining -= len(chunk)
                    if response.read(1):
                        raise ValueError(f"Unexpected file length: {name}")
                staged.seek(0)
                if hashlib.file_digest(staged, "sha256").hexdigest() != row["sha256"]:
                    raise ValueError(f"File SHA-256 mismatch: {name}")
                os.link(staged.name, target)
        print("verified", name, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--all", action="store_true", help="Get all 39 PDFs (default: four core inputs)")
    args = parser.parse_args()
    fetch(args.directory, args.all)
