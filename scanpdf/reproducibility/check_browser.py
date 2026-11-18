import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import json

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workbench
from reproducibility import github

parser = argparse.ArgumentParser(description='Check the repository evidence page with real paper PDFs.')
parser.add_argument('--pdf-dir', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--live', action='store_true', help='Confirm and run two public GitHub API requests through the page')
args = parser.parse_args()
root, out = args.pdf_dir, args.output
out.mkdir(parents=True, exist_ok=False)
chromium = shutil.which('chromium') or shutil.which('chromium-browser')
if chromium is None:
    parser.error('Chromium is required')
with TemporaryDirectory() as folder, ThreadingHTTPServer(('127.0.0.1', 0), workbench.Handler) as server:
    library = workbench.ScanLibrary(Path(folder))
    server.library = library
    server.enable_model = False
    documents = {}
    for filename in ('N19-1423.pdf', '1910.10683v4.pdf', '1706.03762v1.pdf', '2101.03961v3.pdf'):
        documents[filename] = library.add((root / filename).read_bytes(), filename)['doc']['id']
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    proof = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
            page = browser.new_page()
            protected = page.request.post(base+'/api/reproducibility/audit',
                data={'id':documents['N19-1423.pdf'],'reference':'0'*24,'consent':False})
            assert protected.status==403,protected.text()
            observed=[]
            page.on('request', lambda req: observed.append(req.url) if req.url.endswith('/api/reproducibility/audit') else None)
            live_cases = [('N19-1423.pdf', 'google-research/bert'),
                          ('1910.10683v4.pdf', 'google-research/text-to-text-transfer-transformer')] if args.live else []
            for filename, wanted in live_cases:
                page.goto(base+'/reproducibility?id='+documents[filename])
                page.locator('#selection').wait_for(state='visible')
                opts = page.locator('#reference option')
                option=next(o for o in opts.all() if o.text_content()==wanted)
                page.select_option('#reference', option.get_attribute('value'))
                assert page.locator('#audit-button').is_disabled()
                locator=page.locator('#source').get_attribute('href')
                source=page.request.get(base+'/api/source?'+urlsplit(locator).query)
                assert source.ok, source.text()
                original=source.json()
                params=parse_qs(urlsplit(locator).query)
                selected=original['text'][int(params['start'][0]):int(params['end'][0])]
                assert 'github.com' in selected.lower() and original['doc']['id']==documents[filename],original
                assert original['selection']['boxes'],original
                before=len(observed)
                page.check('#consent')
                with page.expect_response(lambda res: res.url.endswith('/api/reproducibility/audit'),timeout=35_000) as request:
                    page.click('#audit-button')
                response=request.value
                assert response.ok,response.text()
                result=response.json()
                assert len(observed)==before+1 and not page.locator('#consent').is_checked()
                page.locator('#result').wait_for(state='visible')
                print(filename,selected,result['audit']['status'],result['audit'].get('reason'),result['audit'].get('commit'),flush=True)
                proof[filename]={'live':True,'source':original,'result':result}
                if result['audit']['status']=='checked':
                    assert result['audit']['groups']['readme']['files']
                    assert result['audit']['groups']['license']['files']
                    assert any(result['audit']['groups'][k]['files'] for k in ('training','evaluation','environment'))
                    assert all('/blob/'+result['audit']['commit']+'/' in f['url']
                               for group in result['audit']['groups'].values() for f in group['files'])
                    assert page.locator('#result').get_by_text('实验运行记录为空',exact=False).count()==1
                page.screenshot(path=str(out/(filename+'.png')),full_page=True)
                page.check('#consent')
                with page.expect_response(lambda res: res.url.endswith('/api/reproducibility/audit'),timeout=35_000) as repeat:
                    page.click('#audit-button')
                assert repeat.value.json()['cached'] is True

            page.goto(base+'/reproducibility?id='+documents['1706.03762v1.pdf'])
            page.get_by_text('当前可检索原文中未提取到完整',exact=False).wait_for()
            assert not page.locator('#selection').is_visible()
            proof['no_link']={'docid':documents['1706.03762v1.pdf'],'document':'1706.03762v1.pdf',
                              'notice':page.locator('#notice').text_content()}
            page.goto(base+'/reproducibility?id='+documents['2101.03961v3.pdf'])
            page.locator('#selection').wait_for(state='visible')
            with patch.object(github,'inspect',side_effect=github.CheckError('timeout')):
                page.check('#consent')
                with page.expect_response(lambda res: res.url.endswith('/api/reproducibility/audit')) as offline:
                    page.click('#audit-button')
                page.get_by_text('本次 GitHub 请求超时。',exact=True).wait_for()
                proof['controlled_timeout']={'document':'2101.03961v3.pdf','live':False,'result':offline.value.json()}
            # A separate library and injected REST responses exercise the full
            # checked branch even when public GitHub is unreachable.
            with TemporaryDirectory() as other:
                server.library = workbench.ScanLibrary(Path(other))
                name = 'N19-1423.pdf'
                docid = server.library.add((root / name).read_bytes(), name)['doc']['id']
                page.goto(base + '/reproducibility?id=' + docid)
                page.locator('#selection').wait_for(state='visible')
                option = next(item for item in page.locator('#reference option').all()
                              if item.text_content() == 'google-research/bert')
                page.select_option('#reference', option.get_attribute('value'))
                commit, tree, blob = 'a' * 40, 'b' * 40, 'c' * 40
                responses = [
                    {'private': False, 'visibility': 'public', 'full_name': 'google-research/bert',
                     'default_branch': 'master', 'license': {'spdx_id': 'Apache-2.0'}},
                    {'sha': commit, 'commit': {'tree': {'sha': tree}}},
                    {'sha': tree, 'truncated': False, 'tree': [
                        {'path': file, 'sha': blob, 'type': 'blob', 'mode': '100644'} for file in (
                            'LICENSE', 'README.md', 'requirements.txt', 'examples/pretrain.py', 'examples/evaluate.py'
                        )
                    ]},
                ]
                with patch.object(github, '_fetch', side_effect=responses) as rest:
                    page.check('#consent')
                    with page.expect_response(lambda res: res.url.endswith('/api/reproducibility/audit')) as checked:
                        page.click('#audit-button')
                    details = checked.value.json()
                    assert details['audit']['commit'] == commit
                    assert rest.call_count == 3
                    page.get_by_text('实验运行记录为空', exact=False).wait_for()
                    hrefs = page.locator('#result .files a').evaluate_all('(links) => links.map(link => link.href)')
                    assert len(hrefs) == 5 and all('/blob/' + commit + '/' in url for url in hrefs)
                proof['controlled_success'] = {'document': name, 'live': False, 'result': details}
                page.screenshot(path=str(out / 'controlled-success.png'), full_page=True)
            browser.close()
    finally:
        server.shutdown()
        thread.join()
(out/'verified.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
