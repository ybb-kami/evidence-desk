import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import pymupdf

import workbench
from reproducibility import github, references, routes

COMMIT = "a" * 40
TREE = "b" * 40
BLOB = "c" * 40


class ReferenceTests(unittest.TestCase):
    def test_verbatim_wrapped_urls_ligatures_cited_sha_and_host_boundaries(self):
        text = ("Code https://github.com/\nExample-Org/Example.Project.git.\n"
                "1https://github.com/tensorﬂow/tensor2tensor\n"
                f"Release https://github.com/Example-Org/Example.Project/tree/{COMMIT}/src.\n"
                "https://github.com.invalid/org/repo https://elsewhere.invalid/github.com/other/host "
                "https://github.com@elsewhere.invalid/wrong/repo https://u@github.com/wrong/repo "
                "Read https://github.com/Example-Org/Example.Project again.")
        doc = {"id": "e" * 64, "chunks": [{"text": text, "page": 2, "block": 9, "part": 1}]}
        refs, limited = references.find_references(doc)
        self.assertFalse(limited)
        self.assertEqual([(ref["label"], ref["cited_commit"]) for ref in refs], [
            ("Example-Org/Example.Project", None), ("tensorflow/tensor2tensor", None),
            ("Example-Org/Example.Project", COMMIT)])
        for ref in refs:
            receipt = ref["citation"]
            self.assertEqual(text[receipt["start"]:receipt["end"]], receipt["exact"])
            self.assertEqual((receipt["doc_sha256"], receipt["page"], receipt["block"]), (doc["id"], 2, 9))


class RepositoryTests(unittest.TestCase):
    def _fetch(self, tree, *, limited=False):
        outcomes = [
            {"private": False, "visibility": "public", "full_name": "Author/Library",
             "default_branch": "main", "license": {"spdx_id": "MIT"}},
            {"sha": COMMIT, "commit": {"tree": {"sha": TREE}}},
        ]
        if limited:
            outcomes.append(github.CheckError("response_limit"))
        outcomes.append({"sha": TREE, "tree": tree, "truncated": False})
        calls = []

        def response(path, *, remaining):
            self.assertGreater(remaining, 0)
            calls.append(path)
            data = outcomes.pop(0)
            if isinstance(data, Exception):
                raise data
            return data

        return response, calls

    def test_persistently_links_only_tracked_files_at_the_verified_commit(self):
        entries = [{"mode": "100644", "type": "blob", "sha": BLOB, "path": path}
                   for path in ("LICENSE", "README.md", "requirements.txt",
                                "examples/a folder/run_train.py", "examples/evaluate.sh", "../README.md")]
        entries.append({"mode": "120000", "type": "blob", "sha": BLOB, "path": "README.symlink"})
        fetch, calls = self._fetch(entries)
        report = github.inspect("Author", "Library", COMMIT, fetch=fetch)
        self.assertEqual(calls, ["/repos/Author/Library", f"/repos/Author/Library/commits/{COMMIT}",
                                 f"/repos/Author/Library/git/trees/{TREE}?recursive=1"])
        self.assertEqual(report["basis"], "cited_commit")
        self.assertEqual(report["api_repository_spdx"], "MIT")
        for group in report["groups"].values():
            self.assertEqual(group["count"], 1)
            self.assertIn(f"/blob/{COMMIT}/", group["files"][0]["url"])
            self.assertEqual(group["files"][0]["blob_sha"], BLOB)
        self.assertIn("a%20folder", report["groups"]["training"]["files"][0]["url"])

    def test_partial_tree_keeps_missing_categories_undetermined(self):
        fetch, calls = self._fetch([{"mode": "100644", "type": "blob", "sha": BLOB, "path": "README.md"}],
                                  limited=True)
        report = github.inspect("Author", "Library", fetch=fetch)
        self.assertEqual(len(calls), 4)
        self.assertFalse(report["tree_complete"])
        self.assertEqual(report["groups"]["readme"]["status"], "found")
        self.assertEqual(report["groups"]["environment"]["status"], "tree_incomplete")

    def test_rejects_api_retarget_and_bad_commit_identity(self):
        with self.assertRaises(github.CheckError) as unsafe:
            github.inspect("Author/Other", "Library")
        self.assertEqual(unsafe.exception.code, "invalid_repository")
        fetch, _ = self._fetch([])
        with self.assertRaises(github.CheckError) as mismatch:
            github.inspect("Author", "Library", "0" * 40, fetch=fetch)
        self.assertEqual(mismatch.exception.code, "commit_mismatch")

    def test_http_requests_are_get_only_and_have_no_authorization(self):
        from io import BytesIO

        response = BytesIO(json.dumps({"example": 1}).encode())
        with patch.object(github, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(github._fetch("/repos/Author/Library", remaining=9), {"example": 1})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, "https://api.github.com/repos/Author/Library")
            self.assertFalse(request.has_header("Authorization"))
        self.assertIsNone(github._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid/"))


class ConsentTests(unittest.TestCase):
    def test_material_check_after_confirmation_and_no_link_and_offline_cases(self):
        with TemporaryDirectory() as directory:
            library = workbench.ScanLibrary(Path(directory))

            def add(label, line):
                with pymupdf.open() as pdf:
                    pdf.new_page(width=620, height=380).insert_text((35, 60), line)
                    return library.add(pdf.tobytes(), label)["doc"]["id"]

            positive = add("paper.pdf", "Code at https://github.com/Author/Library.")
            other = add("paper-two.pdf", "A reference https://github.com/Another/Repository.")
            no_link = add("no-link.pdf", "Pseudocode and equations in the appendix.")
            refs = routes.references(library, positive)
            self.assertEqual(refs["document"]["sha256"], positive)
            self.assertEqual(routes.references(library, no_link)["references"], [])
            payload = {"id": positive, "reference": refs["references"][0]["id"], "consent": True}
            with patch.object(github, "inspect", return_value={"status": "checked", "commit": COMMIT}) as network:
                for request, status in ((dict(payload, consent=False), 403), (dict(payload, id=other), 404)):
                    with self.assertRaises(workbench.app.Bad) as rejected:
                        routes.audit(library, request)
                    self.assertEqual(rejected.exception.code, status)
                network.assert_not_called()
                first = routes.audit(library, payload)
                self.assertFalse(first["cached"])
                self.assertEqual(first["audit"]["commit"], COMMIT)
                reopened = workbench.ScanLibrary(Path(directory))
                self.assertTrue(routes.audit(reopened, payload)["cached"])
                network.assert_called_once_with("Author", "Library", None)
            denied = {"id": other, "reference": routes.references(library, other)["references"][0]["id"], "consent": True}
            with patch.object(github, "inspect", side_effect=github.CheckError("timeout")) as network:
                failure = routes.audit(library, denied)
                self.assertEqual((failure["audit"]["status"], failure["audit"]["reason"]), ("unavailable", "timeout"))
                self.assertTrue(routes.audit(library, denied)["cached"])
                network.assert_called_once()
                with patch.object(routes.time, "time", return_value=time.time() + routes.ERROR_TTL + 1):
                    self.assertFalse(routes.audit(library, denied)["cached"])
                self.assertEqual(network.call_count, 2)


if __name__ == "__main__":
    unittest.main()
