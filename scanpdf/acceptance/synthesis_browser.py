"""Verify the four-paper local review, its links, and every displayed PDF excerpt."""
import argparse
from pathlib import Path
from urllib.parse import quote

import fitz
from playwright.sync_api import sync_playwright

from corpus import call, workbench

PAPERS = ('1706.03762v1.pdf', 'N19-1423.pdf', 'language_understanding_paper.pdf', '2005.14165v4.pdf')


def check(folder, chromium):
    with workbench() as port, sync_playwright() as playwright:
        documents = {}
        for name in PAPERS:
            source = folder / name
            imported = call(port, '/api/import?name=' + quote(name), source.read_bytes(), 'application/pdf')
            documents[imported['doc']['id']] = source
        browser = playwright.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{port}/')
            page.get_by_text('选择文献生成综述').click()
            page.wait_for_selector('#papers input')
            page.check('#all')
            with page.expect_response(lambda response: response.url.endswith('/api/synthesis')) as captured:
                page.click('#local')
            data = captured.value.json()
            page.get_by_role('heading', name='摘要', exact=True).wait_for()
            assert data['abstracts'] == len(PAPERS) and data['mode'] == 'local'
            for source in data['sources']:
                article = page.locator('article#' + source['ref'])
                assert article.locator('blockquote').text_content() == source['text']
                assert article.locator('a').get_attribute('href') == f"/pdf/{source['doc']}#page={source['page']}"
                with fitz.open(documents[source['doc']]) as pdf:
                    blocks = pdf[source['page'] - 1].get_text('blocks', flags=fitz.TEXTFLAGS_BLOCKS)
                    assert any(b[5] + 1 == source['block'] and ' '.join(source['text'].split()) in
                               ' '.join(b[4].split()) for b in blocks)
            print(f"{data['works']} works, {data['edges']} edges, {data['abstracts']} abstracts, "
                  f"{len(data['sources'])} verified source excerpts")
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf_dir', type=Path)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    check(args.pdf_dir, args.chromium)
