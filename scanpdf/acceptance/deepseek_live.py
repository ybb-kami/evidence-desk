"""Verify the configured DeepSeek Flash workbench paths using a public fictional PDF."""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urlsplit

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'DeepSeek-V4.1-Flash-1017'
PLANNER_QUERY = 'Show the citation graph of the selected paper.'
PAGE_QUERY = 'What type of box held the silver eddy-current paddle during the harmonics check?'
MISSING_QUERY = 'What is the manufacturer’s serial number on the silver eddy-current paddle?'
ANSWER = 'hexagonal nesting box'
ANSWER_PAGE = 2
OPERATIONS = ('tool_plan', 'page_answer', 'missing_field')


def fixture():
    with pymupdf.open() as document:
        for lines in (
            ('Fictional harmonics check', 'The brass resonance cone traveled in a round cotton sleeve.',
             'The control observations belong to the morning rehearsal.'),
            ('Afternoon harmonics check',
             'The silver eddy-current paddle rested inside a hexagonal nesting box',
             'during the harmonics check.',
             'The amber calibration tile was stored in a rectangular felt wallet.'),
        ):
            page = document.new_page()
            page.insert_text((52, 70), lines[0], fontsize=16)
            for line_number, line in enumerate(lines[1:]):
                page.insert_text((52, 116 + 20 * line_number), line, fontsize=12)
        return document.tobytes(no_new_id=True)


@contextmanager
def workbench(repo, server_python, *, protocol_only=False):
    names = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL')
    env = {name: os.environ[name] for name in names if name in os.environ}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.setdefault('DEEPSEEK_MODEL', MODEL)
    if protocol_only:
        env.pop('DEEPSEEK_API_KEY', None)
    with tempfile.TemporaryDirectory(prefix='evidence-deepseek-live-') as store:
        command = [server_python, 'scanpdf/workbench.py', '--port', '0', '--data', store, '--enable-deepseek']
        child = subprocess.Popen(command, cwd=repo, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True)
        try:
            if not select.select([child.stdout], [], [], 30)[0]:
                raise RuntimeError('workbench_startup_timeout')
            banner = urlsplit(child.stdout.readline().strip())
            if banner.hostname != '127.0.0.1' or not banner.port:
                raise RuntimeError('workbench_loopback_start_failed')
            yield banner.port
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            child.stdout.close()


class HTTPFailure(RuntimeError):
    def __init__(self, status):
        super().__init__('workbench_http_failure')
        self.status = status


def call(port, path, body=None, mime='application/json', *, events=False):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=120)
    try:
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        connection.request('POST' if body is not None else 'GET', path, body=body,
                           headers={'Content-Type': mime} if body is not None else {})
        outgoing = connection.getresponse()
        raw = outgoing.read()
        if outgoing.status != 200:
            raise HTTPFailure(outgoing.status)
        if events:
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        return json.loads(raw)
    finally:
        connection.close()


def source_match(port, doc_id, hit, data):
    if hit.get('id') != doc_id or hit.get('page') != ANSWER_PAGE or not isinstance(hit.get('text'), str):
        return False
    text = hit['text']
    if ANSWER not in text or type(hit.get('block')) is not int:
        return False
    with pymupdf.open(stream=data, filetype='pdf') as document:
        blocks = document[ANSWER_PAGE - 1].get_text('blocks', flags=pymupdf.TEXTFLAGS_BLOCKS)
        if not any(block[6] == 0 and block[5] + 1 == hit['block'] and text in block[4] for block in blocks):
            return False
    part = hit.get('part', 1)
    scope = urlencode(dict(id=doc_id, page=ANSWER_PAGE, block=hit['block'], part=part))
    full = call(port, '/api/source?' + scope).get('text') or ''
    if text not in full:
        return False
    begin = full.index(text) + text.index(ANSWER)
    end = begin + len(ANSWER)
    located = call(port, f'/api/source?{scope}&start={begin}&end={end}')
    selection = located.get('selection') or {}
    return (located.get('doc', {}).get('id') == doc_id and located.get('page') == ANSWER_PAGE
            and located.get('block') == hit['block'] and located.get('part') == part
            and located.get('text') == full and selection.get('start') == begin and selection.get('end') == end
            and selection.get('precision') == 'glyph' and bool(selection.get('boxes')))


