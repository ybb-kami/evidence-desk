"""Run real workbench HTTP transactions with an isolated in-process provider stub."""

import argparse
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from deepseek_live import (ANSWER, MODEL, HTTPFailure, call, fixture, followup, graph_case, missing_case,
                           page_case, write_or_verify_fixture)

ROOT = Path(__file__).resolve().parents[2]
TERMS = 'Attention Is All You Need arXiv 1706.03762'
CANARY = 'SYNTHETIC NONPUBLIC CANARY this is deliberately absent from all provider requests'


def serve_stub(repo, data, events):
    sys.path[:0] = [str(repo / 'scanpdf'), str(repo)]
    import flash_transport
    from discovery import deepseek_search
    from workbench import main

    def no_network(event, _):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise RuntimeError('An outgoing socket is forbidden in this transport-stub worker')

    sys.addaudithook(no_network)

    def log(kind):
        with events.open('a') as stream:
            stream.write(kind + '\n')

    class Reply(io.BytesIO):
        status = 200

    @contextmanager
    def flash(method, path, key, *, body=None, **_):
        if method == 'GET' and path == '/models':
            log('model_metadata')
            result = {'data': [{'id': 'deepseek-flash', 'name': 'DeepSeek-V4.1-Flash',
                                'client_metadata': {'deepseek-harness': {'badge': {'text': {'en': '1017'}}}}}]}
        elif method == 'POST' and path == '/chat/completions':
            log('model_paid')
            payload = json.loads(body)
            assert payload['model'] == 'deepseek-flash' and CANARY not in json.dumps(payload) and not payload['stream']
            if payload.get('tools'):
                function = {'name': 'citation_graph', 'arguments': '{}'}
                item = {'finish_reason': 'tool_calls', 'message': {'tool_calls': [{'type': 'function', 'function': function}]}}
            else:
                user = payload['messages'][-1]['content']
                if user.startswith('{'):
                    context = json.loads(user)
                    quote = 'The optical power during heating was 43 mW.'
                    assert context['question'] == 'What was the optical power during heating?'
                    source = next(label for label, text in context['excerpts'].items() if quote in text)
                    output = {'claims': [dict(text='43 mW', source=source, quote=quote)]}
                else:
                    cited = re.findall(r'\[(\d+)\]\n(.*?)(?=\n\n(?:\[\d+\]\n|Question:)|\Z)', user, flags=re.S)
                    proof = next(((int(label), text.strip()) for label, text in cited if ANSWER in text), None)
                    output = dict(status='answered', source=proof[0], answer=ANSWER, quote=proof[1]) if (
                        proof and 'What type of box held' in user) else dict(status='insufficient', source=0, answer='', quote='')
                item = {'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}
            result = {'choices': [item]}
        else:
            raise AssertionError('Unexpected Flash transport operation')
        with Reply(json.dumps(result).encode()) as response:
            yield response

    def search(request):
        assert request.full_url == 'https://api.deepseek.com/v1/tools/search'
        assert json.loads(request.data) == {'query': TERMS, 'count': 5, 'need_content': False}
        assert CANARY not in request.data.decode()
        log('search')
        return Reply(json.dumps({'results': [{'url': 'https://arxiv.org/abs/1706.03762', 'title': 'A fixture result',
                                             'snippet': 'A synthetic result returned by a stub.'}]}).encode())

    flash_transport._response = flash
    deepseek_search._open = search
    sys.argv = ['scanpdf/workbench.py', '--port', '0', '--data', str(data), '--enable-deepseek']
    main()


@contextmanager
def worker(directory):
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'LANG', 'LC_ALL', 'TZ', 'EVIDENCE_DENSE_MODEL_DIR'}}
    store = directory / 'library'
    ledger = directory / 'budget.sqlite3'
    events = directory / 'stub-calls.txt'
    env.update(PYTHONDONTWRITEBYTECODE='1', DEEPSEEK_API_KEY='acceptance-stub-placeholder',
               DEEPSEEK_INPUT_USD_PER_MILLION='0.1', DEEPSEEK_OUTPUT_USD_PER_MILLION='0.1',
               DEEPSEEK_SEARCH_USD_PER_CALL='0.01', DEEPSEEK_FETCH_USD_PER_CALL='0.01', EVIDENCE_BUDGET_DB=str(ledger))
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--stub-worker', str(store), str(events)],
                               cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    try:
        if not select.select([process.stdout], [], [], 30)[0]:
            raise RuntimeError('Stub workbench startup timed out')
        parsed = urlsplit(process.stdout.readline().strip())
        assert parsed.hostname == '127.0.0.1' and parsed.port
        yield parsed.port, ledger, events
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def search_only(port):
    events = call(port, '/api/copilot', dict(ids=[], question=CANARY, model_planner=False, model_consent=False,
                                            publisher_consent=False, search_terms=TERMS, fetch_first=False), events=True)
    return events[-1]


