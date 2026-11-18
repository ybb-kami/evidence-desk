"""Bind the existing approved dispatcher to one authenticated filesystem Unix socket.

One connection carries a framed JSON header and the exact approved wire bytes,
followed by one framed JSON response. Dispatch and accounting remain in
ApprovedGroupCaller and GroupBudget. This module's command runs offline fixtures
only. A separately authorized process may explicitly inject a fixed-endpoint
sender, such as broker_sender.approved_sender, without giving the gateway secrets.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import stat
import struct
import threading
from time import monotonic
from types import MappingProxyType

from discovery import academic_url
from group_budget import ENDPOINT, MODEL, NANO, VERSION, Denied, GroupBudget


IDENTITY = MappingProxyType({"version": 1, "provider": "deepseek", "model": VERSION})
DESTINATIONS = MappingProxyType(dict(ENDPOINT))
OPERATIONS = MappingProxyType({url: op for op, url in DESTINATIONS.items()})
HEADER_LIMIT = 1024
WIRE_LIMIT = 65536
REPLY_LIMIT = 524288
ERROR_CODES = {"invalid_request", "invalid_reply", "unavailable", "unreserved"}
PROVIDER_MODES = {"offline_fixture", "approved_unix"}
LENGTH = struct.Struct("!I")
PEER = struct.Struct("=iII")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class PrivateTransportError(OSError):
    """Client error safe to map to the existing no-retry service response."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _uid(value):
    if type(value) is not int or not 0 <= value < (1 << 32) - 1:
        raise ValueError("Require a Linux numeric UID or GID.")
    return value


def _path(value):
    if not hasattr(socket, "SO_PEERCRED"):
        raise ValueError("Linux Unix peer authentication is required.")
    path = Path(value)
    encoded = os.fsencode(path)
    if not path.is_absolute() or not path.name or ".." in path.parts or b"\x00" in encoded or len(encoded) > 107:
        raise ValueError("Require one absolute filesystem Unix socket path of at most 107 bytes.")
    return path


def _directory(path, owner):
    parent = path.parent
    try:
        canonical = parent.resolve(strict=True)
    except RuntimeError:
        raise PrivateTransportError("peer_rejected") from None
    info = parent.lstat()
    if (canonical != parent or not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, owner}
            or info.st_mode & 0o022):
        raise PrivateTransportError("peer_rejected")
    for ancestor in parent.parents:
        mode = ancestor.lstat().st_mode
        if mode & 0o022 and not mode & stat.S_ISVTX:
            raise PrivateTransportError("peer_rejected")


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not .01 <= value <= 120:
        raise ValueError("Timeout must be 0.01 to 120 seconds.")
    return value


def _mode(value):
    if not isinstance(value, str) or value not in PROVIDER_MODES:
        raise ValueError("Unsupported provider mode.")
    return value


def _peer(connection):
    return PEER.unpack(connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, PEER.size))[1]


def _remaining(deadline):
    time = deadline - monotonic()
    if time <= 0:
        raise TimeoutError
    return time


def _read(connection, size, deadline):
    buffer = bytearray()
    while len(buffer) < size:
        connection.settimeout(_remaining(deadline))
        piece = connection.recv(size - len(buffer))
        if not piece:
            raise ValueError("Incomplete frame.")
        buffer.extend(piece)
    return bytes(buffer)


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate JSON field.")
        obj[key] = value
    return obj


def _nonfinite(_):
    raise ValueError("Non-finite JSON value.")


