"""Follow the workbench's public UI from an empty temporary library at three widths."""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
GPU_QUESTION = '这篇论文中提到的 Transformer 架构，在训练 Base 模型时使用了多少个 GPU？具体是什么型号？'
ARCHITECTURE = '请对比 BERT 的编码器如何承袭原始 Transformer 的 Encoder，丢弃了哪个部分？'


@contextmanager
def workbench(checkout, executable):
    configured = {'EVIDENCE_LOCAL_QA_URL', 'EVIDENCE_LOCAL_QA_MODEL', 'EVIDENCE_QA_READER', 'DEEPSEEK_API_KEY'}
    env = {key: value for key, value in os.environ.items() if key not in configured}
    with TemporaryDirectory(prefix='evidence-ui-empty-library-') as library:
        server = subprocess.Popen(
            [executable, str(checkout / 'scanpdf' / 'workbench.py'), '--port', '0', '--data', library],
            cwd=checkout, env=env, stdout=subprocess.PIPE, text=True,
        )
        try:
            origin = server.stdout.readline().strip()
            assert origin.startswith('http://127.0.0.1:'), origin
            yield origin
        finally:
            server.terminate()
            server.wait(timeout=10)
            server.stdout.close()


def layout(page):
    return page.evaluate('''() => {
      const root = document.documentElement;
      const wordmark = document.querySelector('.mast .brand span:nth-child(2)');
      const nav = document.querySelector('.mast nav');
      const upload = document.getElementById('choose');
      const rect = n=>n ? Math.round(n.getBoundingClientRect().height) : null;
      return {overflow:root.scrollWidth-root.clientWidth, wordmark_height:rect(wordmark),
        wordmark_line:wordmark ? Math.round(parseFloat(getComputedStyle(wordmark).lineHeight)) : null,
        nav_height:rect(nav), import_top:upload ? Math.round(upload.getBoundingClientRect().top) : null};
    }''')


def upload(page, files):
    with page.expect_file_chooser() as event:
        page.locator('#choose').focus()
        page.locator('#choose').press('Enter')
    event.value.set_files(files)


def home(page, origin):
    page.get_by_role('link', name='返回工作台', exact=True).click()
    assert page.url == origin + '/'


def single(page, digest, *, fixed):
    ask = page.locator(f'a[href="/followup?id={digest}"]')
    ask.focus()
    ask.press('Enter')
    expect(page.locator('#doc')).to_have_value(digest)
    page.fill('#question', GPU_QUESTION)
    with page.expect_response(lambda reply: reply.url.endswith('/api/followup')) as response:
        page.locator('#question').press('Enter')
    assert response.value.ok
    expect(page.locator('#history strong').last).to_contain_text('8 块 NVIDIA P100 GPU')
    original = page.locator('#history').inner_text()
    selector = '#history a[href^="/source?"]' + ('[href*="start="]' if fixed else '')
    with page.expect_popup() as event:
        page.locator(selector).first.click()
    with event.value as source:
        expect(source.locator('#anchor')).to_contain_text('物理页 7 · 块 9')
        source.wait_for_function("() => document.querySelector('#image').naturalWidth > 0")
        expect(source.locator('#overlay .region').first).to_be_visible()
        marked = source.locator('#excerpt mark').all_text_contents()
        if fixed:
            assert any('NVIDIA P100 GPUs' in text for text in marked), marked
        assert layout(source)['overflow'] == 0
    assert page.locator('#history').inner_text() == original
    return {'answer': response.value.json()['answer'], 'physical_page': 7, 'block': 9,
            'marked_span': marked, 'popup_return_keeps_answer': True}


