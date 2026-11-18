from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app import Bad, Library
from . import fetch, references, routes
from .test_download import Handler

FIXTURES = Path(os.environ.get("SCANPDF_CONTEST_PDF_DIR", "/workspace/shared/contest-pdf"))
PATH = "/api/download/references"


class ReferenceTests(TestCase):
    def test_explicit_identifiers_only(self):
        for text, expected in [
            ("arXiv:1705.03122v2.", ("arxiv", "1705.03122v2")),
            ("https://arxiv.org/pdf/hep-th/9901001v2.pdf", ("arxiv", "hep-th/9901001v2")),
            ("doi:10.48550/arXiv.2303.08774", ("arxiv", "2303.08774")),
            ("https://doi.org/10.18653/v1/p19-1472.", ("acl", "10.18653/v1/P19-1472")),
            ("DOI 10.18653/v1/2022.acl-long.229", ("acl", "10.18653/v1/2022.acl-long.229")),
            ("Title 1705.03122", (None, None)),
            ("https://example.org/abs/1705.03122", (None, None)),
            ("arXiv:1705.03122v0", (None, None)),
            ("arXiv:1705.03122 and 10.18653/v1/P19-1472", (None, None)),
            ("doi:10.18653/v1/P19-1472@extra", (None, None)),
        ]:
            with self.subTest(text=text):
                self.assertEqual(references._target(text)[:2], expected)

    def test_contest_reference_rows_and_mocked_imports(self):
        path = FIXTURES / "2303.08774v6.pdf"
        target = FIXTURES / "1910.10683v4.pdf"
        if not path.is_file() or not target.is_file():
            self.skipTest("Contest PDFs not supplied")
        with TemporaryDirectory() as directory:
            library = Library(Path(directory))
            docid = library.add(path.read_bytes(), path.name)["doc"]["id"]
            handler = Handler(library)
            handler.path = PATH + "?doc_id=" + docid
            body = target.read_bytes()
            digest = sha256(body).hexdigest()
            with patch.object(fetch, "retrieve") as upstream:
                self.assertTrue(routes.handle(handler, False, PATH))
                result = handler.response[1]
                self.assertTrue(result["detected"])
                self.assertTrue(any(row["status"] == "unresolved" for row in result["items"]))
                by_identifier = {item["identifier"]: item for item in result["items"]}
                matched = by_identifier["1910.10683"]
                not_found = by_identifier["10.18653/v1/P19-1472"]
                self.assertEqual((matched["page"], not_found["page"]), (18, 21))
                upstream.assert_not_called()

                def send(item, **extra):
                    handler.data = {"doc_id": docid, "candidate_id": item["candidate_id"]} | extra
                    self.assertTrue(routes.handle(handler, True, PATH))
                    return handler.response[1]

                for extra, status in [({}, 403), ({"consent": True, "url": "https://evil.invalid"}, 400)]:
                    with self.assertRaises(Bad) as bad:
                        send(matched, **extra)
                    self.assertEqual(bad.exception.code, status)
                unresolved = next(row for row in result["items"] if row["status"] == "unresolved")
                self.assertEqual(send(unresolved, consent=True)["status"], "unresolved")
                upstream.assert_not_called()

                upstream.return_value = (fetch.UNAVAILABLE, b"")
                self.assertEqual(send(matched, consent=True)["status"], "failed")
                upstream.return_value = (0, body)
                imported = send(matched, consent=True)
                self.assertEqual(imported["status"], "success")
                self.assertEqual(imported["imported_id"], digest)
                self.assertEqual(library.pdf(digest), body)
                self.assertEqual(send(matched, consent=True), imported)
                upstream.return_value = (fetch.NOT_FOUND, b"")
                self.assertEqual(send(not_found, consent=True)["status"], "not_found")
                self.assertEqual([call.args for call in upstream.call_args_list],
                                 [("1910.10683",), ("1910.10683",), ("10.18653/v1/P19-1472", "acl")])
            saved = {row["identifier"]: row for row in references.candidates(Library(Path(directory)), docid)["items"]}
            self.assertEqual(saved["1910.10683"]["status"], "success")
            self.assertEqual(saved["10.18653/v1/P19-1472"]["status"], "not_found")