def _decode(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (UnicodeError, RecursionError):
        raise ValueError("Invalid JSON.") from None


def _receive(connection, limit, deadline):
    size, = LENGTH.unpack(_read(connection, LENGTH.size, deadline))
    if not 0 < size <= limit:
        raise ValueError("Invalid frame length.")
    return _decode(_read(connection, size, deadline))


def _frame(obj, limit):
    data = json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(data) <= limit:
        raise ValueError("Invalid frame length.")
    return LENGTH.pack(len(data)) + data


def _send(connection, raw, deadline):
    connection.settimeout(_remaining(deadline))
    connection.sendall(raw)


def _identity(obj):
    if not isinstance(obj, dict) or type(obj.get("version")) is not int or any(
            obj.get(name) != value for name, value in IDENTITY.items()):
        raise ValueError("Wrong protocol identity.")


def _body(op, wire):
    """Enforce the outward destination and supported wire shape at the process boundary."""
    if not isinstance(wire, bytes) or not 0 < len(wire) <= WIRE_LIMIT:
        raise ValueError("Invalid wire length.")
    data = _decode(wire)
    if not isinstance(data, dict):
        raise ValueError("Require an object body.")
    if op == "search":
        if (set(data) != {"query", "count", "need_content"} or not isinstance(data["query"], str)
                or not 1 <= len(data["query"]) <= 128 or not data["query"].strip() or not data["query"].isprintable()
                or type(data["count"]) is not int or not 1 <= data["count"] <= 5 or data["need_content"] is not False):
            raise ValueError("Wrong search body.")
    elif op == "fetch":
        if set(data) != {"url"} or not academic_url(data["url"]):
            raise ValueError("Wrong fetch body.")
    elif op == "model":
        if (set(data) != {"model", "messages", "max_tokens", "stream", "thinking", "response_format"}
                or data["model"] != MODEL or data["stream"] is not False or data["thinking"] != {"type": "disabled"}
                or data["response_format"] != {"type": "json_object"}
                or type(data["max_tokens"]) is not int or not 1 <= data["max_tokens"] <= 512):
            raise ValueError("Wrong model body.")
        messages = data["messages"]
        if not isinstance(messages, list) or len(messages) != 2 or any(
            not isinstance(item, dict) or set(item) != {"role", "content"} or item["role"] != role
            or not isinstance(item["content"], str) or not item["content"]
            for item, role in zip(messages, ("system", "user"))
        ):
            raise ValueError("Require the supported system/user text messages.")
    else:
        raise ValueError("Unknown operation.")
    return data


def _result(op, result):
    if (not isinstance(result, dict) or op == "search" and not isinstance(result.get("results"), list)
            or op == "fetch" and not isinstance(result.get("content"), str)
            or op == "model" and not isinstance(result.get("choices"), list)):
        raise ValueError("Wrong provider result type.")
    return result


class UnixProviderTransport:
    """Callable with the existing ApprovedGroupCaller transport signature."""

    def __init__(self, path, *, broker_uid, timeout=45, provider_mode="offline_fixture"):
        self.path, self.uid, self.timeout = _path(path), _uid(broker_uid), _timeout(timeout)
        self.mode = _mode(provider_mode)

    def __call__(self, endpoint, wire):
        raise PrivateTransportError("reservation_required")

    def send_reserved(self, endpoint, wire, *, rid):
        try:
            op = OPERATIONS[endpoint]
            _body(op, wire)
            if not isinstance(rid, str) or not HEX.fullmatch(rid):
                raise ValueError("Invalid reservation identifier.")
            digest = sha256(wire).hexdigest()
            metadata = {**IDENTITY, "provider_mode": self.mode, "op": op, "wire_sha256": digest, "rid": rid}
            header = _frame({**metadata, "wire_bytes": len(wire)}, HEADER_LIMIT)
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise PrivateTransportError("invalid_request") from None
        deadline = monotonic() + self.timeout
        try:
            _directory(self.path, self.uid)
            info = self.path.lstat()
            if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != self.uid
                    or stat.S_IMODE(info.st_mode) not in (0o600, 0o660)):
                raise PrivateTransportError("peer_rejected")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(_remaining(deadline))
                connection.connect(os.fspath(self.path))
                if _peer(connection) != self.uid:
                    raise PrivateTransportError("peer_rejected")
                _send(connection, header + wire, deadline)
                result = _receive(connection, REPLY_LIMIT, deadline)
            _identity(result)
            if any(result.get(key) != value for key, value in metadata.items()) or type(result.get("ok")) is not bool:
                raise ValueError("Response does not bind the request.")
            expected = {*metadata, "ok"}
            if result["ok"] and set(result) == expected | {"result"}:
                return _result(op, result["result"])
            if result["ok"] is False and set(result) == expected | {"error"} and isinstance(result["error"], str) and result["error"] in ERROR_CODES:
                raise PrivateTransportError(result["error"])
            raise ValueError("Unexpected response fields.")
        except PrivateTransportError:
            raise
        except TimeoutError:
            raise PrivateTransportError("timeout") from None
        except OSError:
            raise PrivateTransportError("unavailable") from None
        except (TypeError, ValueError, UnicodeError):
            raise PrivateTransportError("invalid_reply") from None


