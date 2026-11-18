"""Regression and optional real-book acceptance checks for the persistent cache."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from . import DenseIndex, DenseUnavailable, index, persistence


ROOT = Path(__file__).resolve().parents[2]


class Encoder:
    np = np

    def __init__(self):
        self.passage_calls = 0

    def encode(self, texts, prefix):
        if prefix == index.PASSAGE_PREFIX:
            self.passage_calls += 1
        vectors = np.zeros((len(texts), index.DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            vectors[row, 0 if "photoluminescence" in text else 1] = 1
        return vectors


def service(folder):
    dense = DenseIndex(cache_dir=folder)
    dense._encoder = Encoder()
    return dense


def document():
    return {"id": "9" * 64, "name": "Control.pdf", "page_labels": ["i", "1"], "chunks": [
        {"page": 1, "block": 1, "part": 1, "offset": 0, "text": "The instrument was calibrated daily."},
    ]}


class PersistentIndexTest(TestCase):
    def test_reopened_index_skips_passage_encoding_and_keeps_scope(self):
        with TemporaryDirectory() as root:
            folder = Path(root) / "first-library"
            doc = document()
            first = service(folder)
            original = first.search([doc], "instrument calibration")
            self.assertEqual(first._encoder.passage_calls, 1)
            restarted = service(folder)
            self.assertEqual(restarted.search([doc], "instrument calibration"), original)
            self.assertEqual(restarted._encoder.passage_calls, 0)
            second_library = service(Path(root) / "second-library")
            self.assertEqual(second_library.search([doc], "instrument calibration"), original)
            self.assertEqual(second_library._encoder.passage_calls, 1)

    def test_recognized_page_and_scoped_passage_use_distinct_cache_entries(self):
        with TemporaryDirectory() as root:
            before = document()
            self.assertEqual(service(root).search([before], "photoluminescence")[0]["page"], 1)
            ocr = {"page": 2, "block": 3, "part": 2, "offset": 17, "source": "ocr",
                   "text": "The photoluminescence peak was observed at 530 nm."}
            after = dict(before, chunks=before["chunks"] + [ocr])
            updated = service(root)
            expected = updated.search([after], "photoluminescence")
            self.assertEqual(updated._encoder.passage_calls, 1)
            self.assertEqual(expected[0], dict(ocr, id=after["id"], name=after["name"], page_label="1", score=1.0))
            scoped = dict(after, chunks=[ocr])
            scoped_hits = updated.search([scoped], "photoluminescence")
            self.assertEqual(scoped_hits, expected[:1])
            self.assertEqual(updated._encoder.passage_calls, 2)
            restarted = service(root)
            self.assertEqual(restarted.search([scoped], "photoluminescence"), scoped_hits)
            self.assertEqual(restarted.search([after], "photoluminescence"), expected)
            self.assertEqual(restarted.search([before], "photoluminescence")[0]["page"], 1)
            self.assertEqual(restarted._encoder.passage_calls, 0)

    def test_preprocessing_model_and_order_are_part_of_cache_identity(self):
        with TemporaryDirectory() as root:
            doc = document()
            doc["chunks"].append(dict(doc["chunks"][0], page=2, text="The illumination remained stable."))
            service(root).search([doc], "illumination")
            reordered = dict(doc, chunks=list(reversed(doc["chunks"])))
            dense = service(root)
            dense.search([reordered], "illumination")
            self.assertEqual(dense._encoder.passage_calls, 1)
            with patch.object(index, "PREPROCESSING_VERSION", index.PREPROCESSING_VERSION + 1):
                revised = service(root)
                revised.search([doc], "illumination")
                self.assertEqual(revised._encoder.passage_calls, 1)
            with patch.object(index, "REVISION", "revised-model"):
                revised = service(root)
                revised.search([doc], "illumination")
                self.assertEqual(revised._encoder.passage_calls, 1)
            another_original = dict(doc, id="8" * 64)
            dense = service(root)
            dense.search([another_original], "illumination")
            self.assertEqual(dense._encoder.passage_calls, 1)

    def test_wrong_shape_and_payload_damage_rebuild_automatically(self):
        with TemporaryDirectory() as root:
            doc = document()
            service(root).search([doc], "instrument")
            file, = Path(root).glob("*.vec")
            header, payload = file.read_bytes().split(b"\n", 1)
            wrong_shape = json.loads(header) | {"shape": [index.DIMENSIONS, 1]}
            for corrupted in (header + b"\n" + payload[:-1] + bytes([payload[-1] ^ 1]),
                              json.dumps(wrong_shape).encode() + b"\n" + payload,
                              b"half-written cache"):
                with self.subTest(corruption=corrupted[:32]):
                    file.write_bytes(corrupted)
                    dense = service(root)
                    dense.search([doc], "instrument")
                    self.assertEqual(dense._encoder.passage_calls, 1)
                    dense = service(root)
                    dense.search([doc], "instrument")
                    self.assertEqual(dense._encoder.passage_calls, 0)


class VectorCacheTest(TestCase):
    def test_recency_and_disk_bounds(self):
        with (TemporaryDirectory() as root, patch.object(persistence, "MAX_ENTRIES", 2),
              patch.object(persistence, "MAX_DISK_BYTES", 4096)):
            cache = persistence.VectorCache(root, "test-model", 128)
            values = np.ones((2, 128), dtype=np.float32)
            keys = [(str(i), sha256(str(i).encode()).digest()) for i in range(4)]
            cache.save(keys[0], 2, values)
            cache.save(keys[1], 2, values)
            np.testing.assert_array_equal(cache.load(keys[0], 2), values)
            cache.save(keys[2], 2, values)
            self.assertIsNone(cache.load(keys[1], 2))
            self.assertIsNotNone(cache.load(keys[0], 2))
            self.assertIsNotNone(cache.load(keys[2], 2))
            with patch.object(persistence, "MAX_DISK_BYTES", 2000):
                cache.save(keys[3], 2, values)
                files = list(Path(root).glob("*.vec"))
                self.assertEqual(len(files), 1)
                self.assertLessEqual(sum(file.stat().st_size for file in files), 2000)

    def test_writers_publish_only_complete_matrices(self):
        with TemporaryDirectory() as root, ThreadPoolExecutor(max_workers=4) as executor:
            key = "document", sha256(b"chunks").digest()
            ones = np.ones((4, 16), dtype=np.float32)

            def publish(i):
                cache = persistence.VectorCache(root, "test-model", 16)
                cache.save(key, len(ones), ones * i)
                return cache.load(key, len(ones))

            matrices = list(executor.map(publish, range(1, 9)))
            self.assertTrue(all(matrix is not None for matrix in matrices))
            self.assertTrue(all(np.array_equal(matrix, matrices[0]) for matrix in matrices))
            self.assertEqual(len(list(Path(root).glob("*.vec"))), 1)

    def test_abrupt_exit_does_not_publish_a_pending_file(self):
        with TemporaryDirectory() as root:
            folder = Path(root)
            script = """
