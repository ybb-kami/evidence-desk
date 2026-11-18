"""Files for the pinned multilingual-e5-small ONNX export."""

import hashlib
import os
from pathlib import Path


MODEL = "intfloat/multilingual-e5-small"
EXPORT = "Xenova/multilingual-e5-small int8 ONNX"
MIRROR = "66lizhenhua66/multilingual-e5-small"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
WEIGHTS = "model_quantized.onnx"
TOKENIZER = "tokenizer.json"
FILES = {
    WEIGHTS: (118_308_185, "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193"),
    TOKENIZER: (17_082_730, "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
}


def model_directory():
    default = (Path(__file__).resolve().parents[2] / ".cache" / "semantic"
               / f"multilingual-e5-small-{REVISION[:12]}")
    return Path(os.environ.get("EVIDENCE_DENSE_MODEL_DIR", default)).expanduser()


def validate(directory, *, checksum=True):
    for name, (size, digest) in FILES.items():
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size != size:
            raise ValueError(f"Incorrect size for {name}")
        if checksum:
            with path.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != digest:
                    raise ValueError(f"Incorrect SHA-256 for {name}")
