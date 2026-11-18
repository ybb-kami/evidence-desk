"""Exercise a frozen local workbench using its public one-page rehearsal PDF."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import http.client
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
from threading import Barrier
import time
from urllib.parse import urlencode, urlsplit

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path('demo/A-lantern.pdf')
FIXTURE_SHA = 'b5b2e136ca4a8405cb9783032885374e90a2683edb5592c34bb2517491618dbe'
QUERY = 'amber lantern northern shore'
QUOTE = 'The amber lantern was recorded on the northern shore.'
CLIENTS = 4
OPERATIONS = ('import', 'search', 'original_sha256', 'citation_locator')


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def call(port, path, body=None, mime='application/json', *, raw=False):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        headers = {'Content-Type': mime} if body is not None else {}
        connection.request('GET' if body is None else 'POST', path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        check(response.status == 200, f'HTTP {response.status} for {path.split("?", 1)[0]}')
        if raw:
            check(response.headers.get_content_type() == 'application/pdf', 'original MIME type changed')
            return data
        return json.loads(data)
    finally:
        connection.close()


@contextmanager
def workbench(repo, server_python, log):
    environment = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TZ') if key in os.environ}
    environment.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1')
    with tempfile.TemporaryDirectory(prefix='evidence-desk-soak-') as data, log.open('xb') as stderr:
        environment['EVIDENCE_DENSE_MODEL_DIR'] = str(Path(data) / 'absent-dense-model')
        cmd = [server_python, 'scanpdf/workbench.py', '--port', '0', '--data', data]
        proc = subprocess.Popen(cmd, cwd=repo, env=environment, stdout=subprocess.PIPE, stderr=stderr, text=True)
        try:
            check(select.select([proc.stdout], [], [], 30)[0], 'workbench startup timed out')
            banner = urlsplit(proc.stdout.readline().strip())
            check(banner.hostname == '127.0.0.1' and banner.port, 'workbench did not bind loopback')
            yield proc, banner.port
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()


def rss_kib(pid):
    result = {}
    try:
        with Path(f'/proc/{pid}/status').open() as status:
            for line in status:
                name, _, value = line.partition(':')
                if name in ('VmRSS', 'VmHWM'):
                    result[name] = int(value.split()[0])
    except FileNotFoundError:
        pass
    return result


def read_source(path):
    data = path.read_bytes()
    check(sha256(data).hexdigest() == FIXTURE_SHA, 'public fixture SHA-256 changed')
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        check(len(pdf) == 1, 'expected a one-page fixture')
        page = pdf[0]
        boxes = page.search_for(QUOTE)
        check(len(boxes) == 1, 'expected one occurrence of the known quote')
        return data, tuple(boxes[0]), (page.rect.width, page.rect.height)


def verify_locator(port, hit, expected_box, page_size):
    text, page, block, part = (hit[key] for key in ('text', 'page', 'block', 'part'))
    begin = text.index(QUOTE)
    end = begin + len(QUOTE)
    path = '/api/source?' + urlencode(dict(id=FIXTURE_SHA, page=page, block=block, part=part, start=begin, end=end))
    result = call(port, path)
    check(result['doc']['id'] == FIXTURE_SHA and result['source'] == 'native', 'locator document or provenance changed')
    check((result['page'], result['block'], result['part']) == (page, block, part), 'locator anchor changed')
    check((result['width'], result['height']) == page_size, 'locator page dimensions changed')
    check(result['pdf_url'] == f'/pdf/{FIXTURE_SHA}#page={page}', 'locator PDF link changed')
    selection = result['selection']
    check(result['text'] == text and selection and selection['precision'] == 'glyph', 'locator did not return glyph evidence')
    check((selection['start'], selection['end']) == (begin, end), 'locator quote offsets changed')
    check(len(selection['boxes']) == 1 and len(selection['boxes'][0]) == 4, 'expected one locator rectangle')
    check(all(abs(got - wanted) < 0.5 for got, wanted in zip(selection['boxes'][0], expected_box, strict=True)),
          'locator rectangle differs from original-page quote')


def capture(name, function, results):
    start = time.perf_counter()
    try:
        value = function()
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError, AttributeError) as error:
        results[name] = dict(state='failed', ms=round(1000 * (time.perf_counter() - start), 4),
                             error=f'{type(error).__name__}: {error}')
        return None
    results[name] = dict(state='passed', ms=round(1000 * (time.perf_counter() - start), 4))
    return value


def verify_pdf(port):
    body = call(port, f'/pdf/{FIXTURE_SHA}', raw=True)
    check(sha256(body).hexdigest() == FIXTURE_SHA, 'downloaded original bytes changed')


def client(port, source, barrier):
    data, box, size = source
    records = {}
    barrier.wait(timeout=10)

    def upload():
        name = urlencode({'name': FIXTURE.name})
        result = call(port, f'/api/import?{name}', data, 'application/pdf')
        check(result['doc']['id'] == FIXTURE_SHA and result['doc']['pages'] == 1,
              'imported document ID or physical page count changed')
        check(type(result['existing']) is bool, 'duplicate receipt is missing')
        return not result['existing']

    def search():
        result = call(port, '/api/search', {'query': QUERY, 'ids': [FIXTURE_SHA]})
        hits = [hit for hit in result['hits'] if hit['id'] == FIXTURE_SHA and hit['page'] == 1 and QUOTE in hit['text']]
        check(len(hits) == 1, 'expected one exact native hit on physical page 1')
        return hits[0]

    created = capture('import', upload, records)
    if created is not None:
        records['import']['created'] = created
    hit = capture('search', search, records)
    capture('original_sha256', lambda: verify_pdf(port), records)
    if hit:
        capture('citation_locator', lambda: verify_locator(port, hit, box, size), records)
    else:
        records['citation_locator'] = dict(state='skipped', reason='search did not return the expected quote')
    return records


def waiting(target):
    remaining = target - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def quantiles_ms(samples):
    ordered = sorted(samples)
    if not ordered:
        return {'observations': 0, 'p50_ms': None, 'p95_ms': None}
    return {'observations': len(ordered), **{f'p{p}_ms': ordered[math.ceil(len(ordered) * p / 100) - 1]
                                           for p in (50, 95)}}


def aggregate(rounds):
    clients = [record for row in rounds for record in row['clients']]
    operations = {}
    for name in OPERATIONS:
        attempts = [record[name] for record in clients]
        counts = Counter(action['state'] for action in attempts)
        operations[name] = {'scheduled': len(attempts), 'passed': counts['passed'], 'failed': counts['failed'],
                            'skipped': counts['skipped'],
                            'latency': quantiles_ms([x['ms'] for x in attempts if x['state'] != 'skipped']),
                            'latency_passed': quantiles_ms([x['ms'] for x in attempts if x['state'] == 'passed']),
                            'failure_reasons': dict(Counter(x['error'] for x in attempts if x['state'] == 'failed'))}
    created = sum(record['import'].get('created') is True for record in clients)
    duplicate = sum(record['import'].get('created') is False for record in clients)
    return operations, created, duplicate


def run(args):
    fixture = args.repo / FIXTURE
    source = read_source(fixture)
    results = []
    memory = []
    start_utc = utc_now()
    log_path = args.progress.with_suffix('.stderr.log')
    with args.progress.open('x') as journal, workbench(args.repo, args.server_python, log_path) as (proc, port):
        initial = call(port, '/api/library')
        check(not initial['docs'] and initial['deepseek_enabled'] is False, 'expected an empty offline library')
        memory.append(rss_kib(proc.pid))
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=CLIENTS) as workers:
            for number in range(1, args.rounds + 1):
                slot = start + args.interval_seconds * (number - 1)
                waiting(slot)
                before = time.monotonic()
                barrier = Barrier(CLIENTS)
                jobs = [workers.submit(client, port, source, barrier) for _ in range(CLIENTS)]
                observations = [job.result() for job in jobs]
                memory.append(rss_kib(proc.pid))
                row = {'round': number, 'started_after_seconds': round(before - start, 4),
                       'schedule_delay_seconds': round(before - slot, 4), 'elapsed_seconds': round(time.monotonic() - before, 4),
                       'rss_kib': memory[-1], 'clients': observations}
                results.append(row)
                journal.write(json.dumps(row) + '\n')
                journal.flush()
        waiting(start + args.rounds * args.interval_seconds)
        elapsed = time.monotonic() - start
        final_ops = {}
        final = capture('library', lambda: call(port, '/api/library'), final_ops)
        capture('original_sha256', lambda: verify_pdf(port), final_ops)
        capture('fixture_sha256', lambda: check(sha256(fixture.read_bytes()).hexdigest() == FIXTURE_SHA,
                                                 'input fixture file changed'), final_ops)
        memory.append(rss_kib(proc.pid))
        server_alive = proc.poll() is None
    actions, created, duplicate = aggregate(results)
    final_docs = [row['id'] for row in final['docs']] if final else None
    final_checks = {'server_alive': server_alive, 'active_document_ids': final_docs,
                    'expected_single_document_present': final_docs == [FIXTURE_SHA],
                    'deepseek_disabled': bool(final and final['deepseek_enabled'] is False), 'operations': final_ops}
    memory_values = [x['VmRSS'] for x in memory if 'VmRSS' in x]
    highest_values = [x['VmHWM'] for x in memory if 'VmHWM' in x]
    passed = (len(results) == args.rounds and created == 1 and duplicate == args.rounds * CLIENTS - 1
              and all(a['failed'] == a['skipped'] == 0 for a in actions.values()) and server_alive
              and final_checks['expected_single_document_present'] and final_checks['deepseek_disabled']
              and all(x['state'] == 'passed' for x in final_ops.values()) and len(memory_values) == args.rounds + 2)
    return {'schema_version': 1, 'revision': args.revision, 'passed': passed, 'started_at_utc': start_utc,
            'finished_at_utc': utc_now(), 'workload_seconds': round(elapsed, 4), 'rounds_planned': args.rounds,
            'rounds_completed': len(results), 'round_interval_seconds': args.interval_seconds,
            'max_schedule_delay_seconds': max(x['schedule_delay_seconds'] for x in results),
            'clients_per_round': CLIENTS, 'client_sequences': len(results) * CLIENTS,
            'fixture': {'path': str(FIXTURE), 'sha256': FIXTURE_SHA, 'bytes': len(source[0]), 'physical_pages': 1},
            'configuration': {'HTTP': 'loopback', 'data': 'fresh temporary library', 'dense_model': 'disabled by absent path',
                              'OCR_or_generation_calls': 0, 'Qwen': 'disabled', 'DeepSeek': 'disabled',
                              'cache': 'fresh application cache at start, retained throughout the same process, OS cache unmanaged',
                              'latency_percentiles': 'nearest rank over complete HTTP operation and its validation'},
            'operations': actions, 'imports': {'created': created, 'duplicate': duplicate}, 'final': final_checks,
            'server_rss_kib': {'samples': len(memory_values), 'expected_samples': args.rounds + 2,
                               'initial': memory[0].get('VmRSS'), 'final': memory[-1].get('VmRSS'),
                               'min': min(memory_values, default=None), 'max': max(memory_values, default=None),
                               'peak_vm_hwm': max(highest_values, default=None)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=ROOT, help='source checkout or git archive directory')
    parser.add_argument('--revision', required=True, help='tested Git object ID')
    parser.add_argument('--server-python', default=sys.executable)
    parser.add_argument('--rounds', type=int, default=90)
    parser.add_argument('--interval-seconds', type=float, default=60)
    parser.add_argument('--progress', type=Path, required=True, help='new JSONL journal with one row per completed round')
    parser.add_argument('--out', type=Path, required=True, help='new JSON aggregate written after the final checks')
    args = parser.parse_args()
    if args.rounds < 1 or not math.isfinite(args.interval_seconds) or args.interval_seconds <= 0:
        parser.error('round count and interval must be positive')
    if args.out.exists():
        parser.error('output file must not exist')
    result = run(args)
    encoded = json.dumps(result, indent=2) + '\n'
    with args.out.open('x') as output:
        output.write(encoded)
    print(encoded, end='')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
