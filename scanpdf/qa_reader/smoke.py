"""Run a verified MobileBERT QA model on new synthetic facts, gaps and ambiguous sources."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from time import perf_counter

from .assets import MODEL_SHA256
from .reader import SpanReader


def hit(number, text):
    return {'id': f'{number:064x}', 'page': number, 'block': number * 2, 'part': 1, 'offset': 12, 'text': text}


def run(model):
    reader = SpanReader(model)
    station = hit(3, 'The floating weather station runs on two batteries made by Adaria in Wekalo. '
                     'On Friday its solar panel sent 37 readings to the field office. '
                     'The manual specifies that visits occur every May.')
    freezer = hit(4, 'A room was labelled Réserve. On Tuesday the freezer held 58 sealed samples. '
                     'The room was closed on Friday.')
    ships = [hit(5, 'The vessel called the Eldrin departed at dawn. Hali Mora captained the Eldrin.'),
             hit(6, 'The vessel called the Eldrin departed after noon. Nera Sol captained the Eldrin.')]
    cases = (
        ('positive', 'How many readings did its solar panel send on Friday?', [station]),
        ('positive_unicode_offsets', 'How many samples were stored in the freezer?', [freezer]),
        ('missing_role', 'What is the mayor’s name?', [station]),
        ('missing_value', 'How much did a battery cost?', [station]),
        ('conflicting_sources', 'Who captained the Eldrin?', ships),
    )

    def check(case):
        label, question, hits = case
        start = perf_counter()
        candidates = reader.read(question, hits)
        elapsed = round((perf_counter() - start) * 1000, 1)
        for candidate in candidates:
            source = hits[candidate['hit_index']]
            text = source['text']
            a, b = candidate['answer_start'], candidate['answer_end']
            c, d = candidate['context_start'], candidate['context_end']
            assert c <= a < b <= d and text[a:b] == candidate['answer'] and text[c:d] == candidate['context']
            assert all(source[key] == candidate[key] for key in ('id', 'page', 'block', 'part'))
        return label, {'elapsed_ms': elapsed, 'question': question, 'hits': len(hits), 'candidates': candidates}

    results = dict(map(check, cases))
    assert results['positive']['candidates'][0]['answer'] == '37'
    assert results['positive_unicode_offsets']['candidates'][0]['answer'] == '58'
    conflicts = {row['answer']: row['page'] for row in results['conflicting_sources']['candidates']}
    assert conflicts.get('Hali Mora') == 5 and conflicts.get('Nera Sol') == 6
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = dict(pool.map(check, cases[:2]))
    assert all(result['candidates'] == results[label]['candidates'] for label, result in concurrent.items())
    return {'weight_sha256': MODEL_SHA256, 'device': 'CPU', 'concurrency_checked': True, 'cases': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.model), indent=2, ensure_ascii=False))
