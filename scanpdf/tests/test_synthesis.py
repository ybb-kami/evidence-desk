import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from synthesis import _model_sources, abstract_sentence, evidence_set, generated_paragraphs, handle


def paper(number, *, abstract=True, text='', reference='', day='2 Jan 2020', text_hash=None):
    arxiv_id = f'2001.{number:05d}'
    chunks = [{'page': 1, 'block': 1, 'text': f'Example article on policy and evidence {number}'}]
    if day:
        chunks.append({'page': 1, 'block': 2, 'text': f'arXiv:{arxiv_id}v1 [cs.CL] {day}', 'arxiv_stamp': True})
    if abstract:
        chunks.append({'page': 1, 'block': 3, 'text': 'Abstract\nWe examine the design of a useful sparse coding system.'})
    if text:
        chunks.append({'page': 2, 'block': 1, 'text': text})
    if reference:
        chunks.extend([{'page': 3, 'block': 1, 'text': 'References'}, {'page': 3, 'block': 2, 'text': reference}])
    return {'id': f'{number:064x}', 'name': f'{number}.pdf', 'arxiv': {'id': arxiv_id, 'version': 1},
            'text_hash': text_hash, 'text_chars': 600, 'chunks': chunks}


class SynthesisTests(unittest.TestCase):
    def test_first_page_abstract_remains_verbatim(self):
        doc = {'chunks': [{'page': 1, 'block': i, 'text': t} for i,t in enumerate([
            'Paper heading', 'Abstract', 'An important context is gradually changing.',
            'We propose a retrieval method built on a graph of evidence.',
            '1\nIntroduction', 'We achieve twice the performance of a new baseline.'],1)]}
        chunk, text = abstract_sentence(doc)
        self.assertEqual(chunk['block'], 4)
        self.assertEqual(text, chunk['text'])

    def test_only_front_matter_with_a_visible_abstract_heading_qualifies(self):
        chunks = [{'page': 1, 'block': 1, 'text': 'A paper with a long authorship section'},
                  {'page': 2, 'block': 1, 'text': 'Abstract\nWe provide a distinctive description of a research method.'},
                  {'page': 2, 'block': 2, 'text': '1. Introduction'}]
        self.assertEqual(abstract_sentence({'chunks': chunks})[0]['page'], 2)
        chunks.append({'page': 1, 'block': 2, 'text': '1\nIntroduction'})
        self.assertIsNone(abstract_sentence({'chunks': chunks}))

    def test_abstract_boundary_inside_a_pdf_block(self):
        abstract = 'Background information motivates the research questions.'
        block = {'page': 1, 'block': 1, 'text': f'Abstract\n{abstract}\n1 Introduction\nWe present a new analysis method here.'}
        self.assertEqual(abstract_sentence({'chunks': [block]})[1], abstract)

    def test_every_selected_pdf_has_coverage_or_gap_and_model_keeps_its_budget(self):
        docs = [paper(n) for n in range(1, 9)]
        docs.extend([paper(9, abstract=False, text_hash='duplicate'),
                     paper(10, text_hash='duplicate')])
        result = evidence_set(docs)
        self.assertEqual((len(result['papers']), result['works'], result['abstracts']), (10, 9, 9))
        rows = {row['id']: row for row in result['papers']}
        self.assertIsNone(rows[docs[8]['id']]['abstract'])
        self.assertEqual(rows[docs[8]['id']]['work'], rows[docs[9]['id']]['work'])
        self.assertTrue(rows[docs[9]['id']]['abstract'])
        model = _model_sources(result)
        self.assertEqual(len(model), 6)
        self.assertTrue(all(source['category'] == '摘要' for source in model))
        self.assertEqual(len(result['groups']), 9)

    def test_coverage_groups_keep_directed_links_with_real_pdf_anchors(self):
        earlier = paper(1, text='Future work should characterize sparse attention bottlenecks under long sequences.')
        matched = 'We measure sparse attention bottlenecks in networks with a smaller memory budget.'
        reference = '[1] A. Author. An earlier work. arXiv:2001.00001.'
        later = paper(2, text=matched, day='4 Feb 2023', reference=reference)
        undated = paper(3, text=matched, day=None, reference=reference)
        unrelated = paper(4, text=matched, day='8 May 2024')
        future_only = paper(5, text='Future work should investigate sparse attention bottlenecks in memory caches.',
                            day='3 Mar 2024', reference=reference)
        result = evidence_set([earlier, later, undated, unrelated, future_only])
        self.assertEqual([len(group['works']) for group in result['groups']], [4, 1])
        row = next(row for row in result['follow_ups'] if row['date']['year'] == 2020)
        self.assertEqual((row['citations'], row['unordered'], row['later'], row['checked'], row['matches']), (3, 1, 2, 2, 1))
        candidate, = row['candidates']
        self.assertEqual(candidate['date']['year'], 2023)
        sources = {source['ref']: source for source in result['sources']}
        self.assertEqual((sources[candidate['excerpt']]['page'], sources[candidate['excerpt']]['text']), (2, matched))
        self.assertEqual((sources[candidate['citation']]['page'], sources[candidate['citation']]['text']), (3, reference))
        self.assertEqual(sources[candidate['excerpt']]['doc'], later['id'])

    @patch('synthesis.provider.configured', return_value=True)
    @patch('synthesis.generated_paragraphs', side_effect=RuntimeError('offline'))
    def test_model_connection_failure_returns_local_evidence(self, _generate, _configured):
        docs = [paper(1), paper(2)]
        handler = SimpleNamespace(
            json_request=lambda: {'ids': [doc['id'] for doc in docs], 'mode': 'model', 'consent': True},
            server=SimpleNamespace(library=SimpleNamespace(records=lambda ids: docs), enable_model=True),
            payload=lambda code, data: (code, data),
        )
        code, data = handle(handler)
        self.assertEqual((code, data['mode'], data['abstracts'], data['paragraphs']), (200, 'unavailable', 2, []))

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
