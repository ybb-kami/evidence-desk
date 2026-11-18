import unittest

from direct_coverage import covers_parts
from question_parts import split_question
from scan_table_qa import part_can_use_table


class DirectCoverageTests(unittest.TestCase):
    def setUp(self):
        fields = [("Layers", "30"), ("Hidden dim.", "4096"), ("Attention heads", "32")]
        self.table = {"answer": "Three reported cells", "answer_method": "native_table", "gap": None,
                      "table_cells": [{"row_label": label, "column_label": "BLOOM-7.1B", "value": value,
                                       "bbox_pt": [80, 100 + n, 100, 101 + n], "table": 3}
                                      for n, (label, value) in enumerate(fields)]}

    def check(self, question, expected):
        parts = split_question(question)
        self.assertEqual(len(parts), 2)
        self.assertIs(covers_parts(self.table, parts), expected)

    def test_resolved_cells_cover_anaphoric_pure_table_question(self):
        self.check("For BLOOM-7.1B in Table 3, how many layers and attention heads, and what was its hidden dimension?", True)

    def test_one_known_field_must_not_cover_missing_or_explanatory_fields(self):
        self.check("In Table 3 how many layers? What GPU model was used for training?", False)
        self.check("In Table 3 how many layers? Why did the authors choose that number of layers?", False)
        self.check("In Table 3 how many layers? What is the hidden dimension in Table 4?", False)
        self.check("In Table 3 how many layers? How many layers are there in an unpublished variant?", False)
        self.check("In Table 3 how many layers? What were the hidden dimension and batch size?", False)

    def test_an_unresolved_or_unlocated_cell_cannot_cover_a_part(self):
        self.table["table_cells"][1]["value"] = None
        self.check("In Table 3 how many layers? What was its hidden dimension?", False)

    def test_process_questions_go_to_paragraph_retrieval(self):
        self.assertFalse(part_can_use_table("Why did they pick this learning rate?"))
        self.assertFalse(part_can_use_table("How was the batch size determined?"))
        self.assertFalse(part_can_use_table("为什么作者选这个批量大小？"))
        self.assertTrue(part_can_use_table("What was their batch size?"))
        self.assertTrue(part_can_use_table("How many samples did the study collect?"))

    def test_opaque_direct_answers_have_no_proof_of_multiple_fields(self):
        full = {"answer_method": "source_fields", "answer": "8 GPUs"}
        self.assertFalse(covers_parts(full, ["How many GPUs?", "What random seed?"]))

    def test_comparison_summary_covers_only_its_reported_roles(self):
        full = {"answer_method": "table_calculation", "answer": "A score, a baseline, and their difference",
                "metric_comparison": {"task": "EN-DE", "target_score": "28.4", "prior_score": "26.36", "difference": "2.04"}}
        score = "How many English-to-German BLEU points did Transformer (big) get?"
        prior = "What was the previous state-of-the-art and by how much did the model improve?"
        self.assertTrue(covers_parts(full, [score, prior]))
        self.assertFalse(covers_parts(full, [score, "What random seed was used by the previous state of the art?"]))
        self.assertFalse(covers_parts(full, [score, "What was the previous best training cost?"]))
        self.assertFalse(covers_parts(full, [score, "What was the previous state-of-the-art hardware?"]))
        self.assertFalse(covers_parts(full, [score, "What was the English-to-French BLEU score?"]))
        self.assertFalse(covers_parts(full, [score, "Why did the model improve?"]))
