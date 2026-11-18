"""Exercise idempotent import and storage failure boundaries on temporary libraries."""

from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import patch

import pymupdf

import app
from .load import V1, V7, preload


def pdf(text=None):
    with pymupdf.open() as source:
        page = source.new_page()
        if text:
            page.insert_text((72,72), text)
        return source.tobytes()


class PreloadTest(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = self.root / "pdf"
        self.sources.mkdir()
        self.data = self.root / "library"

    def run_preload(self, *names):
        return preload(self.data, self.sources, mode="explicit" if names else "all", names=names)

    def test_existing_user_and_archive_state_survive_aliases_and_retries(self):
        store = app.Library(self.data)
        personal = store.add(pdf("This is the user's original PDF."), "my-upload.pdf")["doc"]
        original = pdf("This paper was already archived.")
        older = store.add(original, "previous name.pdf")["doc"]
        store.set_archived(older["id"], True)
        (self.sources / "archived alias.pdf").write_bytes(original)
        first = pdf("New calibration results.")
        (self.sources / "preloaded.pdf").write_bytes(first)
        report = self.run_preload()
        self.assertEqual(report["states"], {"existing_archived":1,"imported":1})
        self.assertEqual(report["files"][0]["library_name"], older["name"])
        self.assertEqual(report["before"]["active_docs"], 1)
        self.assertEqual(report["after"]["archived_docs"], 1)
        self.assertEqual(store.pdf(older["id"]), original)
        self.assertEqual(store.records([personal["id"]])[0]["name"], personal["name"])
        self.assertEqual(store.records([older["id"]], archived=True)[0]["name"], older["name"])
        again = self.run_preload()
        self.assertEqual(again["states"], {"existing_archived":1,"existing_active":1})
        self.assertEqual(again["before"], again["after"])

    def test_failure_is_per_file_and_retry_keeps_successful_rows(self):
        before = pdf("A complete paper.")
        broken = self.sources / "02-broken.pdf"
        broken.write_bytes(b"unreadable content")
        (self.sources / "01-first.pdf").write_bytes(before)
        (self.sources / "03-last.pdf").write_bytes(pdf("A later successful paper."))
        report = self.run_preload()
        self.assertEqual(report["states"], {"imported":2,"rejected":1})
        self.assertEqual(report["failed"], 1)
        broken.write_bytes(pdf("The original folder is now complete."))
        again = self.run_preload()
        self.assertEqual(again["states"], {"existing_active":2,"imported":1})
        self.assertEqual(app.Library(self.data).pdf(sha256(before).hexdigest()), before)

    def test_interruption_during_next_import_preserves_committed_item(self):
        first = pdf("A complete paper before the interruption.")
        second = pdf("An import interrupted during extraction.")
        (self.sources / "first.pdf").write_bytes(first)
        (self.sources / "second.pdf").write_bytes(second)
        marker = self.root / "in-second-transaction"
        script = """
import app
from pathlib import Path
import sys
from time import sleep
from scanpdf.preload.load import preload

parse = app.parse_pdf
def wait_during_import(data, name):
    if name == 'second.pdf':
        Path(sys.argv[3]).touch()
        sleep(60)
    return parse(data, name)

app.parse_pdf = wait_during_import
preload(sys.argv[1], sys.argv[2], mode='all')
"""
        process = subprocess.Popen([sys.executable, "-c", script, str(self.data), str(self.sources), str(marker)],
                                   cwd=Path(__file__).resolve().parents[2])
        try:
            deadline = monotonic() + 8
            while not marker.is_file() and process.poll() is None and monotonic() < deadline:
                sleep(0.05)
            self.assertTrue(marker.is_file())
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        report = self.run_preload()
        self.assertEqual(report["states"], {"existing_active":1,"imported":1})
        store = app.Library(self.data)
        self.assertEqual(store.pdf(sha256(first).hexdigest()), first)
        self.assertEqual(store.pdf(sha256(second).hexdigest()), second)

    def test_upload_and_library_capacity_can_reject_without_eviction(self):
        (self.sources / "one.pdf").write_bytes(pdf("First."))
        (self.sources / "two.pdf").write_bytes(pdf("Second."))
        with patch.object(app, "MAX_DOCS", 1):
            report = self.run_preload()
            self.assertEqual(report["states"], {"imported":1,"rejected":1})
            self.assertEqual(report["after"]["max_next_upload_original_bytes"], 0)
            self.assertEqual(self.run_preload("one.pdf")["states"], {"existing_active":1})
        with (self.sources / "over-limit.pdf").open("wb") as file:
            file.truncate(app.UPLOAD_LIMIT + 1)
        report = self.run_preload("over-limit.pdf")
        self.assertEqual(report["states"], {"rejected":1})
        self.assertIn("24 MiB", report["files"][0]["error"])
        with patch.object(app, "MAX_TEXT", 1):
            report = self.run_preload("two.pdf")
            self.assertEqual(report["states"], {"rejected":1})
            self.assertIn("索引", report["files"][0]["error"])

    def test_basename_scope_and_nonregular_files(self):
        outside = self.root / "out.pdf"
        outside.write_bytes(pdf("An unselected external original."))
        (self.sources / "folder.pdf").mkdir()
        names = ["../out.pdf", str(outside), "folder.pdf", "missing.pdf", "filename\n.pdf"]
        if os.name != "nt":
            (self.sources / "alias.pdf").symlink_to(outside)
            os.mkfifo(self.sources / "pipe.pdf")
            names.extend(["alias.pdf", "pipe.pdf"])
        self.assertEqual(self.run_preload(*names)["failed"], len(names))
        self.assertEqual(self.run_preload(*names)["after"]["active_docs"], 0)

    def test_version_order_and_textless_status(self):
        (self.sources / V1).write_bytes(pdf("Version one sample."))
        (self.sources / V7).write_bytes(pdf("Version seven sample."))
        (self.sources / "image-only.pdf").write_bytes(pdf())
        report = self.run_preload()
        self.assertEqual([item["file"] for item in report["files"][:2]], [V7,V1])
        scan = report["files"][2]
        self.assertEqual(scan["pending_textless_pages"], [1])
        self.assertEqual(scan["recognized_textless_pages"], [])
        with closing(sqlite3.connect(self.data / "library.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE scan_pages(id TEXT, page INTEGER, result BLOB, PRIMARY KEY(id,page))")
            connection.execute("INSERT INTO scan_pages VALUES (?,?,?)", (scan["id"],1,b"cached"))
        result = self.run_preload("image-only.pdf")["files"][0]
        self.assertEqual(result["pending_textless_pages"], [])
        self.assertEqual(result["recognized_textless_pages"], [1])
