"""Drive the judging UI on an existing loopback workbench, without client credentials."""

import argparse
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit

try:
    import deepseek_live as live
except ImportError:
    live = None

NAME = 'judging-fictional-harmonics.pdf'
SEARCH_TERMS = 'Attention Is All You Need arXiv 1706.03762'
SEARCH_QUESTION = 'Find the original paper.'
MODEL = 'DeepSeek-V4.1-Flash-1017'


class CheckFailed(Exception):
    pass


def check(condition, reason):
    if not condition:
        raise CheckFailed(reason)


def origin_url(value):
    try:
        url = urlsplit(value)
        if url.port and value.rstrip('/') == f'http://127.0.0.1:{url.port}' and url.path in ('', '/'):
            return f'http://127.0.0.1:{url.port}'
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Provide an existing loopback URL such as http://127.0.0.1:8765')


def system_chromium():
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'chrome'):
        if executable := shutil.which(name):
            return executable
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LocalAppData'):
        if root := os.environ.get(variable):
            for relative in ('Google/Chrome/Application/chrome.exe', 'Chromium/Application/chrome.exe'):
                candidate = Path(root) / relative
                if candidate.is_file():
                    return str(candidate)
    for name in ('Google Chrome', 'Chromium'):
        candidate = Path(f'/Applications/{name}.app/Contents/MacOS/{name}')
        if candidate.is_file():
            return str(candidate)
    return None


def launch_browser(pw, explicit, playwright_error):
    environment = {'path', 'lang', 'lc_all', 'tz', 'systemroot', 'windir', 'temp', 'tmp', 'tmpdir',
                   'localappdata', 'appdata', 'userprofile', 'programfiles', 'programfiles(x86)'}
    options = {'env': {key: value for key, value in os.environ.items() if key.casefold() in environment},
               'args': ['--disable-background-networking']}
    if explicit is not None:
        return pw.chromium.launch(executable_path=str(explicit.expanduser()), **options)
    if system := system_chromium():
        try:
            return pw.chromium.launch(executable_path=system, **options)
        except playwright_error:
            pass
    return pw.chromium.launch(**options)


def bounded_browser_route(origin, data, digest, search, submitted):
    """Keep browser traffic on this origin and authorize only the declared transactions."""
    def handle(route):
        request = route.request
        target = urlsplit(request.url)
        if f'{target.scheme}://{target.netloc}' != origin:
            route.abort()
            return
        if request.method == 'GET':
            route.continue_()
            return
        name = None
        if request.method == 'POST' and target.path == '/api/import' and request.post_data_buffer == data:
            name = 'upload'
        if request.method == 'POST' and target.path in ('/api/followup', '/api/copilot'):
            try:
                body = request.post_data_json
            except (ValueError, TypeError):
                body = None
            if target.path == '/api/followup':
                for label, question in (('page_answer', live.PAGE_QUERY), ('missing_field', live.MISSING_QUERY)):
                    if body == dict(id=digest, question=question, anchor=None, model_consent=True):
                        name = label
            elif search and body == dict(ids=[], question=SEARCH_QUESTION, model_planner=False, model_consent=False,
                                         publisher_consent=False, search_terms=SEARCH_TERMS, fetch_first=False):
                name = 'search'
        if name is None or name in submitted:
            route.abort()
            return
        submitted.append(name)
        route.continue_()
    return handle


def upload_and_open(page, expect, data, digest):
    with page.expect_file_chooser() as chooser:
        page.locator('#choose').click()
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/import') as uploaded:
        chooser.value.set_files({'name': NAME, 'mimeType': 'application/pdf', 'buffer': data})
    response = uploaded.value
    check(response.ok, 'fixture_upload_http_failed')
    document = response.json()['doc']
    check(document['id'] == digest and document['pages'] == 2, 'fixture_upload_integrity_failed')
    page.locator(f'a[href="/followup?id={digest}"]').filter(has_text='问这篇论文').click()
    expect(page.locator('#doc')).to_have_value(digest)
    return {'fixture_sha256_matches': True, 'physical_pages': document['pages']}


