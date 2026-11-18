"""Read public Git references and fixed raw files without downloading a repository."""

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha1, sha256
from http.client import HTTPException
import json
import re
import sqlite3
from time import monotonic, time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from .github import CATEGORIES, ENVIRONMENT, LICENSE, README, SCRIPT, CheckError, _NoRedirect, public_https_proxies
from .references import SHA, identity


GIT_ROOT = "https://github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
GIT_TYPE = "application/x-git-upload-pack-advertisement"
RAW_TYPES = frozenset({"text/plain", "application/octet-stream"})
MAX_BYTES = 256 * 1024
MAX_FILES = 10
TOTAL_SECONDS = 30
MAX_STORED = 64
_BRANCH = re.compile(rb"refs/heads/[a-zA-Z0-9_./-]{1,140}\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_.+-]{1,120}\Z")


def _open(request, timeout):
    return build_opener(ProxyHandler(public_https_proxies()), _NoRedirect()).open(request, timeout=timeout)


def _read(url, content_types, deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise CheckError("timeout")
    request = Request(url, headers={"Accept": ", ".join(sorted(content_types)),
                                    "User-Agent": "EvidenceDesk-RepositorySnapshot/1"})
    try:
        with _open(request, min(6, remaining)) as response:
            media = response.headers.get_content_type()
            if response.status != 200 or response.geturl() != url or media not in content_types:
                raise CheckError("invalid_response")
            data = bytearray()
            while len(data) <= MAX_BYTES:
                if monotonic() >= deadline:
                    raise CheckError("timeout")
                part = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
    except HTTPError as error:
        with error:
            if error.code in (403, 429):
                raise CheckError(f"http_{error.code}") from None
            if error.code == 404:
                raise CheckError("not_found") from None
            if 300 <= error.code < 400:
                raise CheckError("redirect") from None
            raise CheckError("unavailable") from None
    except TimeoutError:
        raise CheckError("timeout") from None
    except URLError as error:
        raise CheckError("timeout" if isinstance(error.reason, TimeoutError) else "unavailable") from None
    except (OSError, HTTPException):
        raise CheckError("unavailable") from None
    if len(data) > MAX_BYTES:
        raise CheckError("response_limit")
    return bytes(data), media


def _packets(data):
    parts = []
    at = 0
    while at < len(data):
        prefix = data[at:at + 4]
        if not re.fullmatch(rb"[0-9a-fA-F]{4}", prefix):
            raise CheckError("invalid_git_advertisement")
        size = int(prefix, 16)
        if size == 0:
            parts.append(b"")
            at += 4
        elif 4 < size <= 65520 and at + size <= len(data):
            parts.append(data[at + 4:at + size])
            at += size
        else:
            raise CheckError("invalid_git_advertisement")
    return parts


def advertised_head(data):
    packets = _packets(data)
    if len(packets) < 5 or packets[:2] != [b"# service=git-upload-pack\n", b""] or packets[-1] != b"":
        raise CheckError("invalid_git_advertisement")
    reference, separator, capabilities = packets[2].strip().partition(b"\x00")
    head = re.fullmatch(rb"([a-f0-9]{40}) HEAD", reference)
    symbolic = [value[len(b"symref=HEAD:"):] for value in capabilities.split() if value.startswith(b"symref=HEAD:")]
    if not separator or not head or len(symbolic) != 1 or not _BRANCH.fullmatch(symbolic[0]) or b".." in symbolic[0]:
        raise CheckError("invalid_git_advertisement")
    branch = symbolic[0]
    refs = {}
    for packet in packets[3:-1]:
        line = re.fullmatch(rb"([a-f0-9]{40}) (refs/\S+)\n?", packet)
        if not line or line[2] in refs:
            raise CheckError("invalid_git_advertisement")
        refs[line[2]] = line[1]
    if refs.get(branch) != head[1]:
        raise CheckError("invalid_git_advertisement")
    return {"default_branch":branch[len(b"refs/heads/"):].decode("ascii"), "commit":head[1].decode("ascii"),
            "advertised":{value.decode("ascii") for value in refs.values()}}


def _candidate(category, path):
    if not isinstance(path, str) or len(path) > 360:
        raise CheckError("invalid_candidate")
    pieces = path.split("/")
    if any(not _SEGMENT.fullmatch(part) or part.casefold() in {".", "..", ".git"} for part in pieces):
        raise CheckError("invalid_candidate")
    name = pieces[-1]
    accepted = {"license": lambda: len(pieces) == 1 and LICENSE.fullmatch(name),
                "readme": lambda: README.fullmatch(name), "environment": lambda: ENVIRONMENT.fullmatch(name),
                "training": lambda: SCRIPT.search(name), "evaluation": lambda: SCRIPT.search(name)}
    if category not in accepted or not accepted[category]():
        raise CheckError("invalid_candidate")


def inspect_reference(reference, requested, *, read=None):
    owner, repo, cited = (reference[key] for key in ("owner", "repo", "cited_commit"))
    if not identity(owner, repo) or (cited is not None and not re.fullmatch(SHA, cited)):
        raise CheckError("invalid_repository")
    candidates = list(dict.fromkeys(tuple(item) for item in requested))
    if not 1 <= len(candidates) <= MAX_FILES:
        raise CheckError("candidate_limit")
    for category, path in candidates:
        _candidate(category, path)
    fetch = read or _read
    deadline = monotonic() + TOTAL_SECONDS
    repository = f"{GIT_ROOT}/{owner}/{repo}"
    git_url = repository + ".git/info/refs?service=git-upload-pack"
    advertisement, _ = fetch(git_url, {GIT_TYPE}, deadline)
    head = advertised_head(advertisement)
    if cited and cited not in head["advertised"]:
        raise CheckError("cited_commit_not_advertised")
    commit = cited or head["commit"]
    groups = {name:{"files":[], "count":0, "status":"not_requested"} for name in CATEGORIES}
    unavailable = []
    for category, path in candidates:
        group = groups[category]
        if group["status"] == "not_requested":
            group["status"] = "unavailable"
        raw_url = f"{RAW_ROOT}/{owner}/{repo}/{commit}/{quote(path, safe='/')}"
        try:
            content, media = fetch(raw_url, RAW_TYPES, deadline)
            if b"\x00" in content:
                raise CheckError("invalid_response")
            content.decode("utf-8")
        except UnicodeError:
            unavailable.append({"category":category, "path":path, "reason":"invalid_response"})
        except CheckError as error:
            if error.code in {"http_403", "http_429", "invalid_proxy", "timeout", "unavailable", "redirect"}:
                raise
            unavailable.append({"category":category, "path":path, "reason":error.code})
        else:
            blob = sha1(f"blob {len(content)}\0".encode() + content, usedforsecurity=False).hexdigest()
            group["files"].append({"path":path, "sha256":sha256(content).hexdigest(), "bytes":len(content),
                                   "blob_sha":blob, "media_type":media, "raw_url":raw_url,
                                   "url":f"{repository}/blob/{commit}/{quote(path, safe='/')}"})
            group["count"] += 1
            group["status"] = "found"
    if not any(group["files"] for group in groups.values()):
        raise CheckError("no_files_fetched")
    return {"status":"snapshot", "schema":"evidence-desk-github-snapshot-v1",
            "checked_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo":f"{owner}/{repo}", "commit":commit, "commit_url":f"{repository}/commit/{commit}",
            "default_branch":head["default_branch"], "default_head":head["commit"],
            "basis":"cited_advertised_commit" if cited else "default_branch_at_fetch", "groups":groups,
            "unavailable":unavailable,
            "git_advertisement":{"url":git_url,"sha256":sha256(advertisement).hexdigest(),"bytes":len(advertisement)}}


def save(library, docid, refid, result):
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = sha256(payload.encode()).hexdigest()
    with closing(sqlite3.connect(library.db)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS repository_snapshot_v1 "
                   "(doc_id TEXT, ref_id TEXT, saved INTEGER, payload TEXT, digest TEXT, PRIMARY KEY(doc_id,ref_id))")
        db.execute("INSERT INTO repository_snapshot_v1 VALUES(?,?,?,?,?) ON CONFLICT(doc_id,ref_id) "
                   "DO UPDATE SET saved=excluded.saved,payload=excluded.payload,digest=excluded.digest",
                   (docid, refid, int(time()), payload, digest))
        db.execute("DELETE FROM repository_snapshot_v1 WHERE rowid IN (SELECT rowid FROM repository_snapshot_v1 "
                   "ORDER BY saved DESC,rowid DESC LIMIT -1 OFFSET ?)", (MAX_STORED,))
    return digest


def load_for(library, docid):
    with closing(sqlite3.connect(library.db)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repository_snapshot_v1'").fetchone()
        if not exists:
            return {}
        rows = db.execute("SELECT ref_id,payload,digest FROM repository_snapshot_v1 WHERE doc_id=?", (docid,)).fetchall()
    snapshots = {}
    for refid, payload, digest in rows:
        if not isinstance(payload, str) or sha256(payload.encode()).hexdigest() != digest:
            continue
        try:
            result = json.loads(payload)
        except (ValueError, RecursionError):
            continue
        if isinstance(result, dict) and result.get("schema") == "evidence-desk-github-snapshot-v1":
            snapshots[refid] = result | {"record_sha256":digest}
    return snapshots


def matches_reference(result, reference):
    """Keep displayed links bound to the same repository, commit, and HTTPS origins."""
    repo = result.get("repo")
    if not isinstance(repo, str) or repo.casefold() != reference["label"].casefold():
        return False
    commit = result.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(SHA, commit) or reference["cited_commit"] not in (None, commit):
        return False
    name = reference["label"]
    repository = f"{GIT_ROOT}/{name}"
    try:
        if (result["commit_url"] != f"{repository}/commit/{commit}"
                or result["git_advertisement"]["url"] != f"{repository}.git/info/refs?service=git-upload-pack"
                or set(result["groups"]) != set(CATEGORIES) or not isinstance(result["unavailable"], list)):
            return False
        for category, group in result["groups"].items():
            if not isinstance(group["files"], list):
                return False
            for file in group["files"]:
                _candidate(category, file["path"])
                path = quote(file["path"], safe="/")
                if (file["url"] != f"{repository}/blob/{commit}/{path}"
                        or file["raw_url"] != f"{RAW_ROOT}/{name}/{commit}/{path}"):
                    return False
    except (TypeError, KeyError, AttributeError, CheckError):
        return False
    return True
