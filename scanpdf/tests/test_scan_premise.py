"""Assertions about scan table values must be grounded in a single located cell."""

import unittest

from . import test_scan_table_qa as fixtures


class ScanPremiseTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ScanTableTests()
        fixture.setUp()
        self.ask = fixture.ask

    def test_false_premise_uses_actual_value_not_the_question_number(self):
        questions = (
            "表七 Example 大模型英意 BLEU 为什么是149？",
            "Why does Table 7 report 149 BLEU for Example (big) in English-to-Italian?",
            "Was the English-to-Italian BLEU of Example (big) in Table 7 149?",
            "为什么表七第4行第2列写成１４９？",
        )
        for question in questions:
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertEqual(result["premise_check"], {
                    "status": "contradicted", "stated": "149", "reported": "14.9",
                })
                cell, = result["table_cells"]
                self.assertEqual((cell["page"], cell["row"], cell["column"]), (3, 4, 2))
                self.assertEqual(cell["bbox_pt"], [140, 170, 240, 190])
                self.assertIsNone(result["gap"])

    def test_correct_premise_leaves_explanation_to_prose(self):
        self.assertIsNone(self.ask("Why is the English-to-Italian BLEU of Example (big) in Table 7 14.9?"))
        self.assertIsNone(self.ask("Why was the English-to-Italian BLEU of Example (big) in Table 7 above 14.9?"))
        result = self.ask("Was the English-to-Italian BLEU of Example (big) in Table 7 14.90?")
        self.assertIn("14.9", result["answer"])
        self.assertNotIn("premise_check", result)

    def test_missing_ambiguous_or_different_unit_cannot_disprove_a_premise(self):
        for question in (
            "Why does Table 7 report 142 BLEU for Example (base) in English-to-Spanish?",
            "Why does Table 7 report 149 BLEU for Example (big)?",
            "Why does Table 7 report 149 BLEU for Example in English-to-Italian?",
            "Why does Table 7 report 149% BLEU for Example (big) in English-to-Italian?",
        ):
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertIsNone(result["answer"])
                self.assertTrue(result["gap"])
                self.assertNotIn("premise_check", result)

    def test_scientific_unit_still_identifies_the_column_after_removing_value(self):
        result = self.ask("Did Table 7 report that Example (big) in English-to-Italian got 2.7e11 FLOPs?")
        self.assertEqual(result["premise_check"]["reported"], "2.1 × 10^11 FLOPs")
        self.assertIn("Training Cost", result["table_cells"][0]["column_label"])