def ask(page, question):
    page.locator('#question').fill(question)
    page.locator('#flash-consent').check()
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/followup', timeout=120_000) as pending:
        page.locator('#send').click()
    check(pending.value.ok, f'followup_http_{pending.value.status}')
    return pending.value.json()


def page_answer(page, expect, digest):
    result = ask(page, live.PAGE_QUERY)
    generation = result.get('generation') or {}
    check(generation.get('model') == live.MODEL and generation.get('status') == 'cited_draft'
          and generation.get('model_reply') == 'accepted' and result.get('answer_method') == 'deepseek_cited_draft',
          'verified_model_answer_not_reported')
    check(live.ANSWER in (result.get('answer') or '').casefold(), 'answer_did_not_match_fixture')
    claim = next((claim for claim in generation.get('claims', []) if claim.get('id') == digest
                  and claim.get('page') == live.ANSWER_PAGE and live.ANSWER in (claim.get('span') or '')), None)
    check(claim is not None, 'accepted_claim_not_bound_to_fixture_page_two')
    begin, end = claim.get('answer_start'), claim.get('answer_end')
    check(type(begin) is int and type(end) is int and 0 <= begin < end, 'accepted_claim_has_no_valid_offsets')
    turn = page.locator('#history > section').last
    expect(turn.locator('.model-receipt[data-state="returned"]')).to_be_visible()
    expect(turn.locator('strong')).to_contain_text(live.ANSWER, ignore_case=True)
    citation = turn.locator(f'a[href^="/source?"][href*="id={digest}"][href*="page={live.ANSWER_PAGE}"]'
                            f'[href*="start={begin}"][href*="end={end}"]').first
    with page.expect_popup() as opened:
        citation.click()
    with opened.value as original:
        expect(original.locator('#anchor')).to_contain_text(f'物理页 {live.ANSWER_PAGE}')
        expect(original.locator('#excerpt mark')).to_contain_text(live.ANSWER, ignore_case=True)
        expect(original.locator('#overlay .region').first).to_be_visible()
        expect(original.locator('#pdf')).to_have_attribute('href', f'/pdf/{digest}#page={live.ANSWER_PAGE}')
    return {'provider_reply': 'accepted', 'physical_page': live.ANSWER_PAGE,
            'ui_citation_markup_verified': True, 'ui_original_pdf_link_verified': True}


def missing_field(page, expect):
    result = ask(page, live.MISSING_QUERY)
    generation = result.get('generation') or {}
    check(generation.get('model') == live.MODEL and generation.get('status') == 'insufficient_evidence'
          and generation.get('model_reply') == 'declined', 'explicit_model_decline_not_reported')
    check(not result.get('answer') and result.get('gap'), 'missing_field_was_not_left_as_a_gap')
    turn = page.locator('#history > section').last
    expect(turn.locator('.model-receipt[data-state="declined"]')).to_be_visible()
    expect(turn).to_contain_text(result['gap'])
    return {'provider_reply': 'declined', 'gap_visible': True, 'answer_supplied': False}


