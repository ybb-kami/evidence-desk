"""A bounded, per-library cache for validated dense passage vectors."""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MAX_DISK_BYTES = 64 * 1024**2
MAX_ENTRIES = 128
HEADER_LIMIT = 2048
FORMAT_VERSION = 1
_CACHE_FILE = re.compile(r"[0-9a-f]{64}\.vec\Z")


@contextmanager
def _exclusive(path):
    with path.open("a+b") as lock:
        if os.name == "nt":
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class VectorCache:
    def __init__(self, directory, model, dimensions):
        self.directory = Path(directory).expanduser().absolute()
        self.model = model
        self.dimensions = dimensions
        self._pending = self.directory / ".vectors.pending"

    @contextmanager
    def _locked(self):
        with _exclusive(self.directory / ".vectors.lock"):
            # A writer killed before publication can leave at most this one file.
            self._pending.unlink(missing_ok=True)
            yield

    def _identity(self, key, rows):
        document, fingerprint = key
        return {"format": FORMAT_VERSION, "model": self.model, "document": document,
                "fingerprint": fingerprint.hex(), "shape": [rows, self.dimensions], "dtype": "<f4"}

    def _file(self, identity):
        key = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return self.directory / (sha256(key).hexdigest() + ".vec")

    def _read(self, file, identity, length):
        if not stat.S_ISREG(file.lstat().st_mode):
            return None
        with file.open("rb") as stream:
            header = json.loads(stream.readline(HEADER_LIMIT))
            if not isinstance(header, dict):
                return None
            digest = header.pop("payload_sha256", None)
            if header != identity:
                return None
            payload = stream.read(length + 1)
            if len(payload) != length or sha256(payload).hexdigest() != digest:
                return None
        import numpy as np

        vectors = np.frombuffer(payload, dtype="<f4").reshape(identity["shape"])
        return vectors if np.isfinite(vectors).all() else None

    def load(self, key, rows):
        length = rows * self.dimensions * 4
        if not 0 < length <= MAX_DISK_BYTES:
            return None
        identity = self._identity(key, rows)
        file = self._file(identity)
        try:
            if not self.directory.is_dir():
                return None
            with self._locked():
                try:
                    vectors = self._read(file, identity, length)
                except (ValueError, RecursionError):
                    vectors = None
                except FileNotFoundError:
                    return None
                if vectors is None:
                    file.unlink(missing_ok=True)
                else:
                    try:
                        os.utime(file, None)
                    except OSError:
                        pass
                return vectors
        except (ImportError, OSError):
            # Bad or unavailable cache storage never disables local retrieval.
            return None

    def _make_room(self, length):
        entries = []
        for file in self.directory.iterdir():
            if _CACHE_FILE.fullmatch(file.name):
                attributes = file.lstat()
                if stat.S_ISREG(attributes.st_mode):
                    entries.append((attributes.st_mtime_ns, attributes.st_size, file))
        total = sum(size for _, size, _ in entries)
        count = len(entries)
        for _, size, file in sorted(entries):
            if total + length <= MAX_DISK_BYTES and count < MAX_ENTRIES:
                break
            file.unlink()
            total -= size
            count -= 1

    def save(self, key, rows, vectors):
        import numpy as np

        if vectors.shape != (rows, self.dimensions) or not np.isfinite(vectors).all():
            return
        identity = self._identity(key, rows)
        payload = vectors.astype("<f4", copy=False).tobytes()
        header = json.dumps(identity | {"payload_sha256": sha256(payload).hexdigest()}).encode() + b"\n"
        length = len(header) + len(payload)
        if len(header) >= HEADER_LIMIT or length > MAX_DISK_BYTES:
            return
        file = self._file(identity)
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self._locked():
                try:
                    if self._read(file, identity, len(payload)) is not None:
                        os.utime(file, None)
                        return
                except (FileNotFoundError, ValueError, RecursionError):
                    pass
                self._make_room(length)
                try:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                    fd = os.open(self._pending, flags, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(header)
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(self._pending, file)
                finally:
                    self._pending.unlink(missing_ok=True)
        except OSError:
            pass
