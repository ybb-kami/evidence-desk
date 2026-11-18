"""Replay the scan-derived formula audit, including two independently selected page 4 crops."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image
import pymupdf

if __package__:
    from .formula_candidates import _load_model, generate_candidates
else:
    from formula_candidates import _load_model, generate_candidates


SOURCE_SHA = "64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d"
# Coordinates were selected on the raster page before running the model.
HELD_OUT = (
    ("p4-manual-1", [182, 533, 400, 550]),
    ("p4-manual-2", [249, 550, 428, 568]),
)


def audit(pdf, scan_result, original_crops, weights, destination):
    raw = scan_result.read_bytes()
    result = json.loads(raw)
    data = pdf.read_bytes()
    if (
        hashlib.sha256(data).hexdigest() != SOURCE_SHA
        or result["source"]["sha256"] != SOURCE_SHA
        or result["render"]["dpi"] != 240
    ):
        raise ValueError("Use the fixed scanned contest PDF and its 240 DPI OCR result.")
    pages = {entry["physical_page"]: entry for entry in result["pages"]}
    if not {3, 4, 5, 7} <= pages.keys():
        raise ValueError("The OCR result must include physical pages 3, 4, 5 and 7.")
    result["pages"] = [pages[number] for number in (3, 4, 5, 7)]
    automatic = {
        (page["physical_page"], region.get("equation_number"))
        for page in result["pages"] for region in page["regions"]
        if region["type"] == "formula"
    }
    if not {(3, "1"), (5, "2"), (7, "3")} <= automatic:
        raise ValueError("The OCR result does not contain all three numbered source regions.")
    destination.mkdir()
    crops = destination / "crops"
    crops.mkdir()
    original_crops = original_crops.resolve(strict=True)
    for page in result["pages"]:
        for region in page["regions"]:
            if region["type"] != "formula":
                continue
            name = Path(region["crop"])
            source = (original_crops / name).resolve(strict=True)
            if not source.is_relative_to(original_crops) or name != Path(name.name):
                raise ValueError(f"Unexpected formula crop path: {name}")
            shutil.copyfile(source, crops / name)

    with pymupdf.open(stream=data, filetype="pdf") as document:
        for name, box in HELD_OUT:
            pix = document[3].get_pixmap(
                dpi=240, clip=pymupdf.Rect(box), colorspace=pymupdf.csGRAY, alpha=False
            )
            filename = name + ".png"
            Image.frombytes("L", (pix.width, pix.height), pix.samples).save(crops / filename)
            pages[4]["regions"].append({
                "id": name,
                "type": "formula",
                "status": "manual_crop",
                "bbox_pt": box,
                "bbox": [pix.x, pix.y, pix.x + pix.width, pix.y + pix.height],
                "crop": filename,
            })
    predict, model = _load_model(weights)
    report = {
        "source": result["source"],
        "model": model,
        "scan_result_sha256": hashlib.sha256(raw).hexdigest(),
        "candidates": generate_candidates(result, crops, predict),
    }
    with (destination / "candidates.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("scan_result", type=Path)
    parser.add_argument("--crops", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        audit(args.pdf, args.scan_result, args.crops, args.weights, args.output)
    except (ImportError, KeyError, OSError, ValueError) as error:
        parser.exit(1, f"Formula audit failed: {error}\n")


if __name__ == "__main__":
    main()
