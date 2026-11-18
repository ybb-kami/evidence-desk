"""Evidence and boundary tests for numbered original exercise statements."""

import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from book_exercises import locate_exercise
from engine import parse_pdf


def doc(*rows):
    return {"id": "c" * 64, "name": "Book.pdf", "page_labels": ["i", "1", "2"], "chunks": [
        {"page": page, "block": block, "part": 1, "text": text} for page, block, text in rows
    ]}


def source(hit):
    return hit["text"][hit["start"]:hit["end"]]


class BookExercisesTest(unittest.TestCase):
    def test_exact_number_boundaries_and_prose_reference(self):
        book = doc((1, 1, "6.7\nA section with the same number"),
                   (1, 2, "The proof will be used again in Problem 6.7."),
                   (1, 3, "Problem 6.7\nor is a sum of those from the other regions."),
                   (2, 1, "Problems"), (2, 2, "Problem 6.70 Consider a different system."),
                   (2, 3, "Problem 16.7 Show the alternate trace."),
                   (2, 4, "Problem 6.7.1 Derive a related lemma."),
                   (2, 5, "Problem 6.7∗Show that the estimates are consistent."),
                   (2, 6, "Problem 6.7a Prove the extension."))
        result = locate_exercise(book, "Where is Problem 6.7?")
        hit, = result["hits"]
        self.assertEqual((hit["page"], hit["page_label"], hit["block"], hit["id"]), (2, "1", 5, book["id"]))
        self.assertEqual(source(hit), "Problem 6.7∗Show that the estimates are consistent.")
        for wrong in ("Problem 6.71", "Problem 6.7b", "Problem 6.7.10"):
            self.assertEqual(locate_exercise(book, wrong)["exercise"]["status"], "not_found")
        self.assertIsNone(locate_exercise(book, "Where does Section 6.7 start?"))

    def test_two_exercises_in_one_block_remain_independent(self):
        merged = "Exercise 7.1: Find the eigenvalues.\nExercise 7.2: Prove the bound."
        book = doc((1, 1, "Exercises"), (1, 2, merged))
        first, = locate_exercise(book, "Ｅｘｅｒｃｉｓｅ ７．１")["hits"]
        self.assertEqual(source(first).strip(), "Exercise 7.1: Find the eigenvalues.")
        second, = locate_exercise(book, "习题 7 . 2 在哪里？")["hits"]
        self.assertEqual(source(second), "Exercise 7.2: Prove the bound.")
        self.assertEqual(second["start"], merged.index("Exercise 7.2"))
        self.assertEqual(locate_exercise(book, "Exercises 7.1–7.2")["exercise"]["status"], "ambiguous")

    def test_location_does_not_answer_a_content_question_or_expose_a_neighbor_to_the_reader(self):
        from followup import _answer_one
        book = doc((1, 1, "Exercises"),
                   (1, 2, "Exercise 1.1 Find the sample serial number.\nExercise 1.2 Identify the probe."))
        request = {"question": "Which probe does Exercise 1.2 identify?"}
        found = locate_exercise(book, request["question"])
        self.assertIsNone(found["answer"])
        self.assertTrue(found["exercise"]["read_statement"])
        model = Mock()
        model.answer.return_value = {"status": "disabled"}
        reader = Mock()
        reader.candidates.return_value = ([], "disabled", "")
        result = _answer_one(SimpleNamespace(local_generator=model, passage_reader=reader), request, found)
        hit, = model.answer.call_args.args[1]
        self.assertNotIn("serial number", hit["text"])
        self.assertEqual(hit["text"].index("Identify"), found["hits"][0]["text"].index("Identify"))
        self.assertIsNone(result["answer"])
        self.assertEqual(result["hits"], found["hits"])
        self.assertIn("physical page 1", result["gap"])

    def test_page_continuation_keeps_verbatim_math_and_stops_at_next_heading(self):
        book = doc((1, 1, "Exercises"), (1, 2, "Exercise 2.1 Find the zero of the following polynomial:"),
                   (1, 3, "Copyright a.example"), (2, 1, "Notes\n2"),
                   (2, 2, "Figure 2.2 Unrelated chart"), (2, 3, "p[x] = x² − 1"),
                   (2, 4, "where x ranges over all real numbers."),
                   (2, 5, "Exercise 2.2 Show a different result."))
        result = locate_exercise(book, "Exercise 2.1 原文")
        self.assertEqual(result["exercise"]["pages"], [1, 2])
        self.assertEqual([(h["page"], h["block"]) for h in result["hits"]], [(1, 2), (2, 3), (2, 4)])
        self.assertEqual(source(result["hits"][1]), "p[x] = x² − 1")

    def test_duplicate_exercise_numbers_require_explicit_scope(self):
        book = doc((1, 1, "Chapter 4"), (1, 2, "Exercises"), (1, 3, "Exercise 1 Explain the first experiment."),
                   (3, 1, "Chapter 5"), (3, 2, "Exercises"), (3, 3, "Exercise 1 Explain the second experiment."))
        result = locate_exercise(book, "Where is Exercise 1?")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["exercise"]["status"], "ambiguous")
        selected, = locate_exercise(book, "Locate Exercise 1 on physical page 3")["hits"]
        self.assertEqual((selected["page"], selected["block"]), (3, 3))

    def test_chinese_ordinal_and_solution_abstention(self):
        book = doc((1, 1, "本章习题"), (1, 2, "第八题\n已知直线斜率，求其交点。"), (1, 3, "参考文献"))
        first, = locate_exercise(book, "第８题的原文在哪里？")["hits"]
        self.assertEqual(source(first), "第八题\n已知直线斜率，求其交点。")
        solve = locate_exercise(book, "请解答第八题")
        self.assertIsNone(solve["answer"])
        self.assertEqual(solve["exercise"]["status"], "located")
        self.assertIn("无法据此验证解答", solve["gap"])

    def test_dimension_field_requires_a_unique_set_in_sampling_sentence(self):
        book = doc((1, 1, "Exercises"),
                   (1, 2, "Problem 4.2 The original image is in 2 and 3 dimensions. Sample the reference law in 12, 20, and 37 dimensions."),
                   (1, 3, "Problem 4.3 Sample the reference law in many dimensions."),
                   (1, 4, "Problem 4.4 For training, sample in 2 and 4 dimensions. For testing, sample in 6 and 8 dimensions."))
        field = locate_exercise(book, "习题4.2要求在哪三个维度采样参考分布？")
        self.assertEqual(field["exercise"]["values"], ["12", "20", "37"])
        assert_hit, = field["hits"]
        self.assertEqual(source(assert_hit), "Sample the reference law in 12, 20, and 37 dimensions.")
        for number in ("4.3", "4.4"):
            missing = locate_exercise(book, f"What dimensions does Problem {number} ask us to sample in?")
            self.assertIsNone(missing["answer"])
            self.assertIn("没有明确给出一组", missing["gap"])

    @unittest.skipUnless(os.environ.get("EVIDENCE_EXERCISE_PDF"), "set path to the real benchmark textbook PDF")
    def test_real_textbook_across_chapters_and_cross_page_layout(self):
        path = Path(os.environ["EVIDENCE_EXERCISE_PDF"])
        book = parse_pdf(path.read_bytes(), path.name)
        expected = (('Problem 3.1', 53, 4), ('习题3.10', 54, 13), ('Problem 3.9', 54, 12),
                    ('第3.18题', 54, 21), ('Exercise 8.7', 150, 12), ('练习8.8', 151, 9),
                    ('Problem 12.10', 253, 14), ('Problem 13.10', 281, 20), ('Problem 21.13', 450, 15))
        for question, page, block in expected:
            with self.subTest(question=question):
                result = locate_exercise(book, question)
                self.assertEqual((result["hits"][0]["page"], result["hits"][0]["block"]), (page, block))
                self.assertEqual(result["hits"][0]["id"], book["id"])
        extended = locate_exercise(book, "Exercise 8.7")
        self.assertEqual([(h["page"], h["block"]) for h in extended["hits"]],
                         [(150, 12), (151, 3), (151, 4), (151, 5), (151, 8)])
        public = locate_exercise(book, "教材习题8.9要求在哪三个维度采样标准正态分布？")
        self.assertEqual(public["exercise"]["values"], ["25", "100", "500"])
        hit, = public["hits"]
        self.assertEqual((hit["page"], hit["block"], hit["page_label"]), (151, 10, "137"))
        self.assertIn("25, 100, and 500 dimensions", source(hit))
