"""Loopback fake upstream only; production broker code, simulated provider responses."""
import json
from functools import partial
from hashlib import sha256
import shutil
from unittest.mock import patch
import browser_single_paper as flow
from playwright.sync_api import expect
import operator_runtime, broker_sender

usage={'prompt_tokens':101,'completion_tokens':17,'total_tokens':118}
fake_id='test-only-no-upstream-invoice-171'
calls=[]

def catalog(_):
    calls.append('synthetic_catalog')
    return {'data':[{'id':'deepseek-flash','name':'DeepSeek-V4.1-Flash','client_metadata':{'deepseek-harness':{'badge':{'text':'1017'}}}}]}

def sim_post(endpoint,wire):
    calls.append('synthetic_post')
    assert endpoint=='https://api.deepseek.com/chat/completions'
    body=json.loads(wire)
    quote=json.loads(body['messages'][1]['content'])['documents'][0][0]
    return {'id':fake_id,'model':'deepseek-flash','usage':usage,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'claims':[{'text':quote['excerpt'],'refs':[quote['ref']]}]})}}]}

sender=broker_sender.approved_sender('irrelevant-fixture-credential-only', catalog=catalog,post_factory=lambda _:sim_post)

def collector(browser,origin,pdf,ledger_path,*,opening_fixture=False):
    digest=sha256(pdf).hexdigest()
    with browser.new_context(service_workers='block') as visitor:
        visitor.route('**/*',lambda route: route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
        page=visitor.new_page()
        panel=flow.upload_and_select(page,origin,'synthetic-calibration.pdf',pdf)
        panel.get_by_role('textbox').fill(flow.QUESTION)
        pre=flow.json_response(page,'/api/synthesis/preview',panel.get_by_role('button',name='预览单篇问答'))
        assert pre['provider_mode']=='approved_unix' and pre['status']=='ready' and 'generation_receipt' not in pre and calls==[]
        panel.locator('label input[type=checkbox]').last.check()
        result=flow.json_response(page,'/api/synthesis/confirm',panel.get_by_role('button',name='发送并生成待审回答'))
        assert calls==['synthetic_catalog','synthetic_post']
        r=result['generation_receipt']
        assert r['status']=='available' and r['model']=='deepseek-flash' and r['usage']==usage,r
        assert r['provider_request_id_sha256']==sha256(fake_id.encode()).hexdigest() and fake_id not in json.dumps(result)
        ui=panel.locator('#synthesis-receipt')
        expect(ui).to_be_visible(timeout=3000)
        text=ui.inner_text()
        assert all(str(value) in text for value in usage.values()) and fake_id not in text,text
        claim=result['claims'][0]
        src=claim['evidence'][0]
        assert (src['page'],src['block'],src['doc_id'],src['text'])==(2,1,digest,flow.SENTENCE)
        link=panel.locator('article').get_by_role('link', name='第 2 页 · 块 1',exact=True)
        expect(link).to_have_attribute('href',f'/pdf/{digest}#page=2')
        flow.click_original(visitor,link,origin,digest,pdf)
        return {'browser':browser.version,'source':'injected catalog and completion only; no upstream network',
                'actual_paid_calls':0,'synthetic_confirm':result['status'],'provider_mode_in_exercised_path':result['provider_mode'],
                'generation_receipt':r,'browser_receipt_text':text,'physical_page':2,'block':1,'original_pdf_matched':True,
                'raw_provider_id_exposed':False}
if __name__ == '__main__':
    with (patch.object(flow, 'collect', collector),
          patch.object(flow, 'UnixProviderBroker', partial(flow.UnixProviderBroker, provider_mode='approved_unix')),
          patch.object(flow, 'fake_response', sender),
          patch.object(operator_runtime, 'private_runtime', partial(operator_runtime.private_runtime, provider_mode='approved_unix'))):
        print(json.dumps(flow.run(shutil.which('chromium') or shutil.which('chromium-browser'), opening_fixture=True),
                         ensure_ascii=False, indent=2))