def counts(path):
    return Counter(path.read_text().splitlines()) if path.exists() else Counter()


def run():
    sys.path.insert(0, str(ROOT / 'scanpdf'))
    from paid_budget import Budget, LIMIT_NUSD

    data = fixture()
    doc_id = sha256(data).hexdigest()
    with tempfile.TemporaryDirectory(prefix='evidence-flash-stub-') as temporary, worker(Path(temporary)) as (port, ledger, log):
        status = call(port, '/api/model-status')
        assert status['provider'] == 'deepseek' and status['model'] == MODEL and status['configured'] is True
        assert write_or_verify_fixture(port, data, doc_id)
        search = search_only(port)
        assert search['event'] == 'final' and search['web']['state'] == 'ok' and search['web']['fetch'] is None
        assert search['reports'] == search['publications'] == [] and counts(log) == Counter(search=1)
        planner = graph_case(port, doc_id, MODEL)
        page = page_case(port, doc_id, MODEL, data)
        missing = missing_case(port, doc_id, MODEL)
        assert planner['passed'] and page['passed'] and missing['passed'] and missing['model_identity_verified'], (planner, page, missing)
        before = counts(log)
        assert before.get('model_paid') == 3 and before.get('search') == 1
        budget = Budget(ledger)
        existing = budget.status()
        assert existing['rounds'] == 4 and existing['search_calls'] == 1 and existing['model_calls'] == 3
        budget.start_round().reserve('fetch', existing['remaining_nusd'])
        assert budget.status()['spent_nusd'] == LIMIT_NUSD
        rejected_search = search_only(port)
        assert rejected_search['event'] == 'error' and '0.71' in rejected_search['message']
        try:
            followup(port, doc_id, 'What type of box held the silver eddy-current paddle during the harmonics check?')
        except HTTPFailure as rejected:
            model_http = rejected.status
        else:
            model_http = 200
        assert model_http == 503
        after = counts(log)
        delta = after - before
        assert not (delta['model_paid'] or delta['search'] or delta['fetch']), delta
        transport_blocked = not delta
        return {'schema_version': 1, 'status': 'stub_passed' if transport_blocked else 'stub_partial',
                'transport': 'isolated in-process stubs',
                'real_workbench_http': True, 'live_provider_requests': 0, 'provider_model_identity': MODEL,
                'fixture_sha256': doc_id, 'search_only': {'search_calls': 1, 'fetch_calls': 0,
                                                        'only_explicit_public_terms_sent': True},
                'tool_plan': planner, 'single_paper_answer': page, 'missing_field': missing,
                'budget_saturated': {'reserved_nusd': LIMIT_NUSD, 'search_refused': True,
                                     'model_http_status': model_http, 'additional_paid_calls': 0,
                                     'additional_metadata_calls': delta['model_metadata'],
                                     'additional_transport_calls': sum(delta.values()),
                                     'all_outgoing_transport_blocked': transport_blocked}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--stub-worker', nargs=2, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.stub_worker:
        serve_stub(ROOT, *args.stub_worker)
        return
    result = json.dumps(run(), indent=2) + '\n'
    if args.out:
        with args.out.open('x') as stream:
            stream.write(result)
    print(result, end='')


if __name__ == '__main__':
    main()
