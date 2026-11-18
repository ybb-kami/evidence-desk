"""Synthetic offline tests, plus optional acceptance using public arXiv originals."""
import os
from pathlib import Path
import unittest

from engine import parse_pdf
from evidence_questions import locate_question
from test_acceptance import LocalHTTPFixture
from test_v02_acceptance import fixture


def sample_docs():
    before = fixture("Imaginary Work on Translation and Paper Lanterns",
                     "For English-to-French, the model achieves a BLEU score of 32.5.")
    after = fixture("Invented Study on Translation and Violet Lanterns",
                    "For English-to-French, the model achieves a BLEU score of 34.5.",
                    "For English-to-French, the model achieves a BLEU score of 32.8.")
    return before, after


class EvidenceQuestionsTests(unittest.TestCase):
    def test_bounded_source_specific_values_and_verbatim_anchors(self):
        docs = [parse_pdf(content, f"D{i}.pdf") for i, content in enumerate(sample_docs())]
        r = locate_question(docs, "英法BLEU分数是多少？")
        self.assertEqual(r["status"], "evidence_found")
        self.assertEqual(r["concepts"][0]["id"], "bleu")
        self.assertEqual({row["value"] for row in r["observations"]}, {"32.5", "34.5", "32.8"})
        self.assertTrue(any("不选定唯一分数" in note for note in r["notes"]))
        for doc, paper in zip(docs, r["papers"]):
            self.assertEqual(doc["id"], paper["id"])
            for hit in paper["hits"]:
                self.assertIn(hit["text"], [c["text"] for c in doc["chunks"] if c["page"] == hit["page"]])
        self.assertEqual(locate_question(docs, "英译德的 BLEU")["status"], "insufficient_evidence")

    def test_localized_terms_compound_question_missing_other_document(self):
        data = fixture("Invented Measurements at a Lighthouse",
                       "Training used 3 RTX 4060 GPUs. The AdamW optimizer used beta1 = 0.8 and beta2 = 0.95.")
        docs = [parse_pdf(data, "synthetic.pdf"),
                parse_pdf(fixture("Unrelated Measurements at a Lighthouse","Empirical lantern patterns only."),
                          "none.pdf")]
        r = locate_question(docs,"GPU数量、优化器及β₁和β₂是多少？")
        self.assertEqual([c["id"] for c in r["concepts"]],["hardware","optimizer","betas"])
        self.assertEqual(r["papers"][1]["hits"], [])
        self.assertIn("3 RTX 4060 GPUs",r["papers"][0]["hits"][0]["text"])
        unsupported = locate_question(docs,"unicornxqzy")
        self.assertEqual(unsupported["status"],"insufficient_evidence")
        self.assertFalse(unsupported["observations"])
        with self.assertRaises(ValueError):
            locate_question([], "GPU")

    def test_only_explicit_report_labels_are_emitted_but_original_remarks_remain(self):
        statements = (
            "For English-to-French, the model did not achieve a BLEU score of 39.0.",
            "For English-to-French, the target is a BLEU score of 42.0.",
            "For English-to-French, we improved by 2.0 BLEU.",
            "For English-to-French, we trained 24 models and evaluate BLEU.",
            "For English-to-French, a future model may achieve a BLEU score of 59.5.",
            "For English-to-French, the earlier system reaches 32 BLEU points higher than baseline.",
            "For English-to-French, the model reaches a BLEU score of 35.5%.",
            "For English-to-French; a separate experiment achieves 41.5 BLEU.",
        )
        for sentence in statements:
            with self.subTest(sentence=sentence):
                doc = parse_pdf(fixture("Illustrative Translation Notes and Lanterns", sentence), "sample.pdf")
                result = locate_question([doc], "英法 BLEU")
                self.assertFalse(result["observations"])
                self.assertEqual(result["status"], "evidence_found")
                original = " ".join(sentence.split())
                self.assertTrue(any(original in " ".join(hit["text"].split())
                                    for hit in result["papers"][0]["hits"]))

    def test_mapped_and_literal_clauses_retrieve_separate_original_evidence(self):
        raw = fixture("Training Architecture in a Fictional Study",
                      "Our training uses 8 A100 GPUs.",
                      "A separate method names the frobnicator baseline.")
        doc = parse_pdf(raw, "mixed.pdf")
        for query in ("GPU and frobnicator baseline", "GPU、frobnicator baseline"):
            with self.subTest(query=query):
                result = locate_question([doc], query)
                self.assertEqual([c["id"] for c in result["concepts"]], ["hardware"])
                hits = result["papers"][0]["hits"]
                self.assertTrue(any("A100" in h["text"] and h["concept_ids"] == ["hardware"] for h in hits))
                self.assertTrue(any("frobnicator baseline" in h["text"] and h["concept_ids"] == [] for h in hits))

    def test_compound_english_question_discards_interrogative_scaffolding(self):
        raw = fixture("Training Architecture in a Fictional Study",
                      "Our training uses 8 A100 GPUs.",
                      "The learning rate was 0.003.")
        r = locate_question([parse_pdf(raw,"mixed.pdf")], "What GPU and learning rate were used?")
        hits = r["papers"][0]["hits"]
        self.assertTrue(any("A100" in hit["text"] and hit["page"] == 1 for hit in hits))
        self.assertTrue(any("learning rate was 0.003" in hit["text"] and hit["page"] == 2 for hit in hits))

    @unittest.skipUnless(os.getenv("EVIDENCE_DESK_PUBLIC_ATTENTION_V7"), "Optional public original not supplied")
    def test_actual_public_attention_v7(self):
        p = Path(os.environ["EVIDENCE_DESK_PUBLIC_ATTENTION_V7"])
        doc = parse_pdf(p.read_bytes(), p.name)
        self.assertEqual(doc["arxiv"],{"id":"1706.03762","version":7})
        checks=[("用的什么显卡？","8 NVIDIA P100 GPUs"),
                ("用了什么优化器，β₁ 和 β₂ 怎么选？","Adam optimizer")]
        for query, target in checks:
            self.assertTrue(any(target in hit["text"] for hit in locate_question([doc],query)["papers"][0]["hits"]))
        result=locate_question([doc],"英译法的 BLEU 分数？")
        self.assertEqual({(o["page"],o["value"]) for o in result["observations"]},{(1,"41.8"),(8,"41.0")})
        self.assertTrue(any("不选定唯一分数" in note for note in result["notes"]))


class EvidenceQuestionsHTTPTests(LocalHTTPFixture, unittest.TestCase):
    def test_http_keeps_chosen_document_and_separate_insufficient_status(self):
        before,after = sample_docs()
        ida=self.import_pdf("before.pdf",before)["doc"]["id"]
        idb=self.import_pdf("after.pdf",after)["doc"]["id"]
        response=self.read_json("/api/questions",{"query":"英译法 BLEU","ids":[idb]})
        self.assertEqual([p["id"] for p in response["papers"]],[idb])
        self.assertEqual({o["id"] for o in response["observations"]},{idb})
        response=self.read_json("/api/questions",{"query":"不知所谓术语","ids":[ida,idb]})
        self.assertEqual(response["status"],"insufficient_evidence")
        self.assertTrue(all(not p["hits"] for p in response["papers"]))


if __name__ == "__main__":
    unittest.main()
