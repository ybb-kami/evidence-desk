import unittest
from abbreviations import asked_term, answer


class DefinitionQuestions(unittest.TestCase):
    def setUp(self):
        self.doc = {"id": "f" * 64, "name": "study.pdf", "chunks": [
            {"page": 2, "block": 4, "part": 1, "text": "We used a recurrent neu-\nral network (RNN)."},
            {"page": 7, "block": 5, "part": 1, "text": "The RNN (random nearest neighbor) baseline is defined here."}]}

    def test_competing_definitions_and_scoped_answer(self):
        result = answer(self.doc, "What does RNN stand for?")
        self.assertIsNone(result["answer"])
        self.assertEqual([hit["page"] for hit in result["hits"]], [2, 7])
        result = answer(self.doc, "RNN的英文全称是什么？", self.doc["chunks"][0])
        self.assertEqual(result["answer"], "RNN 的全称是 recurrent neural network。")
        hit, = result["hits"]
        self.assertEqual(hit["text"][hit["start"]:hit["end"]], "recurrent neu-\nral network (RNN)")

    def test_no_expansion_and_distinct_task(self):
        result = answer(self.doc, "What does CNN stand for?")
        self.assertFalse(result["answer"])
        self.assertIn("CNN", result["gap"])
        self.assertIsNone(asked_term("What does RNN predict and why?"))
        self.assertIsNone(answer(self.doc, "Which RNN method performs better?"))
