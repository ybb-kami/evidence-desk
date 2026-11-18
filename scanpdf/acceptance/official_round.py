"""Repeat all 15 official instances by direct HTTP and served Chromium UI."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from time import perf_counter
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import sync_playwright

import official_http as official


ROOT = Path(__file__).resolve().parents[2]


def version_info(expected):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args], stderr=subprocess.DEVNULL, text=True).strip()

    commit = None
    dirty = None
    try:
        if Path(git('rev-parse', '--show-toplevel')).resolve() == ROOT:
            commit = git('rev-parse', 'HEAD')
            dirty = bool(git('status', '--porcelain', '--untracked-files=no'))
    except (OSError, subprocess.CalledProcessError):
        pass
    if expected and commit != expected:
        raise ValueError(f'Expected checkout {expected}, found {commit or "no Git checkout"}')
    files = ('app.py', 'engine.py', 'scanpdf/workbench.py', 'scanpdf/followup.py', 'scanpdf/architecture.py',
             'scanpdf/rag_search.py', 'scanpdf/source_locator.py', 'scanpdf/acceptance/official_http.py',
             'scanpdf/acceptance/official_round.py')
    return {'commit': commit, 'tracked_files_dirty': dirty,
            'file_sha256': {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in files}}


def query_set(official, gpt2):
    attention = [(number, [name], question) for name in official.ATTENTION.values()
                 for number, question in enumerate(official.QUESTIONS[:5], 1)]
    attention.extend((6, [name, official.BERT], official.QUESTIONS[5]) for name in official.ATTENTION.values())
    all_gpt = [official.GPT1] + ([official.GPT2] if gpt2 else []) + [official.GPT3]
    for number, question in enumerate(official.GPT_QUESTIONS, 7):
        names = [official.GPT1, official.GPT2] if number == 8 and gpt2 else all_gpt
        attention.append((number, names, question))
    assert len(attention) == 15
    return attention


def get_papers(response, values):
    return response.get('papers', [{'id': values.get('id'), 'hits': response.get('hits', [])}])


def same_anchor_members(left, right):
    key = lambda anchor: tuple(sorted(anchor.items()))
    return Counter(map(key, left)) == Counter(map(key, right))


def summarize_response(official, number, names, values, response, sources):
    papers = get_papers(response, values)
    hits = official.check_hits(papers, sources)
    criteria = official.requirements(number, names, hits, sources)
    required = [item for item in criteria if item['required']]
    found = sum(bool(item['matches']) for item in required)
    complete = found == len(required) and all(item['verbatim'] for item in hits)
    result = {
        'answer_method': response.get('answer_method'), 'answer': response.get('answer'), 'gap': response.get('gap'),
        'evidence': 'complete' if complete else 'partial' if found else 'missing',
        'answer_status': official.answer_status(number, response.get('answer'), complete, criteria),
        'criteria': criteria, 'hits': hits,
        'raw_anchors': [{k: h[k] for k in ('id','page','block','part','start','end') if k in h}
                        for paper in papers for h in paper['hits']],
    }
    result['missing_required'] = [item['criterion'] for item in required if not item['matches']]
    if official.GPT2 not in sources and number >= 7:
        result['missing_gpt2_disclosed'] = 'GPT-2' in str(result['gap']) or 'GPT-2' in str(result['answer'])
    return result


def go_source(page, link, href, source_map, official):
    values = {key: entries[0] for key, entries in parse_qs(urlsplit(href).query).items()}
    record = {'href': href, 'id': values.get('id'), 'page': int(values['page']), 'passed': False}
    for key in ('block','part','region','row','column','start','end'):
        if key in values:
            record[key] = values[key] if key == 'region' else int(values[key])
    with page.expect_popup(timeout=8000) as opened:
        link.click(timeout=8000)
    popup = opened.value
    try:
        popup.locator('#evidence:not([hidden])').wait_for(timeout=8000)
        popup.wait_for_function("() => document.querySelector('#image').naturalWidth > 0", timeout=8000)
        physical = int(values['page'])
        marker = popup.locator('#anchor').inner_text()
        record['displayed_anchor'] = marker
        record['pdf_href'] = popup.locator('#pdf').get_attribute('href')
        record['rendered_page_image'] = True
        record['highlight_boxes'] = popup.locator('#overlay .region').count()
        record['precision'] = popup.locator('#precision').inner_text()
        assert re.search(rf'物理页\s+{physical}(?!\d)', marker), marker
        assert values['id'] in popup.locator('#identity').text_content()
        assert record['pdf_href'] == f"/pdf/{values['id']}#page={physical}"
        excerpt = popup.locator('#excerpt').text_content()
        if 'block' in values:
            original = source_map[values['id']][1].get((physical, int(values['block'])), '')
            assert int(values['block']) and re.search(rf"块\s+{int(values['block'])}(?!\d)", marker)
            assert official.normalized(excerpt) in official.normalized(original) and excerpt.strip()
            if 'start' in values:
                marked = popup.locator('#excerpt mark').text_content()
                assert marked == excerpt[int(values['start']):int(values['end'])]
                record['marked_excerpt'] = marked
        record['passed'] = True
    except Exception as exc:
        record['error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
    finally:
        popup.close()
    return record


def browser_case(official, context, origin, endpoint, values, source_map):
    single = endpoint == '/api/followup'
    page = context.new_page()
    try:
        page.goto(origin + ('/followup' if single else '/architecture'))
        if single:
            page.select_option('#doc', values['id'])
            page.fill('#question', values['question'])
            submit = '#send'
        else:
            for digest in values['ids']:
                page.check(f'input[value="{digest}"]')
            page.fill('#query', values['query'])
            submit = '#form button'
        with page.expect_response(lambda response: response.url == origin + endpoint, timeout=60000) as pending:
            page.click(submit)
        status = pending.value.status
        result = pending.value.json()
        observed = pending.value.request.post_data_json
        if status != 200:
            return result, {'http_status': status, 'dom_correct': False, 'citations': []}
        errors = []
        if single:
            scope = page.locator('#history > section').last
            scope.wait_for(timeout=10000)
            if scope.locator('h2').inner_text() != values['question'] or observed != {**values, 'model_consent': False}:
                errors.append('question_or_selection_mismatch')
            if result.get('answer') and official.normalized(scope.locator('strong').first.text_content()) != official.normalized(result['answer']):
                errors.append('displayed_answer_mismatch')
            if not result.get('answer') and result.get('gap') and result['gap'] not in scope.inner_text():
                errors.append('displayed_gap_missing')
            groups = [(scope, result['hits'])]
        else:
            scope = page.locator('#output')
            summary = page.locator('#summary')
            summary.wait_for(timeout=10000)
            if (observed['query'] != values['query'] or set(observed['ids']) != set(values['ids'])
                    or official.normalized(summary.inner_text()) != official.normalized(result['answer'] or result['gap'])):
                errors.append('question_selection_or_summary_mismatch')
            groups = [(page.locator('.papers article').nth(i), paper['hits']) for i, paper in enumerate(result['papers'])]
        for container, hits in groups:
            try:
                official.assert_displayed_evidence(container, hits)
            except Exception as exc:
                errors.append('displayed_evidence_mismatch: ' + str(exc)[:150])

        citations = []
        links = scope.locator('a[href^="/source?"]')
        seen = set()
        for n in range(links.count()):
            link = links.nth(n)
            href = link.get_attribute('href')
            if href in seen:
                continue
            seen.add(href)
            try:
                citations.append(go_source(page, link, href, source_map, official))
            except Exception as exc:
                citations.append({'href': href, 'passed': False, 'error': f'{type(exc).__name__}: {str(exc)[:250]}'})
        expected = {(h['id'], h['page'], h['block'], h.get('part', 1)) for _, hits in groups for h in hits}
        opened = {(h.get('id'), h.get('page'), h.get('block'), h.get('part')) for h in citations if h['passed']}
        if expected - opened:
            errors.append('response_passage_missing_in_successful_source_links')
        return result, {'http_status': status, 'dom_correct': not errors, 'errors': errors, 'citations': citations}
    finally:
        page.close()


def run(args, official, browser, runtime):
    sources = official.source_records(args.pdfs, args.gpt2)
    source_map = {doc['id']: (canonical, blocks) for canonical, (doc, blocks) in sources.items()}
    case_rows = []
    started = perf_counter()
    with official.workbench() as port:
        origin = f'http://127.0.0.1:{port}'
        context = browser.new_context(viewport={'width': 1280, 'height': 900}, locale='zh-CN')
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        try:
            verified = {}
            for name, (doc, _) in sources.items():
                path = args.gpt2 if name == official.GPT2 else args.pdfs / name
                recorded = official.call(port, '/api/import?name=' + quote(doc['name']), path.read_bytes(), 'application/pdf')['doc']
                assert recorded['id'] == doc['id'] and recorded['pages'] == doc['pages']
                official.verify_pdf_route(port, doc['id'])
                verified[name] = {'file': doc['name'], 'id': doc['id'], 'pages': doc['pages'], 'http_pdf_sha256_verified': True}
            for number, names, question in query_set(official, args.gpt2):
                ids = [sources[name][0]['id'] for name in names]
                single = number <= 5
                endpoint = '/api/followup' if single else '/api/architecture'
                values = {'id': ids[0], 'question': question, 'anchor': None} if single else {'ids': ids, 'query': question}
                row = {'number': number, 'selected': names, 'question': question, 'endpoint': endpoint}
                try:
                    start = perf_counter()
                    direct = official.call(port, endpoint, values)
                    row['http'] = summarize_response(official, number, names, values, direct, sources)
                    row['http']['elapsed_ms'] = round((perf_counter() - start) * 1000)
                except Exception as exc:
                    row['http'] = {'error': f'{type(exc).__name__}: {str(exc)[:300]}'}
                    direct = {}
                try:
                    received, review = browser_case(official, context, origin, endpoint, values, source_map)
                    row['browser'] = (summarize_response(official, number, names, values, received, sources)
                                      if review['http_status'] == 200 else {'error': received.get('error')})
                    row['browser'].update(review)
                    row['browser']['same_answer_as_http'] = received.get('answer') == direct.get('answer')
                    row['browser']['same_gap_as_http'] = received.get('gap') == direct.get('gap')
                    row['browser']['same_anchors_as_http'] = row['browser'].get('raw_anchors') == row['http'].get('raw_anchors')
                    row['browser']['same_anchor_members_as_http'] = same_anchor_members(
                        row['browser'].get('raw_anchors', []), row['http'].get('raw_anchors', []))
                except Exception as exc:
                    row['browser'] = {'error': f'{type(exc).__name__}: {str(exc)[:400]}'}
                case_rows.append(row)
                print(number, names[0], row['http'].get('answer_status'), row['browser'].get('answer_status'),
                      'browser citations', sum(link['passed'] for link in row['browser'].get('citations', [])),
                      '/', len(row['browser'].get('citations', [])), file=sys.stderr, flush=True)
        finally:
            context.close()
    summaries = {}
    for mode in ['http','browser']:
        outcomes = [row[mode] for row in case_rows]
        summaries[mode] = {
            'answer': dict(Counter(item.get('answer_status','error') for item in outcomes)),
            'evidence': dict(Counter(item.get('evidence','error') for item in outcomes)),
            'dom_passed': sum(item.get('dom_correct',False) for item in outcomes) if mode == 'browser' else None,
            'citations_opened': sum(len(item.get('citations', [])) for item in outcomes) if mode == 'browser' else None,
            'citations_passed': sum(link['passed'] for item in outcomes for link in item.get('citations', [])) if mode == 'browser' else None,
        }
    return {
        'runtime': runtime, 'chromium': browser.version,
        'duration_seconds': round(perf_counter() - started, 3), 'gpt2_original_present': bool(args.gpt2), 'sources': verified,
        'summary': summaries, 'cases': case_rows,
    }


def regressions(report):
    expected = 15 if report['gpt2_original_present'] else 12
    problems = []
    for case in report['cases']:
        number = case['number']
        should_answer = report['gpt2_original_present'] or number <= 6
        for mode in ('http', 'browser'):
            result = case[mode]
            if should_answer and not (result.get('answer_status') == 'supported' and result.get('evidence') == 'complete'):
                problems.append((number, mode, case['selected'], 'expected_complete_answer'))
            if not should_answer and not (result.get('answer_status') == 'missing' and result.get('missing_gpt2_disclosed')):
                problems.append((number, mode, case['selected'], 'expected_explicit_missing_gpt2'))
        browser_result = case['browser']
        if not all(browser_result.get(key) for key in ('dom_correct', 'same_answer_as_http', 'same_gap_as_http',
                                                      'same_anchor_members_as_http')):
            problems.append((number, 'browser', case['selected'], 'browser_response_or_citations_mismatch'))
        if any(not link.get('passed') or not link.get('highlight_boxes') for link in browser_result.get('citations', ())):
            problems.append((number, 'browser', case['selected'], 'source_popup_or_highlight_failed'))
    if len(report['cases']) != 15 or report['summary']['http']['answer'].get('supported') != expected:
        problems.append(('round', 'http', [], 'unexpected_total'))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdfs', type=Path, help='Directory containing the original contest PDFs')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gpt2', type=Path, help='Add the pinned GPT-2 course PDF before running the same 15 cases')
    parser.add_argument('--chromium', type=Path, help='Chromium executable, otherwise try PATH or Playwright Chromium')
    parser.add_argument('--expect-sha', help='Require this checkout SHA')
    parser.add_argument('--check', action='store_true', help='Return nonzero on a regression after writing the full report')
    args = parser.parse_args()
    try:
        runtime = version_info(args.expect_sha)
    except ValueError as exc:
        parser.error(str(exc))
    os.environ.pop('DEEPSEEK_API_KEY', None)
    os.environ.pop('EVIDENCE_LOCAL_QA_URL', None)
    os.environ.pop('EVIDENCE_QA_READER', None)
    binary = str(args.chromium) if args.chromium else shutil.which('chromium') or shutil.which('chromium-browser')
    launch = {'executable_path': binary} if binary else {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch, args=['--no-sandbox', '--disable-background-networking'])
        try:
            output = run(args, official, browser, runtime)
        finally:
            browser.close()
    output['regressions'] = regressions(output)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    if args.check and output['regressions']:
        sys.exit(f'{len(output["regressions"])} official round regressions. See {args.output}.')


if __name__ == '__main__':
    main()