from hashlib import sha256
from pathlib import Path
from time import sleep
import sys
import numpy as np
from scanpdf.semantic import persistence

def pause_before_publish(_source, _target):
    Path(sys.argv[1], 'ready').touch()
    sleep(30)

persistence.os.replace = pause_before_publish
cache = persistence.VectorCache(sys.argv[1], 'test-model', 3)
cache.save(('document', sha256(b'chunks').digest()), 2, np.ones((2, 3), dtype=np.float32))
"""
            process = subprocess.Popen([sys.executable, "-c", script, root], cwd=ROOT)
            try:
                deadline = monotonic() + 10
                while not (folder / "ready").exists() and process.poll() is None and monotonic() < deadline:
                    sleep(0.05)
                self.assertTrue((folder / "ready").is_file())
                self.assertTrue((folder / ".vectors.pending").is_file())
                self.assertEqual(list(folder.glob("*.vec")), [])
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            reopened = persistence.VectorCache(folder, "test-model", 3)
            key = "document", sha256(b"chunks").digest()
            self.assertIsNone(reopened.load(key, 2))
            self.assertFalse((folder / ".vectors.pending").exists())
            values = np.ones((2, 3), dtype=np.float32)
            reopened.save(key, 2, values)
            np.testing.assert_array_equal(reopened.load(key, 2), values)


def check_book(source, folder):
    """Independent process entry point for the opt-in 541-page acceptance test."""
    import engine
    import pymupdf

    raw = source.read_bytes()
    doc = engine.parse_pdf(raw, source.name)
    original = index._Encoder.encode
    passage_rows = 0

    def tracked(self, texts, prefix):
        nonlocal passage_rows
        if prefix == index.PASSAGE_PREFIX:
            passage_rows += len(texts)
        return original(self, texts, prefix)

    started = monotonic()
    background = False
    dense = DenseIndex(cache_dir=folder)
    with patch.object(index._Encoder, "encode", tracked):
        while True:
            try:
                hits = dense.search([doc], "What conditions make a gradient descent step decrease the loss?")
                break
            except DenseUnavailable as error:
                if error.reason != "index_preparing" or monotonic() - started > 180:
                    raise
                background = True
                sleep(0.25)
    elapsed = monotonic() - started
    anchors = []
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        for hit in hits:
            rows = pdf[hit["page"] - 1].get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS, sort=True)
            text = next(row[4] for row in rows if row[5] + 1 == hit["block"])
            assert " ".join(hit["text"].split()) in " ".join(text.split()), hit
            anchors.append([hit["page"], hit["block"], hit["part"], hit["score"]])
    return {"seconds": round(elapsed, 3), "pages": doc["pages"], "sha256": doc["id"],
            "background": background, "encoded_passages": passage_rows, "anchors": anchors}


class RealPersistenceTest(TestCase):
    def test_book_same_anchors_after_process_restart(self):
        book = os.environ.get("EVIDENCE_DENSE_LONG_PDF")
        if not book or not DenseIndex().available():
            self.skipTest("Set EVIDENCE_DENSE_LONG_PDF and install the optional dense runtime")
        script = """
