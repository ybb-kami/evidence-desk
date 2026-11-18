import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
from flash_verify import handle
from paid_budget import BudgetExceeded


class FlashVerifyTests(unittest.TestCase):
    def setUp(self):
        self.quote = "The authors benchmarked the same network configuration."
        self.hit = {"id": "a" * 64, "name": "private.pdf", "page": 2, "block": 6, "text": self.quote}
        self.handler = Mock()
        self.handler.server = SimpleNamespace(enable_model=True, library=Mock())
        self.handler.json_request.return_value = {"query": "  Which configuration?  ",
                                                  "ids": [self.hit["id"]], "consent": True}
        self.handler.payload.side_effect = lambda status, data: (status, data)
        self.key = "fixture-verification-key"
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": self.key})
        env.start()
        self.addCleanup(env.stop)

    def test_checks_consent_and_ids_before_loading_any_evidence(self):
        self.handler.json_request.return_value["consent"] = None
        with self.assertRaises(app.Bad) as denied:
            handle(self.handler)
        self.assertEqual(denied.exception.code, 403)
        self.handler.json_request.return_value.update(ids=["invalid"], consent=True)
        with self.assertRaises(app.Bad) as invalid:
            handle(self.handler)
        self.assertEqual(invalid.exception.code, 400)
        self.handler.server.library.records.assert_not_called()
        self.handler.flash_model.assert_not_called()

    def test_attaches_model_and_does_not_export_local_identifiers(self):
        returned = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"quotes": [{"id": "e1", "text": self.quote}]})}}]}
        transport = Mock(return_value=json.dumps(returned).encode())
        self.handler.flash_model.return_value = SimpleNamespace(legacy_transport=transport,
                                                                model="DeepSeek-V4.1-Flash-1017")
        with patch("app.find_evidence", return_value=[self.hit]), patch("provider._send") as bypass:
            code, result = handle(self.handler)
        self.assertEqual((code, result["status"], result["model"]), (200, "supported", "DeepSeek-V4.1-Flash-1017"))
        self.assertIn(result["model"], result["answer"])
        self.assertEqual(result["quotes"][0]["text"], self.quote)
        body, key = transport.call_args.args
        self.assertEqual(key, self.key)
        self.assertNotIn(self.hit["id"].encode(), body)
        self.assertNotIn(self.hit["name"].encode(), body)
        bypass.assert_not_called()

    def test_budget_denial_survives_quote_provider_wrapper(self):
        transport = Mock(side_effect=BudgetExceeded("money_limit"))
        self.handler.flash_model.return_value = SimpleNamespace(legacy_transport=transport)
        with patch("app.find_evidence", return_value=[self.hit]), patch("provider._send") as bypass:
            with self.assertRaises(app.Bad) as denied:
                handle(self.handler)
        self.assertEqual(denied.exception.code, 429)
        self.assertIn("预算", denied.exception.msg)
        transport.assert_called_once()
        bypass.assert_not_called()


if __name__ == "__main__":
    unittest.main()
