"""Opt-in HTTP acceptance against the public 2026-02-09 UDL author release.

Run: python test_longdoc.py /path/to/UnderstandingDeepLearning_02_09_26_C.pdf
No download at test time; no remote model. Requires Poppler and Linux procfs.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import quote, urlsplit

SHA256 = 'f8237d393163900fa8e43210e680a3f987b45ccac7750b372e156fae3df0bf32'
BYTES = 22_344_992
PAGES = 541
FIXTURE = 'UnderstandingDeepLearning_02_09_26_C.pdf'
QUERIES = [
    ('chapter prose', 'asymmetry of information between the user and the AI system', 440, '426'),
    ('appendix prose', 'maximum distance between the cumulative probability curves', 476, '462'),
    ('final-page index entry only', 'holes in latent space, 346', 541, '527'),
]


def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def rss(pid):
    """Linux server-process lifetime high-water RSS in KiB (not client RSS)."""
    line = next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines()
                if x.startswith('VmHWM:'))
    return int(line.split()[1])


def request(port, method, path, data=None, kind=None, expected=200, length=None):
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=90)
    headers = {'Content-Type': kind} if kind else {}
    if length is not None:
        headers['Content-Length'] = str(length)
    started = time.perf_counter()
    try:
        client.request(method, path, body=data, headers=headers)
        resp = client.getresponse()
        output = resp.read()
        elapsed = time.perf_counter() - started
        assert resp.status == expected, (path, resp.status, output[:1000])
        if resp.headers['Content-Type'].startswith('application/json'):
            output = json.loads(output)
        return output, elapsed
    finally:
        client.close()


def acceptance(pdf):
    info = subprocess.check_output(['pdfinfo', str(pdf)], text=True)
    assert int(re.search(r'^Pages:\s+(\d+)', info, re.M)[1]) == PAGES
    data = pdf.read_bytes()
    assert len(data) == BYTES and hashlib.sha256(data).hexdigest() == SHA256

    observations = {'bytes': BYTES, 'pages': PAGES, 'sha256': SHA256, 'queries': []}
    with tempfile.TemporaryDirectory(prefix='evidence-udl-') as tmp:
        server = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name('app.py')),
             '--port', '0', '--data', tmp],
            stdout=subprocess.PIPE, text=True, encoding='utf-8',
        )
        try:
            banner = server.stdout.readline()
            match = re.search(r'http://127\.0\.0\.1:\d+', banner)
            assert match, banner
            port = urlsplit(match[0]).port
            baseline, _ = request(port, 'GET', '/api/library')
            assert baseline == {'docs': [], 'deepseek_enabled': False}
            observations['baseline_peak_rss_kib'] = rss(server.pid)

            path = f'/api/import?name={quote(FIXTURE)}'
            # The length guard rejects before it attempts any read of this body.
            request(port, 'POST', path, b'', 'application/pdf', expected=413,
                    length=24 * 1024**2 + 1)
            result, elapsed = request(port, 'POST', path, data, 'application/pdf')
            obs = result['doc']
            assert not result['existing'] and obs['id'] == SHA256 and obs['pages'] == PAGES
            assert obs['name'] == FIXTURE and obs['size'] == BYTES and obs['indexed_blocks'] > 6000
            observations.update(import_seconds=round(elapsed, 3), import_peak_rss_kib=rss(server.pid),
                                indexed_passages=obs['indexed_blocks'], textless_pdf_pages=obs['textless_pages'])

            for category, phrase, page, label in QUERIES:
                response, elapsed = request(port, 'POST', '/api/search',
                    json.dumps({'ids': [SHA256], 'query': phrase}).encode(), 'application/json')
                source = subprocess.check_output(
                    ['pdftotext', '-f', str(page), '-l', str(page), str(pdf), '-'], text=True
                )
                assert normalize(phrase) in normalize(source), (category, page, 'Poppler phrase missing')
                matching = [(rank, hit) for rank, hit in enumerate(response['hits'], 1)
                            if hit['id'] == SHA256 and hit['page'] == page and hit['page_label'] == label
                            and normalize(phrase) in normalize(hit['text'])]
                assert matching, (category, page, [hit['page'] for hit in response['hits']])
                observations['queries'].append(dict(category=category, pdf_page=page, printed_page=label,
                    rank=matching[0][0], query_seconds=round(elapsed, 3), server_peak_rss_kib=rss(server.pid)))

            original, _ = request(port, 'GET', f'/pdf/{SHA256}')
            assert hashlib.sha256(original).hexdigest() == SHA256
        finally:
            server.terminate()
            server.wait(timeout=10)
            server.stdout.close()
    return observations


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    print(json.dumps(acceptance(parser.parse_args().pdf), indent=2))
