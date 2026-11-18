import json
import os
import unittest
from unittest.mock import patch

from synthesis import abstract_sentence, generated_paragraphs


class SynthesisTests(unittest.TestCase):
    def test_first_page_abstract_remains_verbatim(self):
        doc = {'chunks': [{'page': 1, 'block': i, 'text': t} for i,t in enumerate([
            'Paper heading', 'Abstract', 'An important context is gradually changing.',
            'We propose a retrieval method built on a graph of evidence.',
            '1\nIntroduction', 'We achieve twice the performance of a new baseline.'],1)]}
        chunk, text = abstract_sentence(doc)
        self.assertEqual(chunk['block'], 4)
        self.assertEqual(text, chunk['text'])

    @patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'unit-test-private-value'})
    def test_generation_cites_real_refs_and_sends_only_aliases(self):
        sources = [dict(doc='opaque1', ref='E1', category='摘要', text='A first study finds that signals overlap.'),
                   dict(doc='opaque2', ref='E2', category='摘要', text='A second study reports another finding.')]
        def run(refs):
            def transport(raw, key):
                self.assertNotIn(b'opaque',raw)
                content = {'paragraphs':[{'text':'两个论文都讨论了相应的信号。','refs':refs}]}
                return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}]}).encode()
            return generated_paragraphs(sources,transport=transport)
        self.assertTrue(run(['E1','E2']))
        self.assertEqual(run(['E1','bogus']), [])
        self.assertEqual(run(['E1']), [])
