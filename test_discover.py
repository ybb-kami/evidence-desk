import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import discover


def reply(items):
    return io.BytesIO(json.dumps({
        "status": "ok", "message-type": "work-list", "message": {"items": items},
    }).encode())


class DiscoveryTests(unittest.TestCase):
    def test_success_has_only_the_explicit_query_and_fixed_parameters(self):
        record = {
            "DOI": "10.1234/abc.1", "title": ["A public paper"],
            "author": [{"given": "Kai", "family": "Test"}, {"name": "The Working Group"}],
            "issued": {"date-parts": [[2020, 5]]}, "container-title": ["Test Journal"],
            "type": "journal-article", "URL": "https://attacker.invalid/link",
            "link": [{"URL": "https://attacker.invalid/file.pdf"}],
        }
        with patch.object(discover, "_open", return_value=reply([record])) as net:
            result = discover.search_public(" 公开 文献 &rows=500 ")
        net.assert_called_once()
        req = net.call_args.args[0]
        parts = urlsplit(req.full_url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "api.crossref.org", "/v1/works"))
        self.assertEqual(parse_qs(parts.query), {
            "query.bibliographic": ["公开 文献 &rows=500"], "rows": ["5"],
            "select": [discover.FIELDS],
        })
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(set(req.header_items()), {
            ("Accept", "application/json"), ("User-agent", "EvidenceDesk/0.2"),
        })
        self.assertEqual(net.call_args.kwargs["timeout"], discover.TIMEOUT)
        self.assertEqual(result["items"], [{
            "doi": "10.1234/abc.1", "url": "https://doi.org/10.1234/abc.1",
            "title": "A public paper", "authors": ["Kai Test", "The Working Group"], "year": 2020,
            "venue": "Test Journal", "kind": "journal-article",
        }])
        self.assertIsNone(result["error"])
        self.assertTrue(result["retrieved_at"].endswith("+00:00"))

    def test_no_egress_for_empty_oversize_or_multiline_input(self):
        with patch.object(discover, "_open") as net:
            for query in (None, {}, "", "  ", "a" * 301, "not\nsingle line", "\ud800"):
                self.assertEqual(discover.search_public(query)["error"]["code"], "invalid_query")
            net.assert_not_called()

    def test_untrusted_links_and_duplicates_are_not_exposed(self):
        with patch.object(discover, "_open", return_value=reply([
            {"title": ["Look"] , "DOI": "https://evil.invalid/10.1234/fake"},
            {"title": ["Look"] , "DOI": "10.1234/x/../../outside"},
            {"title": ["Look"] , "DOI": "10.1234/x%2foutside"},
            {"title": ["Look"] , "DOI": "10.1234/def", "author": "invalid"},
            {"title": ["Look"] , "DOI": "10.1234/DEF"},
        ])):
            result = discover.search_public("a public title")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["url"], "https://doi.org/10.1234/def")

    def test_http_error_rate_limit_timeout_and_network_fail_closed(self):
        for thrown, code in [
            (HTTPError(discover.ENDPOINT, 429, "rate limited", {"Retry-After": "12"}, None), "rate_limited"),
            (HTTPError(discover.ENDPOINT, 503, "unavailable", {}, None), "http_error"),
            (HTTPError(discover.ENDPOINT, 302, "redirect", {"Location": "https://evil.invalid"}, None), "redirect_refused"),
            (URLError("private URL"), "network_error"),
            (URLError(TimeoutError()), "timeout"),
            (TimeoutError(), "timeout"),
        ]:
            with self.subTest(code=code), patch.object(discover, "_open", side_effect=thrown) as net:
                result = discover.search_public("a public title")
                self.assertIsNone(result["retrieved_at"])
                self.assertEqual(result["items"], [])
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn("private URL", str(result))
                if code == "rate_limited":
                    self.assertEqual(result["error"]["retry_after_seconds"], 12)
                net.assert_called_once()

    def test_redirect_policy_will_not_create_a_new_request(self):
        self.assertIsNone(discover._NoRedirect().redirect_request(
            None, None, 302, "", {}, "https://evil.invalid"
        ))

    def test_invalid_response_is_not_a_successful_empty_search(self):
        for raw in (b"<html>", b"null", b'{"status":"failed"}', b" " * (discover.MAX_BODY + 1)):
            with self.subTest(length=len(raw)), patch.object(discover, "_open", return_value=io.BytesIO(raw)):
                self.assertEqual(discover.search_public("a public title")["error"]["code"], "invalid_response")
        with patch.object(discover, "_open", return_value=reply([])):
            self.assertIsNone(discover.search_public("a public title")["error"])


if __name__ == "__main__":
    unittest.main()