def graph_events(port, doc_id):
    return call(port, '/api/copilot', {'ids': [doc_id], 'question': PLANNER_QUERY, 'model_planner': True,
                                      'publisher_consent': False, 'model_consent': True}, events=True)


def same_model(observed, expected):
    return isinstance(observed, str) and observed.casefold() == expected.casefold()


def graph_case(port, doc_id, model):
    events = graph_events(port, doc_id)
    plan = next((entry for entry in events if entry.get('event') == 'plan'), {})
    result = events[-1] if events else {}
    planner = plan.get('planner')
    observed_model = same_model(plan.get('model'), model)
    proposed = 'citation_graph' in plan.get('proposed', [])
    receipt = any(event.get('event') == 'receipt' and event.get('tool') == 'citation_graph' and event.get('status') == 'ok'
                  for event in events)
    passed = (planner in {'model_tool_calls', 'deepseek_tool_calls'} and observed_model and proposed and receipt
              and result.get('event') == 'final' and result.get('publications') == [])
    return dict(passed=passed, model_planned=observed_model and proposed, citation_graph_tool_receipt=receipt,
                final_event=result.get('event') == 'final', publisher_requested=False)


def followup(port, doc_id, question):
    return call(port, '/api/followup', dict(id=doc_id, question=question, anchor=None, model_consent=True))


def provider_answer(response, model):
    generation = response.get('generation') or {}
    provenance = same_model(generation.get('model', response.get('model')), model)
    status = generation.get('status', response.get('status'))
    if status not in {'cited_draft', 'supported', 'insufficient_evidence', 'validation_failed', 'unavailable', 'not_configured'}:
        status = 'unrecognized'
    return provenance, status


def page_case(port, doc_id, model, data):
    response = followup(port, doc_id, PAGE_QUERY)
    provenance, status = provider_answer(response, model)
    answer = response.get('answer') or ''
    correct_value = isinstance(answer, str) and ANSWER in answer.casefold()
    claims = (response.get('generation') or {}).get('claims', [])
    citations = [{**claim, 'text': claim.get('span', claim.get('quote'))} for claim in claims]
    citations.extend(response.get('quotes', []))
    source = any(source_match(port, doc_id, citation, data) for citation in citations)
    return dict(passed=provenance and status in {'cited_draft', 'supported'} and correct_value and source,
                model_identity_verified=provenance, model_status=status, expected_value_present=correct_value,
                verified_physical_page=ANSWER_PAGE if source else None, original_citation_locator_verified=source)


def missing_case(port, doc_id, model):
    response = followup(port, doc_id, MISSING_QUERY)
    provenance, status = provider_answer(response, model)
    absent = not response.get('answer')
    gap = bool(response.get('gap')) or status == 'insufficient_evidence'
    return dict(passed=absent and gap, final_value_supplied=not absent, gap_reported=gap,
                model_identity_verified=provenance, model_status=status)


def timed(operation):
    begin = time.monotonic()
    try:
        state = operation()
    except HTTPFailure as exc:
        state = dict(passed=False, http_status=exc.status)
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError, AttributeError):
        # Do not persist response bodies, URLs with queries, exception text or runtime credentials.
        state = dict(passed=False, reason='http_or_response_validation_failed')
    return {**state, 'seconds': round(time.monotonic() - begin, 4)}


def write_or_verify_fixture(port, data, doc_id):
    path = '/api/import?' + urlencode({'name': 'rehearsal-harmonics.pdf'})
    imported = call(port, path, data, 'application/pdf')['doc']
    return imported['id'] == doc_id and imported['pages'] == 2


