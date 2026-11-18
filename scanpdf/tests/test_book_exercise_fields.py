"""Direct exercise fields come only from the selected statement's source ranges."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pymupdf

from book_exercises import locate_exercise
from engine import parse_pdf
from followup import _answer_one


def exercise(text):
    return {'id': 'd' * 64, 'name': 'Measurements.pdf', 'chunks': [
        {'page': 7, 'block': 2, 'part': 1, 'text': text},
    ]}


def quote(hit):
    return hit['text'][hit['start']:hit['end']]


def native_exercise(html):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_htmlbox(pymupdf.Rect(25, 40, 570, 260), html)
        content = pdf.tobytes()
    return parse_pdf(content, 'Measurements.pdf'), content


class ExerciseFieldsTest(unittest.TestCase):
    def test_comparison_returns_the_stated_objects_only(self):
        doc = exercise('Problem 3.6 Suppose a periodic input is given. Compare the cost of (i) evaluating the dense basis '
                       'on each sample and (ii) evaluating the sparse basis on each window. Prove a uniform bound.')
        result = locate_exercise(doc, '习题 3.6 要求比较哪两种计算？')
        self.assertIn('dense basis on each sample', result['answer'])
        self.assertIn('sparse basis on each window', result['answer'])
        self.assertNotIn('uniform bound', result['answer'])
        self.assertTrue(quote(result['hits'][0]).startswith('Compare'))
        self.assertIsNone(result['gap'])

    def test_statement_formula_and_input_output_are_independent(self):
        doc, data = native_exercise('Exercise 9.7 Consider the mapping y = x<sup>2</sup> + 2 with input x and output y. '
                                    'Find the inverse of the mapping.')
        formula = locate_exercise(doc, 'What equation is given in Exercise 9.7?', load_pdf=lambda: data)
        self.assertIn('y = x² + 2', formula['answer'])
        self.assertNotIn('inverse', formula['answer'])
        hit, = formula['hits']
        self.assertEqual(quote(hit), 'y = x2 + 2')
        result = locate_exercise(doc, 'Which variables represent the input and output in Exercise 9.7?')
        self.assertIn('Input variable: x', result['answer'])
        self.assertIn('Output variable: y', result['answer'])
        self.assertIsNone(result['gap'])
        solved = locate_exercise(doc, 'Solve Exercise 9.7.')
        self.assertIsNone(solved['answer'])

    def test_subscript_and_superscript_with_same_flat_text_do_not_collapse(self):
        q = 'What equation is given in Exercise 1.2?'
        for tag, value in [('sup', 'x²'), ('sub', 'x₂'), ('span', 'x2')]:
            with self.subTest(tag=tag):
                doc, data = native_exercise(f'Exercise 1.2 Consider the formula y = x<{tag}>2</{tag}>. Evaluate it.')
                result = locate_exercise(doc, q, load_pdf=lambda: data)
                self.assertIn(f'y = {value}', result['answer'])
                self.assertEqual(quote(result['hits'][0]), 'y = x2')
        doc, data = native_exercise('Exercise 1.2 Assume y = x<sup>2</sup> in run A. '
                                    'In run B use y = x<sub>2</sub>. Compare the two runs.')
        result = locate_exercise(doc, q, load_pdf=lambda: data)
        self.assertIsNone(result['answer'])
        self.assertEqual(result['exercise']['fields'][0]['reason'], 'ambiguous')

    def test_missing_or_mismatched_original_keeps_partial_fields_and_original_quote(self):
        doc, data = native_exercise('Exercise 1.2 Assume the base density is defined on z ∈[0, 1]. '
                                    'Consider the formula x = z<sup>3</sup>. Check the setup.')
        q = '习题 1.2 的基密度定义在哪个区间？题目用什么函数把 z 变成 x？'
        for load in (None, lambda: data + b'changed'):
            result = locate_exercise(doc, q, load_pdf=load)
            self.assertEqual(result['exercise']['completeness'], 'partial')
            self.assertIn('[0, 1]', result['answer'])
            self.assertNotIn('z3', result['answer'])
            self.assertIn('请在原页图像', result['gap'])
            self.assertTrue(any('x = z3' in quote(hit) for hit in result['hits']))

    def test_figure_partial_does_not_swallow_unrelated_field(self):
        doc = exercise('Exercise 1.8 Explain why the branch values of the estimator shown in figure 5.11b are uncorrelated.')
        result = locate_exercise(doc, 'Which figure is referenced by Exercise 1.8, and who wrote this book?')
        self.assertEqual(result['exercise']['completeness'], 'partial')
        self.assertIn('5.11b', result['answer'])
        self.assertIn('who wrote this book', result['gap'])
        generator = Mock()
        final = _answer_one(SimpleNamespace(local_generator=generator), {}, result)
        generator.answer.assert_not_called()
        self.assertEqual(final, result)

    def test_uses_only_the_selected_statement_in_a_shared_block(self):
        doc = exercise('Exercise 8.7 Assume a Bernoulli distribution. Show the result.\n'
                       'Exercise 8.8 Assume a uniform distribution. Show the result.')
        answer = locate_exercise(doc, 'What distribution is used by Exercise 8.8?')
        self.assertIn('uniform distribution', answer['answer'])
        self.assertNotIn('Bernoulli', quote(answer['hits'][0]))
        self.assertEqual(answer['hits'][0]['page'], 7)

    def test_declared_task_is_not_a_solved_equation(self):
        doc = exercise('Exercise 2.9 Consider a loss L. Derive an expression for the average risk.')
        requirement = locate_exercise(doc, 'What does Exercise 2.9 ask you to derive?')
        self.assertIn('Derive an expression for the average risk', requirement['answer'])
        self.assertEqual(quote(requirement['hits'][0]), 'Derive an expression for the average risk.')
        computation = locate_exercise(doc, 'Derive the average risk for Exercise 2.9.')
        self.assertIsNone(computation['answer'])

    def test_symbolic_variance_never_uses_the_stated_mean(self):
        doc = exercise('Exercise 6.9 Consider a variable with variance Var[t] = σ² and mean E[t] = 0. Calculate the risk.')
        result = locate_exercise(doc, '习题 6.9 中方差的数值是多少？')
        self.assertIsNone(result['answer'])
        self.assertIn('只写成 σ²', result['gap'])
        self.assertIn('未给数值', result['gap'])
        mean = locate_exercise(doc, 'What is the mean in Exercise 6.9?')
        self.assertIn('Mean: 0', mean['answer'])

    def test_does_not_choose_between_different_stated_conditions(self):
        doc = exercise('Exercise 7.1 Assume the discount factor is 0.5 in run A. The discount factor is 0.8 in run B. '
                       'Compute both returns.')
        result = locate_exercise(doc, 'What is the discount factor specified in Exercise 7.1?')
        self.assertIsNone(result['answer'])
        self.assertEqual(result['exercise']['fields'][0]['reason'], 'ambiguous')
        self.assertEqual(len(result['hits']), 2)