def bilingual(page, digest, width, *, fixed):
    page.get_by_role('link', name='双语精读', exact=True).click()
    page.select_option('#doc', digest)
    left = '#source-pane' if width > 680 else '#stacked'
    right = '#translation-pane' if width > 680 else '#stacked'
    original = page.locator(left + ' .source-card').filter(has_text='Attention Is All You Need').first
    original.wait_for()
    anchor = original.get_attribute('data-anchor')
    translated = page.locator(right + f' .translation-card[data-anchor="{anchor}"]')
    with page.expect_response(lambda r:r.url.endswith('/api/bilingual/translate')) as response:
        original.locator('.run-segment').focus()
        original.locator('.run-segment').press('Enter')
    assert response.value.ok
    expect(translated.locator('.translated')).not_to_have_text('待翻译')
    translated_text = translated.locator('.translated').inner_text()
    original.locator('.locate').press('Enter')
    expect(translated).to_be_focused()
    translated.locator('.locate').press('Enter')
    expect(original).to_be_focused()
    original.locator('summary').click()
    image = original.locator('details img')
    expect(image).to_have_attribute('src', f'/api/bilingual/crop/{digest}?page=1&block=2')
    page.wait_for_function("() => [...document.images].some(n=>n.complete && n.naturalWidth>0)")
    page.fill('#page', '999')
    if fixed:
        page.locator('#page').press('Enter')
        expect(page.locator('#status')).to_have_text('请输入 1 至 15 的物理页码，当前显示第 1 页。')
        expect(page.locator('#page')).to_have_value('1')
        expect(translated.locator('.translated')).to_have_text(translated_text)
        expect(image).to_be_visible()
    else:
        page.locator('#go').click()
        expect(page.locator('#status')).to_have_text('页码超出范围。')
    return {'translation': translated_text, 'round_trip_focus': True, 'visible_after_invalid': translated.is_visible(),
            'page_after_invalid': page.locator('#page').input_value(), 'invalid_message': page.locator('#status').inner_text()}


def citation_graph(page, origin):
    page.get_by_role('link', name='本地引文图', exact=True).click()
    page.locator('#load-graph').press('Enter')
    expect(page.locator('#graph-status')).to_contain_text('1 条可定位引文。')
    selector = page.locator('#graph-node')
    expect(selector.locator('option')).to_have_count(2)
    selector.press('End')
    expect(selector).to_have_value(selector.locator('option').last.get_attribute('value'))
    assert layout(page)['overflow'] == 0
    edge = page.locator('#graph-edges a[href^="/pdf/"]').first
    href = edge.get_attribute('href')
    original = page.request.get(origin + href)
    assert original.ok and sha256(original.body()).hexdigest() == href.split('/')[-1].split('#')[0]
    return {'message': page.locator('#graph-status').inner_text(), 'link': href}


def csv_chart(page, *, fixed):
    page.get_by_role('link', name='CSV 图表', exact=True).click()
    page.get_by_role('link', name='按变量绘图与核对趋势', exact=True).click()
    field = page.locator('#trend-file')
    field.set_input_files({'name':'invalid.csv', 'mimeType':'text/csv', 'buffer':b'epoch,accuracy\n1,"broken'})
    expect(page.locator('#trend-source')).to_contain_text('未闭合的引号')
    field.set_input_files({'name':'results.csv', 'mimeType':'text/csv', 'buffer':b'epoch,accuracy\n1,80\n2,83\n3,81\n4,90\n'})
    page.select_option('#trend-x', '0')
    page.select_option('#trend-y', '1')
    page.locator('#trend-draw').press('Enter')
    expect(page.locator('#trend-kind')).to_be_focused()
    if fixed:
        expect(page.locator('#trend-status')).to_have_text('请选择横轴类型。')
    missing = page.locator('#trend-status').inner_text()
    page.select_option('#trend-kind', 'numeric')
    page.locator('#trend-draw').press('Enter')
    expect(page.locator('#trend-caption')).to_contain_text('CSV 第 5 行')
    assert layout(page)['overflow'] == 0
    preview = page.locator('#trend-preview')
    visible, content = preview.evaluate('(node) => [node.clientWidth,node.scrollWidth]')
    if fixed and content > visible:
        expect(page.locator('#trend-preview-hint')).to_be_visible()
        preview.focus()
        preview.press('ArrowRight')
        page.wait_for_function("() => document.querySelector('#trend-preview').scrollLeft > 0")
    with page.expect_download() as download:
        page.locator('#trend-svg').click()
    assert b'<svg' in download.value.path().read_bytes()
    page.get_by_role('link', name='返回 CSV 图表', exact=True).click()
    assert page.url.endswith('/#csv-workbench')
    return {'preview_width': visible, 'content_width': content, 'missing_axis_live_message': missing,
            'download': download.value.suggested_filename, 'returned_anchor': 'csv-workbench'}


