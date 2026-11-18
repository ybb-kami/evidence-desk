"""Selected-PDF training hardware retrieval and condition boundaries."""

import os
from pathlib import Path
import unittest
from unittest.mock import Mock

from engine import parse_pdf
from followup import answer_question
from paper_facts import extract_facts, hardware_parts
from question_parts import split_question


def document(*passages, version=7, paper="2611.10001", title="Calculating Luminous Field Responses", name="paper.pdf"):
    return {"id": "a" * 64, "name": name, "arxiv": {"id": paper, "version": version},
            "bibliographic_identity": {"title": title}, "chunks": [
                {"page": number, "block": 2, "part": 1, "text": text}
                for number, text in enumerate(passages, 1)]}


class TrainingHardwareTest(unittest.TestCase):
    def test_word_count_passive_voice_and_other_phases(self):
        doc = document(
            "For inference the base model was served on two Zenith Z80 GPUs.",
            "The base model was trained using six Zenith Z90 GPUs.",
            "The large model was trained using ten Zenith Z99 GPUs.",
            "The base model of Lee et al. was trained using three Other X28 GPUs.",
            "[6] The base model was trained using twenty Other X30 GPUs.",
        )
        question = "What accelerator setup did they use to fit the base model?"
        result = extract_facts(doc, question, [])
        self.assertIn("6 Zenith Z90 GPUs", result["answer"])
        hit, = result["hits"]
        self.assertEqual((hit["page"], hit["block"], hit["part"], hit["id"]), (2, 2, 1, doc["id"]))
        self.assertEqual(hit["text"][hit["start"]:hit["end"]], doc["chunks"][1]["text"])
        self.assertIsNone(extract_facts(doc, "Which GPUs served inference on the trained base model?", []))

    def test_preserves_conflicts_and_requires_explicit_training_conditions(self):
        doc = document("We trained our base model using 6 Zenith Z90 GPUs at FP32.",
                       "We trained our base model using 4 Zenith Z80 GPUs at BF16.",
                       "We trained our baseline model using 2 Zenith Z70 GPUs.")
        question = "Which GPUs and how many were used for training the base model?"
        conflict = extract_facts(doc, question, [])
        self.assertIsNone(conflict["answer"])
        self.assertIn("不同", conflict["gap"])
        self.assertEqual({hit["page"] for hit in conflict["hits"]}, {1, 2})
        self.assertIn("4 Zenith Z80", extract_facts(doc, question.rstrip("?") + " at bf16?", [])["answer"])
        for qualifier in ("at FP16", "on Heliotrope dataset", "for fine-tuning", "from scratch"):
            with self.subTest(qualifier=qualifier):
                result = extract_facts(doc, question.rstrip("?") + " " + qualifier + "?", [])
                self.assertIsNone(result["answer"])
                self.assertEqual(result["hits"], [])

    def test_scope_is_the_selected_work_and_original_version(self):
        source = "We trained the base model using 6 Zenith Z90 GPUs."
        intended = document(source, version=7, name="someone-renamed-it.pdf")
        older = document(source, version=1, name="misleading-v7.pdf")
        other = document("Our base model was trained using 9 Titan X85 GPUs.", paper="2611.10002",
                         title="Estimating Other Induced Responses")
        ask = "What GPUs did the authors use to train the base model in arXiv:2611.10001v7?"
        self.assertIn("6 Zenith", extract_facts(intended, ask, [])["answer"])
        for doc in (older, other):
            mismatch = extract_facts(doc, ask, intended["chunks"])
            self.assertIsNone(mismatch["answer"])
            self.assertIn("版本", mismatch["gap"])
        title_query = "《Calculating Luminous Field Responses》v7 训练Base模型用了多少块GPU？"
        self.assertIn("6 块", extract_facts(intended, title_query, [])["answer"])
        self.assertIn("题名", extract_facts(other, title_query, [])["gap"])
        unrelated = extract_facts(other, "Which GPUs did prior work use for training its base model?", [])
        self.assertIsNone(unrelated["answer"])
        self.assertIn("对比研究", unrelated["gap"])

    def test_selected_passage_stays_scoped_and_unknown_details_are_marked(self):
        full = document("This passage introduces the task.", "We trained the base network with 6 GPUs.")
        ask = "How many GPUs and what type were used for training the base network?"
        result = extract_facts(full, ask, [])
        self.assertIn("6 GPUs", result["answer"])
        self.assertIn("does not specify the model", result["answer"])
        anchored = dict(full, chunks=full["chunks"][:1])
        result = extract_facts(anchored, ask, full["chunks"][1:])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["hits"], [])

    def test_other_training_fields_are_not_mistaken_for_hardware(self):
        doc = document("We trained the base model with 6 Zenith Z90 GPUs.")
        for question in ("What was the input size when training on a GPU?",
                         "What was the dataset used for training on GPUs?",
                         "What was the wall-clock time for training on GPUs?",
                         "What is the GPU count difference between base and large during training?",
                         "训练时 GPU 的显存是多少？", "训练硬件和语料是什么？"):
            with self.subTest(question=question):
                self.assertIsNone(extract_facts(doc, question, []))

    def test_split_preserves_non_hardware_subquestions(self):
        coupled = "In version 7, which GPUs did they train on, and how many were used?"
        mixed = ["Which GPUs did they train on, and who was the first author?",
                 "训练使用了哪种硬件？还用了什么语料？"]
        self.assertEqual(len(split_question(coupled)), 2)
        self.assertTrue(hardware_parts(split_question(coupled)))
        self.assertFalse(hardware_parts(split_question(mixed[0])))
        doc = document("We trained a model with 6 Zenith Z90 GPUs.", "We collected our corpus for this experiment.")
        store = Mock(dense=None)
        store.records.return_value = [doc]
        store.pdf.return_value = None
        store.passage_reader.candidates.return_value = [], "disabled", ""
        store.local_generator.answer.return_value = {"status": "disabled"}
        for question, detail, followup in zip(mixed, ("6 Zenith", "6 块 Zenith"), ("first author", "语料")):
            with self.subTest(question=question):
                result = answer_question(store, {"id": doc["id"], "question": question, "anchor": None})
                self.assertEqual(result["answer_method"], "fieldwise")
                self.assertEqual(len(result["fields"]), 2)
                self.assertIn(detail, result["fields"][0]["answer"])
                self.assertIn(followup, result["fields"][1]["question"])

    @unittest.skipUnless(os.environ.get("EVIDENCE_HARDWARE_PDFS"), "set directory containing the verified v1/v7 PDFs")
    def test_actual_versions_return_original_physical_pages_without_dense(self):
        folder = Path(os.environ["EVIDENCE_HARDWARE_PDFS"])
        store = Mock(dense=None)
        store.passage_reader.candidates.return_value = [], "disabled", ""
        store.local_generator.answer.return_value = {"status": "disabled"}
        native = "We trained our models on one machine with 8 NVIDIA P100 GPUs."
        for version, block in [(1, 3), (7, 9)]:
            raw = (folder / f"1706.03762v{version}.pdf").read_bytes()
            doc = parse_pdf(raw, "renamed.pdf")
            store.records.return_value = [doc]
            store.pdf.return_value = raw
            for question in (f"v{version} 版原文中，训练 Transformer 基础版采用了什么计算设备，配了几张？",
                             f"In revision {version}, which GPUs did they train the base model on, and how many were used?"):
                with self.subTest(version=version, question=question):
                    result = answer_question(store, {"id": doc["id"], "question": question, "anchor": None})
                    self.assertEqual(result["answer_method"], "source_fields")
                    hit, = result["hits"]
                    self.assertEqual((hit["id"], hit["page"], hit["block"]), (doc["id"], 7, block))
                    quote = hit["text"][hit["start"]:hit["end"]]
                    self.assertTrue(quote.startswith(native))
                    self.assertIn("For our base models", quote)
