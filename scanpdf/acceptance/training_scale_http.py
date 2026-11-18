"""Verify source-bound training quantities through the normal upload and question APIs."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import quote, urlencode

from corpus import call, workbench


CASES = {
    '2005.14165v4.pdf': [
        ('How many parameters does the largest GPT-3 model have?', '175 billion parameters', 8),
        ('GPT-3 训练使用了多少 token？', '300 billion tokens', 8),
        ('How many training tokens are reported for GPT-3?', '300 billion tokens', 8),
    ],
    '2307.09288v2.pdf': [('Llama 2 预训练使用多少 token？', '2 trillion tokens', 5)],
}


def run(folder):
    results = []
    with workbench() as port:
        for name, cases in CASES.items():
            raw = (folder / name).read_bytes()
            doc = call(port, '/api/import?name=' + quote(name), raw, 'application/pdf')['doc']
            assert doc['id'] == sha256(raw).hexdigest()
            for question, expected, page in cases:
                answer = call(port, '/api/followup', {'id':doc['id'], 'question':question, 'anchor':None})
                assert answer['answer'] == expected, (question, answer.get('answer'))
                hit = answer['hits'][0]
                assert hit['page'] == page and hit['id'] == doc['id']
                at = {key:hit[key] for key in ('id','page','block','part','start','end')}
                source = call(port, '/api/source?' + urlencode(at))
                pointed = source['text'][at['start']:at['end']]
                assert expected in ' '.join(pointed.split()), pointed
                assert source['selection']['precision'] == 'glyph' and source['selection']['boxes']
                results.append({'question':question, 'answer':expected, 'location':at, 'quote':pointed})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdfs', type=Path)
    print(json.dumps(run(parser.parse_args().pdfs), ensure_ascii=False, indent=2))
