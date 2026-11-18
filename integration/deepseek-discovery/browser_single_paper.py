"""Offline Chromium acceptance for the four-seat candidate and a single synthetic PDF.

Run from the repository root:
    python integration/deepseek-discovery/browser_single_paper.py [--chromium /path/to/chrome]
Requires Linux, the candidate's pinned tokenizer, PyMuPDF, Playwright and Chromium.
The fixture and its SQLite ledger are temporary. No provider credentials are used.
"""
import argparse
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import json
import os
import re
from pathlib import Path
from queue import Queue
import shutil
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from unittest.mock import patch
from urllib.parse import urlsplit

import pymupdf
from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / 'candidate/gateway'))
import gateway
import launcher
from group_budget import GroupBudget, NANO
from private_transport import UnixProviderBroker, fake_response

ATTENTION_V1_SHA = '0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476'
QUESTION = 'Which optimizer was used?'
SENTENCE = 'The optimizer used in this calibration was Adam with a learning rate of 0.005.'


def example_pdf():
    with pymupdf.open() as document:
        document.new_page().insert_text((48, 80), 'A synthetic two-page calibration note.')
        document.new_page().insert_text((48, 80), SENTENCE)
        return document.tobytes()


@contextmanager
def loopback_gateway(slots, port, origin, bridges):
    """Retrieve the listening server for orderly shutdown without handling any requests here."""
    started = Queue()

    class ObservablePool(gateway.Pool):
        def serve_forever(self, *args, **kwargs):
            started.put(self)
            super().serve_forever(*args, **kwargs)

    def run():
        try:
            gateway.serve(slots, port, origin, local_test=True, anonymous=True,
                          rotate_slot=slots.rotate_slot, ttl=3600, asset_root=ROOT, **bridges)
        except Exception as exc:
            started.put(exc)

    with patch.object(gateway, 'Pool', ObservablePool):
        thread = Thread(target=run, daemon=True)
        thread.start()
        server = started.get(timeout=12)
        if isinstance(server, Exception):
            raise server
        try:
            yield
        finally:
            server.shutdown()
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError('loopback gateway did not stop')


def json_response(page, path, button):
    with page.expect_response(lambda response: urlsplit(response.url).path == path
                              and response.request.method == 'POST') as pending:
        button.click()
    response = pending.value
    if response.status != 200:
        raise AssertionError(f'{path} returned HTTP {response.status}')
    return response.json()


def upload_and_select(page, origin, filename, pdf):
    digest = sha256(pdf).hexdigest()
    response = page.goto(origin)
    assert response.status == 200
    panel = page.locator('#grounded-synthesis')

    with page.expect_file_chooser() as chooser:
        page.locator('#choose').click()
    with page.expect_response(lambda r: urlsplit(r.url).path == '/api/import') as imported:
        chooser.value.set_files({'name':filename, 'mimeType':'application/pdf', 'buffer':pdf})
    assert imported.value.status == 200
    assert imported.value.json()['doc']['id'] == digest
    expect(page.locator('#import-log')).to_contain_text('已导入')
    panel.get_by_role('button', name='刷新文献').click()
    check = panel.locator('fieldset input[value="' + digest + '"]')
    check.check()
    return panel


def click_original(context, citation, origin, digest, pdf):
    with context.expect_event('response', predicate=lambda r: urlsplit(r.url).path == '/pdf/' + digest) as navigation:
        citation.click()
    response = navigation.value
    assert response.status == 200 and 'application/pdf' in response.headers['content-type']
    # Chromium's PDF viewer takes ownership of this navigation body; verify the identical anchored
    # resource independently in the same visitor context using its HTTP cookie jar.
    assert context.request.get(origin + '/pdf/' + digest).body() == pdf


