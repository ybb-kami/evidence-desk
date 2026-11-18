"""Bounded metadata requests to the public GitHub API."""

from datetime import datetime, timezone
from http.client import HTTPException
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies

from .references import SHA, identity

API_ROOT = "https://api.github.com"
BODY_LIMIT = 2_000_000
TIMEOUT = 6
TOTAL_SECONDS = 22
MAX_FILES_PER_CATEGORY = 6
SCRIPT = re.compile(r"\.(?:py|sh|ipynb|jl|r|js|ts|scala|java|cpp|rs)$", re.I)
ENVIRONMENT = re.compile(
    r"requirements(?:[-_.][^/]{1,70})?\.txt|environment(?:[-_.][^/]{1,70})?\.ya?ml|"
    r"(?:conda|pixi)\.ya?ml|pyproject\.(?:toml|yaml)|setup\.(?:py|cfg)|pipfile(?:\.lock)?|"
    r"poetry\.lock|uv\.lock|dockerfile(?:\.[^/]+)?|containerfile|package(?:-lock)?\.json|cargo\.toml|"
    r"gemfile(?:\.lock)?|go\.mod|project\.toml",
    re.I,
)
README = re.compile(r"readme(?:\.[a-z0-9]{1,12})?", re.I)
LICENSE = re.compile(r"(?:licen[sc]e|copying)(?:[-_.][a-z0-9_.-]{1,30})?", re.I)
TRAIN = re.compile(r"(?:^|[-_])(?:train(?:ing)?|pretrain(?:ing)?|fine[-_]?tun(?:e|ing))(?=[-_.]|$)", re.I)
EVALUATE = re.compile(r"(?:^|[-_])(?:eval(?:uate|uation)?|benchmark|score|predict|inference)(?=[-_.]|$)", re.I)
TRACKED_TYPES = {"100644", "100755"}
CATEGORIES = ("license", "readme", "environment", "training", "evaluation")


class CheckError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        return None


def public_https_proxies():
    configured = getproxies().get("https", "")
    if not configured:
        return {}
    try:
        proxy = urlsplit(configured)
    except ValueError:
        raise CheckError("invalid_proxy") from None
    if proxy.scheme not in {"http", "https"} or not proxy.hostname or proxy.username is not None:
        raise CheckError("invalid_proxy")
    return {"https":configured}


