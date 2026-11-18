"""Check offline dense retrieval and its degradation when the optional model is absent."""

import hashlib
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import fitz
import numpy as np

from engine import parse_pdf
from . import DenseIndex, DenseUnavailable
from . import assets, index


def document(identity, text):
    return {"id": identity, "name": identity + ".pdf", "page_labels": ["i"],
            "chunks": [{"page": 1, "block": 2, "part": 1, "offset": 0, "text": text}]}


class DenseIndexTest(unittest.TestCase):
    def test_missing_and_invalid_model_are_explicit(self):
        with TemporaryDirectory() as folder:
            index = DenseIndex(folder)
            self.assertFalse(index.available())
            doc = document("selected", "Evidence from one page.")
            with self.assertRaises(DenseUnavailable) as missing:
                index.search([doc], "evidence")
            self.assertEqual(missing.exception.reason, "model_missing")
            (Path(folder) / assets.WEIGHTS).write_bytes(b"broken")
            expected = hashlib.sha256(b"valid!").hexdigest()
            with patch.dict(assets.FILES, {assets.WEIGHTS: (6, expected)}, clear=True):
                with self.assertRaises(DenseUnavailable) as invalid:
                    index.search([doc], "evidence")
            self.assertEqual(invalid.exception.reason, "model_invalid")

    def test_ocr_invalidation_and_selected_scope(self):
        class Encoder:
            np = np

            def encode(self, texts, prefix):
                return np.asarray([[1, 0] if "relevant" in text else [0, 1]
                                   for text in texts], dtype=np.float32)

        search = DenseIndex()
        search._encoder = Encoder()
        first = document("first", "relevant passage")
        second = document("second", "distractor")
        hits = search.search([first, second], "relevant", limit=2)
        self.assertEqual([hit["id"] for hit in hits], ["first", "second"])
        self.assertEqual(hits[0]["page_label"], "i")
        revised = document("second", "relevant OCR supplement")
        revised["chunks"][0]["source"] = "ocr"
        hit, = search.search([revised], "relevant", limit=2)
        self.assertEqual((hit["id"], hit["source"], hit["score"]), ("second", "ocr", 1.0))
        cache_bytes = search._cache_bytes
        self.assertEqual(search.score_texts("relevant", ["distractor", "relevant"]), [0.0, 1.0])
        self.assertEqual(search._cache_bytes, cache_bytes)
        with self.assertRaises(ValueError):
            search.score_texts("relevant", ["too long" * 200])

    def test_large_cold_scope_is_built_in_background(self):
        started = threading.Event()
        finish = threading.Event()

        class Encoder:
            np = np

            def encode(self, texts, prefix):
                if prefix == "passage: ":
                    started.set()
                    if not finish.wait(5):
                        raise DenseUnavailable("inference_failed")
                return np.ones((len(texts), 1), dtype=np.float32)

        search = DenseIndex()
        search._encoder = Encoder()
        scope = [document("first", "first passage"), document("second", "second passage")]
        with patch.object(index, "INLINE_CHUNKS", 1), patch.object(index, "_check"):
            try:
                with self.assertRaises(DenseUnavailable) as cold:
                    search.search(scope, "passage")
                self.assertEqual(cold.exception.reason, "index_preparing")
                self.assertTrue(started.wait(2))
                with self.assertRaises(DenseUnavailable) as concurrent:
                    search.search(scope, "passage")
                self.assertEqual(concurrent.exception.reason, "index_preparing")
                with self.assertRaises(DenseUnavailable) as reranking:
                    search.score_texts("passage", ["an excerpt"])
                self.assertEqual(reranking.exception.reason, "index_preparing")
            finally:
                finish.set()
            for _ in range(200):
                try:
                    hits = search.search(scope, "passage")
                except DenseUnavailable as exc:
                    self.assertEqual(exc.reason, "index_preparing")
                    time.sleep(0.01)
                else:
                    break
            else:
                self.fail("The background index did not become ready")
        self.assertEqual([hit["id"] for hit in hits], ["first", "second"])

    def test_real_model_ranks_english_pdf_passage_for_chinese_question(self):
        search = DenseIndex()
        if not search.available():
            self.skipTest("Install the optional multilingual model and runtime to run offline inference")
        passages = (
            "Residual connections add the input of a sublayer to its output before layer normalization.",
            "The random forest was trained on measurements from coastal weather stations.",
            "We mask subsequent positions in the decoder so predictions cannot depend on future tokens.",
            "The bilingual vocabulary used a shared set of subword units for machine translation.",
        )
        with fitz.open() as pdf:
            for text in passages:
                page = pdf.new_page()
                page.insert_textbox(fitz.Rect(50, 50, 500, 120), text)
            doc = parse_pdf(pdf.tobytes(), "local.pdf")
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")):
            hit, = search.search([doc], "为了不让预测依赖后面的词，作者在解码端做了什么？", limit=1)
        self.assertEqual((hit["id"], hit["page"], hit["block"], hit["part"]), (doc["id"], 3, 1, 1))
        self.assertEqual(" ".join(hit["text"].split()), passages[2])


if __name__ == "__main__":
    unittest.main()
