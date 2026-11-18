"""Run optional CPU formula OCR on scanpdf crops and export unverified LaTeX drafts."""

import argparse
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import re

from PIL import Image, ImageOps

if __package__:
    from .formula_symbols import review_latex
else:
    from formula_symbols import review_latex


_RELEASE = "https://github.com/RapidAI/RapidLaTeXOCR/releases/tag/v0.0.0"
_WEIGHTS = {
    "image_resizer_path": "image_resizer.onnx",
    "encoder_path": "encoder.onnx",
    "decoder_path": "decoder.onnx",
    "tokenizer_json": "tokenizer.json",
}
_RELEASE_HASHES = {
    "image_resizer.onnx": "e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d",
    "encoder.onnx": "01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d",
    "decoder.onnx": "bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072",
    "tokenizer.json": "1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019",
}


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _expression_box(page, region):
    """Trim a uniquely identified right-margin OCR label from the model input."""
    left, top, right, bottom = region["bbox"]
    number = region.get("equation_number")
    if number is None:
        return region["bbox"], False
    expected = f"({number})"
    positions = set()
    for line in page["lines"]:
        words = sorted(
            (word for word in line["words"] if
             left <= word["bbox"][0] < word["bbox"][2] <= right and
             top <= word["bbox"][1] < word["bbox"][3] <= bottom),
            key=lambda word: word["bbox"][0],
        )
        for count in range(1, min(5, len(words)) + 1):
            suffix = words[-count:]
            text = re.sub(r"\s+", "", "".join(word["text"] for word in suffix))
            start, finish = suffix[0]["bbox"][0], suffix[-1]["bbox"][2]
            if text == expected and finish == right and start >= left + 0.55 * (right - left):
                positions.add(start)
    if len(positions) == 1:
        return [left, top, positions.pop(), bottom], True
    return region["bbox"], False


def _encode_image(image):
    buffer = BytesIO()
    ImageOps.expand(image, border=4, fill=255).save(buffer, format="PNG")
    return buffer.getvalue()


def generate_candidates(result, crops, predict):
    """Return model drafts linked to the original crop, without changing parser data."""
    root = Path(crops).resolve(strict=True)
    rows = []
    for page in result["pages"]:
        for region in page["regions"]:
            if region["type"] != "formula":
                continue
            name = region.get("crop")
            if not isinstance(name, str):
                raise ValueError("Formula has no crop. Run scanpdf with --crops.")
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or path.suffix.lower() != ".png":
                raise ValueError("Formula crop must be a PNG inside the crop directory.")
            recognition_box, trimmed = _expression_box(page, region)
            left, top, right, bottom = region["bbox"]
            payload = path.read_bytes()
            with Image.open(BytesIO(payload), formats=["PNG"]) as scan:
                if scan.size != (right - left, bottom - top) or scan.width * scan.height > 1_000_000:
                    raise ValueError(f"Crop size differs from expected bounds or exceeds 1 megapixel: {name}")
                expression = scan.convert("L").crop((0, 0, recognition_box[2] - left, bottom - top))
            doubled = expression.resize(
                (expression.width * 2, expression.height * 2), Image.Resampling.LANCZOS
            )
            variants = {}
            for key, image in (("source_scale", expression), ("upsampled_2x", doubled)):
                output, elapsed = predict(_encode_image(image))
                variants[key] = {"latex": output.strip(), "inference_seconds": round(elapsed, 4)}
            source_latex = variants["source_scale"]["latex"]
            magnified_latex = variants["upsampled_2x"]["latex"]
            rows.append({
                "physical_page": page["physical_page"],
                "region_id": region["id"],
                "region_status": region["status"],
                "equation_number": region.get("equation_number"),
                "bbox_pt": region["bbox_pt"],
                "bbox_px": region["bbox"],
                "evidence_crop": name,
                "evidence_crop_sha256": hashlib.sha256(payload).hexdigest(),
                "recognition_bbox_px": recognition_box,
                "trimmed_ocr_equation_number": trimmed,
                "status": "unverified_candidate",
                "confidence": {
                    "calibrated_probability": None,
                    "source": "exact_text_agreement_under_rescaling",
                    "variants_agree": bool(source_latex and source_latex == magnified_latex),
                },
                "variants": variants,
                "review": review_latex(source_latex, magnified_latex, page, expression, recognition_box),
            })
    return rows


def _load_model(weights_dir):
    paths = {argument: Path(weights_dir) / name for argument, name in _WEIGHTS.items()}
    hashes = {path.name: _sha256(path) for path in paths.values()}
    if hashes != _RELEASE_HASHES:
        raise ValueError("Model files do not match the verified RapidLaTeXOCR v0.0.0 release.")
    import numpy as np
    import onnxruntime
    from rapid_latex_ocr import LaTeXOCR

    onnxruntime.disable_telemetry_events()
    np.random.seed(0)
    engine = LaTeXOCR(**paths)
    return engine, {
        "engine": "RapidLaTeXOCR",
        "package_version": version("rapid-latex-ocr"),
        "onnxruntime_version": version("onnxruntime"),
        "provider": "CPUExecutionProvider",
        "upstream_release": _RELEASE,
        "files_sha256": hashes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan_result", type=Path)
    parser.add_argument("--crops", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError(f"Output already exists: {args.output}")
        source = args.scan_result.read_bytes()
        result = json.loads(source)
        predict, model = _load_model(args.weights)
        sidecar = {
            "schema_version": 1,
            "source": result["source"],
            "render": result["render"],
            "scan_result_sha256": hashlib.sha256(source).hexdigest(),
            "model": model,
            "candidates": generate_candidates(result, args.crops, predict),
        }
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(sidecar, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (ImportError, KeyError, ValueError, OSError) as error:
        parser.exit(1, f"Formula candidate export failed: {error}\n")


if __name__ == "__main__":
    main()
