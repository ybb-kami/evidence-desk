"""Fetch and verify the two OPUS-MT archives, or install already downloaded copies."""

import argparse
import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from local import root

MODELS = {
    "en-zh": (
        "https://raw.githubusercontent.com/sankarshan-kundu/argosmodels/9b48df6c5e331600854eb0315dbf2aaf34a1f8a3/translate-en_zh-1_9.argosmodel",
        "433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab",
        "translate-en_zh-1_9",
    ),
    "zh-en": (
        "https://raw.githubusercontent.com/jiangwensm/OPUS-argosmodels/10feffc6ca67d3870ed7a546b17ebdc16246dabc/1.0/translate-zh_en-1_0.argosmodel",
        "1279759b6362817c14e70af4d3ca2056d8596b2ae9c691502b433b631c23194e",
        "translate-zh_en-1_0",
    ),
}
MEMBERS = (
    "README.md",
    "metadata.json",
    "sentencepiece.model",
    "model/config.json",
    "model/model.bin",
    "model/shared_vocabulary.json",
)


def install(direction, destination, archives=None):
    url, digest, prefix = MODELS[direction]
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / direction
    if target.exists():
        if (
            (target / "archive.sha256").is_file()
            and (target / "archive.sha256").read_text().strip() == digest
            and all((target / m).is_file() for m in MEMBERS)
        ):
            return
        raise ValueError(f"Existing model directory is incomplete or differs: {target}")
    with tempfile.TemporaryDirectory(dir=destination) as folder:
        staging = Path(folder)
        archive = staging / "model.zip"
        if archives:
            archive = archives / (direction + ".zip")
        else:
            with (
                urllib.request.urlopen(url, timeout=60) as response,
                archive.open("wb") as file,
            ):
                shutil.copyfileobj(response, file)
        with archive.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != digest:
                raise ValueError(f"Archive digest mismatch for {direction}")
        model_dir = staging / "unpacked"
        with zipfile.ZipFile(archive) as zipped:
            for name in MEMBERS:
                path = model_dir / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with (
                    zipped.open(f"{prefix}/{name}") as source,
                    path.open("wb") as output,
                ):
                    shutil.copyfileobj(source, output)
        (model_dir / "archive.sha256").write_text(digest + "\n")
        os.rename(model_dir, target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root())
    parser.add_argument(
        "--archives", type=Path, help="Directory containing en-zh.zip and zh-en.zip"
    )
    args = parser.parse_args()
    for pair in MODELS:
        install(pair, args.output, args.archives)
        print(f"{pair}: {args.output / pair}")
