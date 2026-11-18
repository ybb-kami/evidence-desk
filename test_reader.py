import unittest

import fitz

from engine import parse_pdf
from reader import build_reader


class ReaderTests(unittest.TestCase):
    def test_pdf_round_trip(self):
        with fitz.open() as pdf:
            p = pdf.new_page()
            p.insert_text((48, 55), '1\nIntroduction')
            p.insert_text((48, 130), 'Evidence [11] and [11, 12]; unavailable [99].')
            p = pdf.new_page()
            p.insert_text((48, 55), 'References\n[11] Alpha Source One, 2019.\n[12] Beta Source Two, 2021.')
            result = build_reader(parse_pdf(pdf.tobytes(), 'demo.pdf'))
        self.assertEqual([(s['title'], s['level']) for s in result['outline']], [('1 Introduction', 1), ('References', 1)])
        body = next(b for b in result['pages'][0]['blocks'] if 'citations' in b)
        self.assertEqual([body['text'][c['start']:c['end']] for c in body['citations']], ['[11]', '[11, 12]'])
        refs = {r['number']:r for r in result['references']}
        self.assertEqual([r['id'] for r in refs[11]['occurrences']], [c['id'] for c in body['citations']])
        self.assertEqual(refs[12]['occurrences'][0]['id'], body['citations'][1]['id'])
        bib_block = next(b for b in result['pages'][1]['blocks'] if 'entries' in b)
        for i, mark in enumerate(bib_block['entries'], 11):
            self.assertEqual(bib_block['text'][mark['start']:mark['end']], f'[{i}]')
            self.assertEqual(refs[i]['id'], mark['id'])

    def test_ambiguity_unicode_unmatched_and_textless(self):
        datum = {'id':'example', 'name':'demo', 'pages':3, 'chunks':[
            {'page':1, 'block':1, 'offset':0, 'text':'😀 主文 [11] [12] [11, 12] [12, 99] [12–11].'},
            {'page':2, 'block':1, 'offset':0, 'text':'References\n[11] Alpha Source One!\n[11] Short\n[12] Beta Source Two!'},
            {'page':2, 'block':2, 'offset':0, 'text':'Appendix A\nA test [12].'},
        ]}
        result = build_reader(datum)
        self.assertEqual(result['ambiguous_numbers'], [11])
        self.assertEqual([r['number'] for r in result['references']], [12])
        self.assertEqual(result['occurrences'], 2)
        b = result['pages'][0]['blocks'][0]
        self.assertEqual(len(b['citations']), 1)
        self.assertEqual(b['text'][b['citations'][0]['start']:b['citations'][0]['end']], '[12]')
        self.assertEqual(result['pages'][2]['blocks'], [])


if __name__ == '__main__':
    unittest.main()
