"""Recheck a selected 20-paper LLM review against the local contest PDFs and Chromium."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import pymupdf

from corpus import call, workbench

# From the contest bundle. One version per work, with no supplementary PDFs.
PAPERS = (
    '1706.03762v1.pdf', 'N19-1423.pdf', 'language_understanding_paper.pdf', '2005.14165v4.pdf',
    '2001.08361v1.pdf', '1910.10683v4.pdf', '1910.02054v3.pdf', '2002.04745v2.pdf',
    '2101.03961v3.pdf', '2106.09685v2.pdf', '2205.14135v2.pdf', '2211.05100v4.pdf',
    '333078981_693988129081760_4712707815225756708_n.pdf', '2307.09288v2.pdf',
    '2407.21783v3.pdf', '2305.14314v1.pdf', '2210.02414v2.pdf', '2303.08774v6.pdf',
    '2412.15115v2.pdf', '2412.19437v1.pdf',
)


def verify_excerpts(sources, documents):
    by_pdf_and_page = defaultdict(lambda: defaultdict(list))
    for source in sources:
        by_pdf_and_page[source['doc']][source['page']].append(source)
    for digest, pages in by_pdf_and_page.items():
        with pymupdf.open(documents[digest]) as pdf:
            for page, references in pages.items():
                raw = pdf[page - 1].get_text('blocks', flags=pymupdf.TEXTFLAGS_BLOCKS)
                blocks = {block[5] + 1: ' '.join(block[4].split()) for block in raw if block[6] == 0}
                for reference in references:
                    assert ' '.join(reference['text'].split()) in blocks[reference['block']], reference


def verify_response(data, graph, documents):
    assert data['mode'] == 'local' and not data['paragraphs']
    assert len(documents) == len(data['papers']) == data['works'] == len(PAPERS)
    assert {paper['id'] for paper in data['papers']} == set(documents)
    assert len({paper['name'] for paper in data['papers']}) == len(PAPERS)
    sources = {source['ref']: source for source in data['sources']}
    papers = {paper['id']: paper for paper in data['papers']}
    graph_by_pair = {(link['source'], link['target']): link for link in graph['edges']}
    assert len(graph_by_pair) == data['edges'] == len(data['links']) > 0
    assert len(sources) == len(data['sources'])
    assert len({paper['work'] for paper in papers.values() if paper['abstract']}) == data['abstracts']
    for paper in papers.values():
        expected = [(paper['abstract'], '摘要')] if paper['abstract'] else []
        expected.extend((ref, '未来工作') for ref in paper['future'])
        if paper['date']:
            expected.append((paper['date']['ref'], '日期'))
        for ref, category in expected:
            assert sources[ref]['doc'] == paper['id'] and sources[ref]['category'] == category
    for link in data['links']:
        edge = graph_by_pair[link['source'], link['target']]
        source = sources[link['ref']]
        assert source['doc'] == link['doc'] == edge['source_document']
        assert [source[key] for key in ('page', 'block', 'text')] == [edge[key] for key in ('page', 'block', 'text')]
        assert source['category'] == '引用'
    assert [(node['id'], node['indegree'], node['outdegree']) for node in sorted(data['nodes'], key=lambda n:n['id'])] == [
        (node['id'], node['indegree'], node['outdegree']) for node in graph['nodes']]
    assert abs(sum(node['pagerank'] for node in data['nodes']) - 1) < 1e-8
    assert Counter(n for g in data['groups'] for n in g['works']) == Counter({n['id']: 1 for n in graph['nodes']})
    assert Counter(ref for g in data['groups'] for ref in g['links']) == Counter({link['ref']: 1 for link in data['links']})
    links = {link['ref']: link for link in data['links']}
    for group in data['groups']:
        assert all(links[ref]['source'] in group['works'] and links[ref]['target'] in group['works']
                   for ref in group['links'])
    years = [(row['date'] or {}).get('year', 9999) for row in data['follow_ups']]
    assert years and sorted(years) == years
    assert {row['direction'] for row in data['follow_ups']} == {ref for paper in papers.values() for ref in paper['future']}
    for row in data['follow_ups']:
        assert 0 <= len(row['candidates']) <= row['matches'] <= row['checked'] <= row['later'] <= row['citations']
        earlier = papers[sources[row['direction']]['doc']]
        assert sum(link['target'] == earlier['work'] for link in data['links']) == row['citations']
        for relative in row['candidates']:
            excerpt, citation = sources[relative['excerpt']], links[relative['citation']]
            later = papers[excerpt['doc']]
            assert later['date'] == relative['date'] and earlier['date'] == row['date']
            assert citation['target'] == earlier['work'] and citation['doc'] == later['id']
            assert (later['date']['year'] > earlier['date']['year'] or
                    later['date']['date'] > earlier['date']['date'])
    verify_excerpts(sources.values(), documents)


def verify_browser(port, data, chromium):
    from playwright.sync_api import sync_playwright

    origin = f'http://127.0.0.1:{port}'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            failures, outside = [], []
            page.on('pageerror', lambda error: failures.append(str(error)))
            page.on('request', lambda request: outside.append(request.url) if not request.url.startswith(origin + '/') else None)
            page.goto(origin + '/')
            page.get_by_text('选择文献生成综述').click()
            page.wait_for_selector('#papers input')
            assert page.locator('#papers input').count() == len(PAPERS)
            assert not page.locator('#remote').is_visible()
            page.check('#all')
            with page.expect_response(lambda response: response.url == origin + '/api/synthesis') as captured:
                page.get_by_role('button', name='整理本地证据').click()
            assert captured.value.json() == data
            page.get_by_role('heading', name='逐份文献', exact=True).wait_for()
            assert page.locator('tr[id^="paper-"]').count() == len(PAPERS)
            for paper in data['papers']:
                row = page.locator('#paper-' + paper['id'])
                if paper['abstract']:
                    assert row.locator(f'a[href="#{paper["abstract"]}"]').count() == 1
                else:
                    assert '未定位到有明确摘要标题的完整摘录' in row.inner_text()
                if not paper['date']:
                    assert '缺少可定位日期' in row.inner_text()
            assert page.locator('details[id^="group-"]').count() == len(data['groups'])
            assert page.locator('details[data-direction]').count() == len(data['follow_ups'])
            for source in data['sources']:
                article = page.locator('article#' + source['ref'])
                assert article.locator('blockquote').text_content() == source['text']
                assert article.locator('a').get_attribute('href') == f"/pdf/{source['doc']}#page={source['page']}"
            first_citation = next(source for source in data['sources'] if source['category'] == '引用')
            original = page.request.get(f"{origin}/pdf/{first_citation['doc']}")
            assert original.ok and hashlib.sha256(original.body()).hexdigest() == first_citation['doc']
            assert not failures and not outside, (failures, outside)
        finally:
            browser.close()


def run(folder, chromium):
    with workbench() as port:
        documents = {}
        for name in PAPERS:
            source = folder / name
            raw = source.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            doc = call(port, '/api/import?name=' + quote(name), raw, 'application/pdf')['doc']
            assert doc['id'] == digest
            documents[digest] = source
        graph = call(port, '/api/graph')
        data = call(port, '/api/synthesis', {'ids': list(documents), 'mode': 'local', 'consent': False})
        verify_response(data, graph, documents)
        if chromium:
            verify_browser(port, data, chromium)
    return {
        'subject': 'LLM architecture, pre-training, scaling and efficient adaptation',
        'papers': [{'file': paper['name'], 'sha256': paper['id'], 'abstract': paper['abstract'],
                    'date': paper['date'], 'future': paper['future']} for paper in data['papers']],
        'works': data['works'], 'citation_edges': data['edges'], 'citation_group_sizes': [len(g['works']) for g in data['groups']],
        'excerpts_verified_in_pdf': dict(Counter(source['category'] for source in data['sources'])),
        'future_directions_checked': len(data['follow_ups']),
        'later_citing_full_text_comparisons': sum(row['checked'] for row in data['follow_ups']),
        'later_topic_candidates': sum(row['matches'] for row in data['follow_ups']),
        'browser_checked': bool(chromium), 'live_model_called': False,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf_dir', type=Path)
    parser.add_argument('--chromium', help='Also verify the on-screen result using this Chromium executable')
    args = parser.parse_args()
    print(json.dumps(run(args.pdf_dir, args.chromium), ensure_ascii=False, indent=2))
