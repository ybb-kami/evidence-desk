"""Verify selected-set Future Work, a many-node graph and its real PDF links."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from urllib.parse import quote

import pymupdf

from corpus import call, workbench
from synthesis_twenty import PAPERS, verify_excerpts


EXTRA = ('2203.02155v1.pdf', '2305.18290v3.pdf')
PROPOSAL = 'Our future work will profile decoder memory fragmentation on the Atlas test split using 8 GPUs.'
OBSERVED = 'We measured decoder memory fragmentation on the Atlas test split using 8 GPUs.'
PARAPHRASE = 'Future research should measure accelerator allocation gaps in the Atlas test split with 8 graphics processors.'


def fixtures(folder):
    proposals = [PROPOSAL, PROPOSAL.replace('memory', 'MEMORY'), PROPOSAL.replace('profile', 'quantify'),
                 PROPOSAL.replace('using 8 GPUs', 'using 16 GPUs'), OBSERVED,
                 OBSERVED.replace('8 GPUs', '16 GPUs'), PARAPHRASE]
    for i, text in enumerate(proposals, 1):
        path = folder / f'future-sample-{i}.pdf'
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=612, height=792)
            page.insert_text((16, 710), f'arXiv:2201.{i:05d}v1 [cs.CL] {i} Jan 2022', fontsize=10, rotate=90)
            page.insert_text((85, 75), f'Independent Evidence Case {i}', fontsize=18)
            page.insert_text((85, 145), 'Abstract')
            page.insert_text((85, 171), f'This report describes a distinct analysis of benchmark reproducibility case {i}.')
            page.insert_text((85, 250), '1. Introduction')
            assert page.insert_textbox(pymupdf.Rect(85, 282, 500, 340), text, fontsize=10) > 0
            if i in (5, 6):
                page.insert_text((85, 530), 'References')
                page.insert_text((85, 560), '[1] A. Author. Independent Evidence Case 1. arXiv:2201.00001.')
            pdf.save(path)
        yield path


def upload(port, paths):
    documents = {}
    for path in paths:
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        result = call(port, '/api/import?name=' + quote(path.name), data, 'application/pdf')
        assert result['doc']['id'] == digest
        documents[digest] = path
    return documents


def browser_run(browser, port, documents, *, vector=False):
    origin = f'http://127.0.0.1:{port}'
    tab = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors, external = [], []
    tab.on('pageerror', lambda exc: errors.append(str(exc)))
    tab.on('request', lambda req: external.append(req.url) if not req.url.startswith(origin + '/') else None)
    try:
        tab.goto(origin + '/synthesis')
        tab.wait_for_selector('#papers input')
        assert tab.locator('#papers input').count() == len(documents)
        tab.check('#all')
        if vector:
            tab.check('#future-vector')
        start = perf_counter()
        with tab.expect_response(lambda response: response.url == origin + '/api/synthesis') as response:
            tab.get_by_role('button', name='整理本地证据').click()
        assert response.value.ok
        data = response.value.json()
        graph = tab.locator('.graph-map button[data-graph-work]')
        graph.first.wait_for()
        elapsed = round((perf_counter() - start) * 1000, 1)
        assert graph.count() == data['works'] == len(documents)
        assert len({button.get_attribute('aria-label') for button in graph.all()}) == data['works']
        assert tab.locator('#future-proposals').count() == 1
        for group in data['proposals']['groups']:
            detail = tab.locator('#proposal-' + group['id'])
            assert detail.locator('.proposal-evidence blockquote').count() == len(group['members'])
        edge = data['links'][0]
        tab.locator(f'[data-graph-work="{edge["source"]}"]').click()
        tab.locator(f'#graph-selection a[href="#{edge["ref"]}"]').click()
        assert tab.evaluate('location.hash') == '#' + edge['ref']
        src = next(row for row in data['sources'] if row['ref'] == edge['ref'])
        excerpt = tab.locator('article#' + edge['ref'])
        assert excerpt.locator('blockquote').text_content() == src['text']
        destination = excerpt.locator('a').get_attribute('href')
        assert destination == f'/pdf/{src["doc"]}#page={src["page"]}'
        original = tab.request.get(origin + destination)
        assert original.ok and sha256(original.body()).hexdigest() == src['doc']
        assert not errors and not external, (errors, external)
        return data, {'nodes_rendered': graph.count(), 'edges': len(data['links']), 'click': edge['ref'],
                      'physical_page': src['page'], 'submit_to_render_ms': elapsed}
    finally:
        tab.close()


def run(folder, browser, *, vector=False):
    with workbench() as port:
        papers = upload(port, [folder / name for name in (*PAPERS, *EXTRA)])
        data, real = browser_run(browser, port, papers)
        verify_excerpts(data['sources'], papers)
        assert len(data['papers']) == 22
        real.update(proposals=len(data['follow_ups']), exact_duplicates=data['proposals']['normalized_duplicates'],
                    near_pairs=data['proposals']['candidate_count'],
                    comparisons=sum(row['checked'] for row in data['follow_ups']),
                    reported_same_scope=sum(row['same_scope'] for row in data['follow_ups']))

    with TemporaryDirectory(prefix='evidence-future-fixtures-') as temp, workbench() as port:
        samples = upload(port, list(fixtures(Path(temp))))
        data, controlled = browser_run(browser, port, samples, vector=vector)
        verify_excerpts(data['sources'], samples)
        layer = data['proposals']
        assert (len(layer['groups']), layer['normalized_duplicates']) == (4, 1)
        assert layer['vector'] == ('ready' if vector else 'not_requested')
        assert {'conditions_differ', 'review'} <= {pair['relation'] for pair in layer['candidates']}
        vector_pairs = [pair for pair in layer['candidates'] if pair['method'] == 'e5']
        assert bool(vector_pairs) is vector
        dated = next(group for group in layer['groups'] if len(group['members']) == 2)
        assert all(row['date'] for row in dated['members'])
        observed = next(row for row in data['follow_ups'] if row['direction'] == dated['members'][0]['ref'])
        assert observed['state'] == 'reported_same_scope'
        assert {hit['kind'] for hit in observed['candidates']} == {'reported_same_scope', 'topic_candidate'}
        controlled.update(duplicates=layer['normalized_duplicates'], near_pairs=layer['candidate_count'],
                          later_matches=observed['matches'], same_scope=observed['same_scope'], vector=layer['vector'],
                          vector_only_pairs=len(vector_pairs))
    return {'real_corpus': real, 'controlled': controlled}


if __name__ == '__main__':
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf_dir', type=Path)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--vector', action='store_true', help='Verify optional E5 only on the small controlled samples')
    args = parser.parse_args()
    with sync_playwright() as playwright:
        with playwright.chromium.launch(executable_path=args.chromium) as browser:
            print(json.dumps(run(args.pdf_dir, browser, vector=args.vector), indent=2))
