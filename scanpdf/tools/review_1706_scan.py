"""Compare parser JSON for the pinned contest-package mirror with visual page anchors.

The reference boxes enclose the ruled table body or the full numbered equation
(including its label), in visible page points. This script measures localization.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


EXPECTED_SHA = "64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d"
# Measured on the scan; the four table captions sit above the reference boxes.
ANCHORS = [
    ("Table 1", "table", 6, [118, 113, 494, 187]),
    ("Table 2", "table", 8, [130, 94, 482, 244]),
    ("Table 3", "table", 9, [108, 130, 509, 372]),
    ("Table 4", "table", 9, [145, 414, 467, 546]),
    ("Equation 1", "formula", 3, [220, 700, 504, 725]),
    ("Equation 2", "formula", 5, [227, 294, 504, 305]),
    ("Equation 3", "formula", 7, [163, 280, 504, 294]),
]


def iou(left, right):
    def area(box):
        return max(0, box[2] - box[0]) * max(0, box[3] - box[1])

    intersection = area([max(left[0], right[0]), max(left[1], right[1]),
                         min(left[2], right[2]), min(left[3], right[3])])
    return intersection / (area(left) + area(right) - intersection)


def review(result):
    if result["source"]["sha256"] != EXPECTED_SHA:
        raise ValueError("Input SHA-256 differs from the reviewed mirror")
    pages = {p["physical_page"]: p for p in result["pages"]}
    if sorted(pages) != list(range(1, 16)):
        raise ValueError("The comparison requires physical pages 1 through 15")
    matches = []
    for label, kind, number, box in ANCHORS:
        candidates = [r for r in pages[number]["regions"] if r["type"] == kind]
        region = max(candidates, key=lambda r: iou(r["bbox_pt"], box), default=None)
        score = iou(region["bbox_pt"], box) if region else 0
        matches.append({
            "anchor": label, "physical_page": number, "reference_bbox_pt": box,
            "iou": round(score, 3), "localized_at_0.5": score >= 0.5,
            "best_overlapping_candidate": region["id"] if score else None,
            "candidate_status": region["status"] if score else None,
        })
    counts = Counter((r["type"], r["status"]) for p in pages.values() for r in p["regions"])
    return {
        "source": result["source"], "render": result["render"],
        "pages_with_ocr_lines": sum(bool(p["lines"]) for p in pages.values()),
        "region_counts": {f"{kind}/{status}": n for (kind, status), n in sorted(counts.items())},
        "anchors": matches,
    }


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("result", type=Path, help="Full scanpdf JSON from this 15-page scan")
    args = cli.parse_args()
    print(json.dumps(review(json.loads(args.result.read_text(encoding="utf-8"))), indent=2))
