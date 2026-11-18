"""Install the pinned Linux x86-64 llama.cpp CPU runtime and a Qwen3 GGUF."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import tarfile
import tempfile
from urllib.parse import urlencode, urlsplit
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


RUNTIME = "llama-b7940"
ARCHIVE = RUNTIME + "-bin-ubuntu-x64.tar.gz"
RUNTIME_SHA = "1b05844f4570c630fa1eb08483ef629bf92939712cab426cb269b5dcaccbfbd7"
RUNTIME_SIZE = 24_513_551
RUNTIME_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/b7940/{ARCHIVE}"
REPOSITORY = "ai/qwen3"
MANIFEST_SHA = "5eef42cd04fdde10a06e082f030545f8c29ec182e0f304a9af9815e106e2ec5c"
MODEL = "Qwen3-1.7B-Q4_K_M.gguf"
MODEL_SHA = "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897"
MODEL_SIZE = 1_107_409_472
MODELS = {
    "1.7b": (MODEL, MODEL_SHA, MODEL_SIZE, MANIFEST_SHA),
    "4b-instruct-2507": (
        "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        2_497_281_120,
        "8cd2927dc3a33531aa57f2479e2dcfb16e3bf53e31de561a612fcbc3cf6a6893",
    ),
}


class RegistryRedirect(HTTPRedirectHandler):
    """Drop the registry's anonymous scoped token before following a CDN redirect."""

    def redirect_request(self, original, fp, code, msg, headers, destination):
        source_url, next_url = urlsplit(original.full_url), urlsplit(destination)
        if next_url.scheme != "https":
            raise URLError("下载重定向需要 HTTPS。")
        redirect = super().redirect_request(
            original, fp, code, msg, headers, destination
        )
        if next_url.netloc != source_url.netloc:
            redirect.remove_header("Authorization")
        return redirect


def file_digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_existing(path, digest, size):
    if path.stat().st_size != size or file_digest(path) != digest:
        raise ValueError(f"校验失败，请检查已有下载文件 {path}")


def download(opener, request, target, digest, size):
    if target.exists():
        verify_existing(target, digest, size)
        return
    checksum = hashlib.sha256()
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".download-") as staging:
        partial = Path(staging) / target.name
        total = 0
        with opener.open(request, timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024**2):
                total += len(chunk)
                if total > size:
                    raise ValueError(f"下载超过预期大小 {target.name}")
                output.write(chunk)
                checksum.update(chunk)
        if total != size or checksum.hexdigest() != digest:
            raise ValueError(f"校验失败 {target.name}")
        partial.replace(target)


def model_request(opener, manifest_sha=MANIFEST_SHA, model_sha=MODEL_SHA, model_size=MODEL_SIZE):
    token_query = urlencode(
        {"service": "registry.docker.io", "scope": f"repository:{REPOSITORY}:pull"}
    )
    with opener.open("https://auth.docker.io/token?" + token_query, timeout=30) as response:
        token = json.load(response)["token"]
    authorization = {"Authorization": "Bearer " + token}
    registry = f"https://registry-1.docker.io/v2/{REPOSITORY}"
    manifest_request = Request(
        f"{registry}/manifests/sha256:{manifest_sha}",
        headers={**authorization, "Accept": "application/vnd.oci.image.manifest.v1+json"},
    )
    with opener.open(manifest_request, timeout=30) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != manifest_sha:
        raise ValueError("OCI manifest 校验失败。")
    layers = json.loads(data)["layers"]
    if not any(
        layer["digest"] == "sha256:" + model_sha and layer["size"] == model_size
        for layer in layers
    ):
        raise ValueError("OCI manifest 未包含待安装权重。")
    return Request(f"{registry}/blobs/sha256:{model_sha}", headers=authorization)


def install(directory, variant="1.7b"):
    filename, digest, size, manifest_sha = MODELS[variant]
    directory.mkdir(parents=True, exist_ok=True)
    opener = build_opener(RegistryRedirect())
    archive = directory / ARCHIVE
    runtime = directory / RUNTIME
    model = directory / filename
    print("校验 llama.cpp b7940 CPU 运行时。", flush=True)
    download(opener, Request(RUNTIME_URL), archive, RUNTIME_SHA, RUNTIME_SIZE)
    if not runtime.exists():
        with tempfile.TemporaryDirectory(dir=directory, prefix=".unpack-") as staging:
            with tarfile.open(archive, "r:gz") as bundle:
                bundle.extractall(path=staging, filter="data")
            (Path(staging) / RUNTIME).rename(runtime)
    if not (runtime / "llama-server").is_file():
        raise ValueError(f"运行时目录缺少 llama-server {runtime}")
    print(f"校验 {filename}（约 {size / 1e9:.2f} GB）。", flush=True)
    if model.exists():
        verify_existing(model, digest, size)
    else:
        download(opener, model_request(opener, manifest_sha, digest, size), model, digest, size)
    with model.open("rb") as source:
        if source.read(4) != b"GGUF":
            raise ValueError("下载文件缺少 GGUF 文件头。")
    print(runtime / "llama-server")
    print(model)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path.home() / ".cache" / "evidence-desk-local-llm",
    )
    parser.add_argument("--model", choices=MODELS, default="1.7b")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("此安装脚本仅提供 Linux x86-64 CPU 运行时。")
    try:
        install(args.directory, args.model)
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
