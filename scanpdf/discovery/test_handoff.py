from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app import Bad, Library
from . import fetch, handoff, joss, routes
from .test_download import Handler, Response, pdf_bytes
from .test_joss import DOI, record


class HandoffTests(TestCase):
    def test_known_publication_addresses_preserve_the_exact_identifier(self):
        cases = (
            ("https://arxiv.org/abs/1706.03762v1?campaign=1", "arxiv", "1706.03762v1"),
            ("https://arxiv.org/pdf/hep-ph/9904271v2.pdf", "arxiv", "hep-ph/9904271v2"),
            ("https://doi.org/10.48550/arXiv.1706.03762v3", "arxiv", "1706.03762v3"),
            ("https://aclanthology.org/n19-1423.pdf", "acl", "10.18653/v1/N19-1423"),
            ("https://doi.org/10.18653/v1/2024.acl-long.1", "acl", "10.18653/v1/2024.acl-long.1"),
            ("https://joss.theoj.org/papers/10.21105/joss.03021", "joss", "10.21105/joss.03021"),
            ("https://doi.org/10.21105%2Fjoss.03021", "joss", "10.21105/joss.03021"),
        )
        for url, provider, identifier in cases:
            with self.subTest(url=url):
                result = handoff.from_url(url)
                self.assertEqual((result["provider"], result["identifier"]), (provider, identifier))
                target = urlsplit(result["href"])
                self.assertEqual((target.scheme, target.netloc, target.path), ("", "", "/download"))
                self.assertEqual(parse_qs(target.query), {"provider": [provider], "identifier": [identifier]})

    def test_untrusted_links_do_not_become_download_candidates(self):
        for url in (
            "https://joss.theoj.org.other.org/papers/10.21105/joss.03021",
            "https://some-blog.org/10.21105/joss.03021", "https://doi.org/10.1000/1234",
            "https://user@arxiv.org/abs/1706.03762", "https://arxiv.org:8443/abs/1706.03762",
            "http://arxiv.org/abs/1706.03762", "https://arxiv.org/abs/1706.03762v0",
            "https://arxiv.org/abs/1706.03762/%2e%2e/foo", "https://arxiv.org/?id=1706.03762",
        ):
            with self.subTest(url=url):
                self.assertIsNone(handoff.from_url(url))

    def test_cold_preview_and_separate_confirmations_before_metadata_or_pdf(self):
        folder = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"EVIDENCE_JOSS_CATALOG": str(folder / "catalog")}))
        library = Library(folder / "library")
        handler = Handler(library)
        preview_path = "/api/download/candidate"
        handler.path = preview_path + "?provider=joss&identifier=" + DOI
        metadata, pdf = record(), pdf_bytes()
        with (patch.object(fetch, "_open", return_value=Response(metadata, url=fetch.joss_metadata_url(DOI))) as publisher,
              patch.object(fetch, "retrieve", return_value=(0, pdf)) as download):
            routes.handle(handler, False, preview_path)
            self.assertIsNone(handler.response[1]["publication"])
            for body, status in (
                ({"provider": "joss", "identifier": DOI}, 403),
                ({"provider": "joss", "identifier": DOI, "consent": "true"}, 403),
                ({"provider": "joss", "identifier": DOI, "consent": True, "url": "https://example.org"}, 400),
                ({"provider": "arxiv", "identifier": "1706.03762", "consent": True}, 400),
            ):
                handler.data = body
                with self.assertRaises(Bad) as rejection:
                    routes.handle(handler, True, preview_path)
                self.assertEqual(rejection.exception.code, status)
            publisher.assert_not_called()
            download.assert_not_called()
            self.assertEqual(library.records(), [])

            handler.data = {"provider": "joss", "identifier": DOI, "consent": True}
            routes.handle(handler, True, preview_path)
            verified = handler.response[1]["publication"]
            self.assertEqual(verified["metadata_sha256"], sha256(metadata).hexdigest())
            self.assertEqual(verified["license"], joss.LICENSE)
            self.assertEqual(publisher.call_args.args[0].full_url, fetch.joss_metadata_url(DOI))
            routes.handle(handler, False, preview_path)
            self.assertEqual(handler.response[1]["publication"], verified)
            self.assertEqual(library.records(), [])
            download.assert_not_called()

            handler.data = {"doi": DOI, "consent": True}
            routes.handle(handler, True, "/api/download/joss")
            download.assert_called_once_with(DOI, "joss")
            imported = handler.response[1]
            self.assertEqual(imported["source"]["sha256"], sha256(pdf).hexdigest())
            self.assertEqual(library.pdf(imported["doc"]["id"]), pdf)
