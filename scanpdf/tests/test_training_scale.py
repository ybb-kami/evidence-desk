import unittest

from training_scale import read_scale


def paper(*blocks, title='Juniper: A language model'):
    return {'id': 'd' * 64, 'name': 'study.pdf', 'bibliographic_identity': {'title': title},
            'chunks': [{'page': i+1, 'block': 1, 'part': 1, 'text': block} for i, block in enumerate(blocks)]}


class TrainingScaleTests(unittest.TestCase):
    def test_reads_the_authors_total_not_a_baseline_or_per_step_measurement(self):
        src = paper('Earlier work reports an alternative schedule.',
                    'The reference baseline was trained on 19 million tokens.',
                    'We trained on 64 tokens per batch.',
                    'Preparation varied. We trained on a total of 2.7 billion tokens.')
        for q in ('How many training tokens are reported for Juniper?', 'Juniper 预训练用了多少 token？'):
            with self.subTest(q=q):
                r = read_scale(src, q)
                self.assertEqual(r['answer'], '2.7 billion tokens')
                hit, = r['hits']
                self.assertEqual((hit['page'],hit['text'][hit['start']:hit['end']]),
                                 (4, 'We trained on a total of 2.7 billion tokens.'))
        self.assertIsNone(read_scale(src, q, src['chunks'][0]))
        self.assertIsNone(read_scale(src, 'How many training tokens are reported for Elm?'))
        self.assertIsNone(read_scale(src, 'How many English training tokens are reported for Juniper?'))

    def test_conflicting_author_reports_keep_both_sources_and_no_number(self):
        src = paper('We trained on 13 million tokens.', 'We trained on 17 million tokens.')
        r = read_scale(src,'How many tokens was Juniper trained on?')
        self.assertIsNone(r['answer'])
        self.assertEqual([h['page'] for h in r['hits']], [1, 2])

    def test_explicit_model_or_own_paper_and_unit_conditions(self):
        src = paper('We present Larch, a multilingual model.',
                    'We trained on 820 hours of accelerator time.',
                    'We trained on over 530 hours of labeled audio.',title='Experiments in speech.')
        self.assertEqual(read_scale(src, 'How many hours of audio were used to train Larch?')['answer'], 'over 530 hours')
        self.assertIsNone(read_scale(src, 'How many hours of audio were used to train Cherry?'))
        paired = paper('We trained Cedar on a dataset of 12 million (image, text) pairs.',title='Learning to align')
        self.assertEqual(read_scale(paired, 'How many image-text pairs were used to train Cedar?')['answer'],
                         '12 million (image, text) pairs')

    def test_largest_requires_an_explicit_upper_endpoint(self):
        q = 'How many parameters does the largest Cypress model have?'
        self.assertIsNone(read_scale(paper('We present Cypress, a language model with 340 million parameters.'), q))
        self.assertIsNone(read_scale(paper('Cypress has 47 million parameters, whereas our largest model is Elm.'), q))
        for lower, expected in [('150 million','340 million parameters'), ('1.2 billion',None)]:
            r = read_scale(paper(f'We train a family ranging from {lower} parameters to 340 million parameters, with the last being the model we call Cypress.'), q)
            self.assertEqual(r and r['answer'], expected)

    def test_unrelated_or_conditional_readings_do_not_supply_a_quantity(self):
        src = paper('If we trained on 40 million tokens, latency would double.',
                    'We trained on 50 million tokens per step.', 'We trained on 13 or 17 tokens per batch.')
        self.assertIsNone(read_scale(src,'How many tokens was Juniper trained on?'))
        self.assertIsNone(read_scale(src,'How many tokens was Juniper trained on per epoch?'))
