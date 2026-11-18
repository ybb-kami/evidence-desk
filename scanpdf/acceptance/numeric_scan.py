"""Replay fixed-coordinate scans against numerical and layout references."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import unicodedata

import pymupdf

HERE = Path(__file__).resolve().parent
GOLD = HERE / "numeric_scan_gold.json"
PIXEL_SHA256 = {
    "2406.14491v2.pdf": "3423e0739ee188cf216a588b15a82ff8a1e198c5ae313cce71a0b591fc4646dd",
    "2206.07682v2.pdf": "bfb435b09e4966163d846c87a6069edcad96e7a2855ddacd15c78e81df2d93e4",
}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare_input(case, pdf_directory, output):
    if "source" not in case:
        path = pdf_directory / case["input"]
        if digest(path) != case["input_sha256"]:
            raise ValueError(f"Unexpected source PDF hash for {path.name}")
        return path

    path = pdf_directory / case["source"]
    if digest(path) != case["source_sha256"]:
        raise ValueError(f"Unexpected source PDF hash for {path.name}")
    image_pdf = output / case["input"]
    with pymupdf.open(path) as original, pymupdf.open() as raster:
        source = original[case["source_page"] - 1]
        pixels = source.get_pixmap(dpi=220, colorspace=pymupdf.csGRAY, alpha=False)
        if hashlib.sha256(pixels.samples).hexdigest() != PIXEL_SHA256[path.name]:
            raise ValueError(f"Unexpected raster samples for {path.name}. Replay used PyMuPDF 1.26.6.")
        page = raster.new_page(width=source.rect.width, height=source.rect.height)
        page.insert_image(page.rect, pixmap=pixels)
        if page.get_text().strip() or len(page.get_images()) != 1:
            raise ValueError("A replay input must be a text-free PDF containing one raster")
        raster.save(image_pdf, deflate=True)
    return image_pdf


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold().translate(str.maketrans("×−–—", "·---"))
    return "".join(text.split())


def table_overlap(region, reference):
    table, expected = map(pymupdf.Rect, (region, reference))
    intersection = (table & expected).get_area()
    return intersection / (table.get_area() + expected.get_area() - intersection)


def score(case, page):
    tables = [region for region in page["regions"] if region["type"] == "table"]
    quality, region = max(((table_overlap(region["bbox_pt"], case["bbox"]), region) for region in tables),
                          key=lambda value: value[0], default=(0, {}))
    cells = region.get("cells", []) if quality >= .5 else []
    located = []
    for expected in case["checks"]:
        x, y = expected["point"]
        matches = [cell for cell in cells if cell["bbox_pt"][0] <= x < cell["bbox_pt"][2]
                   and cell["bbox_pt"][1] <= y < cell["bbox_pt"][3]]
        located.append(matches[0] if len(matches) == 1 else None)
    values_per_cell = Counter((cell["row"], cell["column"])
                              for cell, expected in zip(located, case["checks"]) if cell and expected["text"])
    tally = Counter()
    discrepancies = []
    for cell, expected in zip(located, case["checks"]):
        text = expected["text"]
        actual = cell["text"] if cell else None
        if actual is None:
            verdict = "missing_value" if text else "missing_blank"
        elif not text:
            verdict = "preserved_blank" if not actual.strip() else "false_fill"
        elif not actual.strip() or cell["status"] in {"numeric_unresolved", "empty_or_unread"}:
            verdict = "unresolved_value"
        elif values_per_cell[cell["row"], cell["column"]] != 1:
            verdict = "joined_values"
        else:
            verdict = "exact" if normalize(actual) == normalize(text) else "wrong_value"
        tally[verdict] += 1
        if verdict not in {"exact", "preserved_blank"}:
            discrepancies.append(dict(expected, actual=actual, verdict=verdict))
    rows = max((cell["row"] + cell.get("row_span", 1) - 1 for cell in cells), default=0)
    cols = max((cell["column"] + cell.get("column_span", 1) - 1 for cell in cells), default=0)
    spans = [[cell["row"], cell["row_span"], cell["text"]] for cell in cells
             if cell["column"] == 1 and cell.get("row_span", 1) > 1]
    return {"case": case["case"], "status": region.get("status"), "iou": round(quality, 3),
            "row_col": [rows, cols], "expected_shape": case["shape"], "spans": spans,
            "tally": dict(tally), "discrepancies": discrepancies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--parser-dir", type=Path, default=HERE.parent,
                        help="Directory containing the inner scanpdf package")
    args = parser.parse_args()
    sys.path.insert(0, str(args.parser_dir.resolve()))
    from scanpdf import parse_pdf

    gold = json.loads(GOLD.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    cases_by_file = defaultdict(list)
    for case in gold["tables"]:
        cases_by_file[case["input"]].append(case)
    results = []
    for cases in cases_by_file.values():
        path = prepare_input(cases[0], args.pdf_dir, args.output)
        selected = ",".join(map(str, sorted({case["page"] for case in cases})))
        parsed = parse_pdf(path, pages=selected, dpi=gold["dpi"], lang=gold["lang"], crops=args.output / "crops")
        (args.output / f"{path.stem}.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n")
        pages = {page["physical_page"]: page for page in parsed["pages"]}
        for case in cases:
            result = score(case, pages[case["page"]])
            results.append(result)
            print(result["case"], result["status"], result["row_col"], result["tally"], flush=True)
    (args.output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