def advanced(page, origin):
    page.get_by_role('link', name='科研 Copilot', exact=True).click()
    page.locator('#documents input').first.wait_for()
    for box in page.locator('#documents input').all():
        box.set_checked(True)
    page.locator('#planning').set_checked(False)
    page.fill('#question', 'Show the citation graph of the selected papers.')
    page.locator('#run').press('Enter')
    expect(page.locator('#state')).to_have_text('本次任务已结束。')
    expect(page.locator('#steps')).to_contain_text('引用与未来工作')
    assert layout(page)['overflow'] == 0
    steps = page.locator('#steps').inner_text()
    home(page, origin)
    page.get_by_role('link', name='跨篇问答', exact=True).click()
    page.locator('#docs input').first.wait_for()
    for box in page.locator('#docs input').all():
        box.set_checked(True)
    page.fill('#query', ARCHITECTURE)
    page.locator('#form button').press('Enter')
    expect(page.locator('#summary')).to_contain_text('独立解码器')
    assert page.locator('.papers article').count() == 2
    assert layout(page)['overflow'] == 0
    home(page, origin)
    return {'copilot_trace': steps, 'comparison_papers': 2, 'returned_to_home': True}


def run(browser, origin, folder, width, height, *, fixed):
    attention, bert = (folder / name for name in ('1706.03762v7.pdf', 'N19-1423.pdf'))
    digest = sha256(attention.read_bytes()).hexdigest()
    start = perf_counter()
    with browser.new_page(viewport={'width':width,'height':height}, is_mobile=width<500,
                          has_touch=width<500, accept_downloads=True) as page:
        errors, external = [], []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.on('request', lambda req: external.append(req.url)
                if not req.url.startswith((origin + '/', 'blob:', 'data:')) else None)
        page.goto(origin)
        expect(page.locator('#library-count')).to_have_text('0 篇')
        measured = layout(page)
        assert measured['overflow'] == 0
        if fixed:
            assert measured['wordmark_height'] <= measured['wordmark_line'] + 1, measured
        page.keyboard.press('Tab')
        expect(page.get_by_role('link', name='跳转到检索区')).to_be_focused()
        upload(page, {'name':'broken.pdf','mimeType':'application/pdf','buffer':b'broken file'})
        expect(page.locator('#import-log')).to_contain_text('broken.pdf')
        upload_error = page.locator('#import-log').inner_text()
        if fixed:
            assert '有效的 PDF' in upload_error, upload_error
        upload(page, [str(attention),str(bert)])
        expect(page.locator('#library-count')).to_have_text('2 篇')
        answer = single(page,digest,fixed=fixed)
        home(page,origin)
        translation = bilingual(page,digest,width,fixed=fixed)
        assert layout(page)['overflow'] == 0
        home(page,origin)
        graph = citation_graph(page,origin)
        csv = csv_chart(page,fixed=fixed)
        more = advanced(page,origin)
        assert not errors and not external, (errors,external)
    return {'viewport':[width,height], 'homepage':measured, 'invalid_pdf':upload_error, 'answer':answer,
            'translation':translation, 'graph':graph, 'csv':csv, **more,
            'seconds':round(perf_counter()-start,2)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf_dir', type=Path)
    parser.add_argument('--checkout', type=Path, default=ROOT)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--fixed', action='store_true', help='Assert the interaction improvements after c94c628')
    args = parser.parse_args()
    with sync_playwright() as playwright, playwright.chromium.launch(executable_path=args.chromium) as browser:
        results = []
        for width, height in ((1280,900),(390,844),(320,740)):
            with workbench(args.checkout.resolve(), args.python) as origin:
                results.append(run(browser,origin,args.pdf_dir,width,height,fixed=args.fixed))
    print(json.dumps(results,ensure_ascii=False,indent=2))
