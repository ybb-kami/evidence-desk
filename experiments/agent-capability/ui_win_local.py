"""Windows/portable UI acceptance against an ALREADY STARTED disposable offline
anonymous gateway on 127.0.0.1:8819 ONLY; no public address, no model request.
Run: python ui_win_local.py --docs transformer.pdf bert.pdf [--channel msedge]
"""
import argparse,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from urllib.parse import quote
ORIGIN='http://127.0.0.1:8819'
def check(paths,channel,exe=None):
    with sync_playwright() as pw:
        launch={'headless':True}
        if exe:launch['executable_path']=exe
        else:launch['channel']=channel
        browser=pw.chromium.launch(**launch);ctx=browser.new_context(service_workers='block')
        rejected=[]; confirms=[]
        def guard(route):
            u=route.request.url
            if u.startswith(ORIGIN+'/api/synthesis/confirm'):
                confirms.append(u);return route.abort()
            if not u.startswith(ORIGIN+'/'):
                rejected.append(u.split(':')[0]);return route.abort()
            route.continue_()
        ctx.route('**/*',guard)
        page=ctx.new_page();page.goto(ORIGIN,wait_until='domcontentloaded')
        assert page.url.startswith(ORIGIN+'/'), 'not local'
        panel=page.locator('#grounded-synthesis');expect(panel).to_be_visible(timeout=10000)
        for path in paths:
            raw=path.read_bytes()
            assert raw.startswith(b'%PDF') and len(raw)<24*1024**2
            rr=ctx.request.post(ORIGIN+'/api/import?name='+quote(path.name),data=raw,headers={'Content-Type':'application/pdf','Origin':ORIGIN},max_redirects=0)
            assert rr.status==200,('import',rr.status)
        panel.get_by_text('刷新文献',exact=True).click()
        for path in paths:
            panel.locator('fieldset input[value="'+hashlib.sha256(path.read_bytes()).hexdigest()+'"]').check()
        q=panel.locator('input[placeholder]');q.fill('encoder')
        panel.get_by_role('button',name='核对所选文献并预览').click()
        expect(panel.locator('pre')).to_contain_text('deepseek-flash',timeout=45000)
        expect(panel).to_contain_text('付费生成未启用')
        expect(panel.get_by_role('button',name='发送并生成待审草稿')).to_be_disabled()
        expect(panel.get_by_label('仅同意将下列本次问题和摘录发给 DeepSeek，可能计费。')).to_be_hidden()
        links=panel.locator('a');assert links.count()>=2
        anchors=[x.get_attribute('href') for x in links.all()]
        assert all(a.startswith('/pdf/') and '#page=' in a for a in anchors)
        assert all(any(hashlib.sha256(p.read_bytes()).hexdigest() in a for a in anchors) for p in paths)
        q.fill('changed');expect(panel.locator('pre')).to_be_empty();assert not confirms
        browser.close()
        print('PASS Chromium DOM real PDFs; offline only; no confirm; anchors:',anchors, 'blocked nonlocal requests:',len(rejected))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--docs',type=Path,nargs=2,required=True);p.add_argument('--channel',default='msedge');p.add_argument('--exe');a=p.parse_args();check(a.docs,a.channel,a.exe)
