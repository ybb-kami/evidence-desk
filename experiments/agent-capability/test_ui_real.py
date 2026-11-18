"""Local UI acceptance, real public PDFs, optional injected fake provider only."""
import argparse,hashlib,json,socket,tempfile,threading,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'gateway'))
from playwright.sync_api import sync_playwright,expect
from synthesis_bridge import SynthesisBridge
import launcher,gateway
def run(app,docs,offline):
    logs=[]
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    class Gate:
        def reserve(self,**kw):logs.append('reserve')
    def fake(wire):
        refs=[d[0]['ref'] for d in json.loads(json.loads(wire)['messages'][1]['content'])['documents']]
        logs.append('mock'); content={'claims':[{'text':'Mock review fixture, not a scientific finding.','refs':refs}]}
        return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}]}).encode()
    b=SynthesisBridge() if offline else SynthesisBridge(Gate(),fake)
    with tempfile.TemporaryDirectory() as td,launcher.demo_slots(app,Path(td)/'seats',2,origin) as slots,sync_playwright() as pw:
        threading.Thread(target=gateway.serve,args=(slots,port,origin,True),kwargs={'anonymous':True,'synthesis_bridge':b},daemon=True).start()
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',args=['--disable-background-networking'])
        context=browser.new_context(); context.route('**/*',lambda r:r.continue_() if r.request.url.startswith(origin+'/') else r.abort())
        page=context.new_page();page.goto(origin)
        for f in docs:
            result=context.request.post(origin+'/api/import?name='+f.name,data=f.read_bytes(),headers={'Content-Type':'application/pdf','Origin':origin})
            assert result.ok,result.status
        panel=page.locator('#grounded-synthesis')
        panel.get_by_text('刷新文献',exact=True).click()
        boxes=panel.locator('fieldset input');expect(boxes).to_have_count(2)
        for box in boxes.all():box.check()
        question=panel.get_by_placeholder('例如：两篇文献分别如何描述编码器？');question.fill('encoder')
        preview=panel.get_by_role('button',name='核对所选文献并预览');preview.click()
        expect(panel.locator('pre')).to_contain_text('deepseek-flash')
        send=panel.get_by_role('button',name='发送并生成待审草稿')
        expect(send).to_be_disabled();assert not logs
        assert panel.locator('blockquote').count()>=2
        if offline:
            expect(panel.locator('label input').last).to_be_hidden()
        else:
            agree=panel.get_by_label('仅同意将下列本次问题和摘录发给 DeepSeek，可能计费。')
            agree.check();expect(send).to_be_enabled()
            question.fill('new question');expect(send).to_be_disabled();expect(panel.locator('pre')).to_be_empty()
            question.fill('encoder');preview.click();agree.check();send.click()
            expect(panel.locator('article')).to_contain_text('Mock review fixture')
            links=panel.locator('article a')
            expect(links).to_have_count(2)
            assert all('#page=' in a.get_attribute('href') for a in links.all())
            assert logs==['reserve','mock'];expect(send).to_be_disabled()
        browser.close();print('Chromium UI PASS:', 'offline' if offline else 'injected mock')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--app',type=Path,required=True);p.add_argument('--docs',nargs=2,type=Path,required=True);args=p.parse_args()
    for offline in (True,False):run(args.app,args.docs,offline)