def _fetch(path, *, remaining):
    if not path.startswith("/repos/") or remaining <= 0:
        raise CheckError("timeout")
    request = Request(API_ROOT + path, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "EvidenceDesk-Reproducibility/1",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        deadline = time.monotonic() + remaining
        with build_opener(ProxyHandler(public_https_proxies()), _NoRedirect()).open(request, timeout=min(TIMEOUT, remaining)) as response:
            payload = bytearray()
            while len(payload) <= BODY_LIMIT:
                if time.monotonic() >= deadline:
                    raise CheckError("timeout")
                part = response.read1(min(65_536, BODY_LIMIT + 1 - len(payload)))
                if not part:
                    break
                payload.extend(part)
    except HTTPError as error:
        with error:
            if 300 <= error.code < 400:
                raise CheckError("redirect") from None
            if error.code == 404:
                raise CheckError("not_found") from None
            if error.code == 429 or (error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"):
                raise CheckError("rate_limited") from None
            if error.code == 403:
                raise CheckError("forbidden") from None
            raise CheckError("unavailable") from None
    except TimeoutError:
        raise CheckError("timeout") from None
    except URLError as error:
        if isinstance(error.reason, OSError) and re.fullmatch(r"Tunnel connection failed: 403(?: [^\r\n]+)?", str(error.reason)):
            raise CheckError("forbidden") from None
        raise CheckError("timeout" if isinstance(error.reason, TimeoutError) else "unavailable") from None
    except (OSError, HTTPException):
        raise CheckError("unavailable") from None
    if len(payload) > BODY_LIMIT:
        raise CheckError("response_limit")
    try:
        decoded = json.loads(payload)
    except ValueError:
        raise CheckError("invalid_response") from None
    if not isinstance(decoded, dict):
        raise CheckError("invalid_response")
    return decoded


def _git_id(value):
    if not isinstance(value, str) or not re.fullmatch(SHA, value, re.I):
        raise CheckError("invalid_response")
    return value.lower()


def _tree_path(path):
    return isinstance(path, str) and 0 < len(path) <= 600 and path.isprintable() and all(
        segment not in {"", ".", ".."} for segment in path.split("/")
    ) and "\\" not in path


def _groups(tree, repository, commit, complete):
    matches = {name: [] for name in CATEGORIES}
    for entry in tree:
        if not isinstance(entry, dict) or entry.get("type") != "blob" or entry.get("mode") not in TRACKED_TYPES:
            continue
        path = entry.get("path")
        sha = entry.get("sha")
        if not _tree_path(path) or not isinstance(sha, str) or not re.fullmatch(SHA, sha, re.I):
            continue
        filename = path.rsplit("/", 1)[-1]
        categories = []
        if "/" not in path and LICENSE.fullmatch(filename):
            categories.append("license")
        if README.fullmatch(filename):
            categories.append("readme")
        if ENVIRONMENT.fullmatch(filename):
            categories.append("environment")
        if SCRIPT.search(filename):
            if TRAIN.search(filename):
                categories.append("training")
            if EVALUATE.search(filename):
                categories.append("evaluation")
        for kind in categories:
            matches[kind].append({"path": path, "blob_sha": sha.lower(),
                                  "url": f"{repository}/blob/{commit}/{quote(path, safe='/')}"})

    groups = {}
    for key, files in matches.items():
        files = sorted({item["path"]: item for item in files}.values(),
                       key=lambda item: (item["path"].count("/"), item["path"].casefold()))
        groups[key] = {"status": "found" if files else "not_found_in_tree" if complete else "tree_incomplete",
                       "count": len(files), "files": files[:MAX_FILES_PER_CATEGORY]}
    return groups


def inspect(owner, repo, cited_commit=None, *, fetch=None):
    if not identity(owner, repo):
        raise CheckError("invalid_repository")
    if cited_commit is not None:
        cited_commit = _git_id(cited_commit)
    deadline = time.monotonic() + TOTAL_SECONDS
    fetch = fetch or _fetch
    base = f"/repos/{owner}/{repo}"

    def get(path):
        return fetch(path, remaining=deadline - time.monotonic())

    data = get(base)
    if data.get("private") is not False or data.get("visibility", "public") != "public":
        raise CheckError("not_found")
    full_name = data.get("full_name")
    if not isinstance(full_name, str) or full_name.casefold() != f"{owner}/{repo}".casefold():
        raise CheckError("repository_mismatch")
    default_branch = data.get("default_branch")
    if (not isinstance(default_branch, str) or not (0 < len(default_branch) <= 140)
            or not default_branch.isprintable() or ".." in default_branch or default_branch == "."):
        raise CheckError("invalid_response")
    resolved = get(f"{base}/commits/{quote(cited_commit or default_branch, safe='')}")
    commit = _git_id(resolved.get("sha"))
    if cited_commit and cited_commit != commit:
        raise CheckError("commit_mismatch")
    metadata = resolved.get("commit")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("tree"), dict):
        raise CheckError("invalid_response")
    tree_sha = _git_id(metadata["tree"].get("sha"))
    path = f"{base}/git/trees/{tree_sha}"
    complete = True
    try:
        tree = get(path + "?recursive=1")
    except CheckError as error:
        if error.code != "response_limit":
            raise
        tree = get(path)
        complete = False
    if _git_id(tree.get("sha")) != tree_sha or not isinstance(tree.get("tree"), list):
        raise CheckError("invalid_response")
    complete = complete and tree.get("truncated") is False
    repository = f"https://github.com/{full_name}"
    groups = _groups(tree["tree"], repository, commit, complete)
    license_info = data.get("license") if isinstance(data.get("license"), dict) else {}
    spdx = license_info.get("spdx_id")
    if not isinstance(spdx, str) or not re.fullmatch(r"[A-Za-z0-9.+-]{1,40}", spdx) or spdx == "NOASSERTION":
        spdx = None
    checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"status": "checked", "checked_at": checked, "repo": full_name, "default_branch": default_branch,
            "commit": commit, "commit_url": f"{repository}/commit/{commit}", "tree_sha": tree_sha,
            "basis": "cited_commit" if cited_commit else "default_branch_at_check",
            "tree_complete": complete, "api_repository_spdx": spdx, "groups": groups}
