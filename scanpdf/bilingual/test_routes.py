import unittest
from pathlib import Path

import fitz

from engine import parse_pdf

from .routes import bibliography, read_page, scan_segments


class SourceTests(unittest.TestCase):
    def test_real_pdf_page_and_native_code(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 60), "The model predicts the next token.")
            page.insert_text((40, 110), "print(value)", fontname="cour")
            source = pdf.tobytes()
        result = read_page(source, 1)
        self.assertEqual(result["pages"], 1)
        prose, code = result["segments"]
        self.assertEqual(prose["text"], "The model predicts the next token.")
        self.assertEqual(code["protected"], [[0, len(code["text"])]])
        self.assertLess(prose["bbox"][3], code["bbox"][1])

    def test_ocr_formula_protection_and_page_grouping(self):
        result = scan_segments(
            {
                "lines": [
                    {"line_id": "1.1.1", "text": "Result:", "bbox_pt": [0, 0, 30, 10]},
                    {"line_id": "1.1.2", "text": "x=2", "bbox_pt": [2, 20, 10, 30]},
                ],
                "regions": [{"type": "formula", "bbox_pt": [0, 19, 20, 31]}],
            }
        )
        self.assertEqual(
            result,
            [
                {
                    "block": 1,
                    "text": "Result:\nx=2",
                    "bbox": [0, 0, 30, 30],
                    "protected": [[8, 11]],
                }
            ],
        )

    def test_only_confirmed_numbered_references_are_protected(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            lines = [
                "[3] This is prose before the bibliography.",
                "References",
                "[1] A. Author. An example article, 2020.",
                "[2] B. Author. A second article, 2021.",
                "Appendix",
                "[4] Supplementary example outside the bibliography.",
            ]
            for n, line in enumerate(lines, 1):
                page.insert_text((30, 40 * n), line)
            source = pdf.tobytes()
        refs = bibliography(parse_pdf(source, "fixture.pdf"))
        self.assertEqual(refs, {(1, 3): lines[2], (1, 4): lines[3]})

    def test_repository_sample(self):
        sample = Path(__file__).resolve().parents[2] / "sample.pdf"
        result = read_page(sample.read_bytes(), 2)
        self.assertTrue(
            any("A test you can reproduce" in row["text"] for row in result["segments"])
        )
