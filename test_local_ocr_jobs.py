"""The explicit background job neither modifies nor imports an original scan."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

import fitz

from local_ocr_jobs import LocalOCRJobs, OCRJobError, _filename
from ocr_local import MAX_BYTES, create_derivative
from test_ocr_local import english_available


def wait_for(manager, status, duration=8):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        current = manager.status()
        if current["status"] == status:
            return current
        if current["status"] in {"succeeded", "failed"}:
            raise AssertionError(current)
        time.sleep(.02)
    raise AssertionError(manager.status())


@unittest.skipUnless(os.name == "posix", "local OCR requires POSIX")
class LocalOCRJobTests(unittest.TestCase):
    def test_exclusive_and_manual_output_lifecycle(self):
        original = b"%PDF-simulated-original"
        derivative = b"%PDF-simulated-derivative"
        release = threading.Event()
        def worker(source, output, *, cancel_event):
            while not release.wait(.01):
                if cancel_event.is_set():
                    raise ValueError("stopped")
            if source.read_bytes() != original:
                raise ValueError("original changed")
            output.write_bytes(derivative)

        jobs = LocalOCRJobs(worker=worker)
        self.addCleanup(jobs.close)
        self.assertEqual(jobs.status(), {"status": "idle"})
        first = jobs.start("a"*64, "已有稿\r\n.pdf", original)
        self.assertEqual(first["status"], "running")
        self.assertEqual(first["source_id"], "a"*64)
        with self.assertRaises(OCRJobError) as pending:
            jobs.result(first["job_id"])
        self.assertEqual(pending.exception.code, 409)
        with self.assertRaises(OCRJobError) as active:
            jobs.start("b"*64, "second.pdf", original)
        self.assertEqual(active.exception.code, 409)
        old_dir = jobs._directory
        release.set()
        succeeded = wait_for(jobs, "succeeded")
        self.assertEqual(jobs.result(succeeded["job_id"]), (derivative, "已有稿__.ocr.pdf"))
        self.assertFalse((old_dir/"source.pdf").exists())
        second = jobs.start("b"*64, "second.pdf", original)
        self.assertNotEqual(second["job_id"], first["job_id"])
        self.assertFalse(old_dir.exists())
        with self.assertRaises(OCRJobError) as expired:
            jobs.result(first["job_id"])
        self.assertEqual(expired.exception.code, 404)
        self.assertEqual(wait_for(jobs, "succeeded")["filename"], "second.ocr.pdf")
        last_dir = jobs._directory
        jobs.close()
        self.assertFalse(last_dir.exists())

    def test_close_signals_worker_and_cleans_private_job(self):
        entered = threading.Event()
        cancelled = threading.Event()
        def worker(source, output, *, cancel_event):
            entered.set()
            if cancel_event.wait(2):
                cancelled.set()
                raise ValueError("cancelled")
        jobs = LocalOCRJobs(worker=worker)
        self.addCleanup(jobs.close)
        jobs.start("b"*64, "source.pdf", b"%PDF-input")
        self.assertTrue(entered.wait(1))
        active_dir = jobs._directory
        jobs.close()
        self.assertTrue(cancelled.is_set())
        self.assertFalse(active_dir.exists())
        with self.assertRaises(OCRJobError) as closed:
            jobs.start("b"*64, "source.pdf", b"%PDF-input")
        self.assertEqual(closed.exception.code, 503)

    def test_oversize_and_non_pdf_fail_before_worker(self):
        jobs = LocalOCRJobs()
        self.addCleanup(jobs.close)
        for data, status in [(b"data",400), (b"%PDF-"+b"0"*MAX_BYTES,413)]:
            with self.assertRaises(OCRJobError) as rejected:
                jobs.start("b"*64, "source.pdf", data)
            self.assertEqual(rejected.exception.code, status)
            self.assertEqual(jobs.status()["status"], "idle")
        self.assertEqual(_filename("../escape.pdf"), "escape.ocr.pdf")

    def test_cancelled_cli_helper_keeps_source_and_writes_no_derivative(self):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder)/"source.pdf", Path(folder)/"result.ocr.pdf"
            original = b"%PDF-cancelled-before-worker"
            source.write_bytes(original)
            event = threading.Event()
            event.set()
            with self.assertRaisesRegex(ValueError, "stopped"):
                create_derivative(source, destination, cancel_event=event)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(destination.exists())

    @unittest.skipUnless(english_available(), "requires local Tesseract with English trained data")
    def test_real_image_only_pdf_to_downloadable_derivative(self):
        with fitz.open() as vector, fitz.open() as scan:
            page = vector.new_page(width=520, height=300)
            page.insert_text((45,100), "A verified local scan", fontsize=28, fontname="hebo")
            copy = scan.new_page(width=520,height=300)
            copy.insert_image(copy.rect,pixmap=page.get_pixmap(dpi=150,alpha=False))
            original = scan.tobytes(deflate=True)
        jobs = LocalOCRJobs()
        self.addCleanup(jobs.close)
        first = jobs.start("a"*64,"local.pdf",original)
        self.assertEqual(first["status"],"running")
        current = wait_for(jobs,"succeeded")
        content, name = jobs.result(current["job_id"])
        self.assertEqual(name,"local.ocr.pdf")
        with fitz.open(stream=content,filetype="pdf") as result:
            self.assertIn("A verified local scan",result[0].get_text())
        with fitz.open(stream=original,filetype="pdf") as original_pdf:
            self.assertFalse(original_pdf[0].get_text().strip())


if __name__ == "__main__":
    unittest.main()
