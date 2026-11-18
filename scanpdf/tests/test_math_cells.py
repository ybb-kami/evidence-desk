from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
import zlib

import pymupdf

from scanpdf.math_cells import review_math_cells
from workbench import ScanLibrary


class MathematicalCellReviewTests(unittest.TestCase):
    def test_ambiguous_notation_keeps_raw_ocr_and_source_geometry(self):
        values = ("O(a? - z)", "O(x-y-z)", "Θ(x²)", "√(x + 1)", "O(log|,(")
        cells = [{"text": text, "status": "ocr_text", "ocr_confidence": 94.2, "bbox": [14, 40, 92, 58],
                  "row": index + 1, "column": 2} for index, text in enumerate(values)]
        table = {"type": "table", "status": "structure_recognized", "cells": cells, "id": "p2-r1"}
        review_math_cells([table])
        self.assertEqual(table["status"], "structure_recognized")
        for original, cell in zip(values, cells):
            self.assertEqual((cell["text"], cell["status"]), ("", "math_unverified"))
            self.assertEqual(cell["ocr_raw"], {"text": original, "confidence": 94.2})
            self.assertEqual(cell["bbox"], [14, 40, 92, 58])
        first = deepcopy(table)
        review_math_cells([table])
        self.assertEqual(table, first)

    def test_simple_math_with_confident_ocr_and_measured_values_survive(self):
        cells = [{"text": text, "status": "ocr_text", "ocr_confidence": 93.0}
                 for text in ("O(x)", "28.4", "6.4 × 10^8", "Measured load", "80 (n=10)")]
        weak = {"text": "O(1)", "status": "ocr_text", "ocr_confidence": 40.0}
        table = {"type": "table", "cells": [*cells, weak]}
        original = deepcopy(cells)
        review_math_cells([table])
        self.assertEqual(cells, original)
        self.assertEqual(weak["math_review"]["reason"], "low_confidence_notation")

    def test_published_cached_page_keeps_original_stored_cell_and_coordinate(self):
        with TemporaryDirectory() as folder, pymupdf.open() as pdf:
            pdf.new_page(width=120, height=180)
            store = ScanLibrary(Path(folder))
            identity = store.add(pdf.tobytes(), "reviewable.pdf")["doc"]["id"]
            cell = {"text": "O(x? - v)", "status": "ocr_text", "ocr_confidence": 89,
                    "bbox_pt": [24, 79, 92, 98], "row": 2, "column": 2}
            cached = {"physical_page": 1, "region_revision": 3,
                      "regions": [{"type": "table", "id": "p1-r1", "cells": [cell]}]}
            with sqlite3.connect(store.db) as db:
                db.execute("INSERT INTO scan_pages VALUES(?,?,?)",
                           (identity, 1, zlib.compress(json.dumps(cached).encode())))
            visible = store.page_info(identity, 1)
            actual = visible["page"]["regions"][0]["cells"][0]
            self.assertEqual((actual["status"], actual["text"]), ("math_unverified", ""))
            self.assertEqual(actual["bbox_pt"], cell["bbox_pt"])
            self.assertEqual(actual["ocr_raw"]["text"], cell["text"])
            self.assertEqual(store.recognize(identity, 1), visible)
            self.assertEqual(store.page_info(identity, 1, raw_cache=True)["page"], cached)
