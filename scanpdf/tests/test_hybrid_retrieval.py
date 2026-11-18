import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workbench
import hybrid_retrieval


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
