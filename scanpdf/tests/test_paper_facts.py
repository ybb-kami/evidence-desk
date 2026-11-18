import unittest
import workbench
from paper_facts import reference_answer, training_hardware, byline_answer


class PaperFactsTests(unittest.TestCase):
    def test_conflicting_hardware_and_wrong_variant_abstain(self):
        query = '训练 Base 时多少个 GPU？'
        first = {'text':'We trained our models with 4 Vert A80 GPUs. For our base models this took an hour.'}
        other = {'text':'We trained the base model with 2 Vert A20 GPUs.'}
        wrong = {'text':'We trained the big model with 2 Vert A20 GPUs. For our base models we did not do this.'}
        self.assertIn('4 块 Vert A80', training_hardware(query, [first])['answer'])
        self.assertIsNone(training_hardware(query, [first,other]))
        self.assertIsNone(training_hardware(query, [wrong]))

    def test_author_footnote_only_with_matching_marker(self):
        doc = {'id':'id','name':'paper', 'chunks':[{'text':'‡Work performed while at Other Lab.', 'page':1, 'block':11}]}
        hit = {'page':1,'block':3,'text':'Ren Wu†\nExample Lab\nren@example.org'}
        result = byline_answer(doc,2,[hit]);self.assertNotIn('Other Lab', result['answer'])
        self.assertEqual(result['hits'],[hit]);self.assertIn('Example Lab',result['answer'])

    def test_reference_does_not_merge_years_from_multiple_editions(self):
        q = '请列出“Micro Arrays”的参考文献作者和年份。'
        row = {'text':'[8] Y. Tan and N. Wu. Micro Arrays. Proc. 1994, reprinted 2014.'}
        self.assertIsNone(reference_answer(q,[row]));row['text']='[8] Y. Tan and N. Wu. Micro Arrays. Proc. 2011.'
        self.assertIn('2011',reference_answer(q,[row])['answer'])
