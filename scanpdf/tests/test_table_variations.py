"""Regression coverage for ruled variation tables with sparse settings."""

import os
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from scanpdf import parse_pdf
from scanpdf.tables import detect_tables


class GroupedVariationTests(unittest.TestCase):
    def test_shared_left_labels_and_repeated_results_leave_parameters_blank(self):
        page = Image.new("L", (720, 500), 255)
        draw = ImageDraw.Draw(page)
        for y in [61, 111, 151, 243, 333]:
            draw.line((80, y, 650, y), fill=0, width=2)
        for x in [140, 470]:
            draw.line((x, 61, x, 333), fill=0, width=2)
        ocr = []

        def add(y, values):
            for x, text in values:
                ocr.append(dict(text=text, bbox=[x - 16, y, x + 16, y + 14], line_id=str(y), confidence=98))

        add(77, [(186, "layers"), (286, "heads"), (382, "width"), (515, "loss"), (610, "score")])
        add(122, [(108, "base"), (186, "6"), (286, "4"), (382, "64"), (515, "4.50"), (610, "24.1")])
        add(191, [(108, "(X)")])
        add(164, [(186, "2"), (515, "5.25"), (610, "22.2")])
        add(208, [(186, "4"), (515, "4.83"), (610, "23.0")])
        add(282, [(108, "(Y)")])
        add(255, [(382, "128"), (515, "4.81"), (610, "23.3")])
        add(298, [(382, "256"), (515, "4.44"), (610, "24.5")])

        (table,) = detect_tables(page, ocr, 144)
        self.assertEqual(table["status"], "partial_structure")
        self.assertEqual(table["method"], "vertical_rules_grouped_ocr_alignment")
        cells = {(cell["row"], cell["column"]): cell for cell in table["cells"]}
        self.assertEqual((cells[3, 1]["text"], cells[3, 1]["row_span"]), ("(X)", 2))
        self.assertEqual((cells[5, 1]["text"], cells[5, 1]["row_span"]), ("(Y)", 2))
        self.assertNotIn((4, 1), cells)
        self.assertEqual(cells[6, 4]["text"], "256")
        self.assertEqual(cells[6, 6]["text"], "24.5")
        self.assertEqual(cells[6, 2]["status"], "empty_or_unread")
        self.assertEqual(cells[6, 2]["text"], "")

        # A broken outcome lane cannot support the same group reconstruction.
        reduced = [word for word in ocr if word["text"] != "23.3"]
        self.assertFalse(any(t["method"] == table["method"] for t in detect_tables(page, reduced, 144)))


class OfficialVariationTests(unittest.TestCase):
    def test_sample_variation_groups_and_verified_scores(self):
        root = Path(os.environ.get("SCANPDF_CONTEST_PDF_DIR", "/workspace/shared/contest-pdf"))
        source = root / "1706.03762v1_img.pdf"
        if not source.is_file():
            self.skipTest("Contest scan not supplied")
        result = parse_pdf(source, pages="9")
        tables = [region for region in result["pages"][0]["regions"] if region["type"] == "table"]
        variations = next(table for table in tables if table["method"] == "vertical_rules_grouped_ocr_alignment")
        self.assertEqual(variations["status"], "partial_structure")
        cells = {(cell["row"], cell["column"]): cell for cell in variations["cells"]}
        self.assertEqual(len(cells), 247)
        self.assertIn("PPL", cells[1, 11]["text"])
        self.assertIn("BLEU", cells[1, 12]["text"])
        self.assertIn("steps", cells[1, 10]["text"])
        self.assertEqual(
            [(cells[row, 1]["text"], cells[row, 1]["row_span"]) for row in [3, 7, 9, 16]],
            [("(A)", 4), ("(B)", 2), ("(C)", 7), ("(D)", 4)],
        )
        self.assertEqual([cells[7, col]["text"] for col in [6, 11, 12, 13]], ["16", "5.16", "25.1", "58"])
        self.assertEqual([cells[8, col]["text"] for col in [6, 11, 12, 13]], ["32", "5.01", "25.4", "60"])
        self.assertEqual(cells[8, 2]["text"], "")
        self.assertNotIn((8, 1), cells)
