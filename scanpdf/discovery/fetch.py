"""Download one PDF from a supported publisher endpoint in an isolated worker."""

import os
import re
import subprocess
import sys
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies

ENDPOINT = "https://arxiv.org/pdf/"
ACL_ENDPOINT = "https://aclanthology.org/"
JOSS_ENDPOINT = "https://raw.githubusercontent.com/openjournals/joss-papers/master/"
JOSS_DOI = re.compile(r"10\.21105/joss\.([0-9]{5,6})", re.ASCII | re.IGNORECASE)
ACL_DOI = re.compile(
    r"10\.18653/v1/([a-z][0-9]{2}-[0-9]{4,6}|[0-9]{4}\.[a-z0-9]+(?:-[a-z0-9]+)*\.[0-9]{1,6})",
    re.ASCII | re.IGNORECASE,
)
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


def acl_doi(value):
    """Return a canonical DOI for a supported ACL Anthology article."""
    match = ACL_DOI.fullmatch(value) if isinstance(value, str) and len(value) <= 100 else None
    if match is None:
        return None
    identifier = match[1]
    identifier = identifier.upper() if identifier[0].isalpha() else identifier.lower()
    return "10.18653/v1/" + identifier


def joss_doi(value):
    match = JOSS_DOI.fullmatch(value) if isinstance(value, str) else None
    if match and int(match[1]):
        return "10.21105/joss." + match[1]
    return None


def _joss_stem(doi):
    identifier = doi.rsplit("/", 1)[1]
    return JOSS_ENDPOINT + identifier + "/10.21105." + identifier


def joss_metadata_url(identifier):
    doi = joss_doi(identifier)
    return _joss_stem(doi) + ".crossref.xml" if doi else None


def pdf_url(identifier, provider):
    if provider == "arxiv" and valid_id(identifier):
        return ENDPOINT + identifier
    if provider == "acl" and (doi := acl_doi(identifier)):
        return ACL_ENDPOINT + doi.rsplit("/", 1)[1] + ".pdf"
    if provider == "joss" and (doi := joss_doi(identifier)):
        return _joss_stem(doi) + ".pdf"
    return None


def _open(request, timeout):
    proxies = {}
    if (request.full_url.startswith(JOSS_ENDPOINT)
            and os.environ.get("EVIDENCE_JOSS_USE_SYSTEM_PROXY") == "1"):
        proxy = getproxies().get("https", "")
        parsed = urlsplit(proxy)
        if parsed.scheme in ("http", "https") and parsed.hostname and parsed.username is None:
            proxies["https"] = proxy
    return build_opener(ProxyHandler(proxies), NoRedirect()).open(request, timeout=timeout)


def _download(identifier, provider="arxiv"):
    url = pdf_url(identifier, provider)
    if url is None:
        return UNAVAILABLE, b""
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


def retrieve(identifier, provider="arxiv"):
    if pdf_url(identifier, provider) is None:
        return UNAVAILABLE, b""
    try:
        worker = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), identifier, provider],
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
    status, body = _download(*sys.argv[1:]) if len(sys.argv) in (2, 3) else (UNAVAILABLE, b"")
    if status == 0:
        sys.stdout.buffer.write(body)
    raise SystemExit(status)