def collect(browser, origin, pdf, ledger_path):
    digest = sha256(pdf).hexdigest()
    external_attempts = []

    def only_local(route):
        url = urlsplit(route.request.url)
        if url.scheme in ('http', 'https') and f'{url.scheme}://{url.netloc}' != origin:
            external_attempts.append(url.hostname)
            return route.abort()
        return route.continue_()

    def reservations():
        with sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True) as db:
            return db.execute('SELECT op, nano, wire_sha FROM spend ORDER BY rid').fetchall()

    historical = reservations()
    assert len(historical) == 1 and historical[0][:2] == ('search', NANO['search'])
    with ExitStack() as clients:
        def new_visitor():
            context = browser.new_context(service_workers='block')
            clients.callback(context.close)
            context.route('**/*', only_local)
            return context

        first = new_visitor()
        page = first.new_page()
        page.set_default_timeout(12_000)
        panel = upload_and_select(page, origin, 'single-calibration.pdf', pdf)
        question = panel.get_by_role('textbox')
        preview = panel.get_by_role('button', name='预览单篇问答')
        send = panel.get_by_role('button', name='发送并生成待审回答')
        question.fill('nebulalatticequasars')
        missing = json_response(page, '/api/synthesis/preview', preview)
        assert missing['status'] == 'insufficient_evidence' and not missing.get('token')
        expect(send).to_be_disabled()
        assert reservations() == historical

        question.fill(QUESTION)
        receipt = json_response(page, '/api/synthesis/preview', preview)
        assert receipt['status'] == 'ready' and receipt['online'] and receipt['mode'] == 'single'
        assert receipt['provider_mode'] == 'offline_fixture'
        expect(panel).to_contain_text('离线模拟')
        assert sha256(receipt['wire_text'].encode()).hexdigest() == receipt['wire_sha256']
        source, = receipt['sources'].values()
        assert source['page'] == 2 and source['doc_id'] == digest and source['text'] == SENTENCE
        assert source['block'] == 1
        expect(panel.locator('.s-proof blockquote')).to_have_text(SENTENCE)
        expect(send).to_be_disabled()
        assert reservations() == historical

        # New cookie jars allocate the other three seats. Verify the same original URL is isolated.
        for _ in range(3):
            other = new_visitor()
            assert other.request.get(origin).status == 200
            assert other.request.get(origin + '/api/library').json()['docs'] == []
            assert other.request.get(origin + '/pdf/' + digest).status == 404
            reject = other.request.post(origin + '/api/synthesis/confirm',
                    headers={'Origin':origin}, data={'token':receipt['token'], 'consent':True})
            assert reject.status == 409
        assert new_visitor().request.get(origin).status == 503
        assert reservations() == historical

        consent = panel.locator('label input[type=checkbox]').last
        consent.check()
        result = json_response(page, '/api/synthesis/confirm', send)
        assert result['status'] == 'draft_for_review' and result['claim_support'] == 'not_assessed'
        assert result['provider_mode'] == 'offline_fixture' and result['mode'] == 'single'
        claim, = result['claims']
        assert claim['evidence'] == [source]
        assert SENTENCE in claim['text'], 'the offline fixture must answer with the supported sentence'
        article = panel.locator('article').filter(has_text=claim['text'])
        expect(article.locator('blockquote')).to_have_text(SENTENCE)
        citation = article.get_by_role('link')
        expect(citation).to_have_attribute('href', f'/pdf/{digest}#page=2')
        click_original(first, citation, origin, digest, pdf)
        assert sorted(reservations()) == sorted(historical + [('model', NANO['model'], receipt['wire_sha256'])])
        again = first.request.post(origin + '/api/synthesis/confirm', headers={'Origin':origin},
                                   data={'token':receipt['token'], 'consent':True})
        assert again.status == 409
        assert sorted(reservations()) == sorted(historical + [('model', NANO['model'], receipt['wire_sha256'])])
        assert not external_attempts
        return {'browser':browser.version, 'visitors':4, 'fifth_visitor_http':503,
                'absent_evidence':missing['status'], 'status':result['status'],
                'provider_mode':result['provider_mode'], 'displayed_simulated_mode':True,
                'physical_page':source['page'], 'block':source['block'], 'original_pdf_matched':True,
                'other_seats_denied_pdf_and_preview_replay':3, 'same_seat_replay_http':again.status,
                'new_model_reservation_nano_usd':NANO['model'], 'actual_paid_calls':0}


def collect_attention(browser, origin, pdf):
    # Same frozen arXiv v1 bytes as the independent public-PDF fixture in test_v03_revisions.
    if sha256(pdf).hexdigest() != ATTENTION_V1_SHA:
        raise ValueError('Unexpected Attention v1 SHA-256; supply the unmodified 1706.03762v1.pdf')
    cases = [('hardware', 'What hardware was used for training?'),
             ('gpu', 'How many GPUs did they use for training and what was the model?')]
    rows = []
    for label, query in cases:
        with browser.new_context(service_workers='block') as visitor:
            visitor.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
            page = visitor.new_page()
            page.set_default_timeout(20_000)
            panel = upload_and_select(page, origin, '1706.03762v1.pdf', pdf)
            panel.get_by_role('textbox').fill(query)
            ready = json_response(page, '/api/synthesis/preview', panel.get_by_role('button', name='预览单篇问答'))
            row = {'case':label, 'question':query, 'preview_status':ready['status'], 'p100_source_verified':False}
            if ready['status'] != 'ready':
                assert ready['status'] == 'insufficient_evidence' and not ready.get('token')
                rows.append(row)
                continue
            assert ready['provider_mode'] == 'offline_fixture'
            payload = json.loads(ready['wire_text'])
            assert json.loads(payload['messages'][1]['content'])['question'] == query
            expect(panel).to_contain_text('离线模拟')
            panel.locator('label input[type=checkbox]').last.check()
            result = json_response(page, '/api/synthesis/confirm', panel.get_by_role('button', name='发送并生成待审回答'))
            assert result['provider_mode'] == 'offline_fixture'
            row['confirm_status'] = result['status']
            matches = [e for claim in result.get('claims', []) for e in claim['evidence']
                       if e['doc_id'] == ATTENTION_V1_SHA and e['page'] == 7
                       and re.search(r'8\s+NVIDIA\s+P100\s+GPUs', e['text'])
                       and e['text'] in claim['text']]
            if matches:
                source = matches[0]
                link = panel.locator('article').get_by_role('link', name=f"第 7 页 · 块 {source['block']}", exact=True).first
                expect(link).to_have_attribute('href', f'/pdf/{ATTENTION_V1_SHA}#page=7')
                click_original(visitor, link, origin, ATTENTION_V1_SHA, pdf)
                with pymupdf.open(stream=pdf, filetype='pdf') as original:
                    original_blocks = {block[5]+1:block[4].strip() for block in original[6].get_text('blocks') if block[6]==0}
                assert original_blocks[source['block']] == source['text']
                row.update(p100_source_verified=True, physical_page=7, block=source['block'], original_pdf_matched=True)
            rows.append(row)
    assert all(row['p100_source_verified'] for row in rows), rows
    return {'pdf_sha256':ATTENTION_V1_SHA, 'browser':browser.version, 'cases':rows,
            'provider_mode':'offline_fixture', 'actual_paid_calls':0}


