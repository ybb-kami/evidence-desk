"""Install the verified public Docker Hub Qwen3-Reranker-0.6B artifact."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from scanpdf.local_llm.install import RegistryRedirect, download


REPOSITORY = "ai/qwen3-reranker"
MANIFEST_SHA = "bcf5005067bd62a99a68d91e15f23bcc7f1d48706c81d7c97e4f7323e337c35c"
FILES = {
    "Qwen3-Reranker-0.6B.gguf": (
        "9c9c23f7de6dce710fe77bb698659d06179d42075b13834af260c3f2145af662",
        1_197_634_144,
    ),
    "LICENSE": (
        "609e2cb599f84aaa41d8ef29d8fdb04d164fab22e8d9292ca34a599d0f56a338",
        12_624,
    ),
}


def install(directory):
    directory.mkdir(parents=True, exist_ok=True)
    opener = build_opener(RegistryRedirect())
    params = urlencode({"service": "registry.docker.io", "scope": f"repository:{REPOSITORY}:pull"})
    with opener.open("https://auth.docker.io/token?" + params, timeout=30) as response:
        token = json.load(response)["token"]
    registry = f"https://registry-1.docker.io/v2/{REPOSITORY}"
    authorization = {"Authorization": "Bearer " + token}
    manifest_request = Request(
        registry + "/manifests/sha256:" + MANIFEST_SHA,
        headers={**authorization, "Accept": "application/vnd.oci.image.manifest.v1+json"},
    )
    with opener.open(manifest_request, timeout=30) as response:
        manifest = response.read()
    if hashlib.sha256(manifest).hexdigest() != MANIFEST_SHA:
        raise ValueError("The registry manifest failed SHA-256 verification.")
    layers = {(row["digest"], row["size"]) for row in json.loads(manifest)["layers"]}
    for filename, (sha256, size) in FILES.items():
        layer = "sha256:" + sha256
        if (layer, size) not in layers:
            raise ValueError(f"Missing pinned layer: {filename}")
        download(opener, Request(registry + "/blobs/" + layer, headers=authorization),
                 directory / filename, sha256, size)
    with (directory / "Qwen3-Reranker-0.6B.gguf").open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise ValueError("The verified model is missing the GGUF header.")
    print(directory / "Qwen3-Reranker-0.6B.gguf")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        default=Path.home() / ".cache" / "evidence-desk-rerank")
    args = parser.parse_args()
    try:
        install(args.directory)
    except (KeyError, OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
