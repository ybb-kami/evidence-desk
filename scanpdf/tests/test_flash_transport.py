"""DeepSeek transport tests with scripted HTTPS responses and no network I/O."""

import io
import json
import os
import ssl
from unittest import TestCase
from unittest.mock import patch

from flash_transport import FlashError, FlashGenerator, get_status
from local_generation import SCHEMA
from paid_budget import BudgetExceeded


KEY = "test-only-flash-secret-credential"
QUESTION = "How much distilled water did the trial flask hold during calibration?"
QUOTE = "The trial flask held 12 mL of distilled water during calibration."
HIT = {"id": "a" * 64, "page": 2, "block": 7, "part": 1, "text": QUOTE}


def model(text="1017", *, name="DeepSeek-V4.1-Flash", alias="deepseek-flash"):
    return {"id": alias, "name": name, "client_metadata": {"deepseek-harness": {"badge": {"text": text}}}}


def body(answer="12 mL", quote=QUOTE):
    return json.dumps({"status": "answered", "answer": answer, "source": 1, "quote": quote})


def completion(content):
    return json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode()


def sse(*pieces):
    frames = [{"choices": [{"delta": {"content": text}, "finish_reason": None}]} for text in pieces]
    frames.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    return b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames) + b"data: [DONE]\n\n"


class Response(io.BytesIO):
    def __init__(self, payload, status=200, mime="application/json"):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.status = status
        self.mime = mime

    def getheader(self, key, default=None):
        return self.mime if key.casefold() == "content-type" else default


class HTTPSStub:
    def __init__(self, test, responses, events):
        self.test = test
        self.responses = iter(responses)
        self.events = events

    def connect(self, host, port, *, timeout, context):
        self.test.assertEqual((host, port), ("api.deepseek.com", 443))
        self.test.assertTrue(context.check_hostname)
        self.test.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        return self

    def request(self, method, path, *, body, headers):
        self.events.append((method, path, json.loads(body) if body else None, headers))

    def getresponse(self):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


