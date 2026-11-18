import json
import unittest
from unittest.mock import patch

import workbench
from local_generation import LocalGenerator, excerpts
from passage_answer import assess_spans


class LocalGenerationTests(unittest.TestCase):
    def setUp(self):
        self.model = LocalGenerator("http://127.0.0.1:12345/v1/chat/completions", "test-local")
        self.text = "We labeled 37 validation items. Two reviewers labeled every item independently."
        self.hits = [dict(id="b"*64, name="source.pdf", page=2, block=7, part=1, text=self.text)]

    def _reply(self, *, quote="37 validation items", answer="37 items", status="answered", source=1):
        payload = {"status":status, "source":source, "answer":answer, "quote":quote}
        return {"choices": [{"finish_reason":"stop", "message":{"content":json.dumps(payload)}}]}

    def test_anchor_sent_to_browser_matches_source_offset(self):
        with patch.object(self.model, "_request", return_value=self._reply()) as sender:
            result = self.model.answer("How many validation items?", self.hits)
        claim, = result["claims"]
        self.assertEqual(result["status"], "cited_draft")
        self.assertEqual(result["model_reply"], "accepted")
        self.assertEqual(claim["span"], self.text[claim["answer_start"]:claim["answer_end"]])
        self.assertEqual((claim["page"],claim["block"]), (2,7))
        material = sender.call_args.args[0]["messages"][1]["content"]
        self.assertNotIn(self.hits[0]["id"], material)
        self.assertNotIn("source.pdf", material)

    def test_false_quote_and_new_number_are_rejected(self):
        for fake in [self._reply(quote="42 items"), self._reply(answer="73 items"),
                     self._reply(answer="-37 items"), self._reply(source=9)]:
            with self.subTest(fake=fake), patch.object(self.model, "_request", return_value=fake):
                result = self.model.answer("How many?", self.hits)
                self.assertEqual((result["status"], result["model_reply"]), ("validation_failed", "rejected"))

    def test_only_a_well_formed_reply_counts_as_an_explicit_decline(self):
        refusal = self._reply(status="insufficient", source=0, answer="", quote="")
        with patch.object(self.model, "_request", return_value=refusal) as send:
            no_source = self.model.answer("What price?", [])
            send.assert_not_called()
            explicit = self.model.answer("What price?", self.hits)
        self.assertEqual(no_source["reason"], "no_source")
        self.assertNotIn("model_reply", no_source)
        self.assertEqual((explicit["status"], explicit["model_reply"]), ("insufficient_evidence", "declined"))
        with patch.object(self.model, "_request", return_value=self._reply(status="insufficient")):
            malformed = self.model.answer("What price?", self.hits)
        self.assertEqual((malformed["status"], malformed["model_reply"]), ("validation_failed", "rejected"))

    def test_remote_or_redirectable_configuration_never_receives_source(self):
        for src in ("http://example.org:1234/v1/chat/completions", "http://localhost:1234/v1/chat/completions",
                    "http://127.0.0.1:1234/v1/chat/completions?q=evil", "http://user@127.0.0.1:1/v1/chat/completions"):
            model = LocalGenerator(src, "id")
            with patch.object(model, "_request", side_effect=AssertionError("unexpected egress")):
                self.assertEqual(model.answer("why?", self.hits)["status"], "misconfigured")
        with self.model._lock:
            self.assertEqual(self.model.answer("why?", self.hits)["status"], "busy")

    def test_excerpt_offsets_and_conservative_reader_gate(self):
        sources = excerpts(self.hits)
        for i, offset, raw in sources.values():
            self.assertEqual(raw, self.hits[i]["text"][offset:offset+len(raw)])
        context = self.text.split(".")[0] + "."
        a = self.text.index("37")
        row = {"hit_index":0,"answer":"37","answer_start":a,"answer_end":a+2,"context":context,
               "context_start":0,"context_end":len(context),"span_over_null":39,
               "id":self.hits[0]["id"],"page":2,"block":7,"part":1}
        _, positive = assess_spans("How many validation items were labeled?",
                                   "How many validation items were labeled?",[row],self.hits)
        self.assertIsNotNone(positive)
        for question in ("How many dollars were spent on the validation items?",
                         "What serial number was used by the reviewers?"):
            _, answer = assess_spans(question, question, [row], self.hits)
            self.assertIsNone(answer)
