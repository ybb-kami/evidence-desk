"""Tests for the loopback arXiv download and library integration."""

from email.message import Message
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

import fitz

from app import Bad, Library
from . import fetch, routes


IDENTIFIER = "1706.03762v1"


def pdf_bytes():
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 50), "Ground-truth document for the download test")
        return pdf.tobytes()


class Response:
    def __init__(self, body, *, url=fetch.ENDPOINT + IDENTIFIER, content_type="application/pdf", length=None):
        self.body = body
        self.url = url
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def geturl(self):
        return self.url

    def read(self, cap):
        return self.body[:cap]

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return False


class TransportTests(TestCase):
    def test_only_full_identifiers_can_reach_the_known_endpoint(self):
        for value in (
            "arXiv:1706.03762v1", "https://arxiv.org/pdf/1706.03762v1",
            "1706.03762v0", "1706.03762v01", "1706.03762?x=1", "1706.03762\n",
            "1706.03762%2f", "hep-th/../9901001", "1799.03762", None,
        ):
            with self.subTest(value=value), patch.object(fetch, "_open") as upstream:
                self.assertNotEqual(fetch._download(value)[0], 0)
                upstream.assert_not_called()
        self.assertTrue(fetch.valid_id("hep-th/9901001v2"))
        self.assertTrue(fetch.valid_id("2502.12345"))

    def test_retrieves_pdf_from_exact_https_endpoint(self):
        body = pdf_bytes()
        with patch.object(fetch, "_open", return_value=Response(body, length=len(body))) as upstream:
            self.assertEqual(fetch._download(IDENTIFIER), (0, body))
            request = upstream.call_args.args[0]
            self.assertEqual(request.full_url, fetch.ENDPOINT + IDENTIFIER)
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Accept"), "application/pdf")
            self.assertIsNone(request.data)

    def test_rejects_redirect_size_limit_and_invalid_response(self):
        body = pdf_bytes()
        cases = [
            (Response(body, url="https://evil.invalid/"), fetch.REDIRECT),
            (Response(body, length=fetch.MAX_BYTES + 1), fetch.TOO_LARGE),
            (Response(body, length=len(body) + 1), fetch.INVALID_PDF),
            (Response(body, content_type="text/html"), fetch.INVALID_PDF),
            (Response(b"%PDF-incomplete"), fetch.INVALID_PDF),
        ]
        for response, code in cases:
            with self.subTest(code=code), patch.object(fetch, "_open", return_value=response):
                self.assertEqual(fetch._download(IDENTIFIER), (code, b""))
        with patch.object(fetch, "MAX_BYTES", 20), patch.object(fetch, "_open", return_value=Response(body)):
            self.assertEqual(fetch._download(IDENTIFIER), (fetch.TOO_LARGE, b""))
        with patch.object(fetch, "_open", side_effect=HTTPError(fetch.ENDPOINT, 302, "", {}, None)):
            self.assertEqual(fetch._download(IDENTIFIER), (fetch.REDIRECT, b""))


class Handler:
    def __init__(self, library, data=None):
        self.server = SimpleNamespace(library=library)
        self.data = data
        self.response = None

    def json_request(self):
        return self.data

    def payload(self, code, data, mime=None):
        self.response = code, data


class RouteTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.library = Library(Path(folder.name))
        self.handler = Handler(self.library)
        self.path = "/api/download/arxiv"

    def invoke(self, *, post=False, request=None):
        self.handler.data = request
        self.assertTrue(routes.handle(self.handler, post, self.path))
        return self.handler.response

    def test_consent_and_exact_payload_precede_network_access(self):
        for body, status in [
            ({"arxiv_id": IDENTIFIER}, 403),
            ({"arxiv_id": IDENTIFIER, "consent": 1}, 403),
            ({"arxiv_id": IDENTIFIER, "consent": True, "url": "https://example.org"}, 400),
            ({"arxiv_id": "https://example.org", "consent": True}, 400),
        ]:
            with self.subTest(body=body), patch.object(fetch, "retrieve") as upstream:
                with self.assertRaises(Bad) as error:
                    self.invoke(post=True, request=body)
                self.assertEqual(error.exception.code, status)
                upstream.assert_not_called()

    def test_success_persists_source_and_respects_archive(self):
        pdf = pdf_bytes()
        digest = sha256(pdf).hexdigest()
        request = {"arxiv_id": IDENTIFIER, "consent": True}
        with patch.object(fetch, "retrieve", return_value=(0, pdf)) as upstream:
            status, data = self.invoke(post=True, request=request)
            upstream.assert_called_once_with(IDENTIFIER)
            self.assertEqual(status, 200)
            self.assertFalse(data["existing"])
            self.assertEqual(self.library.records()[0]["id"], digest)
            self.assertEqual(self.library.pdf(digest), pdf)
            self.assertEqual(data["source"]["sha256"], digest)
            self.assertEqual(data["source"]["url"], fetch.ENDPOINT + IDENTIFIER)
            self.assertIn("+00:00", data["source"]["retrieved_at"])
            self.library.set_archived(digest, True)
            _, archived = self.invoke(post=True, request=request)
            self.assertTrue(archived["archived"])
            self.assertEqual(self.library.records(), [])
        _, recent = self.invoke()
        self.assertEqual(recent["receipts"], [archived["source"] | {"archived": True}])

    def test_invalid_pdf_or_transport_failure_leaves_library_and_history_empty(self):
        request = {"arxiv_id": IDENTIFIER, "consent": True}
        for upstream_result in [(0, b"%PDF-invalid\n%%EOF"), (fetch.UNAVAILABLE, b"")]:
            with self.subTest(upstream_result=upstream_result), patch.object(fetch, "retrieve", return_value=upstream_result):
                with self.assertRaises(Bad) as error:
                    self.invoke(post=True, request=request)
                self.assertEqual(error.exception.code, 502)
        self.assertEqual(self.library.records(), [])
        self.assertEqual(self.invoke()[1], {"receipts": []})

    def test_import_rejection_leaves_no_visible_receipt_and_can_retry(self):
        pdf = pdf_bytes()
        request = {"arxiv_id": IDENTIFIER, "consent": True}
        with patch.object(fetch, "retrieve", return_value=(0, pdf)):
            with patch.object(self.library, "add", side_effect=ValueError("library limit")):
                with self.assertRaisesRegex(ValueError, "library limit"):
                    self.invoke(post=True, request=request)
            self.assertEqual(self.invoke()[1], {"receipts": []})
            self.assertEqual(self.invoke(post=True, request=request)[0], 200)
        self.assertEqual(len(self.invoke()[1]["receipts"]), 1)