def protocol(args, result, data, doc_id):
    with workbench(args.repo, args.server_python, protocol_only=True) as port:
        status = call(port, '/api/model-status')
        settings = dict(provider_matches=status.get('provider') == 'deepseek',
                        expected_model_matches=same_model(status.get('model'), MODEL),
                        configured=status.get('configured'))
        result['model_status_endpoint'] = settings
        if not (settings['provider_matches'] and settings['expected_model_matches'] and settings['configured'] is False):
            return {**result, 'status': 'failed', 'reason': 'unexpected_no_credential_model_status'}
        if not write_or_verify_fixture(port, data, doc_id):
            return {**result, 'status': 'failed', 'reason': 'fixture_import_not_verified'}
        events = graph_events(port, doc_id)
        plan = next((entry for entry in events if entry.get('event') == 'plan'), {})
        found = followup(port, doc_id, PAGE_QUERY)
        missing = followup(port, doc_id, MISSING_QUERY)
        _, found_status = provider_answer(found, MODEL)
        _, missing_status = provider_answer(missing, MODEL)
        checks = dict(local_citation_graph_completed=events[-1].get('event') == 'final' and any(
                          event.get('event') == 'receipt' and event.get('tool') == 'citation_graph'
                          and event.get('status') == 'ok' for event in events),
                      provider_did_not_plan=bool(plan) and plan.get('model') is None,
                      followup_returned_local_hits=any(hit.get('id') == doc_id and hit.get('page') == ANSWER_PAGE
                                                      for hit in found.get('hits', [])),
                      no_provider_answer=all(state not in {'cited_draft', 'supported'}
                                             for state in (found_status, missing_status)),
                      model_not_configured_reported=found.get('model_status') == missing.get('model_status') == 'not_configured',
                      missing_field_did_not_guess=not missing.get('answer'))
        return {**result, 'protocol_checks': checks, 'workbench_protocol_requests': 4,
                'model_status_http_status': 200, 'followup_model_status': found.get('model_status'),
                'status': 'protocol_passed' if all(checks.values()) else 'failed'}


def run(args):
    data = fixture()
    doc_id = sha256(data).hexdigest()
    result = dict(schema_version=1, status='blocked', expected_model=MODEL, model_calls_requested=0,
                  scope='generated public fictional fixture only', pdf_sha256=doc_id,
                  search_enabled=False, fetch_enabled=False, planned=list(OPERATIONS), cases={})
    if not args.protocol_only and not os.environ.get('DEEPSEEK_API_KEY', '').strip():
        return {**result, 'reason': 'deepseek_credential_not_configured'}
    if not args.protocol_only and not Path(os.environ.get('EVIDENCE_BUDGET_DB', '')).is_absolute():
        return {**result, 'reason': 'configure_same_persistent_budget_as_judging_service'}
    try:
        if args.protocol_only:
            return protocol(args, result, data, doc_id)
        with workbench(args.repo, args.server_python) as port:
            status = call(port, '/api/model-status')
            verified = (status.get('provider') == 'deepseek' and same_model(status.get('model'), MODEL)
                        and status.get('configured') is True)
            result['model_status_endpoint'] = dict(provider_matches=status.get('provider') == 'deepseek',
                                                  expected_model_matches=same_model(status.get('model'), MODEL),
                                                  configured=status.get('configured') is True)
            if not verified:
                return {**result, 'reason': 'deepseek_flash_configuration_not_confirmed'}
            if not write_or_verify_fixture(port, data, doc_id):
                return {**result, 'status': 'failed', 'reason': 'fixture_import_not_verified'}
            operations = {
                'tool_plan': lambda: graph_case(port, doc_id, MODEL),
                'page_answer': lambda: page_case(port, doc_id, MODEL, data),
                'missing_field': lambda: missing_case(port, doc_id, MODEL),
            }
            for name, operation in operations.items():
                result['model_calls_requested'] += 1
                result['cases'][name] = timed(operation)
            result['status'] = 'passed' if all(case['passed'] for case in result['cases'].values()) else 'failed'
            return result
    except HTTPFailure as exc:
        unavailable = args.protocol_only and exc.status == 404 and 'model_status_endpoint' not in result
        return {**result, 'status': 'blocked' if unavailable else 'failed',
                'reason': 'workbench_model_status_route_missing' if unavailable else 'workbench_handshake_failed',
                'http_status': exc.status}
    except (RuntimeError, OSError, http.client.HTTPException, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return {**result, 'status': 'failed', 'reason': 'workbench_or_model_status_validation_failed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--server-python', default=sys.executable)
    parser.add_argument('--protocol-only', action='store_true', help='Exercise local HTTP routes with credentials removed')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = run(args)
    record = json.dumps(result, indent=2) + '\n'
    if args.out:
        with args.out.open('x') as output:
            output.write(record)
    print(record, end='')
    return {'passed': 0, 'protocol_passed': 0, 'failed': 1, 'blocked': 2}[result['status']]


if __name__ == '__main__':
    sys.exit(main())
