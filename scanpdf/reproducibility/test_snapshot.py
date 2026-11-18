"""Validate public Git advertisement, scope, hash, and refusal behavior without network."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pymupdf

import workbench
from reproducibility import github, routes, snapshot, sync


HEAD = "b" * 40
REFERENCE = {"owner":"Author", "repo":"Project", "cited_commit":None, "label":"Author/Project"}


def packet(data):
    return f"{4 + len(data):04x}".encode() + data


def advertisement(ref=HEAD):
    return (packet(b"# service=git-upload-pack\n") + b"0000"
            + packet(f"{HEAD} HEAD\0symref=HEAD:refs/heads/main object-format=sha1\n".encode())
            + packet(f"{ref} refs/heads/main\n".encode()) + b"0000")


def evidence(reference=REFERENCE, candidates=(("readme", "README.md"),)):
    def fake(url, types, deadline):
        if url.startswith(snapshot.GIT_ROOT + "/"):
            return advertisement(), snapshot.GIT_TYPE
        return b"# Reference material\n", "text/plain"
    return snapshot.inspect_reference(reference, candidates, read=fake)


class SnapshotTests(unittest.TestCase):
    def test_require_consistent_git_head_and_named_branch(self):
        result = snapshot.advertised_head(advertisement())
        self.assertEqual((result["default_branch"], result["commit"]), ("main", HEAD))
        for bad in (advertisement("a" * 40), advertisement()[:-3], b"HTTP/1.1 200 OK", b"fffftruncated"):
            with self.subTest(prefix=bad[:20]), self.assertRaises(github.CheckError):
                snapshot.advertised_head(bad)

    def test_fetches_only_reference_and_fixed_paths_and_records_byte_hash(self):
        with patch.object(snapshot, "_read", side_effect=[(advertisement(),snapshot.GIT_TYPE),
                          (b"test\n","text/plain"),github.CheckError("not_found")]) as request:
            result = snapshot.inspect_reference(REFERENCE, [("readme","README.md"), ("training","src/train.py")])
        locations = [entry.args[0] for entry in request.call_args_list]
        self.assertEqual(locations, ["https://github.com/Author/Project.git/info/refs?service=git-upload-pack",
                                     f"https://raw.githubusercontent.com/Author/Project/{HEAD}/README.md",
                                     f"https://raw.githubusercontent.com/Author/Project/{HEAD}/src/train.py"])
        self.assertEqual(result["groups"]["readme"]["files"][0]["sha256"], sha256(b"test\n").hexdigest())
        self.assertEqual(result["unavailable"], [{"category":"training","path":"src/train.py","reason":"not_found"}])
        self.assertEqual(result["groups"]["license"]["status"], "not_requested")

    def test_selection_and_refusals_prevent_unintended_fetches(self):
        for path in ("../README.md", "https://other.invalid/README.md", "/README.md", ".git/README.md", "not-a-readme.py"):
            with self.subTest(path=path), patch.object(snapshot,"_read") as request, self.assertRaises(github.CheckError):
                snapshot.inspect_reference(REFERENCE, [("readme",path)])
            request.assert_not_called()
        with patch.object(snapshot,"_read",return_value=(advertisement(),snapshot.GIT_TYPE)) as request:
            with self.assertRaisesRegex(github.CheckError,"cited_commit_not_advertised"):
                snapshot.inspect_reference(dict(REFERENCE,cited_commit="d" * 40),[("readme","README.md")])
            self.assertEqual(request.call_count,1)
        with patch.object(snapshot,"_read",side_effect=github.CheckError("http_403")) as request:
            with self.assertRaisesRegex(github.CheckError,"http_403"):
                snapshot.inspect_reference(REFERENCE,[("readme","README.md")])
            self.assertEqual(request.call_count,1)

    def test_declines_authenticated_proxy_while_live_refusal_keeps_403(self):
        with patch.object(github,"getproxies",return_value={"https":"http://user:secret@proxy.invalid"}), \
                patch.object(snapshot,"build_opener") as opener, self.assertRaisesRegex(github.CheckError,"invalid_proxy"):
            snapshot._open(None,6)
        opener.assert_not_called()
        from urllib.error import URLError

        with patch.object(github,"build_opener") as opener:
            opener.return_value.open.side_effect = URLError(OSError("Tunnel connection failed: 403 Forbidden"))
            with self.assertRaisesRegex(github.CheckError,"forbidden"):
                github._fetch("/repos/Author/Project", remaining=6)

    def test_library_scope_and_corruption_validation(self):
        with TemporaryDirectory() as folder:
            store = workbench.ScanLibrary(Path(folder))
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((30,52),"Code at https://github.com/Author/Project.")
                first = store.add(pdf.tobytes(),"one.pdf")["doc"]["id"]
                pdf[0].insert_text((30,100),"A different document with the same link.")
                second = store.add(pdf.tobytes(),"two.pdf")["doc"]["id"]
            opts = {"repo":"Author/Project", "candidates":[("readme","README.md")]}
            with patch.object(snapshot,"inspect_reference",return_value=evidence()) as network:
                with self.assertRaisesRegex(github.CheckError,"consent_required"):
                    sync.acquire(store,first,**opts)
                network.assert_not_called()
                received = sync.acquire(store,first,consent=True,**opts)
            attached = routes.references(workbench.ScanLibrary(Path(folder)),first)["references"][0]["snapshot"]
            self.assertEqual(attached, received["snapshot"])
            self.assertNotIn("snapshot",routes.references(store,second)["references"][0])
            spoofed = evidence()
            spoofed["groups"]["readme"]["files"][0]["raw_url"] = "https://elsewhere.invalid/file"
            snapshot.save(store,first,received["reference_id"],spoofed)
            self.assertNotIn("snapshot",routes.references(store,first)["references"][0])
            snapshot.save(store,first,received["reference_id"],evidence())
            with closing(sqlite3.connect(store.db)) as db, db:
                db.execute("UPDATE repository_snapshot_v1 SET payload='{}'")
            self.assertNotIn("snapshot",routes.references(store,first)["references"][0])