class FlashTransportTest(TestCase):
    def setUp(self):
        self.ticket = object()
        self.generator = FlashGenerator(self.ticket)
        self.events = []
        self.enterContext(patch.dict(os.environ, {"DEEPSEEK_API_KEY": KEY,
                                                "HTTPS_PROXY": "https://proxy.invalid:443",
                                                "DEEPSEEK_MODEL": "wrong-alias"}))
        self.authorize = self.enterContext(patch("flash_costs.authorize", side_effect=self.reserve))

    def reserve(self, ticket, kind, payload):
        self.assertIs(ticket, self.ticket)
        self.assertEqual(kind, "model")
        self.events.append(("reserve", kind, payload))
        return True

    def network(self, *responses):
        api = HTTPSStub(self, responses, self.events)
        return patch("flash_transport.http.client.HTTPSConnection", side_effect=api.connect)

    def test_cited_json_uses_exact_model_gate_and_budget_before_post(self):
        with self.network(Response({"data": [model({"en-US": " 10\t17 ", "zh": "其他"})]}), Response(completion(body()))):
            result = self.generator.answer(QUESTION, [HIT])
        self.assertEqual(result["status"], "cited_draft", result)
        self.assertEqual(result["model"], "DeepSeek-V4.1-Flash-1017")
        self.assertEqual(self.generator.answer_method, "deepseek_cited_draft")
        self.assertEqual(result["claims"][0]["span"], QUOTE)
        self.assertEqual([event[0] for event in self.events], ["GET", "reserve", "POST"])
        method, path, payload, headers = self.events[-1]
        self.assertEqual((method, path), ("POST", "/chat/completions"))
        self.assertEqual(payload, self.events[1][2])
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("seed", payload)
        self.assertNotIn("chat_template_kwargs", payload)
        self.assertNotIn(KEY, json.dumps(payload))
        for field in SCHEMA["required"]:
            self.assertIn(json.dumps(field), payload["messages"][0]["content"])
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertNotIn("x-api-key", headers)
        self.assertEqual(self.events[0][1], "/models")
        self.assertEqual(self.events[0][3]["x-api-key"], KEY)
        self.assertNotIn("Authorization", self.events[0][3])
        self.assertTrue(headers["User-Agent"].startswith("EvidenceDesk/"))

    def test_sse_yields_deltas_and_keeps_citation_validation(self):
        content = body(answer="99 mL")
        shown = []
        with self.network(Response({"data": [model()]}), Response(sse(content[:38], content[38:]), mime="text/event-stream")):
            result = self.generator.answer(QUESTION, [HIT], on_delta=shown.append)
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["reason"], "unsupported_number")
        self.assertEqual("".join(shown), "99 mL")
        self.assertTrue(self.events[-1][2]["stream"])
        self.assertEqual(self.events[-1][3]["Accept"], "text/event-stream")

    def test_incorrect_or_ambiguous_models_fail_closed(self):
        for data in ([model(name="DeepSeek-V4.1-Flash-1017")], [model(alias="DeepSeek-Flash")],
                     [model("1016")], [model({"zh": "1017"})], [model({"en": "1017", "en_US": "1018"})],
                     [model(1017)], [model(), model()], [{"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash"}]):
            with self.subTest(data=data):
                self.events.clear()
                with self.network(Response({"data": data})), self.assertRaises(FlashError) as caught:
                    self.generator.answer(QUESTION, [HIT])
                self.assertEqual(caught.exception.reason, "unverified_model")
                self.assertEqual([e[0] for e in self.events], ["GET"])
        self.authorize.assert_not_called()

    def test_model_status_is_unbilled_and_redirects_are_not_followed(self):
        with self.network():
            self.assertEqual(get_status()["status"], "configured")
        with self.network(Response({"data": [model()]})):
            self.assertTrue(self.generator.get_status(verify=True)["verified"])
        with self.network(Response(b"", status=302)), self.assertRaises(FlashError) as caught:
            self.generator.answer(QUESTION, [HIT])
        self.assertEqual(caught.exception.reason, "metadata_unavailable")
        self.assertEqual([event[0] for event in self.events], ["GET", "GET"])
        self.authorize.assert_not_called()

    def test_legacy_json_retains_its_prompt_and_is_gated(self):
        original = {"model": "ignored", "messages": [{"role": "user", "content": 'Select JSON {"quotes":[]}.'}],
                    "max_tokens": 1200, "response_format": {"type": "json_object"}, "stream": False}
        raw = completion('{"quotes":[]}')
        with self.network(Response({"data": [model()]}), Response(raw)):
            actual = self.generator.legacy_transport(json.dumps(original).encode(), KEY)
        self.assertEqual(actual, raw)
        self.assertEqual(self.events[-1][2]["messages"], original["messages"])
        self.assertEqual(self.events[-1][2]["model"], "deepseek-flash")
        self.assertEqual(self.events[-1][2]["thinking"], {"type": "disabled"})
        self.assertEqual([event[0] for event in self.events], ["GET", "reserve", "POST"])

    def test_budget_exception_and_corrupt_ledger_are_visible(self):
        self.authorize.side_effect = BudgetExceeded("unknown_price")
        with self.network(Response({"data": [model()]})), self.assertRaises(BudgetExceeded) as caught:
            self.generator.answer(QUESTION, [HIT])
        self.assertEqual(caught.exception.reason, "unknown_price")
        self.authorize.side_effect = OSError(KEY)
        with self.network(Response({"data": [model()]})), self.assertRaises(FlashError) as caught:
            self.generator.answer(QUESTION, [HIT])
        self.assertEqual(caught.exception.reason, "authorization_failed")
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual([event[0] for event in self.events], ["GET", "GET"])

    def test_credentials_are_excluded_from_errors_and_model_input(self):
        with self.network(), self.assertRaises(FlashError) as caught:
            self.generator.answer(f"{QUESTION} {KEY}", [HIT])
        self.assertEqual(caught.exception.reason, "credential_in_payload")
        self.assertNotIn(KEY, str(caught.exception))
        self.authorize.assert_not_called()
        with self.network(Response({"data": [model()]}), OSError(KEY)), self.assertRaises(FlashError) as caught:
            self.generator.answer(QUESTION, [HIT])
        self.assertEqual(caught.exception.reason, "provider_unavailable")
        self.assertNotIn(KEY, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_missing_and_malformed_key_fail_without_a_connection(self):
        for key, reason in [("", "missing_key"), ("not\na-valid-key", "invalid_key")]:
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": key}), self.network():
                self.assertEqual(get_status(verify=True)["reason"], reason)
                with self.assertRaises(FlashError) as caught:
                    self.generator.answer(QUESTION, [HIT])
                self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(self.events, [])
