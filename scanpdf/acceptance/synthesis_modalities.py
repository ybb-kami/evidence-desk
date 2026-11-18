"""Verify a real versioned Future Work pair through the local review interface."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from corpus import workbench
from synthesis_future import upload
from synthesis_twenty import verify_excerpts


FILES = {
    '1706.03762v1.pdf': '0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476',
    '1706.03762v7.pdf': 'bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697',
    '2205.14135v2.pdf': 'ca7f9fda10b90fc05dd291a3accc85e9c1a4a860b99b31928dab03ed3fcb14e4',
}


def open_source(page, parent, source, origin):
    parent.locator(f'a[href="#{source["ref"]}"]').click()
    assert page.evaluate('location.hash') == '#' + source['ref']
    article = page.locator('article#' + source['ref'])
    assert article.locator('blockquote').text_content() == source['text']
    target = f'{origin}/pdf/{source["doc"]}#page={source["page"]}'
    with page.expect_popup() as event:
        article.locator('a').click()
    popup = event.value
    try:
        popup.wait_for_url(target)
        response = page.request.get(target)
        assert response.ok and sha256(response.body()).hexdigest() == source['doc']
    finally:
        popup.close()


def run(folder, browser):
    paths = [folder / name for name in FILES]
    assert {path.name: sha256(path.read_bytes()).hexdigest() for path in paths} == FILES
    with workbench() as port:
        documents = upload(port, paths)
        origin = f'http://127.0.0.1:{port}'
        tab = browser.new_page()
        errors, outbound = [], []
        tab.on('pageerror', lambda error: errors.append(str(error)))
        tab.on('request', lambda request: outbound.append(request.url) if not request.url.startswith(origin) else None)
        try:
            tab.goto(origin + '/')
            tab.get_by_text('选择文献生成综述').click()
            tab.wait_for_selector('#papers input')
            assert tab.locator('#papers input').count() == 3
            tab.check('#all')
            with tab.expect_response(origin + '/api/synthesis') as event:
                tab.get_by_role('button', name='整理本地证据').click()
            data = event.value.json()
            assert (data['mode'], data['works'], data['edges']) == ('local', 2, 1)
            assert not data['paragraphs']
            verify_excerpts(data['sources'], documents)
            sources = {source['ref']: source for source in data['sources']}
            review = {sources[row['direction']]['name']: row for row in data['follow_ups']
                      if 'modalities other than text' in sources[row['direction']]['text']}
            earlier, newer = [review[name] for name in ('1706.03762v1.pdf', '1706.03762v7.pdf')]
            assert earlier['date']['date'] == '2017-06-12' and newer['date']['date'] == '2023-08-02'
            assert earlier['state'] == 'partial_modality_scope' and earlier['same_scope'] == 0
            assert newer['later'] == 0 and not newer['candidates'] and newer['citations'] == 1
            [match] = earlier['candidates']
            assert match['date']['date'] == '2022-06-23'
            assert match['coverage'] == {'requested_modalities': ['image', 'audio', 'video'],
                                         'observed_modalities': ['image'], 'unverified_modalities': ['audio', 'video']}
            proposal_refs = {earlier['direction'], newer['direction']}
            group = next(row for row in data['proposals']['groups']
                         if {member['ref'] for member in row['members']} == proposal_refs)
            assert len(group['members']) == 2 and group['works'] == 1
            assert all(member['by_author'] for member in group['members'])
            assert [sources[ref]['page'] for ref in (*sorted(proposal_refs), match['excerpt'])] == [10, 10, 9]

            original_row = tab.locator('#later-' + earlier['direction'])
            original_row.locator('summary').click()
            original_row.get_by_text('后文报告部分模态的应用', exact=False).first.wait_for()
            assert '该摘录涉及 图像。原展望还列出 音频、视频' in original_row.inner_text()
            for ref in (earlier['direction'], match['excerpt'], match['citation']):
                open_source(tab, original_row, sources[ref], origin)
            revised_row = tab.locator('#later-' + newer['direction'])
            revised_row.locator('summary').click()
            assert '日期不晚于所选版本' in revised_row.inner_text()
            open_source(tab, revised_row, sources[newer['direction']], origin)
            assert not errors and not outbound, (errors, outbound)
            return {'works': data['works'], 'edges': data['edges'], 'same_scope': earlier['same_scope'],
                    'merged_versions': group['members'], 'coverage': match['coverage'],
                    'proposals': [sources[earlier['direction']], sources[newer['direction']]],
                    'later_excerpt': sources[match['excerpt']], 'bibliography': sources[match['citation']],
                    'publication_dates': [earlier['date'], match['date'], newer['date']],
                    'original_pages_opened_and_sha_verified': 4, 'external_requests': len(outbound)}
        finally:
            tab.close()


if __name__ == '__main__':
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf_dir', type=Path)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    with sync_playwright() as playwright:
        with playwright.chromium.launch(executable_path=args.chromium) as browser:
            print(json.dumps(run(args.pdf_dir, browser), indent=2, ensure_ascii=False))
