"""Install the pinned public model or copy already downloaded model files."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import urllib.request

from .assets import FILES, MIRROR, model_directory, validate


def download_actions():
    url = f"https://github.com/{MIRROR}.git/info/lfs/objects/batch"
    media = "application/vnd.git-lfs+json"
    payload = {
        "operation": "download",
        "transfers": ["basic"],
        "objects": [{"oid": digest, "size": size} for size, digest in FILES.values()],
    }
    request = urllib.request.Request(url, json.dumps(payload).encode(),
                                     {"Accept": media, "Content-Type": media})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.loads(response.read(64 * 1024))
    return {item["oid"]: item["actions"]["download"] for item in data["objects"]
            if "actions" in item}


def request_file(action):
    location = urlsplit(action["href"])
    if location.scheme != "https" or location.hostname not in {
            "github-cloud.githubusercontent.com", "github-cloud.s3.amazonaws.com",
            "lfs.github.com"}:
        raise ValueError("Unexpected GitHub LFS download origin")
    return urllib.request.urlopen(urllib.request.Request(action["href"],
                                                          headers=action.get("header", {})),
                                  timeout=90)


def copy_verified(source, path, size, digest):
    checksum = hashlib.sha256()
    total = 0
    with path.open("wb") as file:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            total += len(block)
            if total > size:
                raise ValueError(f"Incorrect size for {path.name}")
            checksum.update(block)
            file.write(block)
    if total != size or checksum.hexdigest() != digest:
        raise ValueError(f"Incorrect size or SHA-256 for {path.name}")


def install(destination, files=None):
    if destination.exists():
        validate(destination)
        return destination
    actions = download_actions() if files is None else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dense-", dir=destination.parent) as temp:
        staging = Path(temp) / "verified"
        staging.mkdir()
        for name, (size, digest) in FILES.items():
            source = (files / name).open("rb") if files is not None else request_file(actions[digest])
            with source:
                copy_verified(source, staging / name, size, digest)
        try:
            os.rename(staging, destination)
        except OSError:
            if not destination.is_dir():
                raise
            validate(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=model_directory())
    parser.add_argument("--files", type=Path, help="Copy and verify files from this directory without networking")
    args = parser.parse_args()
    try:
        print(install(args.output, args.files))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Model installation failed: {exc}\n")
