import json
import unittest
from unittest.mock import patch

from claim_support import check as check_claim
from local_generation import LocalGenerator, excerpts


CORPORA = (
    "Comparative experiments were performed on the Cedar corpus. "
    "The first 1,200 words were used for training, the following 180 for validation "
    "(model selection, early stopping) and the remaining 320 for testing. "
    "A second experiment was run on text from the Northern Valley (NV) Archive. "
    "The training set was a stream of 4,000 words, the validation set was a stream "
    "of about 1 million (875,120) words, and the test set contained 400 words."
)
GPUS = (
    "On a V100 GPU, the mean latency was 20 ms, whereas on an A100 GPU, "
    "the mean latency was 8 ms."
)


def hit(text, block=1):
    return dict(id="b" * 64, name="public-source.pdf", page=1, block=block, part=1, text=text)


def check(question, source, answer, quote=None, english=""):
    text = quote or answer
    start = source.index(text)
    return check_claim(question, english, [hit(source)], {
        "text": answer, "hit_index": 0, "answer_start": start, "answer_end": start + len(text), "span": text,
    })


class ConditionSupportTests(unittest.TestCase):
    def test_mixed_split_sizes_use_the_named_corpus(self):
        self.assertEqual(
            check("How many words were reserved for validation?", CORPORA, "180"),
            "ambiguous_measurement",
        )
        self.assertEqual(
            check("NV Archive 语料的验证集有多少词？", CORPORA, "180"),
            "different_condition",
        )
        self.assertIsNone(check("NV Archive 语料的验证集有多少词？", CORPORA, "875,120"))
        self.assertIsNone(check("How many validation words for Cedar corpus?", CORPORA, "180"))
        self.assertEqual(
            check("How many validation words for Indigo corpus?", CORPORA, "180"),
            "different_condition",
        )

    def test_training_and_test_counts_in_same_scope_do_not_create_ambiguity(self):
        source = CORPORA.split("A second experiment")[0]
        self.assertIsNone(check("How many words were used for validation?", source, "180"))

    def test_chinese_scope_and_numbers_need_no_translation(self):
        source = "甲数据集验证集包含200条记录。乙数据集验证集包含350条记录。"
        self.assertEqual(check("验证集有多少条记录？", source, "200"), "ambiguous_measurement")
        self.assertEqual(check("乙数据集验证集有多少条记录？", source, "200"), "different_condition")
        self.assertIsNone(check("乙数据集验证集有多少条记录？", source, "350"))

    def test_comparison_and_range_remain_answerable(self):
        self.assertEqual(check("What was the mean latency?", GPUS, "20 ms"), "ambiguous_measurement")
        self.assertEqual(check("What was the A100 latency?", GPUS, "20 ms"), "different_condition")
        self.assertIsNone(check("What was the A100 latency?", GPUS, "8 ms"))
        self.assertIsNone(check("Compare V100 and A100 latency.", GPUS, "20 ms and 8 ms", GPUS))
        span = "Under condition Fast, latency ranged from 10 to 18 ms."
        self.assertIsNone(check("What was the latency range under condition Fast?", span, "10 to 18 ms"))

    def test_equivalent_latency_units_do_not_conflict(self):
        source = "On GPU V100 latency was 1 second. On GPU A100 latency was 1,000 ms."
        self.assertIsNone(check("What was the latency?", source, "1 second"))

    def test_bare_article_does_not_select_condition_a(self):
        source = "Under condition A, accuracy was 92%. Under condition B, accuracy was 85%."
        self.assertEqual(check("What was the accuracy of a model?", source, "92%"), "ambiguous_measurement")
        self.assertIsNone(check("What was the accuracy under condition A?", source, "92%"))

    def test_explicit_hardware_excludes_other_hardware_even_when_dataset_differs(self):
        source = "On the V100 GPU for the Cedar dataset, latency was 20 ms. " \
                 "On the A100 GPU for the Maple dataset, latency was 8 ms."
        self.assertIsNone(check("What was the A100 latency?", source, "8 ms"))
        self.assertEqual(check("What was the A100 latency?", source, "20 ms"), "different_condition")

    def test_same_value_is_supported_by_the_requested_condition_within_a_long_quote(self):
        source = "Under condition A, accuracy was 92%. Under condition B, accuracy was 92%."
        self.assertIsNone(check("What was condition B accuracy?", source, "92%", source))

    def test_other_document_cannot_create_a_new_named_scope_conflict(self):
        a = "Under condition Fast, accuracy was 92%."
        b = "Accuracy reached a high of 87% under condition Slow."
        rows = [hit(a), dict(hit(b, 2), id="c" * 64)]
        start = a.index("92%")
        claim = {"text": "92%", "span": "92%", "hit_index": 0, "answer_start": start, "answer_end": start + 3}
        self.assertIsNone(check_claim("What was the accuracy?", "", rows, claim))
        rows[1]["id"] = rows[0]["id"]
        self.assertEqual(check_claim("What was the accuracy?", "", rows, claim), "ambiguous_measurement")

    def test_numeric_condition_is_not_the_reading(self):
        source = "At 20 °C, throughput was 86 samples per second. At 40 °C, throughput was 110 samples per second."
        self.assertEqual(check("What was the throughput?", source, "86"), "ambiguous_measurement")
        self.assertEqual(check("What was the throughput at 40 °C?", source, "86"), "different_condition")
        self.assertIsNone(check("What was the throughput at 40 °C?", source, "110"))

    def test_explicit_epoch_comparison_allows_two_named_sizes(self):
        source = "We trained for up to 40 epochs for large datasets and 100 epochs for small datasets."
        self.assertIsNone(check("How many epochs for large and small datasets respectively?",
                                source, "40 epochs and 100 epochs", source))

    def test_unscoped_quote_cannot_supply_an_explicit_corpus_condition(self):
        source = "The validation set contained 120 words."
        self.assertEqual(
            check("How many validation words in the Indigo corpus?", source, "120"),
            "different_condition",
        )

    def test_generation_retains_the_original_bound_quote_for_a_valid_scope(self):
        rows = [hit(CORPORA)]
        model = LocalGenerator("http://127.0.0.1:12345/v1/chat/completions", "test-local")
        for question, answer, reason in (
            ("How many words were reserved for validation?", "180", "ambiguous_measurement"),
            ("How many validation words for NV Archive?", "180", "different_condition"),
            ("How many validation words for NV Archive?", "875,120", None),
        ):
            with self.subTest(question=question, answer=answer):
                selected = excerpts(rows, query=question)
                label = next(label for label, (_, _, text) in selected.items() if answer in text)
                response = {"status": "answered", "answer": answer, "source": label, "quote": answer}
                payload = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response)}}]}
                with patch.object(model, "_request", return_value=payload):
                    result = model.answer(question, rows)
                if reason:
                    self.assertEqual((result["status"], result["reason"]), ("insufficient_evidence", reason))
                else:
                    self.assertEqual(result["status"], "cited_draft")
                    claim, = result["claims"]
                    self.assertEqual(claim["span"], CORPORA[claim["answer_start"]:claim["answer_end"]])


if __name__ == "__main__":
    unittest.main()
