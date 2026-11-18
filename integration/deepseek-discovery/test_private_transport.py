from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
from select import select
import socket
import sqlite3
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from time import monotonic, sleep, time
import unittest
from unittest.mock import patch

import broker_sender
from group_budget import ENDPOINT, MODEL, Denied, GroupBudget
from group_dispatch import ApprovedGroupCaller
from legacy_ledger import upgrade_policy_in_place
from official_input_bound import submitted_count
from private_transport import (
    IDENTITY, LENGTH, REPLY_LIMIT, UnixProviderBroker, UnixProviderTransport,
    PrivateTransportError, _frame, _receive, fake_response,
)
import private_transport


UID = os.geteuid()
SEARCH = {"query": "public encoder references", "count": 5, "need_content": False}
FETCH = {"url": "https://arxiv.org/abs/1706.03762"}
SOURCE = "The selected PDF records traceable evidence on this page."
MODEL_BODY = {"model": MODEL, "messages": [{"role": "system", "content": "Return JSON."},
              {"role": "user", "content": json.dumps({"question": "What is the passage?",
              "documents": [[{"ref": "E1", "excerpt": SOURCE}]]})}], "max_tokens": 512,
              "stream": False, "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}}
FIXTURE_KEY = "fixture-key-used-only-by-tests"


class FixtureEnvironment(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_reads = 0

    def get(self, key, default=None):
        if key == "DEEPSEEK_API_KEY":
            self.key_reads += 1
        return super().get(key, default)


def hex_id(label):
    return sha256(label.encode()).hexdigest()


def encode(body):
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


@contextmanager
def running(path, ledger, **options):
    options.setdefault("allow_uids", [UID])
    with UnixProviderBroker(path, ledger=ledger, **options) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server
        finally:
            server.close()
            thread.join(3)
            if thread.is_alive():
                raise AssertionError("Socket fixture did not stop.")


class PrivateTransportTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="fullwindow-unix-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "provider.sock"
        now = int(time())
        self.config = {"path": os.fspath(self.root / "budget.db"), "approval_sha": hex_id("approval-fixture"),
                       "price_sha": hex_id("price-fixture"), "verified_epoch": now, "expiry_epoch": now+120,
                       "seat_groups": 100}
        self.ledger = GroupBudget(**self.config, input_bound=submitted_count)
        self.ledger.provision()
        self.client = UnixProviderTransport(self.path, broker_uid=UID, timeout=2)

    def reserve(self, op="search", body=SEARCH, label="one", *, private=True):
        rid, wire = hex_id(label+op), encode(body)
        self.ledger.reserve(seat=hex_id("seat"), gid=hex_id(label), rid=rid, op=op,
                            endpoint=ENDPOINT[op], wire=wire, private_dispatch=private)
        return rid, wire

    def error(self, code, call):
        with self.assertRaises(PrivateTransportError) as caught:
            call()
        self.assertEqual((caught.exception.code, str(caught.exception)), (code, code))

    def test_dispatcher_all_three_exact_bytes_and_full_window_still_one_ledger(self):
        seen = {}

        def supplier(endpoint, wire):
            op = next(kind for kind, value in ENDPOINT.items() if endpoint == value)
            with self.ledger._connect() as db:
                row = db.execute("SELECT rid,wire_sha,nano FROM spend WHERE op=? AND wire_sha=? ORDER BY rowid DESC LIMIT 1",
                                 (op, sha256(wire).hexdigest())).fetchone()
                self.assertIsNotNone(row)
                self.assertIsNone(db.execute("SELECT rid FROM private_pending WHERE rid=?", (row[0],)).fetchone())
            seen[row[0]] = (endpoint, wire)
            return fake_response(endpoint, wire)

        with running(self.path, self.ledger, approved_sender=supplier):
            caller = ApprovedGroupCaller(self.ledger, self.client)
            for group in ("one", "two"):
                for op, body in (("search", SEARCH), ("fetch", FETCH), ("model", MODEL_BODY)):
                    label = hex_id(group+op)
                    reply = caller.call(hex_id("seat"), hex_id(group), op, label, body)
                    self.assertEqual(seen[label], (ENDPOINT[op], encode(body)))
                    if op == "model":
                        self.assertEqual(json.loads(reply["choices"][0]["message"]["content"])["claims"],
                                         [{"text": SOURCE, "refs": ["E1"]}])
            with self.assertRaises(Denied):
                caller.call(hex_id("seat"), hex_id("three"), "model", hex_id("third-model"), MODEL_BODY)
        with self.ledger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*),sum(nano) FROM spend").fetchone(), (6, 633374400))
            self.assertEqual(db.execute("SELECT count(*) FROM private_pending").fetchone(), (0,))
        self.assertEqual(len(seen), 6)

    def test_only_post_reservation_api_sends_and_historic_rows_never_send(self):
        rid, wire = self.reserve(private=False)
        eligible, _ = self.reserve(label="eligible")
        with running(self.path, self.ledger):
            self.error("reservation_required", lambda: self.client(ENDPOINT["search"], wire))
            self.error("unreserved", lambda: self.client.send_reserved(ENDPOINT["search"], wire, rid=rid))
            self.assertTrue(self.client.send_reserved(ENDPOINT["search"], wire, rid=eligible)["results"])

    def test_wire_changed_does_not_consume_the_right_reservation(self):
        rid, wire = self.reserve()
        modified = encode({**SEARCH, "query": "other public query"})
        with running(self.path, self.ledger):
            self.error("unreserved", lambda: self.client.send_reserved(ENDPOINT["search"], modified, rid=rid))
            reply = self.client.send_reserved(ENDPOINT["search"], wire, rid=rid)
            self.assertIn("Offline fixture", reply["results"][0]["title"])

    def test_failure_consumes_once_even_across_restart_without_refund(self):
        calls = []
        rid, wire = self.reserve()

        def supplier(endpoint, request):
            calls.append((endpoint, request))
            raise RuntimeError("private provider diagnostic")

        with running(self.path, self.ledger, approved_sender=supplier):
            self.error("unavailable", lambda: self.client.send_reserved(ENDPOINT["search"], wire, rid=rid))
        with running(self.path, self.ledger):
            self.error("unreserved", lambda: self.client.send_reserved(ENDPOINT["search"], wire, rid=rid))
        self.assertEqual(calls, [(ENDPOINT["search"], wire)])
        with self.ledger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*),sum(nano) FROM spend").fetchone(), (1, 750000))

    def test_atomic_replay_race_and_no_internet_socket(self):
        rid, wire = self.reserve()
        observed, families = [], []
        original = socket.socket

        def local_socket(family, kind, *args, **kwargs):
            self.assertEqual((family, kind), (socket.AF_UNIX, socket.SOCK_STREAM))
            families.append(family)
            return original(family, kind, *args, **kwargs)

        def supplier(endpoint, body):
            observed.append((endpoint, body))
            return fake_response(endpoint, body)

        def call(_):
            try:
                self.client.send_reserved(ENDPOINT["search"], wire, rid=rid)
                return "sent"
            except PrivateTransportError as exc:
                return exc.code

        from unittest.mock import patch
        with patch.object(socket, "socket", side_effect=local_socket), running(self.path, self.ledger, approved_sender=supplier):
            with ThreadPoolExecutor(max_workers=4) as clients:
                result = sorted(clients.map(call, range(4)))
        self.assertEqual(result, ["sent", "unreserved", "unreserved", "unreserved"])
        self.assertEqual(observed, [(ENDPOINT["search"], wire)])
        self.assertGreaterEqual(len(families), 5)

    def test_mode_mismatch_cannot_consume_and_external_mode_requires_sender(self):
        rid, wire = self.reserve()
        with self.assertRaises(ValueError):
            UnixProviderBroker(self.path, allow_uids=[UID], ledger=self.ledger, provider_mode="approved_unix")
        with self.assertRaises(ValueError):
            UnixProviderBroker(self.path, allow_uids=[UID], ledger=self.ledger, provider_mode="approved_unix",
                               approved_sender=fake_response)
        bad = UnixProviderTransport(self.path, broker_uid=UID, provider_mode="approved_unix")
        with running(self.path, self.ledger):
            with self.assertRaises(PrivateTransportError):
                bad.send_reserved(ENDPOINT["search"], wire, rid=rid)
            self.assertTrue(self.client.send_reserved(ENDPOINT["search"], wire, rid=rid)["results"])

    def test_checks_current_cap_binding_and_exact_price_before_consume(self):
        rid, wire = self.reserve()
        with running(self.path, self.ledger):
            with self.ledger._connect() as db:
                db.execute("UPDATE spend SET nano=1 WHERE rid=?", (rid,))
            self.error("unreserved", lambda: self.client.send_reserved(ENDPOINT["search"], wire, rid=rid))
            with self.ledger._connect() as db:
                self.assertIsNotNone(db.execute("SELECT rid FROM private_pending WHERE rid=?", (rid,)).fetchone())
                db.execute("UPDATE spend SET nano=750000 WHERE rid=?", (rid,))
                db.execute("PRAGMA user_version=1")
            self.error("unreserved", lambda: self.client.send_reserved(ENDPOINT["search"], wire, rid=rid))
            with self.ledger._connect() as db:
                self.assertIsNotNone(db.execute("SELECT rid FROM private_pending WHERE rid=?", (rid,)).fetchone())
                db.execute("PRAGMA user_version=0")
            self.assertTrue(self.client.send_reserved(ENDPOINT["search"], wire, rid=rid)["results"])

    def test_approved_mode_exercises_explicit_sender_offline_after_in_place_policy_upgrade(self):
        price = {"cny_per_usd_upper": "7.5", "fx_source_sha256": hex_id("exchange-fixture"),
                 "fx_verified_epoch": self.config["verified_epoch"], "fx_expiry_epoch": self.config["expiry_epoch"]}
        pristine = GroupBudget(**{**self.config, "path": os.fspath(self.root / "pristine.db")}, cap_policy=price)
        pristine.provision()
        with self.assertRaises(Denied), UnixProviderBroker(self.path, allow_uids=[UID], ledger=pristine,
                                                            provider_mode="approved_unix", approved_sender=fake_response):
            pass
        old, old_wire = self.reserve(private=False)
        snapshot = self.root / "snapshot.db"
        with closing(self.ledger._connect()) as source, closing(sqlite3.connect(snapshot)) as copy:
            source.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
        ledger = GroupBudget(**self.config, cap_policy=price, input_bound=submitted_count)
        upgrade_policy_in_place(self.ledger, ledger, snapshot=snapshot,
                                snapshot_sha=sha256(snapshot.read_bytes()).hexdigest(), writers_stopped=True)
        client = UnixProviderTransport(self.path, broker_uid=UID, provider_mode="approved_unix")
        with running(self.path, ledger, provider_mode="approved_unix", approved_sender=fake_response):
            self.error("unreserved", lambda: client.send_reserved(ENDPOINT["search"], old_wire, rid=old))
            caller = ApprovedGroupCaller(ledger, client)
            self.assertIn("Offline fixture", caller.call(hex_id("seat"), hex_id("group"), "search", hex_id("rid"), SEARCH)["results"][0]["title"])

    def test_rejects_lookalike_endpoint_fetch_and_model_alias(self):
        rid = hex_id("unused")
        for endpoint, wire in (("http://127.0.0.1/model", encode(MODEL_BODY)),
                               (ENDPOINT["fetch"], encode({"url": "https://example.invalid/hidden"})),
                               (ENDPOINT["model"], encode({**MODEL_BODY, "model": "deepseek-chat"})),
                               (ENDPOINT["search"], b"x" * 65537)):
            self.error("invalid_request", lambda: self.client.send_reserved(endpoint, wire, rid=rid))
        for destination in ("https://example.invalid", "127.0.0.1:8080", "\x00abstract", "relative.sock"):
            with self.assertRaises(ValueError):
                UnixProviderTransport(destination, broker_uid=UID)

    def test_socket_mode_symlink_and_peer_checks_do_not_consume_ticket(self):
        rid, wire = self.reserve()
        with running(self.path, self.ledger):
            self.assertEqual(stat.S_IMODE(self.path.lstat().st_mode), 0o660)
            link = self.root / "alternate.sock"
            link.symlink_to(self.path)
            bad = UnixProviderTransport(link, broker_uid=UID)
            self.error("peer_rejected", lambda: bad.send_reserved(ENDPOINT["search"], wire, rid=rid))
            bad = UnixProviderTransport(self.path, broker_uid=UID+1)
            self.error("peer_rejected", lambda: bad.send_reserved(ENDPOINT["search"], wire, rid=rid))
            self.path.chmod(0o666)
            self.error("peer_rejected", lambda: self.client.send_reserved(ENDPOINT["search"], wire, rid=rid))
            self.path.chmod(0o660)
            self.assertTrue(self.client.send_reserved(ENDPOINT["search"], wire, rid=rid)["results"])

    def test_request_hash_mismatch_is_rejected_before_spend_consumption(self):
        rid, wire = self.reserve()
        with running(self.path, self.ledger):
            meta = {**IDENTITY, "provider_mode": "offline_fixture", "op": "search", "rid": rid,
                    "wire_bytes": len(wire), "wire_sha256": "f" * 64}
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(os.fspath(self.path))
                sock.sendall(_frame(meta, 1024) + wire)
                self.assertEqual(_receive(sock, REPLY_LIMIT, monotonic()+2)["error"], "invalid_request")
            self.assertTrue(self.client.send_reserved(ENDPOINT["search"], wire, rid=rid)["results"])

    def test_refuses_to_provision_missing_ledger_or_replace_an_existing_path(self):
        cfg = {**self.config, "path": os.fspath(self.root / "never-created.db")}
        with self.assertRaises(Denied), UnixProviderBroker(self.path, ledger=GroupBudget(**cfg), allow_uids=[UID]):
            pass
        self.assertFalse(Path(cfg["path"]).exists())
        self.path.write_text("existing")
        with self.assertRaises(OSError), UnixProviderBroker(self.path, ledger=self.ledger, allow_uids=[UID]):
            pass
        self.assertEqual(self.path.read_text(), "existing")

    def test_provider_reply_size_and_timeout_leave_charge_preserved(self):
        rid, wire = self.reserve()
        with running(self.path, self.ledger, approved_sender=lambda *_: {"results": ["x" * REPLY_LIMIT]}):
            self.error("invalid_reply", lambda: self.client.send_reserved(ENDPOINT["search"], wire, rid=rid))
        rid, wire = self.reserve(label="two")

        def slow(endpoint, body):
            sleep(.28)
            return fake_response(endpoint, body)

        with running(self.path, self.ledger, approved_sender=slow):
            short = UnixProviderTransport(self.path, broker_uid=UID, timeout=.13)
            self.error("timeout", lambda: short.send_reserved(ENDPOINT["search"], wire, rid=rid))
        with self.ledger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*),sum(nano) FROM spend").fetchone(), (2, 1500000))
            self.assertEqual(db.execute("SELECT count(*) FROM private_pending").fetchone(), (0,))

    def test_fake_cli_opens_only_provisioned_socket_and_cleans_on_term(self):
        rid, wire = self.reserve()
        cfg = self.root / "config.json"
        cfg.write_text(json.dumps(self.config))
        command = [sys.executable, os.fspath(Path(__file__).with_name("private_transport.py")), "--socket",
                   os.fspath(self.path), "--allow-uid", str(UID), "--ledger-config", os.fspath(cfg)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select([process.stdout], [], [], 3)[0])
            self.assertEqual(process.stdout.readline(), b"READY offline Unix provider fixture\n")
            self.assertTrue(self.client.send_reserved(ENDPOINT["search"], wire, rid=rid)["results"])
        finally:
            process.terminate()
            _, errors = process.communicate(timeout=5)
        self.assertEqual((process.returncode, errors), (0, b""))
        self.assertFalse(self.path.exists())

    def cli(self, config, environment, *options):
        config_file = self.root / "operator-config.json"
        config_file.write_text(json.dumps(config))
        arguments = ["private_transport.py", "--socket", os.fspath(self.path), "--allow-uid", str(UID),
                     "--ledger-config", os.fspath(config_file), *options]
        output, error = StringIO(), StringIO()
        with (patch.object(sys, "argv", arguments),
              patch.object(private_transport.os, "environ", environment),
              patch.object(private_transport.signal, "signal"),
              patch.object(UnixProviderBroker, "serve_forever", autospec=True) as serve,
              patch.object(broker_sender, "approved_sender", wraps=broker_sender.approved_sender) as factory,
              patch.object(broker_sender.http.client, "HTTPSConnection", side_effect=AssertionError("unexpected network")) as internet,
              redirect_stdout(output), redirect_stderr(error)):
            try:
                private_transport.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        internet.assert_not_called()
        self.assertNotIn(FIXTURE_KEY, output.getvalue() + error.getvalue())
        self.assertFalse(self.path.exists())
        return code, output.getvalue(), serve, factory

    def migrated_config(self):
        fixed_rate = {"cny_per_usd_upper": "7.5", "fx_source_sha256": hex_id("exchange-cli-fixture"),
                      "fx_verified_epoch": self.config["verified_epoch"], "fx_expiry_epoch": self.config["expiry_epoch"]}
        config = {**self.config, "cap_policy": fixed_rate}
        snapshot = self.root / "cli-snapshot.db"
        with closing(self.ledger._connect()) as source, closing(sqlite3.connect(snapshot)) as copy:
            source.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
        upgrade_policy_in_place(self.ledger, GroupBudget(**config), snapshot=snapshot,
                                snapshot_sha=sha256(snapshot.read_bytes()).hexdigest(), writers_stopped=True)
        return config

    def test_default_cli_does_not_read_environment_key_or_credential_file(self):
        environment = FixtureEnvironment(DEEPSEEK_API_KEY=FIXTURE_KEY)
        code, output, serve, factory = self.cli(self.config, environment, "--credential-file", "/absent.fixture")
        self.assertEqual((code, output), (0, "READY offline Unix provider fixture\n"))
        self.assertEqual(environment.key_reads, 0)
        factory.assert_not_called()
        self.assertEqual(serve.call_args.args[0].mode, "offline_fixture")

    def test_approved_cli_refuses_missing_finance_before_reading_credential(self):
        environment = FixtureEnvironment(DEEPSEEK_API_KEY=FIXTURE_KEY)
        code, output, serve, factory = self.cli(self.config, environment, "--provider-mode", "approved_unix")
        self.assertEqual((code, output, environment.key_reads), (1, "", 0))
        factory.assert_not_called()
        serve.assert_not_called()

    def test_approved_cli_refuses_missing_key_after_finance_is_checked(self):
        environment = FixtureEnvironment()
        code, output, serve, factory = self.cli(self.migrated_config(), environment, "--provider-mode", "approved_unix")
        self.assertEqual((code, output, environment.key_reads), (1, "", 1))
        factory.assert_not_called()
        serve.assert_not_called()

    def test_approved_cli_uses_environment_and_explicit_private_file_without_network(self):
        config = self.migrated_config()
        environment = FixtureEnvironment(DEEPSEEK_API_KEY=FIXTURE_KEY)
        mode = ("--provider-mode", "approved_unix")
        code, output, serve, factory = self.cli(config, environment, *mode)
        self.assertEqual((code, output, environment.key_reads), (0, "READY approved Unix provider broker\n", 1))
        self.assertEqual(serve.call_args.args[0].mode, "approved_unix")
        factory.assert_called_once_with(FIXTURE_KEY)

        credential = self.root / "mimic-systemd-credential"
        credential.write_text(FIXTURE_KEY + "\n")
        credential.chmod(0o400)
        code, output, serve, factory = self.cli(config, environment, *mode, "--credential-file", os.fspath(credential))
        self.assertEqual((code, output, environment.key_reads), (0, "READY approved Unix provider broker\n", 1))
        self.assertEqual(serve.call_args.args[0].mode, "approved_unix")
        factory.assert_called_once_with(FIXTURE_KEY)

        credential.chmod(0o444)
        code, output, serve, factory = self.cli(config, environment, *mode, "--credential-file", os.fspath(credential))
        self.assertEqual((code, output, environment.key_reads), (1, "", 1))
        factory.assert_not_called()
        serve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
