from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from entry_patch import render
from judge_start import serve_four_seats
from group_budget import Denied


ENTRY = b"""from __future__ import annotations

def start(runtime, port):
    with demo_slots(DEFAULT_APP, run_dir / 'sessions', 4, loopback_origin) as slots:
        serve(slots, port, PUBLIC_ORIGIN, local_test=False, anonymous=True,
              rotate_slot=slots.rotate_slot, ttl=3600, model_bridge=None)
"""


class EntryTest(unittest.TestCase):
    def test_installed_entry_with_inline_loopback(self):
        installed = ENTRY.replace(b"run_dir / 'sessions', 4, loopback_origin",
                                  b'Path(run_dir) / "sessions", 4, f"http://127.0.0.1:{port}"')
        diff = render(installed, sha256(installed).hexdigest())
        self.assertIn("serve_four_seats(serve, slots, port, PUBLIC_ORIGIN", diff)
        for changed in (installed.replace(b'127.0.0.1', b'0.0.0.0'),
                        installed.replace(b'Path(run_dir)', b'Path(runtime)'),
                        installed.replace(b', 4,', b', 2,')):
            with self.assertRaises(ValueError):
                render(changed, sha256(changed).hexdigest())

    def test_entry_hash_and_real_four_seat_contract(self):
        diff = render(ENTRY, sha256(ENTRY).hexdigest())
        self.assertIn('serve_four_seats(serve, slots, port, PUBLIC_ORIGIN', diff)
        self.assertIn('from judge_start import serve_four_seats', diff)
        for changed in (ENTRY.replace(b', 4,', b', 2,'), ENTRY.replace(b"run_dir", b"runtime"),
                        ENTRY.replace(b'model_bridge=None', b'model_bridge=old')):
            with self.assertRaises(ValueError):
                render(changed, sha256(changed).hexdigest())
        with self.assertRaises(ValueError):
            render(ENTRY, '0' * 64)

    def test_original_off_even_without_tokenizer_or_socket(self):
        old = Mock()
        slots = {i: 1000+i for i in range(4)}
        rotate = Mock()
        with patch.dict(os.environ, {}, clear=True):
            serve_four_seats(old, slots, 8000, 'https://judge.example', rotate_slot=rotate)
        old.assert_called_once_with(slots, 8000, 'https://judge.example', local_test=False,
                                    anonymous=True, rotate_slot=rotate, ttl=3600, model_bridge=None)

    def test_private_mode_explicit_and_uses_candidate_not_old_module(self):
        slots = dict(enumerate(range(4)))
        with TemporaryDirectory() as folder:
            cfg = Path(folder) / 'metadata.json'
            cfg.write_text('{"path":"/example/immutable"}')
            env = {'EVIDENCE_JUDGE_PROVIDER':'offline_fixture', 'EVIDENCE_JUDGE_BUDGET_CONFIG':str(cfg),
                   'EVIDENCE_JUDGE_BROKER_UID':'99', 'EVIDENCE_JUDGE_SOCKET':'/example/broker.sock'}
            with patch.dict(os.environ, env, clear=True), patch('operator_runtime.private_runtime') as factory, patch('candidate.gateway.gateway.serve') as new:
                factory.return_value = {'model_bridge':None, 'synthesis_bridge':'fixture'}
                old = Mock()
                serve_four_seats(old, slots, 8000, 'https://judge.example', rotate_slot=None)
                factory.assert_called_once_with({'path':'/example/immutable'}, socket_path='/example/broker.sock',
                                                broker_uid=99, provider_mode='offline_fixture')
                new.assert_called_once()
                old.assert_not_called()
                cfg.write_text('{"path":"one","path":"two"}')
                with self.assertRaises(ValueError):
                    serve_four_seats(old, slots, 8000, 'https://judge.example', rotate_slot=None)
        from operator_runtime import private_runtime
        with self.assertRaises(Denied):
            private_runtime({}, socket_path='/fixture.sock', broker_uid=99, provider_mode='approved_unix')
