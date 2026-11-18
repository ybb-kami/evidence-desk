import unittest

from comparison_retrieval import paired_measurements


class ComparisonPassagesTests(unittest.TestCase):
    def setUp(self):
        self.span = "Quartz used four channels whereas Jasper used seven channels."
        texts = [
            "Other trials took two epochs and eight epochs.",
            "Quartz used four channels. Jasper used seven channels.",
            "QuartzX used four channels whereas Jasper used seven channels.",
            "Quartz and Jasper used one device in 2014 and 2017.",
            "For a different task we use two channels. " + self.span,
        ]
        self.doc = {"id": "example", "name": "report.pdf", "chunks": [
            {"page": i, "block": 1, "part": 1, "text": sample} for i, sample in enumerate(texts, 1)]}

    def test_returns_the_exact_comparison_sentence_in_both_query_languages(self):
        for question in ("How many channels were used by Quartz vs. Jasper?", "Quartz 和 Jasper 各有多少通道？"):
            with self.subTest(question=question):
                hits = paired_measurements(self.doc, question)
                self.assertEqual([h["page"] for h in hits], [5])
                self.assertEqual(hits[0]["text"][hits[0]["start"]:hits[0]["end"]], self.span)

    def test_ambiguous_pair_wrong_unit_or_absent_quantity_request(self):
        for question in ("How many epochs were used by Quartz and Jasper?",
                         "How many channels did Quartz and Jasper and Topaz use?",
                         "Compare Quartz and Jasper.", "How many channels did Quartz and Quartz use?", None):
            with self.subTest(question=question):
                self.assertEqual(paired_measurements(self.doc, question), [])
