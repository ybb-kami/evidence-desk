"""Read-only: verify the two genuine histories exist before integration."""
import argparse
import subprocess
import sys

BASE = "a6b6d27"
LOCAL = "4d05cb80fa2abf8e0297d39ede8c35218aa108c1"
PARTNER = "c18c3a0f2ce98d2b25d02f5fa25f89ccd0426e61"

def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--partner", default=PARTNER)
    args = p.parse_args()
    for rev in (BASE, LOCAL, args.partner):
        if git("rev-parse", "--verify", "--end-of-options", rev + "^{commit}").returncode:
            print("Missing commit object:", rev, file=sys.stderr)
            return 2
    if any(git("merge-base", "--is-ancestor", BASE, tip).returncode for tip in (LOCAL, args.partner)):
        print("Unexpected ancestry; do not merge.", file=sys.stderr)
        return 3
    common = git("merge-base", LOCAL, args.partner).stdout.strip()
    def names(tip):
        return set(git("diff", "--name-only", "-z", common, tip).stdout.rstrip("\0").split("\0")) - {""}
    overlap = sorted(names(LOCAL) & names(args.partner))
    diverged = [s for s in overlap if git("diff", "--quiet", LOCAL, args.partner, "--", s).returncode]
    print("merge base:", common, "overlap:", len(overlap), "different at tips:", len(diverged))
    for name in diverged: print(name)
    print("Preflight only; no refs, files or permissions were changed.")
    return 0
if __name__ == "__main__":
    sys.exit(main())
