import unittest
from pathlib import Path
from engine import parse_pdf
from local_review import collection_review
class TestCollectionReview(unittest.TestCase):
    def test_demo_source_and_empty(self):
        self.assertEqual(collection_review([])['edges'],[])
        docs=[parse_pdf(p.read_bytes(),p.name) for p in (Path(__file__).parent/'demo').glob('*.pdf')]
        r=collection_review(docs);self.assertEqual(len(r['edges']),1)
        self.assertEqual((r['edges'][0]['source_document_name'],r['edges'][0]['page']),('B-cloud.pdf',2))
        self.assertEqual(r['future_mentions'],[])
    def test_retains_speaker_and_limits(self):
        d=parse_pdf((Path(__file__).parent/'sample.pdf').read_bytes(),'sample.pdf')
        text='The authors noted future work may explain this variability.'
        d['chunks'][0]['text']=text
        r=collection_review([d]);self.assertEqual(r['future_mentions'][0]['text'],text)
        self.assertIn('未查证',r['limits'])


class TestLaterCandidates(unittest.TestCase):
    direction = 'Future work should characterize sparse attention bottlenecks under long sequences.'
    later_excerpt = 'Our measurements characterize sparse attention bottlenecks on longer inputs.'
    earlier_stamp = 'arXiv:2001.01001v1 [cs.CL] 2 Jan 2020'
    later_stamp = 'arXiv:2302.02001v1 [cs.CL] 14 Feb 2023'
    references = '[1] T. Author. Earlier published article. arXiv:2001.01001.'
    reverse_references = '[1] U. Author. Later published article. arXiv:2302.02001.'

    def article(self, name, stamp, body, references='', *, margin=True):
        import fitz

        with fitz.open() as pdf:
            cover = pdf.new_page()
            cover.insert_text((80, 80), 'An Example Article for Local Review', fontsize=18)
            cover.insert_text((65, 200), 'Background and author information.', fontsize=11)
            if stamp:
                point = (22, 650) if margin else (65, 265)
                cover.insert_text(point, stamp, fontsize=10, rotate=90 if margin else 0)
            main = pdf.new_page()
            main.insert_text((65, 110), body, fontsize=10)
            if references:
                bibliography = pdf.new_page()
                bibliography.insert_text((65, 110), 'References', fontsize=12)
                bibliography.insert_text((65, 155), references, fontsize=10)
            return parse_pdf(pdf.tobytes(), name)

    def test_later_citation_links_to_each_physical_pdf_excerpt(self):
        earlier = self.article('source.pdf', self.earlier_stamp, self.direction)
        later = self.article('candidate.pdf', self.later_stamp, self.later_excerpt, self.references)
        result = collection_review([earlier, later])
        self.assertEqual(len(result['later_candidates']), 1)
        candidate = result['later_candidates'][0]
        for key, document, page, expected_text in (
            ('direction', earlier, 2, self.direction),
            ('later_excerpt', later, 2, self.later_excerpt),
            ('earlier_stamp', earlier, 1, self.earlier_stamp),
            ('later_stamp', later, 1, self.later_stamp),
            ('citation', later, 3, self.references),
        ):
            with self.subTest(key=key):
                row = candidate[key]
                self.assertEqual((row['source_document'], row['page']), (document['id'], page))
                self.assertEqual(row['text'], expected_text)
                self.assertTrue(any(
                    chunk['page'] == page and chunk['block'] == row['block']
                    and expected_text in chunk['text'] for chunk in document['chunks']
                ))
        self.assertEqual(candidate['earlier_stamp']['date'], '2020-01-02')
        self.assertEqual(candidate['later_stamp']['date'], '2023-02-14')
        self.assertIn('bottlenecks', candidate['matched_terms'])

    def test_candidates_require_a_later_dated_citing_document_and_shared_topic(self):
        cases = [
            ('date missing', 'arXiv:2302.02001v1 [cs.CL]', self.later_excerpt, self.references, True),
            ('date-like filename only', None, self.later_excerpt, self.references, True),
            ('body arxiv mention', self.later_stamp, self.later_excerpt, self.references, False),
            ('invalid calendar date', 'arXiv:2302.02001v1 [cs.CL] 30 Feb 2023',
             self.later_excerpt, self.references, True),
            ('same day', 'arXiv:2302.02001v1 [cs.CL] 2 Jan 2020',
             self.later_excerpt, self.references, True),
            ('source is later', 'arXiv:2302.02001v1 [cs.CL] 1 Jan 2020',
             self.later_excerpt, self.references, True),
            ('no directed citation', self.later_stamp, self.later_excerpt, '', True),
            ('unrelated citing text', self.later_stamp, 'We investigate vanishing gradients in deep networks.',
             self.references, True),
            ('single shared term', self.later_stamp, 'We visualize attention in multilayer networks.',
             self.references, True),
            ('scattered overlap without a shared topic phrase', self.later_stamp,
             'Bottlenecks in memory caches differ from sparse indexing tricks and attention in speech.',
             self.references, True),
            ('overlap only in references', self.later_stamp, 'We examine unrelated handwritten examples.',
             self.references + '\n[2] Z. Other. Characterize sparse attention bottlenecks.', True),
        ]
        earlier = self.article('source.pdf', self.earlier_stamp, self.direction)
        for label, stamp, body, references, margin in cases:
            with self.subTest(label=label):
                later = self.article('2302.02001v1-2023-02-14.pdf', stamp, body, references, margin=margin)
                self.assertEqual(collection_review([earlier, later])['later_candidates'], [])

    def test_reverse_citation_does_not_create_a_candidate(self):
        earlier = self.article('source.pdf', self.earlier_stamp, self.direction, self.reverse_references)
        later = self.article('candidate.pdf', self.later_stamp, self.later_excerpt)
        result = collection_review([earlier, later])
        self.assertEqual(len(result['edges']), 1)
        self.assertEqual(result['edges'][0]['source_document'], earlier['id'])
        self.assertEqual(result['later_candidates'], [])

    def test_future_phrase_in_bibliography_does_not_create_a_candidate(self):
        earlier = self.article(
            'source.pdf', self.earlier_stamp, 'We examine language models.',
            '[1] ' + self.direction,
        )
        later = self.article('candidate.pdf', self.later_stamp, self.later_excerpt, self.references)
        result = collection_review([earlier, later])
        self.assertEqual(len(result['future_mentions']), 1)
        self.assertEqual(result['later_candidates'], [])

    def test_generic_method_language_is_not_topic_evidence(self):
        earlier = self.article(
            'source.pdf', self.earlier_stamp,
            'Future work should improve our pre-trained models using more training data.',
        )
        later = self.article(
            'candidate.pdf', self.later_stamp,
            'Our pre-trained models use data from other training tasks.', self.references,
        )
        self.assertEqual(collection_review([earlier, later])['later_candidates'], [])

    def test_cross_reference_prose_is_not_topic_evidence(self):
        earlier = self.article(
            'source.pdf', self.earlier_stamp,
            'Future work requires more discussion, see Section 5 for our design decisions.',
        )
        later = self.article(
            'candidate.pdf', self.later_stamp,
            'For more discussion, see Section 2 on the sampling technique.', self.references,
        )
        self.assertEqual(collection_review([earlier, later])['later_candidates'], [])
