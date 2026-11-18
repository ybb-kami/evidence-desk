"""Regression cases for document-scoped BLEU table comparisons."""

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from metric_qa import _column, _compare, answer_metric
from workbench import ScanLibrary


QUESTION = (
    "Transformer (big) 模型在 English-to-German 任务上的 BLEU 分数是多少？"
    "当时的 State-of-the-art 模型是多少？提升了多少？"
)
FIXTURES = Path(os.environ.get("SCANPDF_CONTEST_PDF_DIR", "/workspace/shared/contest-pdf"))


def create_table():
    cells = []

    def add(row, column, text, **spans):
        x0, y0 = 30 * column, 30 * row
        cells.append(dict(row=row, column=column, text=text, bbox=[x0, y0, x0 + 30, y0 + 30], **spans))

    add(1, 1, "Model", row_span=2)
    add(1, 2, "BLEU", column_span=2)
    add(1, 4, "Training", column_span=2)
    for column, task in enumerate(["EN-DE", "EN-FR", "EN-DE", "EN-FR"], 2):
        add(2, column, task)
    rows = [
        ("Reference A [5]", "24.75", "65"),
        ("Reference B [8]", "26.22", "60"),
        ("Reference C |9]", "", "59"),
        ("Proposed (base)", "28.1", "90"),
        ("Proposed (large)", "30.00", "92"),
    ]
    for row, values in enumerate(rows, 3):
        for column, text in enumerate([*values, "99", "99"], 1):
            add(row, column, text)
    return {"cells": cells}


class ScoreSelectionTests(unittest.TestCase):
    def setUp(self):
        self.table = create_table()
        self.image = Image.new("L", (240, 280), 255)
        self.question = "Proposed (large) 的 EN-DE BLEU 比以往最佳提升多少？"

    def compare(self):
        headers = _column(self.table, "EN-DE")
        return _compare(self.table, headers, self.question, self.image, 240)

    def test_selects_leaf_metric_and_best_prior_from_updated_inputs(self):
        _, score, maximum, leaders, _, _ = self.compare()
        self.assertEqual(score, Decimal("30.00"))
        self.assertEqual(maximum, Decimal("26.22"))
        self.assertEqual(leaders[0][1], "Reference B")
        score_to_change = next(c for c in self.table["cells"] if (c["row"], c["column"]) == (3, 2))
        score_to_change["text"] = "26.35"
        _, score, maximum, leaders, _, _ = self.compare()
        self.assertEqual(score - maximum, Decimal("3.65"))
        self.assertEqual(leaders[0][1], "Reference A")

    def test_ink_in_unread_cell_and_garbled_score_both_block_comparison(self):
        blank = next(c for c in self.table["cells"] if (c["row"], c["column"]) == (5, 2))
        x0, y0, x1, y1 = blank["bbox"]
        self.image.paste(0, (x0 + 8, y0 + 8, x1 - 8, y1 - 8))
        self.assertIsNone(self.compare())

        self.image.paste(255, blank["bbox"])
        blank["text"] = "= 26.00"
        self.assertIsNone(self.compare())


class ActualAttentionMetricTests(unittest.TestCase):
    def check_comparison(self, store, record):
        result = answer_metric(store, record, QUESTION)
        self.assertIsNone(result["gap"])
        for expected in ("EN-DE", "28.4", "ConvS2S Ensemble", "26.36", "2.04"):
            self.assertIn(expected, result["answer"])
        self.assertTrue(any("BLEU" in hit["text"] and "state-of-the-art" in hit["text"] for hit in result["hits"]))
        target = next(hit for hit in result["hits"] if "Transformer (big)" in hit["text"] and "28.4" in hit["text"])
        prior = next(hit for hit in result["hits"] if "ConvS2S Ensemble" in hit["text"] and "26.36" in hit["text"])
        self.assertEqual(target["page"], prior["page"])
        self.assertTrue(all(hit["id"] == record["id"] for hit in result["hits"]))
        return result

    def test_native_v1_and_v7_and_inconsistent_other_task(self):
        for version in (1, 7):
            path = FIXTURES / f"1706.03762v{version}.pdf"
            if not path.is_file():
                self.skipTest("Contest PDFs not supplied")
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                store = ScanLibrary(Path(tmp))
                docid = store.add(path.read_bytes(), "renamed.pdf")["doc"]["id"]
                record = store.records([docid])[0]
                self.check_comparison(store, record)
                if version == 7:
                    different_task = QUESTION.replace("English-to-German", "English-to-French")
                    inconsistent = answer_metric(store, record, different_task)
                    self.assertIsNone(inconsistent["answer"])
                    self.assertIn("不一致", inconsistent["gap"])

    def test_actual_scan_requires_recognized_table_and_rejects_garbled_score(self):
        path = FIXTURES / "1706.03762v1_img.pdf"
        if not path.is_file():
            self.skipTest("Contest scan not supplied")
        with tempfile.TemporaryDirectory() as tmp:
            store = ScanLibrary(Path(tmp))
            docid = store.add(path.read_bytes(), path.name)["doc"]["id"]
            not_read = store.records([docid])[0]
            self.assertIsNone(answer_metric(store, not_read, QUESTION)["answer"])
            info = store.recognize(docid, 8)
            record = store.records([docid])[0]
            result = self.check_comparison(store, record)
            self.assertTrue(all(hit["source"] == "ocr" for hit in result["hits"]))

            damaged = deepcopy(info)
            table = next(region for region in damaged["page"]["regions"] if region["type"] == "table")
            winner = next(cell for cell in table["cells"] if cell["text"] == "26.36")
            winner["text"] = "26.3b"
            with patch.object(store, "page_info", return_value=damaged):
                self.assertIsNone(answer_metric(store, record, QUESTION)["answer"])