def run(chromium, attention=None):
    from operator_runtime import private_runtime
    from audit_group_ledger import audit
    from legacy_ledger import upgrade_policy_in_place
    from group_budget import ENDPOINT
    with TemporaryDirectory(prefix='judge-browser-') as folder, ExitStack() as stack:
        root = Path(folder)
        now = int(time.time())
        config = dict(path=root/'budget.sqlite', approval_sha='a'*64, price_sha='b'*64,
                      verified_epoch=now, expiry_epoch=now+480, seat_groups=4)
        old = GroupBudget(**config)
        old.provision()  # disposable old-format ledger with an unresolved historical operation
        old.reserve(seat='d'*64, gid='e'*64, rid='f'*64, op='search', endpoint=ENDPOINT['search'],
                    wire=b'{"query":"historical fixture","count":1,"need_content":false}')
        snapshot = root/'static.sqlite'
        with sqlite3.connect(config['path']) as live, sqlite3.connect(snapshot) as backup:
            live.backup(backup)
        config.update(cap_policy={'cny_per_usd_upper':'8.00','fx_source_sha256':'c'*64,
                                  'fx_verified_epoch':now, 'fx_expiry_epoch':now+480})
        ledger = GroupBudget(**config)
        migration = upgrade_policy_in_place(old, ledger, snapshot=snapshot,
                                            snapshot_sha=sha256(snapshot.read_bytes()).hexdigest(),
                                            writers_stopped=True)
        assert migration['historical']['reserved_nano_usd'] == NANO['search']
        unix_path = root / 'model.sock'
        dispatches = []

        def fixture_sender(endpoint, wire):
            with sqlite3.connect(config['path'].as_uri()+'?mode=ro', uri=True) as db:
                count, = db.execute('SELECT count(*) FROM spend WHERE wire_sha=?', (sha256(wire).hexdigest(),)).fetchone()
                assert db.execute('SELECT count(*) FROM private_pending').fetchone() == (0,)
            assert count == 1
            dispatches.append(endpoint)
            return fake_response(endpoint, wire)

        broker = stack.enter_context(UnixProviderBroker(unix_path, allow_uids={os.getuid()},
                                                       ledger=ledger, approved_sender=fixture_sender))
        thread = Thread(target=broker.serve_forever, daemon=True)
        thread.start()
        stack.callback(thread.join, 5)
        stack.callback(broker.close)
        bridges = private_runtime(config, socket_path=unix_path, broker_uid=os.getuid())
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        slots = stack.enter_context(launcher.demo_slots(ROOT/'app.py', root/'sessions', 4, origin))
        stack.enter_context(loopback_gateway(slots, port, origin, bridges))
        playwright = stack.enter_context(sync_playwright())
        browser = stack.enter_context(playwright.chromium.launch(executable_path=chromium,
                         args=['--disable-background-networking']))
        if attention is None:
            result = collect(browser, origin, example_pdf(), config['path'])
            assert len(dispatches) == 1
        else:
            result = collect_attention(browser, origin, attention.read_bytes())
            assert len(dispatches) == sum('confirm_status' in row for row in result['cases'])
        final = audit(config['path'], budget=ledger)
        assert final['reserved_nano_usd'] == NANO['search'] + len(dispatches) * NANO['model']
        assert final['limit_nano_usd'] == 2_500_000_000
        return {**result, 'unix_fake_dispatches':len(dispatches), 'in_place_budget_upgrade':True,
                'historical_unknown_reserved_nano_usd':NANO['search'],
                'cumulative_reserved_nano_usd':final['reserved_nano_usd'],
                'cumulative_limit_nano_usd':final['limit_nano_usd']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('chromium-browser'))
    parser.add_argument('--attention', type=Path, help='Run the separate frozen Attention v1 hardware regression')
    args = parser.parse_args()
    print(json.dumps(run(args.chromium, args.attention), ensure_ascii=False, indent=2))
