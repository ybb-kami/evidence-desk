import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workbench
import hybrid_retrieval
from rag_search import ranked_passages
from semantic import DenseUnavailable


def passage(page, block, text, **metadata):
    return dict(page=page, block=block, part=1, text=text, **metadata)


def document(*passages):
    return dict(id="specimen", name="specimen.pdf", page_labels=["i", "1", "2", "3"], chunks=list(passages))


class HybridRetrievalTests(unittest.TestCase):
    def test_consecutive_term_evidence_keeps_original_anchor(self):
        direct = passage(2, 5, "The porous membrane was imaged by scanning electron microscopy after drying.",
                         offset=118, source="ocr")
        other = passage(1, 1, "A membrane electron emitter was made by scanning the microscopy controls.")
        hits = hybrid_retrieval.retrieve(document(other, direct), "scanning electron microscopy of the membrane")
        self.assertEqual(hits[0], dict(direct, id="specimen", name="specimen.pdf", page_label="1",
                                       score=hits[0]["score"]))
        self.assertGreater(hits[0]["score"], hits[1]["score"])

    def test_independent_clauses_retrieve_both_sources(self):
        treatment = passage(1, 1, "The annealing temperature was 560 °C for each thin film.")
        duplicate = passage(2, 1, treatment["text"])
        measurement = passage(3, 1, "The surface roughness was measured by atomic force microscopy.")
        query = "What annealing temperature was used, and how did they measure surface roughness?"
        hits = hybrid_retrieval.retrieve(document(treatment, duplicate, measurement), query, limit=2)
        self.assertCountEqual([hit["page"] for hit in hits], [1, 3])

    def test_abstains_for_absent_subject_or_numeric_qualifier(self):
        doc = document(passage(1, 1, "The annealing temperature was 560 °C and grain growth was monitored."))
        self.assertEqual(hybrid_retrieval.retrieve(doc, "What isotope mass was used during annealing?"), [])
        self.assertEqual(hybrid_retrieval.retrieve(doc, "What happened during annealing at 900 °C?"), [])

    def test_symbol_matching_does_not_change_quoted_text(self):
        source = "The standard error divides the sample deviation by √n."
        doc = document(passage(2, 1, source))
        hits = hybrid_retrieval.retrieve(doc, "standard error square root")
        self.assertEqual(hits[0]["text"], source)

    def test_cjk_passages_work_without_translation(self):
        doc = document(passage(2, 3, "材料的玻璃化转变温度通过热分析测定。"))
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"EVIDENCE_TRANSLATION_MODELS": root}):
            self.assertEqual(hybrid_retrieval.english_query("玻璃化转变温度是多少？"), "")
            hits = hybrid_retrieval.retrieve(doc, "玻璃化转变温度是多少？")
        self.assertEqual((hits[0]["page"], hits[0]["block"]), (2, 3))

    def test_translated_query_and_english_input_take_expected_paths(self):
        question = "用什么仪器测量薄膜厚度？"
        doc = document(passage(1, 2, "Film thickness was measured by an ellipsometer."))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "zh-en"
            (root / "model").mkdir(parents=True)
            (root / "model/model.bin").touch()
            (root / "sentencepiece.model").touch()
            with (patch.dict(os.environ, {"EVIDENCE_TRANSLATION_MODELS": folder, "EVIDENCE_RAG_TRANSLATION_PYTHON": ""}),
                  patch.object(hybrid_retrieval.bilingual, "available", return_value=["zh-en"]),
                  patch.object(hybrid_retrieval.bilingual, "translate", return_value="How is film thickness measured?") as translation):
                self.assertEqual(hybrid_retrieval.english_query("How is film thickness measured?"), "")
                hits = hybrid_retrieval.retrieve(doc, question)
                translation.assert_called_once_with("zh-en", question)
        self.assertEqual(hits[0]["block"], 2)

    def test_index_tracks_scoped_and_updated_passages(self):
        old = document(passage(1, 1, "Raman spectroscopy was used to measure crystallinity."))
        new = document(passage(2, 1, "Infrared spectroscopy was used to measure crystallinity."))
        self.assertEqual(hybrid_retrieval.retrieve(old, "Raman spectroscopy")[0]["page"], 1)
        self.assertEqual(hybrid_retrieval.retrieve(new, "Raman spectroscopy"), [])
        self.assertEqual(hybrid_retrieval.retrieve(new, "infrared spectroscopy")[0]["page"], 2)

    def test_recall_retains_phrase_when_many_query_words_are_unindexed(self):
        source = "Raman spectroscopy measured crystallinity at 532 nm in the annealed films."
        unrelated = "An index lists Raman badges above the table and near the closing apparatus file."
        distant = "Raman badges occupy the cupboard over the entry in the hallway by the glass. Spectroscopy is listed in a separate catalog."
        doc = document(passage(1, 1, unrelated), passage(2, 2, source), passage(3, 3, distant))
        q = "Does Xylophone's pseudodictionary lexical token phlogiston annotate Raman spectroscopy crystallinity?"
        self.assertEqual(hybrid_retrieval.retrieve(doc, q), [])
        hits = hybrid_retrieval.retrieve_candidates(doc, q)
        self.assertEqual([(row["page"], row["text"]) for row in hits], [(2, source)])
        self.assertTrue(hits[0]["retrieval_candidate"])

    def test_sparse_candidates_require_numeric_source_support_even_if_translation_drops_it(self):
        doc = document(passage(1, 2, "Raman spectroscopy assessed crystallinity at 532 nm."))
        with patch.object(hybrid_retrieval, "english_query", return_value="Raman spectroscopy assessed crystallinity"):
            self.assertEqual(hybrid_retrieval.retrieve_candidates(doc, "900 nm 测量结晶度？"), [])
            hits = hybrid_retrieval.retrieve_candidates(doc, "532 nm 测量结晶度？")
        self.assertEqual([hit["block"] for hit in hits], [2])

    def test_candidate_channel_runs_with_ready_e5_and_falls_back_on_failure(self):
        doc = document(passage(1, 1, "Raman spectroscopy measured crystallinity at 532 nm."))
        q = "Raman spectroscopy in pseudodictionary token phlogiston and xylophone"
        anchor = dict(doc["chunks"][0], id=doc["id"], name=doc["name"])

        class Dense:
            def search(self, docs, question, *, limit):
                return [anchor]

        class Unavailable:
            def search(self, docs, question, *, limit):
                raise DenseUnavailable("model_missing")

        hits, meta = ranked_passages(doc, q, [], Dense())
        self.assertEqual(meta["methods"], ["bm25-recall", "multilingual-e5-small"])
        self.assertTrue(hits[0]["retrieval_candidate"])
        self.assertEqual(hits[0]["retrieval_ranks"], {"bm25-recall": 1, "multilingual-e5-small": 1})
        with patch("rag_search.retrieve_candidates", side_effect=AssertionError("unexpected recall")):
            hits, meta = ranked_passages(doc, q, [anchor], Unavailable())
            self.assertEqual(meta, {"methods": ["keyword"], "dense": "model_missing"})
            self.assertNotIn("retrieval_candidate", hits[0])
            hits, meta = ranked_passages(doc, q, [anchor], Dense())
            self.assertEqual(meta["methods"], ["keyword", "multilingual-e5-small"])
            self.assertNotIn("retrieval_candidate", hits[0])
            hits, meta = ranked_passages(doc, "Raman spectroscopy", [], Dense())
            self.assertEqual(meta["methods"], ["bm25", "multilingual-e5-small"])
            self.assertNotIn("retrieval_candidate", hits[0])
