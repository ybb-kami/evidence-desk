import unittest
from unittest.mock import Mock

import workbench
from followup import beta_values, lookup


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.id = "a" * 64
        self.store = Mock()
        self.store.records.return_value = [dict(id=self.id, name="test.pdf", page_labels=["1","2"], chunks=[
            dict(page=1, block=1, part=1, text="An unreadable Adam optimizer uses 8; = 0.9."),
            dict(page=2, block=3, part=1, text="We use Adam with β₁ = 0.91 and β₂ = 0.987.")])]

    def test_anchor_controls_source_and_gap(self):
        request = dict(id=self.id, question="其中 Adam 的两个 β 分别是多少", anchor=[2,3,1])
        found = lookup(self.store, request)
        self.assertEqual(found["answer"], "β₁ = 0.91，β₂ = 0.987")
        self.assertEqual(found["hits"][0]["page"], 2)
        inherited = lookup(self.store, dict(request, question="其中两个 β 分别是多少？"))
        self.assertEqual(inherited["answer"], found["answer"])
        gap = lookup(self.store, dict(request, anchor=[1,1,1]))
        self.assertIsNone(gap["answer"])
        self.assertTrue(gap["gap"])
        self.assertEqual(gap["hits"][0]["page"], 1)
        self.store.records.assert_called_with([self.id])

    def test_full_paper_official_question_and_scope(self):
        question = "其中 Adam 优化器的超参数 β₁ 和 β₂ 分别被设置为了多少？"
        doc = self.store.records.return_value[0]
        doc["chunks"].insert(0, dict(page=1, block=2, part=1, text="Author: Aidan N. Gomez"))
        request = dict(id=self.id, question=question, anchor=None)
        result = lookup(self.store, request)
        self.assertEqual(result["answer"], "β₁ = 0.91，β₂ = 0.987")
        self.assertEqual(result["hits"][0]["block"], 3)
        self.assertIsNone(lookup(self.store, dict(request, anchor=[1,2,1]))["answer"])
        doc["chunks"].append(dict(page=2, block=4, part=1, text="Adam uses β1=0.8, β2=0.99."))
        self.assertIn("多组", lookup(self.store, request)["gap"])
        doc["chunks"] = doc["chunks"][:2]
        self.assertIsNone(lookup(self.store, request)["answer"])
        self.assertTrue(lookup(self.store, request)["gap"])

    def test_invalid_and_missing_anchor(self):
        for anchor in ([1,1,2], [True,1,1], [], "other"):
            with self.assertRaises(workbench.app.Bad):
                lookup(self.store, dict(id=self.id, question="Adam", anchor=anchor))

    def test_conflicting_or_incomplete_assignment(self):
        for sample in ["β1=0.9, β2=0.998, β1=0.7", "β1=0.8, β2=0.90e-2", "β1=0.1%, β2=0.9"]:
            self.assertIsNone(beta_values(sample))
