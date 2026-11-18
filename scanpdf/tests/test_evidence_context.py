import json
import unittest
from unittest.mock import patch

from evidence_context import MAX_SOURCE_CHARS, MAX_SOURCES, select_excerpts
from local_generation import LocalGenerator


def hit(block, text):
    return {"id": "f" * 64, "page": 4, "block": block, "part": 1, "text": text}


class EvidenceContextTests(unittest.TestCase):
    def test_model_input_retains_complementary_blocks_and_drops_explicitly_excluded_label(self):
        other = "Under condition A the exposure count was 31."
        count = "Under condition B the exposure count was 19."
        distance = "The carriage reached a travel distance of 640 meters."
        hits = [hit(1, other), hit(2, count), hit(3, distance)]
        question = "What were the exposure count and carriage travel distance under condition B instead of condition A?"
        model = LocalGenerator("http://127.0.0.1:8754/v1/chat/completions", "fixture")
        answer = {"status": "insufficient", "answer": "", "source": 0, "quote": ""}
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}
        with patch.object(model, "_request", return_value=response) as send:
            model.answer(question, hits)
        prompt = send.call_args.args[0]["messages"][1]["content"]
        self.assertIn(count, prompt)
        self.assertIn(distance, prompt)
        self.assertNotIn(other, prompt)

    def test_unspecified_conditions_remain_visible_for_ambiguity_validation(self):
        texts = ("Under condition A the exposure count was 31.", "Under condition B the exposure count was 19.")
        sources = select_excerpts([hit(i, text) for i, text in enumerate(texts, 1)], query="What was the exposure count?")
        self.assertEqual({source for _, _, source in sources.values()}, set(texts))

    def test_reference_scoped_question_does_not_extend_into_an_outside_measurement(self):
        subject = "The optical scan response latency was measured at each mount."
        outcome = "The precursor trial reached 27 milliseconds in a disconnected chamber."
        peers = [hit(i, f"Table 4 contains optical scan response latency for the {mount} fixture.")
                 for i, mount in enumerate(("left", "right", "upper", "lower", "red", "blue", "green"), 2)]
        question = "In Table 4, what was the optical scan response latency?"

        def selected(text):
            return [raw for _, _, raw in select_excerpts([hit(1, text), *peers], query=question).values()]

        self.assertIn(subject, selected(f"{subject} {outcome}"))
        self.assertTrue(all(outcome not in raw for raw in selected(f"{subject} {outcome}")))
        self.assertTrue(any(outcome in raw for raw in selected(f"Table 4: {subject} {outcome}")))

    def test_untranslated_query_uses_local_expansion_when_available(self):
        question = "封闭室的传感器读数是多少？"
        target = "The sensor reading in the sealed chamber was 63 kelvin."
        hits = [hit(1, "The pump impeller was installed by a local technician."), hit(2, target)]
        with patch("evidence_context.english_query", return_value="What was the sensor reading in the sealed chamber?"):
            sources = select_excerpts(hits, query=question)
        self.assertEqual(sources[1], (1, 0, target))

    def test_reader_interval_and_bounded_overlapping_windows_remain_verbatim(self):
        target = "pressure stabilization at constant load was verified in all channels"
        long_passage = "setup " * 100 + target + " probe" * 110
        reader = "Preparations took place in a vibration-free room. The resonance peak was measured at 47 Hz."
        hits = [hit(1, long_passage), hit(2, reader)]
        hint = {"hit_index": 1, "context_start": 0, "context_end": len(reader)}
        excerpts = select_excerpts(hits, [hint], query="Where was pressure stabilization at constant load verified?")
        self.assertTrue(any(target in raw for _, _, raw in excerpts.values()))
        self.assertTrue(any(raw == reader for _, _, raw in excerpts.values()))
        self.assertLessEqual(len(excerpts), MAX_SOURCES)
        self.assertLessEqual(sum(len(raw) for _, _, raw in excerpts.values()), MAX_SOURCE_CHARS)
        for index, start, raw in excerpts.values():
            self.assertEqual(raw, hits[index]["text"][start:start + len(raw)])
