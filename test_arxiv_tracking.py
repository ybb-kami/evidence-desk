import http.client
import json
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

import arxiv_tracking as tracker


def entry(key="2611.12345v2", title="Sparse spectral clustering", abstract="Spectral sparse graph clustering. https://github.com/test-lab/code.", day=18, url=None):
    return f"""<entry><id>{escape(url or 'http://arxiv.org/abs/' + key)}</id>
      <title>{escape(title)}</title><published>2026-11-{day:02d}T10:00:00Z</published>
      <updated>2026-11-{day:02d}T11:00:00Z</updated><summary>{escape(abstract)}</summary>
      <author><name>Example Researcher</name></author></entry>"""


def feed(*entries, total=None):
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:os="http://a9.com/-/spec/opensearch/1.1/">'
            f'<os:totalResults>{len(entries) if total is None else total}</os:totalResults>'
            + "".join(entries) + '</feed>').encode()


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.reset = patch.object(tracker, "_NEXT_REQUEST", 0)
        self.reset.start()
        self.addCleanup(self.reset.stop)

    def test_no_consent_or_bad_inputs_never_fetch(self):
        with patch.object(tracker, "_fetch") as fetch:
            for c, feedback, consent, error in [
                ("cs.AI", {}, False, "consent_required"), ("cs.AI", {}, "true", "consent_required"),
                (["cs.AI"], {}, True, "invalid_category"), ("cs.AI OR private", {}, True, "invalid_category"),
                ("cs.AI", {"2611.12345": True}, True, "invalid_feedback"),
                ("cs.AI", {"https://example.com": 1}, True, "invalid_feedback"),
            ]:
                self.assertEqual(tracker.refresh(c, [], feedback, consent=consent)["error"]["code"], error)
            fetch.assert_not_called()

    def test_network_worker_receives_only_public_category(self):
        with patch.object(tracker.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout=feed(entry()))
            doc = {"name": "SECRET filename", "bibliographic_identity": {"title": "Sparse spectral graph"},
                   "chunks": [{"text": "SECRET excerpt clustering", "page": 1}]}
            result = tracker.refresh("cs.AI", [doc], {"2611.12345": -1}, consent=True)
            argv = run.call_args.args[0]
            self.assertEqual(argv[-1], "cs.AI")
            self.assertEqual(len(argv), 4)
            self.assertEqual(run.call_args.kwargs["timeout"], 7)
            hit = result["items"][0]
            self.assertEqual(hit["recommendation"]["score"], hit["recommendation"]["base_score"] - 18)
            self.assertIn("共享", hit["recommendation"]["reasons"][0])
            self.assertNotIn("SECRET", str(result))
            self.assertNotIn("_abstract", hit)
            self.assertEqual(hit["repository_url"], "https://github.com/test-lab/code")
            self.assertEqual(hit["url"], "https://arxiv.org/abs/2611.12345")

    def test_request_is_bounded_fixed_host_and_category(self):
        with patch.object(tracker, "build_opener") as opener:
            client = opener.return_value
            client.open.return_value.__enter__.return_value.read.return_value = feed(entry())
            self.assertEqual(tracker._download("cs.CL")[0], 0)
            args, kwargs = client.open.call_args
            url = urlsplit(args[0].full_url)
            self.assertEqual((url.scheme, url.netloc, url.path), ("https", "export.arxiv.org", "/api/query"))
            self.assertEqual(parse_qs(url.query), {
                "search_query": ["cat:cs.CL"], "start": ["0"], "max_results": ["12"],
                "sortBy": ["submittedDate"], "sortOrder": ["descending"],
            })
            self.assertEqual(kwargs["timeout"], 4)
            self.assertIsNone(opener.call_args.args[1].redirect_request(None, None, 302, "", {}, "https://example.com"))
            client.open.return_value.__enter__.return_value.read.return_value = b"x" * (tracker.MAX_BODY + 1)
            self.assertEqual(tracker._download("cs.CL"), (6, b""))

    def test_safe_links_and_version_dedupe(self):
        rows = tracker._parse(feed(
            entry("2611.12345v1", title="Earlier", day=17),
            entry("2611.12345v2", title="Later", abstract="https://gitHub.com/unsafe/x https://github.com.evil.test/test/x"),
            entry(url="https://arxiv.org.evil.test/abs/2611.12346"),
            entry("math/0501234", title="<img src=x>", abstract="https://github.com/topics/something"),
        ))
        self.assertEqual([(row["id"], row["title"]) for row in rows], [("2611.12345", "Later"), ("math/0501234", "<img src=x>")])
        self.assertTrue(all(row["repository_url"] is None for row in rows))
        self.assertEqual(tracker._parse(feed()), [])

    def test_bad_response_not_silent_empty(self):
        for body in [feed(total=12), b"<html/>",
                     b'<!DOCTYPE data [<!ENTITY x "oops">]><feed>&x;</feed>',
                     feed(entry(url="http://arxiv.org/api/errors#bad")), feed(entry()).decode().encode("utf-16")]:
            with self.subTest(body=body[:100]), self.assertRaises(ValueError):
                tracker._parse(body)

    def test_busy_timeout_cooldown_and_no_retry(self):
        with tracker._LOCK, patch.object(tracker, "_fetch") as fetch:
            self.assertEqual(tracker.refresh("cs.AI", [], {}, consent=True)["error"]["code"], "busy")
            fetch.assert_not_called()
        with patch.object(tracker, "_fetch", side_effect=subprocess.TimeoutExpired("x", 7)) as fetch:
            self.assertEqual(tracker.refresh("cs.AI", [], {}, consent=True)["error"]["code"], "timeout")
            self.assertEqual(tracker.refresh("cs.CL", [], {}, consent=True)["error"]["code"], "cooldown")
            fetch.assert_called_once_with("cs.AI")

    def test_local_http_consent_and_private_payload_rejection(self):
        from app import Handler, ThreadingHTTPServer
        local = {"bibliographic_identity": {"title": "Sparse spectral graph clustering"}}
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server.library = SimpleNamespace(records=lambda: [local])
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def post(data, **extra_headers):
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
                    try:
                        conn.request("POST", "/api/tracking", json.dumps(data),
                                     {"Content-Type": "application/json", **extra_headers})
                        response = conn.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        conn.close()
                valid = {"category": "math.CO", "consent": True, "feedback": {"2611.12345": 1}}
                with patch.object(tracker, "_fetch", return_value=SimpleNamespace(returncode=0, stdout=feed(entry()))) as fetch:
                    self.assertEqual(post({**valid, "consent": False})[0], 403)
                    self.assertEqual(post({**valid, "private_pdf": "untested"})[0], 400)
                    self.assertEqual(post(valid, Origin="https://other.invalid")[0], 403)
                    fetch.assert_not_called()
                    status, body = post(valid)
                    self.assertEqual(status, 200)
                    self.assertIsNone(body["error"])
                    self.assertIn("共享", body["items"][0]["recommendation"]["reasons"][0])
                    self.assertEqual(body["items"][0]["recommendation"]["vote"], 1)
                    fetch.assert_called_once_with("math.CO")
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_rank_repo_feedback_and_known(self):
        base = tracker._parse(feed(entry("2611.12345"), entry("2611.12346", abstract="Synthetic abstract")))
        self.assertEqual(tracker._rank(base, [], {})[0]["id"], "2611.12345")
        base = tracker._parse(feed(entry("2611.12345"), entry("2611.12346", abstract="Synthetic abstract")))
        self.assertEqual(tracker._rank(base, [], {"2611.12345": -1})[0]["id"], "2611.12346")
        base = tracker._parse(feed(entry("2611.12345"), entry("2611.12346", abstract="Synthetic abstract")))
        self.assertEqual(tracker._rank(base, [{"arxiv": {"id": "2611.12345", "version": 1}}], {})[0]["id"], "2611.12346")


if __name__ == "__main__":
    unittest.main()
