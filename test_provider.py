import json
import os
import unittest
from unittest.mock import patch

import provider


def answer(quotes, finish="stop", **extra):
    return json.dumps({"choices": [{"finish_reason": finish,
                       "message": {"content": json.dumps({"quotes": quotes, **extra})}}]}).encode()


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.hit = {"id": "actual-id", "name": "private-filename.pdf", "page": 7, "block": 3,
                    "text": "The theorem is proven only for positive integers."}
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-synthetic-testing"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_verified_quote_and_minimal_payload(self):
        def mock(body, key):
            self.assertEqual(key, "sk-synthetic-testing")
            self.assertNotIn(key, body.decode())
            data = json.loads(body)
            self.assertEqual(data["model"], "deepseek-flash")
            self.assertEqual(data["response_format"], {"type": "json_object"})
            self.assertEqual(json.loads(data["messages"][1]["content"]),
                             {"question": "For which integers?", "extracts":
                              [{"id": "e1", "text": self.hit["text"]}]})
            self.assertNotIn(self.hit["name"], body.decode())
            self.assertNotIn(self.hit["id"], body.decode())
            return answer([{"id": "e1", "text": self.hit["text"]}])
        result = provider.summarize("For which integers?", [self.hit], transport=mock)
        self.assertEqual(result["status"], "supported")
        self.assertEqual(result["quotes"], [self.hit])
        self.assertNotIn(self.hit["text"], result["answer"])

    def test_fails_closed_on_out_of_range_or_injected_output(self):
        valid = {"id": "e1", "text": self.hit["text"]}
        bad = (
            answer([valid, {"id": "e1", "text": "The theorem works for all integers."}]),
            answer([{"id": "e2", "text": self.hit["text"]}]),
            answer([{**valid, "name": "fake"}]),
            answer([valid], conclusion="Unsupported conclusion"),
            answer([valid], finish="length"),
            b'{"choices":[{"finish_reason":"stop","message":{"content":" "}}]}',
            b"not JSON",
        )
        for response in bad:
            with self.subTest(response=response):
                result = provider.summarize("For which integers?", [self.hit],
                                            transport=lambda *_: response)
                self.assertEqual(result["status"], "insufficient_evidence")
                self.assertEqual(result["quotes"], [])

    def test_caps_excerpts_and_rejects_out_of_prefix_quote(self):
        hidden = "Hidden from the model and ineligible."
        hit = {**self.hit, "text": "x" * provider.MAX_EXTRACT + hidden}
        def mock(body, _):
            supplied = json.loads(json.loads(body)["messages"][1]["content"])["extracts"]
            self.assertEqual(len(supplied), provider.MAX_HITS)
            self.assertTrue(all(len(h["text"]) == provider.MAX_EXTRACT for h in supplied))
            self.assertNotIn(hidden, body.decode())
            return answer([{"id": "e1", "text": hidden}])
        self.assertEqual(provider.summarize("Q?", [hit] * 10, transport=mock)["quotes"], [])

    def test_no_egress_without_key_or_evidence(self):
        def unexpected(*_):
            self.fail("transport must not run")
        self.assertEqual(provider.summarize("Q?", [], transport=unexpected)["status"],
                         "insufficient_evidence")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": " "}):
            self.assertFalse(provider.configured())
            self.assertEqual(provider.summarize("Q?", [self.hit], transport=unexpected)["status"],
                             "not_configured")

    def test_invalid_and_credential_inputs(self):
        for query in (" " * 3, "a" * (provider.MAX_QUERY + 1), "sk-synthetic-testing"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                provider.summarize(query, [self.hit], transport=lambda *_: b"")

    def test_transport_failure_is_sanitized_and_output_limited(self):
        def mock(_, key):
            raise Exception(key + " confidential input")
        with self.assertRaisesRegex(RuntimeError, r"^DeepSeek request failed\.$"):
            provider.summarize("Q?", [self.hit], transport=mock)
        with self.assertRaisesRegex(RuntimeError, "exceeded"):
            provider.summarize("Q?", [self.hit],
                               transport=lambda *_: b"x" * (provider.MAX_RESPONSE + 1))

    def test_no_redirect(self):
        self.assertIsNone(provider._NoRedirect().redirect_request(
            None, None, 302, "", {}, "https://untrusted.example/"))


if __name__ == "__main__":
    unittest.main()
