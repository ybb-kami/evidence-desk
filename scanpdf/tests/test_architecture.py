import unittest
from unittest.mock import patch
import workbench
from architecture import _difference, compare, metric_observation


def paper(name, *texts):
    return dict(id=name, name=name, bibliographic_identity={"title":name},
                chunks=[dict(page=1, block=i, part=1, text=text) for i,text in enumerate(texts, 1)])


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.base = paper("A new model",
                          "Encoder: we use a stack of three layers with self-attention and feed-forward units.",
                          "Decoder: a stack of three layers uses attention over the output of the encoder.")
        self.child = paper("Method B",
                           "Atlas’s architecture is a multilayer bidirectional Transformer encoder based on "
                           "the original implementation described in Mora et al. (2020).",
                           "S. Mora, A. Lee. 2020. A new model.")

    def test_roles_in_either_order_resolve_original_work(self):
        result = compare([self.child,self.base], "对比 Encoder 的承袭关系")
        self.assertIn("Atlas", result["answer"])
        self.assertEqual([len(p["hits"]) for p in result["papers"]], [2,2])
        self.assertIn("省去", compare([self.base,self.child],"Encoder")["answer"])

    def test_unmatched_citation_or_requested_title_yields_gap(self):
        self.child["chunks"].pop()
        self.assertIsNone(compare([self.base,self.child],"Encoder")["answer"])
        self.assertIn("题名", compare([self.base,self.child],"《Andere》 Encoder")["gap"])

    def test_series_requires_matching_reference_and_training_values(self):
        first = paper("Pretraining decoders", "We trained a 6-layer decoder-only transformer (256 dimensional states). "
                      "We train on sequences of 300 tokens.")
        second = paper("New model",
                       "Our smallest model is equivalent to the original Atlas, and our largest model, which we call Atlas-2, is larger.",
                       "The model largely follows the details of the Atlas model (Mora et al., 2020)",
                       "Mora et al. Pretraining decoders. 2020.",
                       "We increase the context size from 300 to 700 tokens.")
        third = paper("Large model", "We train Atlas-3, an autoregressive language model. "
                      "All models use a context window of nctx = 1400 tokens.")
        result = compare([second,third,first], "上下文长度")
        self.assertIn("300、Atlas-2 700、Atlas-3 1400", result["answer"])
        second["chunks"][-1]["text"] = "We increase the context size from 480 to 700 tokens."
        self.assertIsNone(compare([first,second,third],"上下文长度")["answer"])

    def test_metric_observations_need_explicit_subject(self):
        self.assertIn("196 million", metric_observation("参数规模", "We train Atlas, a model with 196 million parameters."))
        self.assertIsNone(metric_observation("参数规模", "Referenced models have 196 million parameters."))
        self.assertIn("896 tokens", metric_observation("上下文或训练序列长度", "We train on sequences of 896 tokens."))
        self.assertIn("最大上下文上限", metric_observation("上下文或训练序列长度", "We train on sequences of 896 tokens."))

    def test_context_gaps_and_raw_evidence(self):
        a = paper("A", "We used sequences of 896 tokens.")
        b = paper("B", "Our training schedule was unchanged.")
        result = compare([a,b], "从GPT-1到GPT-3的上下文长度如何变化？")
        self.assertIn("缺少", result["gap"])
        self.assertIsNone(result["answer"])
        self.assertEqual(len(result["papers"][0]["hits"]),1)
        self.assertEqual(result["papers"][1]["hits"],[])

    def test_gpt2_presence_is_checked_by_selected_paper_not_count(self):
        first = paper("Improving Language Understanding by Generative Pre-Training", "We train on sequences of 512 tokens.")
        third = paper("Language Models are Few-Shot Learners", "All models use a context window of nctx = 2048 tokens.")
        unrelated = paper("A paper reviewing GPT-2", "GPT-2 is referenced below.")
        result = compare([third, unrelated, first], "从GPT-1到GPT-3的上下文长度如何变化？")
        self.assertIsNone(result["answer"])
        self.assertIn("缺少 GPT-2 原始论文", result["gap"])

    def test_requested_title_and_version_must_belong_to_same_paper(self):
        earlier = dict(self.base, arxiv={"id": "1706.03762", "version": 1})
        different = dict(self.child, arxiv={"id": "2101.10311", "version": 7})
        result = compare([earlier, different], "《A new model》v7与《Method B》的训练条件各是什么？")
        self.assertIsNone(result["answer"])
        self.assertIn("缺少该题名的 v7", result["gap"])
        explicit = compare([earlier, different], "对照 arXiv:1706.03762v7 与所选文献。")
        self.assertIn("1706.03762v7", explicit["gap"])

    def test_each_paper_must_supply_its_own_original_evidence(self):
        one = paper("First trial", "We measured a latency of 10 seconds in the main trial.")
        two = paper("Second trial", "The experiment used a different scheduling policy.")
        local = dict(one["chunks"][0], id=one["id"], name=one["name"])
        with patch("followup.lookup", side_effect=[{"answer": "10 seconds", "hits": [local]},
                                                   {"answer": "10 seconds", "hits": [local]}]) as lookup:
            result = compare([one, two], "对比两篇论文，各自的时延是多少？差多少？", store=object())
        reqs = [call.args[1] for call in lookup.call_args_list]
        self.assertEqual([req["id"] for req in reqs], [one["id"], two["id"]])
        self.assertTrue(all("差多少" not in req["question"] for req in reqs))
        self.assertEqual(result["papers"][0]["answer"], "10 seconds")
        self.assertIsNone(result["papers"][1]["answer"])
        self.assertEqual(result["papers"][1]["hits"], [])
        self.assertIsNone(result["answer"])

    def test_difference_requires_same_named_protocol_metric_row_and_unit(self):
        def reading(value="83.5 %", target="CrossBench", metric="Accuracy (%)", row="Shared baseline"):
            return {"answer": value, "table_cells": [dict(page=3, method="native_pdf", table=2, value=value,
                                                         row_label=row, column_label=metric)],
                    "hits": [dict(page=3, text=f"Table 2: Accuracy on the {target} test split.")]}

        first, second = reading(), reading(value="80.0 %")
        self.assertIn("3.5个百分点", _difference([first, second]))
        for alternate in (reading(target="OtherBench"), reading(value="80.0"), reading(metric="Precision (%)"),
                          reading(row="Another run"), reading(target="held-out")):
            self.assertIsNone(_difference([first, alternate]))
        self.assertIsNone(_difference([first, second, reading()]))
