"""Synthetic and optional public-source multi-paper acceptance."""
import os
from pathlib import Path
import unittest
from urllib.error import HTTPError

from compare_evidence import compare_evidence
from engine import parse_pdf
from test_acceptance import LocalHTTPFixture
from test_v02_acceptance import fixture


def samples():
    return (
        fixture("A Fictional Lantern Architecture Study",
                "The encoder uses layer normalization. Maximum input length is 17 tokens.",
                "The model has 21 million trainable parameters."),
        fixture("A Synthetic Cloud Architecture Study",
                "The decoder has a context window of 23 tokens. The frobnicator baseline uses test inputs."),
    )


class Concepts(unittest.TestCase):
    def test_matrix_preserves_scope_missing_and_exact_source_chunks(self):
        docs = [parse_pdf(data, f"doc-{i}.pdf") for i,data in enumerate(samples())]
        m = compare_evidence(docs, "Encoder；Decoder；层归一化；上下文窗口；参数量；frobnicator")
        self.assertEqual([p["id"] for p in m["papers"]],[d["id"] for d in docs])
        self.assertEqual([r["id"] for r in m["rows"]], [
            "encoder","decoder","layer_norm","context_window","parameter_count","literal_5"])
        self.assertEqual([[bool(c["hits"]) for c in r["cells"]] for r in m["rows"]],
                         [[True,False],[False,True],[True,False],[True,True],[True,False],[False,True]])
        for row in m["rows"]:
            for cell, doc in zip(row["cells"],docs):
                self.assertEqual(cell["id"],doc["id"])
                self.assertEqual(cell["status"],"candidate_evidence" if cell["hits"] else "no_match")
                for hit in cell["hits"]:
                    self.assertTrue(any(all(c[k]==hit[k] for k in ("page","block","part","text"))
                                        for c in doc["chunks"]))
        self.assertTrue(any("无命中不代表" in note for note in m["notes"]))
        self.assertNotIn("answer",m)

    def test_rejects_one_or_duplicate_and_limits_literal_rows(self):
        docs = [parse_pdf(data, f"doc-{i}.pdf") for i,data in enumerate(samples())]
        for invalid in ([docs[0]], [docs[0], docs[0]], docs*5):
            with self.subTest(ids=[d["id"] for d in invalid]), self.assertRaises(ValueError):
                compare_evidence(invalid,"decoder")
        m=compare_evidence(docs,";".join(f"arbitrary{i}" for i in range(15)))
        self.assertEqual(len(m["rows"]),8)
        self.assertTrue(all(c["status"]=="no_match" for r in m["rows"] for c in r["cells"]))

    @unittest.skipUnless(os.getenv("EVIDENCE_DESK_PUBLIC_ATTENTION_V7") and os.getenv("EVIDENCE_DESK_PUBLIC_BERT"),
                         "Optional public Attention v7/BERT originals not supplied")
    def test_actual_bert_attention_encoder_and_decoder_contexts(self):
        paths=[Path(os.environ[k]) for k in ("EVIDENCE_DESK_PUBLIC_ATTENTION_V7","EVIDENCE_DESK_PUBLIC_BERT")]
        docs=[parse_pdf(p.read_bytes(),p.name) for p in paths]
        self.assertEqual([d["arxiv"]["id"] for d in docs],["1706.03762","1810.04805"])
        self.assertEqual(docs[0]["arxiv"]["version"],7)
        m=compare_evidence(docs,"编码器；decoder；参数量")
        encoder, decoder, params=m["rows"]
        self.assertTrue(any(h["page"]==3 and "stack of N = 6" in h["text"] for h in encoder["cells"][0]["hits"]))
        self.assertTrue(any(h["page"]==3 and "bidirectional Transformer en-" in h["text"] for h in encoder["cells"][1]["hits"]))
        self.assertTrue(any(h["page"]==4 and "left-context-only version" in h["text"] for h in decoder["cells"][1]["hits"]))
        self.assertTrue(any("110M" in h["text"] and "340M" in h["text"] for h in params["cells"][1]["hits"]))
        self.assertNotIn("conclusion",m)


class HTTPMatrix(LocalHTTPFixture, unittest.TestCase):
    def test_independent_columns_in_requested_order(self):
        a,b=[self.import_pdf(f"paper-{i}.pdf",source)["doc"]["id"] for i,source in enumerate(samples())]
        wanted=[a,b]
        result=self.read_json("/api/compare-evidence",{"query":"编码器；decoder","ids":wanted})
        self.assertEqual([d["id"] for d in result["papers"]],wanted)
        self.assertEqual([c["status"] for c in result["rows"][0]["cells"]],["candidate_evidence","no_match"])
        for payload in ({"query":"encoder","ids":[]},{"query":"encoder","ids":[a,a]}):
            with self.assertRaises(HTTPError) as failure:
                self.read_json("/api/compare-evidence",payload)
            self.assertEqual(failure.exception.code,400)
            failure.exception.close()


if __name__=="__main__":
    unittest.main()
