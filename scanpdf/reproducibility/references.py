"""Extract public repository references and keep exact indexed PDF anchors."""

from hashlib import sha256
import re
import unicodedata

OWNER = r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?"
REPO = r"[a-z0-9_.-]{1,100}"
SHA = r"[0-9a-f]{40}"
LINE_BREAK = r"(?:[ \t]*\r?\n[ \t]{0,12})?"
MARKER = r"(?:^[ \t]*[0-9]{1,3}[ \t]*)?"
REPOSITORY = re.compile(
    rf"(?<![a-z0-9_.@/:\-]){MARKER}"
    rf"(?P<url>(?:https?://)?(?:www\.)?github\.com/{LINE_BREAK}"
    rf"(?P<owner>{OWNER})/{LINE_BREAK}(?P<repo>{REPO})(?![a-z0-9_.%~-])"
    rf"(?:/(?:blob|tree)/(?P<commit>{SHA})(?=/|[\s?#),;.]|$))?)",
    re.I | re.ASCII | re.M,
)
REFERENCE_ID = re.compile(r"[0-9a-f]{24}")
MAX_REFERENCES = 24


def identity(owner, repo):
    return isinstance(owner, str) and isinstance(repo, str) and bool(
        re.fullmatch(OWNER, owner, re.I | re.ASCII) and re.fullmatch(REPO, repo, re.I | re.ASCII)
        and repo not in {".", ".."}
    )


def _projection(source):
    # Some born-digital PDFs expose "ﬂ" in displayed URLs as a ligature.
    parts, offsets = [], []
    for index, char in enumerate(source):
        converted = unicodedata.normalize("NFKC", char)
        parts.append(converted)
        offsets.extend([index] * len(converted))
    return "".join(parts), offsets


def find_references(doc):
    results, seen = [], set()
    for chunk in doc["chunks"]:
        source = chunk["text"]
        if "github.com" not in unicodedata.normalize("NFKC", source).casefold():
            continue
        projected, offsets = _projection(source)
        for match in REPOSITORY.finditer(projected):
            owner, repo, commit = match["owner"], match["repo"].rstrip("."), match["commit"]
            if repo.lower().endswith(".git"):
                repo = repo[:-4]
            if not identity(owner, repo):
                continue
            commit = commit.lower() if commit else None
            key = owner.lower(), repo.lower(), commit
            if key in seen:
                continue
            seen.add(key)
            if len(results) == MAX_REFERENCES:
                return results, True
            start, end = match.span("url")
            start, end = offsets[start], offsets[end - 1] + 1
            page, block, part = (chunk[k] for k in ("page", "block", "part"))
            reference_id = sha256(f"{doc['id']}:{page}:{block}:{part}:{start}:{end}".encode()).hexdigest()[:24]
            results.append({"id": reference_id, "owner": owner, "repo": repo, "cited_commit": commit,
                            "label": f"{owner}/{repo}", "url": f"https://github.com/{owner}/{repo}",
                            "citation": {"doc_sha256": doc["id"], "page": page, "block": block,
                                         "part": part, "start": start, "end": end, "exact": source[start:end],
                                         "context": source[max(0, start - 100):min(len(source), end + 100)]}})
    return results, False
