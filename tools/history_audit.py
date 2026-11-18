"""Offline credential-pattern/forbidden-path screen for reachable Git history.
Prints paths and rule names only. Does not certify the absence of secrets.
"""
import re, subprocess, sys
from pathlib import PurePosixPath
def git(*args): return subprocess.check_output(["git", *args])
patterns={
 "private-key":rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
 "github-token":rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
 "model-token":rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{28,}\b",
 "aws-access":rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
}
versions=git("rev-list","--all").decode().splitlines()
seen=set(); issues=set()
for commit in versions:
    for entry in git("ls-tree","-rz",commit).split(b"\0"):
        if not entry: continue
        attrs,name=entry.split(b"\t",1)
        _,kind,oid=attrs.split()
        path=name.decode("utf8","replace"); p=PurePosixPath(path)
        if (p.name.startswith(".env") or p.suffix in {".sqlite",".db",".pem",".p12",".key"} or "data" in p.parts):
            issues.add((path,"unexpected-sensitive-path"))
        if kind!=b"blob" or oid in seen: continue
        seen.add(oid)
        data=git("cat-file","blob",oid.decode())
        for rule,reg in patterns.items():
            if re.search(reg,data): issues.add((path,rule))
print(f"{len(versions)} reachable commits; {len(seen)} unique blobs; {len(issues)} findings.")
for path,rule in sorted(issues): print(f"{rule}: {path}")
sys.exit(bool(issues))