def fake_response(endpoint, wire):
    """Return distinctive offline fixtures in the three existing native shapes."""
    op = OPERATIONS[endpoint]
    data = _body(op, wire)
    if op == "search":
        return {"results": [{"url": "https://arxiv.org/abs/1706.03762", "title": "Offline fixture: Attention"}]}
    if op == "fetch":
        return {"content": "Offline fixture. No website was fetched."}
    claims = []
    try:
        documents = json.loads(data["messages"][1]["content"])["documents"]
        refs = [doc[0]["ref"] for doc in documents]
        if 1 <= len(refs) <= 6 and all(isinstance(ref, str) and re.fullmatch(r"E[1-9][0-9]?", ref) for ref in refs):
            text = documents[0][0]["excerpt"] if len(refs) == 1 else "Offline fixture for protocol verification."
            if isinstance(text, str) and 1 <= len(text.strip()) <= 600:
                claims = [{"text": text, "refs": refs}]
    except (KeyError, TypeError, ValueError, IndexError, RecursionError):
        pass
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"claims": claims})}}],
            "offline_fixture": True}


def _check_ledger(ledger, db, mode):
    ledger._time()
    ledger._check_cap_marker(db, verify_live=True)
    if db.execute("SELECT hash FROM conf").fetchall() != [(ledger.config,)]:
        raise Denied("configuration does not bind ledger")
    if mode == "approved_unix":
        try:
            from legacy_ledger import require_migrated_policy
        except ImportError:
            raise Denied("migration verifier is unavailable") from None
        require_migrated_policy(ledger, db)


