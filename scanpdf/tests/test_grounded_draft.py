import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import workbench
import grounded_draft as draft
from paid_budget import BudgetExceeded


class GroundedDraftTests(unittest.TestCase):
    def setUp(self):
        self.source = "With the same budget, the baseline reached 71.4% after training on 32 sequences."
        self.hits = [{"id": "a" * 64, "name": "private-test.pdf", "page": 13, "block": 2,
                      "part": 3, "text": self.source}]
        self.key = "local-fixture"
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": self.key})
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def response(claims):
        return json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"claims": claims})}}]}).encode()

    def test_minimal_payload_and_server_bound_citation(self):
        claim = {"source": "E1", "text": "The baseline reached 71.4%.", "quote": self.source}
        sender = Mock(return_value=self.response([claim]))
        result = draft.generate("How did the baseline perform?", self.hits, transport=sender)
        self.assertEqual(result["status"], "cited_draft")
        self.assertEqual(result["claims"][0]["quote"], self.source)
        self.assertEqual((result["claims"][0]["page"], result["claims"][0]["part"]), (13, 3))
        payload, key = sender.call_args.args
        self.assertEqual(key, self.key)
        self.assertNotIn(self.hits[0]["id"].encode(), payload)
        self.assertNotIn(b"private-test.pdf", payload)
        self.assertNotIn(self.key.encode(), payload)
        self.assertEqual(set(json.loads(json.loads(payload)["messages"][1]["content"])), {"question", "excerpts"})

    def test_missing_source_fabricated_number_and_nonverbatim_quote(self):
        claim = {"source": "E1", "text": "The baseline reached 71.4%.", "quote": self.source}
        for change in [{"source": "E2"}, {"text": "The baseline reached 73%."}, {"quote": "The baseline reached 71.4%."}]:
            with self.subTest(change=change):
                result = draft.generate("Score?", self.hits,
                    transport=Mock(return_value=self.response([claim, dict(claim, **change)])))
                self.assertEqual(result["status"], "validation_failed")
                self.assertFalse(result["claims"])

    def test_no_evidence_or_configuration_does_not_send(self):
        sender = Mock(side_effect=AssertionError("Unexpected provider request"))
        self.assertEqual(draft.generate("Why?", [], transport=sender)["status"], "insufficient_evidence")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            self.assertEqual(draft.generate("Why?", self.hits, transport=sender)["status"], "not_configured")
        with self.assertRaises(workbench.app.Bad):
            draft.generate(self.key, self.hits, transport=sender)
        sender.assert_not_called()
        self.assertEqual(draft.generate("Why?", self.hits)["status"], "not_configured")

    def test_consent_and_configuration_precede_any_retrieval(self):
        handler = Mock()
        handler.server = SimpleNamespace(enable_model=False)
        request = {"id": self.hits[0]["id"], "question": "Score?", "anchor": None, "consent": False}
        handler.json_request.return_value = request
        with self.assertRaises(workbench.app.Bad) as ctx:
            draft.route(handler)
        self.assertEqual(ctx.exception.code, 403)
        handler.json_request.return_value = dict(request, consent=True)
        draft.route(handler)
        handler.payload.assert_called_once_with(200, {"status": "not_configured", "claims": []})

    def test_structured_gap_with_source_hits_does_not_request_a_draft(self):
        handler = Mock()
        handler.server = SimpleNamespace(enable_model=True, library=SimpleNamespace(dense=None))
        handler.json_request.return_value = {"id": self.hits[0]["id"], "question": "Which one?", "anchor": None, "consent": True}
        for method in ("book_index", "exercise_locator"):
            with self.subTest(method=method), patch("followup.lookup", return_value={"hits": self.hits, "answer_method": method}):
                draft.route(handler)
                handler.payload.assert_called_with(200, {"status": "insufficient_evidence", "claims": []})
                handler.flash_model.assert_not_called()

    def test_route_propagates_budget_denial_without_falling_back(self):
        handler = Mock()
        handler.server = SimpleNamespace(enable_model=True, library=SimpleNamespace(dense=None))
        handler.json_request.return_value = {
            "id": self.hits[0]["id"], "question": "Score?", "anchor": None, "consent": True,
        }
        transport = Mock(side_effect=BudgetExceeded("money_limit"))
        handler.flash_model.return_value = SimpleNamespace(legacy_transport=transport, model="DeepSeek-V4.1-Flash-1017")
        with patch("followup.lookup", return_value={"hits": self.hits}), patch("provider._send") as bypass:
            with self.assertRaises(workbench.app.Bad) as denied:
                draft.route(handler)
        self.assertEqual(denied.exception.code, 429)
        self.assertIn("预算", denied.exception.msg)
        transport.assert_called_once()
        bypass.assert_not_called()
