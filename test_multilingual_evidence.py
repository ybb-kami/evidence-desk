import unittest

from engine import find_evidence
from byline import author_blocks, author_number


def document(*passages):
    return {
        "id": "local-test", "name": "Evidence.pdf",
        "page_labels": [f"S{page}" for page in range(1, len(passages) + 1)],
        "chunks": [{"page": page, "block": 1, "part": 1, "text": passage}
                   for page, passage in enumerate(passages, 1)],
    }


class MultilingualEvidenceTests(unittest.TestCase):
    def test_author_ordinal_uses_visual_rows_and_abstains_on_unparsed_author(self):
        title = "A Study of Reading Order"
        blocks = [
            (30, 10, 90, 20, title, 0, 0),
            (65, 40, 100, 59, "Min Lee\nLab\nml@lab.example", 1, 0),
            (10, 41, 45, 60, "Ren Xi\nLab\nrx@lab.example", 2, 0),
            (30, 70, 75, 90, "Ke Wu\nkw@lab.example", 3, 0),
            (30, 100, 80, 110, "Abstract", 4, 0),
        ]
        self.assertEqual(author_blocks(blocks, title), [3, 2, 4])
        self.assertEqual(author_number("请找第十二位作者"), 12)
        self.assertEqual(author_number("第２作者"), 2)
        blocks[1] = (*blocks[1][:4], "Unparsed name, another name", 1, 0)
        self.assertEqual(author_blocks(blocks, title), [])

    def test_gpu_architecture_context_requires_paper_evidence(self):
        docs = [document("We present the Ray architecture.",
                         "We trained our base model on six Atlas GPUs.")]
        question = "这篇论文中提到的 Ray 架构，在训练 Base 模型时使用了多少个 GPU？具体是什么型号？"
        self.assertEqual(find_evidence(docs, question)[0]["page"], 2)
        self.assertEqual(find_evidence(docs, question.replace("Ray", "Quartz")), [])

    def test_count_and_model_query_prefers_specific_gpu_evidence(self):
        docs = [document("Training ran for 2.5 days on four GPUs.",
                         "The same convolutional network was trained on six Ascend A300 GPUs.")]
        results = find_evidence(docs, "本文训练使用的GPU是什么型号，一共有多少块？")
        self.assertTrue(results)
        self.assertEqual(results[0]["page_label"], "S2")
        self.assertEqual(results[0]["text"], docs[0]["chunks"][1]["text"])

    def test_optimizer_notation_matches_pdf_beta_with_whitespace(self):
        docs = [document("Optimizer", "We used the AdamW optimiser with β 1 = 0.8 and β₂ = 0.95.")]
        results = find_evidence(docs, "用了什么优化器，β₁和 β_2 的取值分别是多少？")
        self.assertEqual([row["page"] for row in results], [2])
        self.assertIn("β 1 = 0.8", results[0]["text"])

    def test_compound_topics_can_point_at_different_blocks(self):
        docs = [document("We trained our network on four V200 GPUs.",
                         "We used the AdamW optimizer with weight decay.")]
        results = find_evidence(docs, "用了什么训练硬件和优化器")
        self.assertEqual([row["page"] for row in results], [1, 2])
        self.assertEqual(find_evidence(docs, "用了什么训练硬件和批归一化"), [])

    def test_citation_intent_prioritizes_reference_and_preserves_original(self):
        docs = [document("We used residual connections followed by layer normalization.",
                         "[6] Authors. Layer Normalization. Journal, 2022.")]
        body = find_evidence(docs, "在哪里使用了层归一化与残差连接？")
        ref = find_evidence(docs, "层归一化的完整参考文献")
        self.assertEqual(body[0]["page"], 1)
        self.assertEqual(ref[0]["text"], docs[0]["chunks"][1]["text"])

    def test_unknown_content_is_not_treated_as_question_scaffolding(self):
        docs = [document("We trained our network on four V200 GPUs using the Adam optimizer.")]
        self.assertEqual(find_evidence(docs, "论文使用了什么量子优化器？"), [])
        self.assertEqual(find_evidence(docs, "请问本文使用了什么？"), [])
        self.assertEqual(find_evidence(docs, "GPU microscope"), [])

    def test_specialized_surface_forms_are_not_partial_word_hits(self):
        docs = [document("Monolayer normalization was used in the microscopy pipeline.",
                         "The layer normalized rapidly after the disturbance.")]
        self.assertEqual(find_evidence(docs, "层归一化"), [])


if __name__ == "__main__":
    unittest.main()