def _consume(ledger, rid, op, digest, mode):
    """Commit consumption of an already charged dispatch ticket on the same ledger."""
    with closing(ledger._connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _check_ledger(ledger, db, mode)
        row = db.execute("SELECT op, wire_sha, nano FROM spend WHERE rid=?", (rid,)).fetchone()
        if row != (op, digest, NANO[op]) or db.execute(
                "DELETE FROM private_pending WHERE rid=?", (rid,)).rowcount != 1:
            raise Denied("absent or consumed private reservation")


class UnixProviderBroker:
    """Reference listener; a private, separately approved process can inject a sender."""

    def __init__(self, path, *, allow_uids, ledger, socket_gid=None, timeout=45, workers=4,
                 approved_sender=None, provider_mode="offline_fixture"):
        self.path, self.uid, self.timeout = _path(path), os.geteuid(), _timeout(timeout)
        self.mode = _mode(provider_mode)
        self.allow_uids = frozenset(_uid(uid) for uid in allow_uids)
        self.gid = None if socket_gid is None else _uid(socket_gid)
        if (not self.allow_uids or type(workers) is not int or not 1 <= workers <= 16
                or not isinstance(ledger, GroupBudget)
                or approved_sender is not None and not callable(approved_sender)
                or self.mode == "approved_unix" and (approved_sender is None or ledger.cap_policy is None)):
            raise ValueError("Invalid broker configuration.")
        self.ledger = ledger
        self.workers = workers
        self.sender = fake_response if approved_sender is None else approved_sender
        self.capacity = threading.BoundedSemaphore(workers)
        self.stopping = threading.Event()
        self.listener = self.pool = self.inode = None

    def __enter__(self):
        with closing(self.ledger._connect()) as db, db:
            db.execute("BEGIN")
            _check_ledger(self.ledger, db, self.mode)
        _directory(self.path, self.uid)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.bind(os.fspath(self.path))
            info = self.path.lstat()
            self.inode = (info.st_dev, info.st_ino)
            if self.gid is not None:
                os.chown(self.path, -1, self.gid, follow_symlinks=False)
            os.chmod(self.path, 0o660, follow_symlinks=False)
            self.listener.listen(self.workers)
            self.listener.settimeout(.2)
            self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="private-provider")
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.stopping.set()
        if self.listener is not None:
            self.listener.close()
        if self.pool is not None:
            self.pool.shutdown(wait=True)
        if self.inode is not None:
            try:
                info = self.path.lstat()
                if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass

    def serve_forever(self):
        if self.listener is None or self.pool is None:
            raise RuntimeError("Open the broker before serving.")
        while not self.stopping.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self.stopping.is_set():
                    break
                raise
            if not self.capacity.acquire(blocking=False):
                connection.close()
                continue
            try:
                self.pool.submit(self._serve, connection, monotonic() + self.timeout)
            except RuntimeError:
                connection.close()
                self.capacity.release()
                if not self.stopping.is_set():
                    raise

    def _serve(self, connection, deadline):
        try:
            with connection:
                if _peer(connection) not in self.allow_uids:
                    return
                header = _receive(connection, HEADER_LIMIT, deadline)
                _identity(header)
                if (set(header) != {*IDENTITY, "op", "wire_sha256", "wire_bytes", "rid", "provider_mode"}
                        or header["provider_mode"] != self.mode):
                    return
                op, digest, size = header["op"], header["wire_sha256"], header["wire_bytes"]
                rid = header["rid"]
                if (not isinstance(op, str) or op not in DESTINATIONS or not isinstance(digest, str)
                        or not HEX.fullmatch(digest) or type(size) is not int or not 0 < size <= WIRE_LIMIT
                        or not isinstance(rid, str) or not HEX.fullmatch(rid)):
                    return
                meta = {**IDENTITY, "op": op, "wire_sha256": digest, "rid": rid, "provider_mode": self.mode}
                wire = _read(connection, size, deadline)
                try:
                    if sha256(wire).hexdigest() != digest:
                        raise ValueError("Unbound wire.")
                    _body(op, wire)
                except (ValueError, UnicodeError):
                    reply = _frame({**meta, "ok": False, "error": "invalid_request"}, REPLY_LIMIT)
                else:
                    try:
                        _remaining(deadline)
                        _consume(self.ledger, rid, op, digest, self.mode)
                    except (Denied, sqlite3.Error, OSError):
                        reply = _frame({**meta, "ok": False, "error": "unreserved"}, REPLY_LIMIT)
                    else:
                        try:
                            _remaining(deadline)
                            self.ledger._time()
                            result = self.sender(DESTINATIONS[op], wire)
                        except Exception:
                            reply = _frame({**meta, "ok": False, "error": "unavailable"}, REPLY_LIMIT)
                        else:
                            try:
                                result = _result(op, result)
                                reply = _frame({**meta, "ok": True, "result": result}, REPLY_LIMIT)
                            except (TypeError, ValueError, UnicodeError, RecursionError):
                                reply = _frame({**meta, "ok": False, "error": "invalid_reply"}, REPLY_LIMIT)
                _send(connection, reply, deadline)
        except (OSError, ValueError):
            pass
        finally:
            self.capacity.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--allow-uid", required=True, type=int, action="append")
    parser.add_argument("--socket-gid", type=int)
    parser.add_argument("--timeout", default=45, type=float)
    parser.add_argument("--ledger-config", required=True, type=Path)
    args = parser.parse_args()

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        metadata = args.ledger_config.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096 or metadata.st_mode & 0o022:
            raise ValueError("Invalid explicit ledger configuration file.")
        with args.ledger_config.open("rb") as stream:
            raw = stream.read(4097)
        config = _decode(raw) if len(raw) <= 4096 else None
        fields = {"path", "approval_sha", "price_sha", "verified_epoch", "expiry_epoch", "seat_groups"}
        if (not isinstance(config, dict) or set(config) not in (fields, fields | {"cap_policy"}, fields | {"cap_policy","opening_evidence_sha256"})
                or not Path(config["path"]).is_absolute()):
            raise ValueError("Invalid explicit ledger configuration.")
        with UnixProviderBroker(args.socket, allow_uids=args.allow_uid, socket_gid=args.socket_gid,
                                timeout=args.timeout, ledger=GroupBudget(**config)) as broker:
            print("READY offline Unix provider fixture", flush=True)
            broker.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, TypeError, sqlite3.Error):
        parser.exit(1, "Could not start offline Unix provider fixture.\n")


if __name__ == "__main__":
    main()
