"""Download one PDF from the fixed arXiv endpoint in an isolated worker."""

import re
import subprocess
import sys
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ENDPOINT = "https://arxiv.org/pdf/"
MAX_BYTES = 24 * 1024 * 1024
ID = re.compile(
    r"(?:[0-9]{2}(?:0[1-9]|1[0-2])\.[0-9]{4,5}|[a-z]+(?:[.-][a-z]+)*/[0-9]{2}(?:0[1-9]|1[0-2])[0-9]{3})(?:v[1-9][0-9]{0,5})?",
    re.ASCII,
)
DEADLINE = 18
NETWORK_TIMEOUT = 8

UNAVAILABLE = 2
NOT_FOUND = 3
TOO_LARGE = 4
INVALID_PDF = 5
REDIRECT = 6
BUSY = 7


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_id(value):
    return isinstance(value, str) and len(value) <= 64 and ID.fullmatch(value) is not None


def _open(request, timeout):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def _download(identifier):
    if not valid_id(identifier):
        return UNAVAILABLE, b""
    url = ENDPOINT + identifier
    request = Request(url, headers={"Accept": "application/pdf", "User-Agent": "EvidenceDesk/0.4"})
    try:
        with _open(request, timeout=NETWORK_TIMEOUT) as response:
            if response.geturl() != url:
                return REDIRECT, b""
            if response.status != 200:
                return UNAVAILABLE, b""
            if response.headers.get_content_type() not in ("application/pdf", "application/octet-stream"):
                return INVALID_PDF, b""
            length = response.headers.get("Content-Length")
            if length is not None:
                if not re.fullmatch(r"[0-9]{1,12}", length):
                    return INVALID_PDF, b""
                if int(length) > MAX_BYTES:
                    return TOO_LARGE, b""
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                return TOO_LARGE, b""
            if length is not None and len(data) != int(length):
                return INVALID_PDF, b""
            if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
                return INVALID_PDF, b""
            return 0, data
    except HTTPError as error:
        error.close()
        if error.code in (404, 410):
            return NOT_FOUND, b""
        if error.code == 429:
            return BUSY, b""
        if 300 <= error.code < 400:
            return REDIRECT, b""
        return UNAVAILABLE, b""
    except (OSError, ValueError, HTTPException):
        return UNAVAILABLE, b""


def retrieve(identifier):
    if not valid_id(identifier):
        return UNAVAILABLE, b""
    try:
        worker = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), identifier],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=DEADLINE, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return UNAVAILABLE, b""
    if worker.returncode != 0:
        return worker.returncode, b""
    if len(worker.stdout) > MAX_BYTES:
        return TOO_LARGE, b""
    return 0, worker.stdout


if __name__ == "__main__":
    status, body = _download(sys.argv[1]) if len(sys.argv) == 2 else (UNAVAILABLE, b"")
    if status == 0:
        sys.stdout.buffer.write(body)
    raise SystemExit(status)
