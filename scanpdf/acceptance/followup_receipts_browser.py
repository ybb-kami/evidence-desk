"""Verify visible follow-up receipts in Chromium with public fixtures and no live provider requests."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
import pymupdf

import deepseek_live as live
from deepseek_stub_http import counts, worker


OPTICAL_QUESTION = 'What was the optical power during heating?'
DRAFT_BUTTON = '根据摘录生成有引文的回答'


def local_fixture():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((52, 70), 'Fictional optical power measurements', fontsize=16)
        page.insert_text((52, 116), 'The optical power during heating was 43 mW.')
        return doc.tobytes(no_new_id=True)


def enter_from_library(page, port, digest):
    page.goto(f'http://127.0.0.1:{port}/')
    page.locator(f'a[href="/followup?id={digest}"]').click()
    expect(page.get_by_role('heading', name='证据追问')).to_be_visible()
    expect(page.locator('#doc')).to_have_value(digest)


def ask(page, question, authorized, *, expect_http=200):
    page.locator('#flash-consent').set_checked(authorized)
    page.locator('#question').fill(question)
    with page.expect_response(lambda response: response.url.endswith('/api/followup')) as pending:
        page.locator('#question').press('Enter')
    response = pending.value
    assert response.status == expect_http, response.status
    expect(page.locator('#flash-consent')).not_to_be_checked()
    return response.json()


def models(page, state):
    newest = page.locator('#history > section').last
    return newest.locator(f'.model-receipt[data-state="{state}"]')


def check_model_status(page, text):
    label = page.locator('#flash-status')
    expect(label).to_contain_text(text)
    expect(label).not_to_contain_text('已接通')
    expect(page.locator('#flash-consent')).to_have_attribute('aria-describedby', 'flash-status')
    expect(page.locator('#flash-consent')).not_to_be_checked()


def manual_draft(page, doc_id, log, outbound):
    turn = page.locator('#history > section').last
    button = turn.get_by_role('button', name=DRAFT_BUTTON)
    dialog_messages = []

    def consent_dialog(dialog, approved):
        dialog_messages.append((dialog.type, dialog.message))
        (dialog.accept if approved else dialog.dismiss)()

    page.once('dialog', lambda dialog: consent_dialog(dialog, False))
    button.click()
    assert len(outbound) == 1 and not counts(log)
    expect(button).to_be_enabled()

    page.once('dialog', lambda dialog: consent_dialog(dialog, True))
    with page.expect_response(lambda response: response.url.endswith('/api/followup/model')) as pending:
        button.click()
    response = pending.value
    assert response.ok and response.json()['status'] == 'cited_draft'
    assert response.request.post_data_json == {
        'id': doc_id, 'question': OPTICAL_QUESTION, 'anchor': None, 'consent': True}
    assert len(dialog_messages) == 2 and all(
        kind == 'confirm' and '原文摘录' in text and 'DeepSeek' in text for kind, text in dialog_messages)
    draft = turn.locator('div[role="status"]')
    expect(draft).to_contain_text('已返回草稿')
    expect(draft.locator('blockquote')).to_contain_text('43 mW')
    expect(draft.locator('a[href^="/pdf/"]')).to_have_attribute('href', f'/pdf/{doc_id}#page=1')
    with page.expect_popup() as clicked:
        draft.get_by_role('link', name='定位原文').click()
    with clicked.value as source:
        expect(source.locator('#anchor')).to_contain_text('物理页 1')
    assert counts(log) == {'model_metadata': 1, 'model_paid': 1}


def render_examples(page):
    """Drive the same DOM with a malformed/incomplete response and a combined response."""
    pattern = '**/api/followup'
    failure = {'model_available': True, 'hits': [], 'generation': {
        'status': 'validation_failed', 'model': live.MODEL, 'model_reply': 'rejected'}}
    page.route(pattern, lambda route: route.fulfill(json=failure), times=1)
    ask(page, 'Fictional invalid quotation', True)
    expect(models(page, 'failed')).to_contain_text('未通过核对')

    unverified = {'model_available': True, 'hits': [], 'answer': 'Fictional answer',
                  'answer_method': 'deepseek_cited_draft',
                  'generation': {'status': 'cited_draft', 'model': live.MODEL}}
    page.route(pattern, lambda route: route.fulfill(json=unverified), times=1)
    ask(page, 'Legacy response without any usable receipt', True)
    expect(page.locator('#history > section').last.locator('.model-receipt[data-state="returned"]')).to_have_count(0)

    no_source = {'model_available': True, 'hits': [], 'generation': {
        'status': 'insufficient_evidence', 'model': live.MODEL, 'reason': 'no_source'}}
    page.route(pattern, lambda route: route.fulfill(json=no_source), times=1)
    ask(page, 'Fictional scope without searchable sources', True)
    expect(models(page, 'no-evidence')).to_contain_text('未调用模型')
    expect(page.locator('#history > section').last.locator('.model-receipt[data-state="declined"]')).to_have_count(0)

    combined = {'model_available': True, 'hits': [], 'fields': [
        {'question': 'Local field', 'method': 'direct_measurement', 'answer': '43 mW'},
        {'question': 'Missing field', 'gap': '缺少原句', 'generation': {
            'status': 'insufficient_evidence', 'model': live.MODEL, 'model_reply': 'declined'}},
    ], 'retrieval': {'methods': ['bm25-phrase', 'bm25-recall'], 'dense': 'not_configured'}}
    page.route(pattern, lambda route: route.fulfill(json=combined), times=1)
    ask(page, 'Combined fictional fields', True)
    expect(models(page, 'local')).to_have_count(1)
    expect(models(page, 'declined')).to_have_count(1)
    expect(page.locator('#history > section').last).to_contain_text('短语匹配 · 扩展词项检索')

    ambiguous = {'model_available': True, 'training_scale': {'status': 'ambiguous'},
                 'gap': '原文提供互相矛盾的训练量。', 'hits': [
                     {'id': 'a' * 64, 'page': 1, 'block': 1, 'part': 1, 'text': 'We used 15 samples.'},
                     {'id': 'a' * 64, 'page': 1, 'block': 2, 'part': 1, 'text': 'We used 20 samples.'}]}
    page.route(pattern, lambda route: route.fulfill(json=ambiguous), times=1)
    ask(page, 'How many training samples were used?', True)
    newest = page.locator('#history > section').last
    expect(newest).to_contain_text(ambiguous['gap'])
    expect(newest.get_by_role('button', name=DRAFT_BUTTON)).to_have_count(0)

    page.route(pattern, lambda route: route.fulfill(status=503, json={'error': '本轮预算不可用。'}), times=1)
    ask(page, 'Fictional unavailable request', True, expect_http=503)
    expect(page.locator('#status')).to_contain_text('未确认模型返回')
    expect(page.locator('#status')).to_contain_text('本轮预算不可用')


def run(chromium):
    data = live.fixture()
    digest = sha256(data).hexdigest()
    outbound, popups = [], []
    with sync_playwright() as pw, pw.chromium.launch(executable_path=str(chromium),
                                                      args=['--disable-background-networking']) as browser:
        with live.workbench(live.ROOT, sys.executable, protocol_only=True) as port:
            assert live.write_or_verify_fixture(port, data, digest)
            page = browser.new_page()
            enter_from_library(page, port, digest)
            check_model_status(page, '服务端未配置或未启用')
            expect(page.locator('#flash-status')).to_contain_text('型号尚未核验')
            response = ask(page, live.PAGE_QUERY, True)
            assert response['model_status'] == 'not_configured'
            expect(models(page, 'not-configured')).to_contain_text('服务端未配置或未启用')
            page.route('**/api/model-status', lambda route: route.abort(), times=1)
            enter_from_library(page, port, digest)
            check_model_status(page, '暂时无法读取 DeepSeek Flash 状态，仍可查找本地原文')
            offline = ask(page, live.PAGE_QUERY, False)
            assert any(hit['id'] == digest and hit['page'] == live.ANSWER_PAGE for hit in offline['hits'])
            expect(models(page, 'not-authorized')).to_contain_text('本次未授权')
            page.close()
        with TemporaryDirectory(prefix='evidence-followup-receipts-') as temp, worker(Path(temp)) as (port, _, log):
            assert live.write_or_verify_fixture(port, data, digest)
            local = local_fixture()
            local_id = sha256(local).hexdigest()
            uploaded = live.call(port, '/api/import?name=optical-power.pdf', local, 'application/pdf')
            assert uploaded['doc']['id'] == local_id
            page = browser.new_page()
            page.on('request', lambda event: outbound.append(event) if '/api/followup' in event.url else None)
            page.on('popup', lambda opened: popups.append(opened))
            enter_from_library(page, port, local_id)
            check_model_status(page, '服务端已配置')
            expect(page.locator('#flash-status')).to_contain_text('型号尚未核验')
            expect(page.locator('#flash-status')).to_contain_text(live.MODEL)
            assert not counts(log), 'Model status must not contact the provider'
            answer = ask(page, OPTICAL_QUESTION, True)
            assert answer['answer_method'] == 'direct_measurement'
            expect(models(page, 'local')).to_contain_text('未调用模型')
            assert not counts(log)
            manual_draft(page, local_id, log, outbound)
            enter_from_library(page, port, digest)
            ask(page, live.PAGE_QUERY, False)
            expect(models(page, 'not-authorized')).to_contain_text('本次未授权')
            assert counts(log) == {'model_metadata': 1, 'model_paid': 1}
            received = ask(page, live.PAGE_QUERY, True)
            assert received['generation']['model_reply'] == 'accepted'
            expect(models(page, 'returned')).to_contain_text('服务端引文核对通过')
            newest = page.locator('#history > section').last
            expect(newest.get_by_role('button', name=DRAFT_BUTTON)).to_have_count(0)
            with page.expect_popup() as clicked:
                newest.locator('a[href^="/source?"][href*="start="]').first.click()
            with clicked.value as source:
                expect(source.locator('#anchor')).to_contain_text('物理页 2')
            refused = ask(page, live.MISSING_QUERY, True)
            assert refused['generation']['model_reply'] == 'declined'
            expect(models(page, 'declined')).to_contain_text('已明确返回证据不足')
            render_examples(page)
            primary = [event for event in outbound if event.url.endswith('/api/followup')]
            manual = [event for event in outbound if event.url.endswith('/api/followup/model')]
            assert len(popups) == 2 and len(primary) == 10 and len(manual) == 1
            assert [event.post_data_json['model_consent'] for event in primary] == [True, False, *([True] * 8)]
            assert counts(log) == {'model_metadata': 3, 'model_paid': 3}
    return {'passed': True, 'scope': 'local browser with transport stub and response fixtures', 'live_provider_requests': 0,
            'status_preflight': ['unconfigured', 'configured_unverified', 'failed_with_local_query_available'],
            'ambiguous_training_scale_has_no_draft_button': True,
            'configured_followup_submissions': len(primary), 'manual_draft_submissions': len(manual),
            'manual_draft_cancellation_sent_no_request': True, 'stub_model_calls': 3,
            'citation_popups_after_two_explicit_clicks': len(popups)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', type=Path, default=Path('/usr/bin/chromium'))
    args = parser.parse_args()
    print(json.dumps(run(args.chromium), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
