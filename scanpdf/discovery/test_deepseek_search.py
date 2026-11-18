"""Offline protocol fixtures for the paid DeepSeek Search and Fetch adapter."""

from contextlib import contextmanager
import json
import os
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from . import deepseek_search as adapter


KEY = "fixture-key-for-deepseek-search"
TERMS = "光学标定 publication"
RESULT_URL = "https://example.org/paper"
RECORD = {"url": RESULT_URL, "title": "A   paper", "snippet": "Abstract excerpt",
          "summary": "Model-facing summary", "date": None}


class BudgetExceeded(Exception):
    pass


class DeepSeekSearchTest(TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"DEEPSEEK_API_KEY": KEY}))
        self.authorize = Mock(return_value=True)

    def test_authorization_precedes_exact_paid_requests(self):
        chronology = []

        def authorize(operation):
            chronology.append(("authorize", operation))
            return True

        @contextmanager
        def api(request):
            operation = request.full_url.rsplit("/", 1)[1]
            chronology.append(("post", operation))
            self.assertEqual(request.full_url, "https://api.deepseek.com/v1/tools/" + operation)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
            expected = {"query": TERMS, "count": 5, "need_content": False} if operation == "search" else {"url": RESULT_URL}
            self.assertEqual(json.loads(request.data), expected)
            response = ({"results": [RECORD, RECORD]} if operation == "search"
                        else {"content": "## Research results\n" + "e" * 9000, "url": "https://ignored.org/"})
            raw = json.dumps(response).encode()
            yield Mock(status=200, read=lambda size: raw[:size])

        with patch.object(adapter, "_open", side_effect=api):
            result = adapter.search(" " + TERMS + " ", authorize=authorize, fetch=True)
        self.assertEqual(chronology, [("authorize", "search"), ("post", "search"),
                                      ("authorize", "fetch"), ("post", "fetch")])
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["items"], [dict(RECORD, title="A paper")])
        self.assertEqual(result["fetch"]["url"], RESULT_URL)
        self.assertEqual(len(result["fetch"]["text"]), adapter.MAX_FETCH_CHARS)
        self.assertTrue(result["fetch"]["truncated"])

    def test_unaccepted_inputs_never_use_ledger_or_network(self):
        with patch.object(adapter, "_open") as network:
            for invalid in ("", "  ", {}, "a\nb", "a" * 129, KEY):
                with self.subTest(terms=invalid):
                    self.assertEqual(adapter.search(invalid, authorize=self.authorize)["state"], "error")
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
                response = adapter.search(TERMS, authorize=self.authorize)
                self.assertEqual(response["error"]["code"], "not_configured")
            self.authorize.assert_not_called()
            network.assert_not_called()

    def test_budget_overrun_propagates_and_prevents_the_next_call(self):
        self.authorize.side_effect = [True, BudgetExceeded()]
        with patch.object(adapter, "_open", side_effect=self.responses({"results": [RECORD]})) as network:
            with self.assertRaises(BudgetExceeded):
                adapter.search(TERMS, authorize=self.authorize, fetch=True)
            self.assertEqual(network.call_count, 1)
        self.authorize.side_effect = BudgetExceeded()
        with patch.object(adapter, "_open") as network:
            with self.assertRaises(BudgetExceeded):
                adapter.search(TERMS, authorize=self.authorize)
            network.assert_not_called()

    def test_unsafe_targets_are_ineligible_and_search_alone_never_fetches(self):
        records = [{"url": url} for url in (
            "http://example.org/paper", "https://127.0.0.0x1/file", "https://a.localhost/file",
            "https://x:y@example.org/file", "https://[::1]/file",
        )]
        for requested_fetch, rows, expected_titles in ((True, records, []), (False, [RECORD], ["A paper"])):
            with self.subTest(fetch=requested_fetch):
                self.authorize.reset_mock()
                with patch.object(adapter, "_open", side_effect=self.responses({"results": rows})) as network:
                    result = adapter.search(TERMS, authorize=self.authorize, fetch=requested_fetch)
                    self.assertEqual(network.call_count, 1)
                self.authorize.assert_called_once_with("search")
                self.assertEqual(result["state"], "ok")
                self.assertIsNone(result["fetch"])
                self.assertEqual([row["title"] for row in result["items"]], expected_titles)

    def test_redirect_transport_error_and_partial_fetch_have_no_fallback(self):
        redirected = HTTPError(adapter.API_BASE, 302, "Redirect", {"Location": "https://example.org"}, None)
        for error, code in ((redirected, "redirect_refused"), (URLError("unreachable"), "network_error"),
                            (TimeoutError(), "timeout")):
            with self.subTest(code=code), patch.object(adapter, "_open", side_effect=error) as network:
                result = adapter.search(TERMS, authorize=self.authorize, fetch=True)
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(network.call_count, 1)
        with patch.object(adapter, "_open", side_effect=self.responses({"results": [RECORD]}, {"wrong": "field"})):
            result = adapter.search(TERMS, authorize=self.authorize, fetch=True)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["error"]["operation"], "fetch")
        self.assertEqual(result["items"][0]["url"], RESULT_URL)

    def test_transport_disables_environment_proxies_and_redirect_following(self):
        request = Request(adapter.API_BASE + "/search", method="POST")
        with patch.object(adapter, "build_opener") as builder:
            adapter._open(request)
        proxy, redirects = builder.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsNone(redirects.redirect_request(request, None, 302, "Found", {}, RESULT_URL))
        builder.return_value.open.assert_called_once_with(request, timeout=adapter.TIMEOUT)

    @staticmethod
    def responses(*payloads):
        @contextmanager
        def response(payload):
            data = json.dumps(payload).encode()
            yield Mock(status=200, read=lambda size: data[:size])

        return [response(payload) for payload in payloads]
