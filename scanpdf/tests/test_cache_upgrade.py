"""Region-only refresh of cached OCR pages and their existing evidence anchors."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zlib

import pymupdf

import workbench
from bilingual.routes import scan_segments
from metric_qa import answer_metric

FIXTURES = Path(os.environ.get("SCANPDF_CONTEST_PDF_DIR", "/workspace/shared/contest-pdf"))
QUESTION = (
    "Transformer (big) 模型在 English-to-German 任务上的 BLEU 分数是多少？"
    "当时的 State-of-the-art 模型是多少？提升了多少？"
)


def write_old_page(store, docid, page):
    old = deepcopy(page)
    old.pop("region_revision", None)
    packed = zlib.compress(json.dumps(old, ensure_ascii=False).encode())
    with sqlite3.connect(store.db) as db:
        db.execute("UPDATE scan_pages SET result=? WHERE id=? AND page=?",
                   (packed, docid, old["physical_page"]))
    return old


class CacheUpgradeTests(TestCase):
    def test_explicit_upgrade_preserves_original_text_and_rolls_back_failure(self):
        with TemporaryDirectory() as folder, pymupdf.open() as pdf:
            pdf.new_page()
            data = pdf.tobytes()
            root = Path(folder)
            store = workbench.ScanLibrary(root)
            docid = store.add(data, "original.pdf")["doc"]["id"]
            page = {
                "physical_page": 1, "width_px": 400, "height_px": 600,
                "width_pt": 120, "height_pt": 180, "rotation": 0, "cropbox": [0, 0, 120, 180],
                "lines": [{"text": "Original OCR line", "line_id": "1.1.1", "bbox_pt": [10, 10, 50, 30]}],
                "regions": [{"id": "p1-r1", "type": "table", "bbox_pt": [10, 40, 50, 60]}],
            }
            result = {
                "source": {"sha256": docid}, "pages": [page],
                "render": {"dpi": 240, "lang": "eng", "frame": "rotated_visible_page_top_left"},
            }
            with patch.object(workbench, "recognize_pdf", return_value=deepcopy(result)):
                recognized = store.recognize(docid, 1)
            old = write_old_page(store, docid, recognized["page"])
            before = store.records([docid])[0]
            store = workbench.ScanLibrary(root, lang="chi_sim")
            old_info = store.page_info(docid, 1)
            self.assertTrue(old_info["region_update_available"])
            self.assertEqual(old_info["region_revision"], 0)

            page["lines"][0]["text"] = "Different OCR from a newer release"
            page["regions"][0]["bbox_pt"] = [10, 40, 60, 80]
            wrong_frame = deepcopy(result)
            wrong_frame["pages"][0]["rotation"] = 90
            with patch.object(workbench, "recognize_pdf", return_value=wrong_frame), self.assertRaisesRegex(ValueError, "坐标"):
                store.recognize(docid, 1, region_revision=0)
            self.assertEqual(store.page_info(docid, 1), old_info)

            def recognize_with_overlapping_request(*args, **kwargs):
                with self.assertRaises(workbench.app.Bad) as overlap:
                    store.recognize(docid, 1, region_revision=0)
                self.assertEqual(overlap.exception.code, 409)
                return result

            with patch.object(workbench, "recognize_pdf", side_effect=recognize_with_overlapping_request) as render:
                self.assertEqual(store.recognize(docid, 1), old_info)
                with self.assertRaises(workbench.app.Bad) as stale:
                    store.recognize(docid, 1, region_revision=2)
                self.assertEqual(stale.exception.code, 409)
                render.assert_not_called()
                with patch.object(workbench.app, "MAX_TEXT", 1), self.assertRaisesRegex(ValueError, "容量"):
                    store.recognize(docid, 1, region_revision=0)
                self.assertEqual(store.page_info(docid, 1), old_info)
                upgraded = store.recognize(docid, 1, region_revision=0)
                self.assertEqual(upgraded["page"]["lines"], old["lines"])
                self.assertEqual(upgraded["page"]["regions"], page["regions"])
                self.assertFalse(upgraded["region_update_available"])
                self.assertEqual(store.recognize(docid, 1, region_revision=0), upgraded)
                self.assertEqual(render.call_count, 2)
                self.assertEqual(render.call_args.kwargs, {"pages": "1", "lang": "eng", "dpi": 240})
            self.assertEqual(store.records([docid])[0], before)
            self.assertEqual(store.pdf(docid), data)
            self.assertEqual(scan_segments(upgraded["page"]), scan_segments(old))

    def test_old_contest_scan_regions_can_be_upgraded_for_bleu_question(self):
        path = FIXTURES / "1706.03762v1_img.pdf"
        if not path.is_file():
            self.skipTest("Contest scan not supplied")
        with TemporaryDirectory() as folder:
            data = path.read_bytes()
            root = Path(folder)
            store = workbench.ScanLibrary(root)
            docid = store.add(data, path.name)["doc"]["id"]
            legacy = deepcopy(store.recognize(docid, 8)["page"])
            for region in legacy["regions"]:
                if region["type"] == "table":
                    # The old detector only located this table, with no cell grid.
                    region.update(status="detected_only", cells=[])
            old = write_old_page(store, docid, legacy)
            store = workbench.ScanLibrary(root)
            record = store.records([docid])[0]
            self.assertIsNone(answer_metric(store, record, QUESTION)["answer"])
            updated = store.recognize(docid, 8, region_revision=0)
            self.assertEqual(updated["page"]["lines"], old["lines"])
            self.assertEqual(updated["page"]["render"], old["render"])
            self.assertEqual(store.records([docid])[0], record)
            self.assertEqual(store.pdf(docid), data)
            comparison = answer_metric(store, record, QUESTION)
            self.assertIsNone(comparison["gap"])
            for expected in ("EN-DE", "28.4", "ConvS2S Ensemble", "26.36", "2.04"):
                self.assertIn(expected, comparison["answer"])
            self.assertTrue(all(hit["source"] == "ocr" for hit in comparison["hits"]))
