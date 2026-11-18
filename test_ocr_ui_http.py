"""HTTP OCR workflow: no auto import, original bytes stable, local download."""
import os
import time
import unittest
from urllib.error import HTTPError

import fitz

from test_acceptance import LocalHTTPFixture
from test_ocr_local import english_available


@unittest.skipUnless(os.name == "posix" and english_available(), "requires POSIX and Tesseract eng")
class OCRHTTPAcceptance(LocalHTTPFixture, unittest.TestCase):
    def test_explicit_request_produces_separate_attachment_and_keeps_original(self):
        with fitz.open() as native, fitz.open() as image_only:
            page = native.new_page(width=520, height=300)
            page.insert_text((45, 110), "A readable local scan", fontsize=28, fontname="hebo")
            copy = image_only.new_page(width=520, height=300)
            copy.insert_image(copy.rect, pixmap=page.get_pixmap(dpi=150, alpha=False))
            source = image_only.tobytes(deflate=True)
        self.assertEqual(self.read_json("/api/ocr")["status"], "idle")
        doc = self.import_pdf("OCR 验收.pdf", source)["doc"]
        with self.assertRaises(HTTPError) as wrong:
            self.read_json("/api/ocr", {"id":"f"*64})
        with wrong.exception as response:
            self.assertEqual(response.code, 404)
        self.assertEqual(self.read_json("/api/ocr")["status"], "idle")
        started = self.read_json("/api/ocr", {"id":doc["id"]})
        self.assertEqual(started["status"], "running")
        self.assertEqual(started["source_id"], doc["id"])
        for _ in range(250):
            state = self.read_json("/api/ocr")
            if state["status"] != "running":
                break
            time.sleep(.02)
        self.assertEqual(state["status"], "succeeded", state)
        self.assertEqual(state["job_id"], started["job_id"])
        result, headers = self.request("/api/ocr/result/"+state["job_id"])
        self.assertTrue(headers["Content-Type"].startswith("application/pdf"))
        self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
        with fitz.open(stream=result, filetype="pdf") as pdf:
            self.assertIn("A readable local scan",pdf[0].get_text())
        self.assertEqual([record["id"] for record in self.read_json("/api/library")["docs"]], [doc["id"]])
        self.assertEqual(self.request("/pdf/"+doc["id"])[0], source)
        derivative = self.import_pdf(state["filename"], result)["doc"]
        self.assertNotEqual(doc["id"], derivative["id"])
        self.assertEqual(len(self.read_json("/api/library")["docs"]), 2)
        evidence = self.read_json("/api/search", {"query":"readable local scan","ids":[derivative["id"]]})
        self.assertEqual(evidence["hits"][0]["page"], 1)
        self.assertEqual(self.request("/pdf/"+doc["id"])[0], source)


if __name__ == "__main__":
    unittest.main()