def search_for_paper(page, expect):
    page.get_by_role('link', name='返回工作台', exact=True).click()
    page.get_by_role('link', name='科研 Copilot', exact=True).click()
    documents = page.locator('#documents input')
    expect(documents.first).to_be_visible()
    for document in documents.all():
        document.uncheck()
    page.locator('#planning').uncheck()
    page.locator('#fetch-first').uncheck()
    page.locator('#web-terms').fill(SEARCH_TERMS)
    page.locator('#question').fill(SEARCH_QUESTION)
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/copilot', timeout=120_000) as pending:
        page.locator('#run').click()
    response = pending.value
    check(response.ok, f'search_http_{response.status}')
    # The page consumes the NDJSON stream. Wait on its completed UI rather than
    # asking Chromium/CDP to supply a second copy of the streamed response body.
    try:
        expect(page.locator('#run')).to_be_enabled(timeout=120_000)
    except AssertionError as exc:
        raise CheckFailed('search_ui_did_not_settle') from exc
    check(page.locator('#state').inner_text().strip() == '本次任务已结束。', 'search_ui_did_not_finish')
    result = page.locator('#result')
    check(result.is_visible() and result.get_by_role('heading', name='公开网页检索结果', exact=True).count() == 1,
          'search_result_panel_not_visible')
    steps = page.locator('#steps li').all_text_contents()
    check(any(text.startswith('DeepSeek 联网检索 · 已完成 · ') for text in steps), 'search_completion_receipt_missing')
    check(not result.get_by_text('查看抓取的网页摘录', exact=True).count(), 'unexpected_fetched_excerpt_rendered')
    paper = re.compile(r'^https://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/1706\.03762(?:v\d+)?(?:\.pdf)?/?$')
    check(any(paper.fullmatch(link.get_attribute('href') or '') and link.is_visible()
              for link in result.get_by_role('link').all()), 'search_did_not_show_original_arxiv_paper')
    return {'original_arxiv_paper_visible': True, 'fetch_requested': False, 'result_links_opened': 0}


def run(origin, chromium, with_search=False):
    result = {'status': 'blocked', 'scope': 'deployment-selected running loopback workbench',
              'model': MODEL, 'cases': {}, 'workbench_model_or_search_submissions': 0}
    if live is None:
        return result | {'reason': 'fixture_dependency_unavailable'}
    try:
        status = live.call(urlsplit(origin).port, '/api/model-status')
    except live.HTTPFailure as error:
        return result | {'reason': 'model_status_unavailable', 'http_status': error.status}
    except (OSError, ValueError, http.client.HTTPException):
        return result | {'reason': 'model_status_unavailable'}
    if not isinstance(status, dict) or status.get('provider') != 'deepseek' or status.get('model') != MODEL:
        return result | {'reason': 'intended_provider_model_not_confirmed'}
    if status.get('configured') is not True:
        return result | {'reason': 'server_model_not_configured'}
    if status.get('model_price_configured') is not True or (with_search and status.get('search_price_configured') is not True):
        return result | {'reason': 'server_prices_not_configured'}

    try:
        from playwright.sync_api import Error, expect, sync_playwright
    except ImportError:
        return result | {'reason': 'playwright_not_installed'}

    submitted = []
    data = live.fixture()
    digest = sha256(data).hexdigest()
    result['fixture_sha256'] = digest
    result['status'] = 'failed'
    stage = 'browser_launch'
    try:
        with sync_playwright() as pw, launch_browser(pw, chromium, Error) as browser:
            stage = 'browser_navigation'
            context = browser.new_context(service_workers='block')
            context.route('**/*', bounded_browser_route(origin, data, digest, with_search, submitted))
            page = context.new_page()
            page.goto(origin + '/')
            stage = 'upload_and_open'
            result['cases'][stage] = upload_and_open(page, expect, data, digest)
            stage = 'page_answer'
            result['cases'][stage] = page_answer(page, expect, digest)
            stage = 'missing_field'
            result['cases'][stage] = missing_field(page, expect)
            if with_search:
                stage = 'search'
                result['cases'][stage] = search_for_paper(page, expect)
        result['status'] = 'passed'
    except CheckFailed as exc:
        result.update(failed_stage=stage, reason=str(exc))
    except (Error, AssertionError, ValueError, KeyError, TypeError):
        if stage == 'browser_launch':
            result.update(status='blocked', reason='browser_not_available')
        else:
            result.update(failed_stage=stage, reason='browser_or_response_assertion_failed')
    result['workbench_model_or_search_submissions'] = sum(name != 'upload' for name in submitted)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', type=origin_url, required=True)
    parser.add_argument('--with-search', action='store_true', help='Also submit one explicit public search without fetching a page')
    parser.add_argument('--chromium', type=Path, help='Explicit Chrome or Chromium executable path')
    args = parser.parse_args()
    result = run(args.url, args.chromium, args.with_search)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {'passed': 0, 'failed': 1, 'blocked': 2}[result['status']]


if __name__ == '__main__':
    sys.exit(main())