import json
from pathlib import Path
import sys
from scanpdf.semantic.test_persistence import check_book
print(json.dumps(check_book(Path(sys.argv[1]), Path(sys.argv[2]))))
"""
        with TemporaryDirectory() as folder:
            command = [sys.executable, "-c", script, str(Path(book).absolute()), folder]
            cold, restarted = (json.loads(subprocess.check_output(command, cwd=ROOT, timeout=200)) for _ in range(2))
        self.assertEqual(cold["pages"], 541)
        self.assertGreater(cold["encoded_passages"], 750)
        self.assertTrue(cold["background"])
        self.assertEqual(restarted["encoded_passages"], 0)
        self.assertFalse(restarted["background"])
        self.assertEqual(len(cold["anchors"]), 8)
        self.assertEqual(cold["anchors"], restarted["anchors"])
        print(json.dumps({"cold": cold, "restarted": restarted}, ensure_ascii=False), flush=True)

    def test_actual_ocr_rebuilds_then_reuses_updated_cache(self):
        if not shutil.which("tesseract") or not DenseIndex().available():
            self.skipTest("Install the optional dense runtime and Tesseract")
        script = "from semantic.test_persistence import RealPersistenceTest; RealPersistenceTest().check_ocr()"
        subprocess.run([sys.executable, "-c", script], cwd=ROOT / "scanpdf", timeout=60, check=True)

    def check_ocr(self):
        import pymupdf
        from workbench import ScanLibrary

        with TemporaryDirectory() as folder, pymupdf.open() as source, pymupdf.open() as artwork:
            source.new_page().insert_text((70, 100), "Optical instruments must be calibrated daily.", fontsize=15)
            raster = artwork.new_page()
            raster.insert_text((70, 100), "The photoluminescence peak was observed at 530 nm.", fontsize=18)
            source.new_page().insert_image(raster.rect, pixmap=raster.get_pixmap(dpi=180))
            root = Path(folder)
            library = ScanLibrary(root)
            imported = library.add(source.tobytes(), "mixed.pdf")
            identity = imported["doc"]["id"]
            before, = library.records([identity])
            self.assertEqual(before["textless_pages"], [2])
            cache_dir = root / "test-vectors"
            question = "At what wavelength was the photoluminescence peak observed?"
            initial = DenseIndex(cache_dir=cache_dir).search([before], question)
            self.assertEqual([hit["page"] for hit in initial], [1])
            recognized = library.recognize(identity, 2)
            self.assertEqual(recognized["page"]["physical_page"], 2)
            after, = library.records([identity])
            self.assertEqual(before["id"], after["id"])
            self.assertEqual(after["textless_pages"], [])
            updated = DenseIndex(cache_dir=cache_dir).search([after], question)
            hit = updated[0]
            self.assertEqual((hit["id"], hit["page"], hit["source"]), (identity, 2, "ocr"))
            self.assertIn("530 nm", hit["text"])
            self.assertEqual(len(list(cache_dir.glob("*.vec"))), 2)
            restarted = DenseIndex(cache_dir=cache_dir)
            recovered, = ScanLibrary(root).records([identity])
            self.assertEqual(recovered, after)
            original = index._Encoder.encode

            def skip_passages(self, texts, prefix):
                if prefix == index.PASSAGE_PREFIX:
                    raise AssertionError("A persisted passage unexpectedly needed re-encoding")
                return original(self, texts, prefix)

            with patch.object(index._Encoder, "encode", skip_passages):
                self.assertEqual(restarted.search([recovered], question), updated)
                self.assertEqual(restarted.search([before], question), initial)
            print(json.dumps({"sha256": identity, "before_pages": [item["page"] for item in initial],
                              "ocr_page": hit["page"], "ocr_block": hit["block"], "ocr_source": hit["source"],
                              "updated_passages_reused": True, "verbatim": hit["text"]}), flush=True)
