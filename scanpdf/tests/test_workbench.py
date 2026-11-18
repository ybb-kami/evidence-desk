"""Regression coverage for atomic mixed-PDF OCR indexing and cached retries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
import workbench


class WorkbenchTests(unittest.TestCase):
    def test_ocr_retry_and_native_page_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = workbench.ScanLibrary(Path(folder))
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((72, 75), "Native control")
                pdf.new_page()
                original = pdf.tobytes()
            digest = store.add(original, "mixed.pdf")["doc"]["id"]
            before = store.records()[0]
            self.assertEqual(store.page_info(digest, 2)["pending_pages"], [2])
            page = {"physical_page": 2, "lines": [{"line_id": "1.1.1", "text": "Page two OCR"}],
                    "regions": []}
            result = {"source": {"sha256": digest}, "render": {"dpi": 240}, "pages": [page]}
            with patch.object(workbench, "recognize_pdf", return_value=result) as recognize:
                with self.assertRaises(workbench.app.Bad):
                    store.recognize(digest, 1)
                recognize.assert_not_called()
                with patch.object(workbench.app, "MAX_TEXT", 1), self.assertRaises(ValueError):
                    store.recognize(digest, 2)
                self.assertEqual(store.records(), [before])
                self.assertIsNone(store.page_info(digest, 2)["page"])
                first = store.recognize(digest, 2)
                second = store.recognize(digest, 2)
                self.assertEqual(first, second)
                self.assertEqual(recognize.call_count, 2)
            after = store.records()[0]
            self.assertEqual(first["native_textless"], [2])
            self.assertEqual(first["pending_pages"], [])
            self.assertEqual(after["textless_pages"], [])
            self.assertEqual([c for c in after["chunks"] if c["page"] == 1], before["chunks"])
            self.assertEqual(after["chunks"][-1]["source"], "ocr")
            self.assertEqual(store.pdf(digest), original)
            self.assertTrue(store.add(original, "renamed.pdf")["existing"])
            self.assertEqual(workbench.ScanLibrary(Path(folder)).page_info(digest, 2), first)
            store.set_archived(digest, True)
            with self.assertRaises(workbench.app.Bad):
                store.recognize(digest, 2)
            with self.assertRaises(workbench.app.Bad):
                store.page_info(digest, 2)
            store.set_archived(digest, False)
            self.assertEqual(store.page_info(digest, 2), first)
