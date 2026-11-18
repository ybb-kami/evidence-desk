"""The numeric crop pass keeps uncertainty and preserves evidence geometry."""
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from scanpdf.numeric import refine_numeric_cells
from scanpdf.tables import detect_tables


def _word(x, y, text, width=32):
    return {"bbox": [x, y, x + width, y + 14], "text": text, "confidence": 97, "line_id": str(y)}


class NumericCellTests(unittest.TestCase):
    def test_agreement_corrects_raw_text_and_disagreement_abstains(self):
        image = Image.new("L", (120, 125), 255)
        draw = ImageDraw.Draw(image)
        cells = [{"row": 1, "column": 2, "bbox": [0, 0, 110, 20], "text": "Score", "status": "ocr_text"}]
        for row, text in enumerate(("16.4", "99.0", "30.1", ""), 2):
            top = 20 + (row - 2) * 25
            cells.append({"row": row, "column": 2, "bbox": [0, top, 110, top + 25],
                          "text": text, "status": "ocr_text" if text else "empty_or_unread",
                          "ocr_confidence": 92 if text else None})
            if text:
                draw.rectangle((41, top + 4, 57, top + 20), fill=0)

        def values(*texts):
            return {i: {"text": text, "confidence": 95, "single_token": True} for i, text in enumerate(texts)}

        strips = [values("18.4", "99.0", "30.1"), values("18.4", "95.0", "30.1")]
        with patch("scanpdf.numeric._strip_reads", side_effect=strips), patch(
                "scanpdf.numeric.recognize", return_value=[]):
            refine_numeric_cells(image, [{"cells": cells}], 144, "eng")

        self.assertEqual(cells[1]["text"], "18.4")
        self.assertEqual(cells[1]["ocr_raw"], {"text": "16.4", "confidence": 92})
        self.assertEqual(cells[1]["numeric_ocr"]["bbox"], [41, 24, 58, 41])
        self.assertEqual(cells[1]["numeric_ocr"]["status"], "agreed")
        self.assertEqual(cells[2]["status"], "numeric_unresolved")
        self.assertEqual(cells[2]["text"], "")
        self.assertEqual(cells[2]["ocr_raw"]["text"], "99.0")
        self.assertEqual({attempt["text"] for attempt in cells[2]["numeric_ocr"]["attempts"] if attempt["valid"]},
                         {"99.0", "95.0"})
        self.assertEqual(cells[3]["text"], "30.1")
        self.assertEqual(cells[4]["status"], "empty_or_unread")
        self.assertNotIn("numeric_ocr", cells[4])

    def test_a_sparse_stub_is_anchored_by_distinct_ruled_groups(self):
        image = Image.new("L", (420, 360), 255)
        draw = ImageDraw.Draw(image)
        for y in (30, 63, 150, 237, 324):
            draw.line((35, y, 385, y), fill=0, width=2)
        words = [_word(43, 40, "System"), _word(149, 40, "Size"), _word(272, 40, "Score")]
        for index, y in enumerate((74, 103, 132, 161, 190, 219, 248, 277, 306)):
            words.extend((_word(164, y, "10M"), _word(277, y, "3.5")))
            if index % 3 == 0:
                words.append(_word(43, y, "Series"))
        table, = detect_tables(image, words, 144)
        self.assertEqual(table["method"], "horizontal_rules_sparse_stub_ocr_alignment")
        cells = {(cell["row"], cell["column"]): cell for cell in table["cells"]}
        self.assertEqual([cells[2, col]["text"] for col in (1, 2, 3)], ["Series", "10M", "3.5"])
        self.assertEqual(cells[3, 1]["status"], "empty_or_unread")
        self.assertEqual(cells[3, 2]["text"], "10M")
