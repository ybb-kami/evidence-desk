"""Produce optional CPU LaTeX drafts for cells already marked for original review."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from math import ceil
from pathlib import Path
import re

import pymupdf
from PIL import Image, ImageOps

if __package__:
    from .formula_candidates import _load_model
else:
    from formula_candidates import _load_model


def _input_crop(original, dpi):
    horizontal, vertical = max(1, round(dpi / 60)), max(1, round(dpi / 120))
    width, height = original.size
    if width <= 2 * horizontal or height <= 2 * vertical:
        return None
    interior = original.crop((horizontal, vertical, width - horizontal, height - vertical))
    ink = interior.point(lambda pixel: 255 if pixel < 179 else 0).getbbox()
    if ink is None:
        return None
    bounds = [ink[0] + horizontal, ink[1] + vertical, ink[2] + horizontal, ink[3] + vertical]
    return ImageOps.expand(original.crop(bounds).convert("RGB"), border=8, fill="white"), bounds


def audit(pdf_path, result_path, number, weights, output):
    result = json.loads(result_path.read_text(encoding="utf-8"))
    data = pdf_path.read_bytes()
    digest = sha256(data).hexdigest()
    if digest != result["source"]["sha256"]:
        raise ValueError("The source PDF differs from the scan result.")
    record = next((entry for entry in result["pages"] if entry["physical_page"] == number), None)
    render = result["render"]
    dpi = render["dpi"]
    if record is None or not 72 <= dpi <= 600 or render["frame"] != "rotated_visible_page_top_left":
        raise ValueError("Provide an existing scan result for this physical page.")
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        if not 1 <= number <= len(pdf):
            raise ValueError("Physical page is outside the source PDF.")
        page = pdf[number - 1]
        if ceil(page.rect.width * dpi / 72) * ceil(page.rect.height * dpi / 72) > 25_000_000:
            raise ValueError("Page render exceeds 25 megapixels.")
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
        if [pix.width, pix.height, page.rotation] != [record["width_px"], record["height_px"], record["rotation"]]:
            raise ValueError("The render frame differs from the stored scan.")
        raster = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    cells = [(table, cell) for table in record["regions"] if table["type"] == "table"
             for cell in table.get("cells", ()) if cell.get("status") == "math_unverified"]
    if not cells:
        raise ValueError("This physical page has no mathematical cells marked for original review.")
    predict, model = _load_model(weights)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for table, cell in cells:
        if not re.fullmatch(rf"p{number}-r[1-9][0-9]*", table["id"]) or any(
                type(cell[key]) is not int or cell[key] < 1 for key in ("row", "column")):
            raise ValueError("A marked table cell has an invalid source identity.")
        left, top, right, bottom = cell["bbox"]
        if not (0 <= left < right <= raster.width and 0 <= top < bottom <= raster.height):
            raise ValueError("A marked table cell lies outside the source page.")
        image = raster.crop(cell["bbox"])
        name = f'{table["id"]}-row{cell["row"]}-col{cell["column"]}.png'
        original = output / name
        with original.open("xb") as stream:
            image.save(stream, format="PNG")
        row = {key: cell[key] for key in ("row", "column", "bbox", "bbox_pt", "ocr_raw", "math_review")}
        row.update(region=table["id"], physical_page=number, source_sha256=digest, crop=name,
                   crop_sha256=sha256(original.read_bytes()).hexdigest(), status="unverified_candidate")
        section = _input_crop(image, dpi)
        if section is not None:
            ink, bounds = section
            variants = {}
            for label, factor in (("source_scale", 1), ("upsampled_2x", 2)):
                buffer = BytesIO()
                ink.resize((factor * ink.width, factor * ink.height)).save(buffer, format="PNG")
                latex, elapsed = predict(buffer.getvalue())
                variants[label] = {"latex": latex.strip(), "inference_seconds": round(elapsed, 4)}
            native, doubled = (variant["latex"] for variant in variants.values())
            row.update(variants=variants, variants_agree=bool(native) and native == doubled,
                       recognition_bbox_px=[bounds[0] + left, bounds[1] + top, bounds[2] + left, bounds[3] + top],
                       calibrated_probability=None)
        else:
            row.update(status="no_readable_ink", variants={})
        rows.append(row)
    with (output / "candidates.json").open("x", encoding="utf-8") as stream:
        json.dump({"source": result["source"], "render": render, "model": model, "cells": rows}, stream,
                  ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("scan_result", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        audit(args.pdf, args.scan_result, args.page, args.weights, args.output)
    except (OSError, KeyError, ValueError) as error:
        parser.exit(1, f"Mathematical cell candidate audit failed: {error}\n")
