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
        self.assertEqual(m["omitted_term_count"],7)
        self.assertTrue(any("另有 7 项未检索" in note for note in m["notes"]))
        self.assertTrue(all(c["status"]=="no_match" for r in m["rows"] for c in r["cells"]))

    def test_contest_question_ln_context_parameters_with_unmapped_comma_terms(self):
        docs=[parse_pdf(fixture("Synthetic System Configurations Alpha",
                    "Our model uses pre-LN. The context window is 611 tokens. We use dropout.",
                    "A contrast system reports 27 million trainable parameters."),
                    "alpha.pdf"),
              parse_pdf(fixture("Synthetic System Configurations Beta",
                    "Our model uses post-LN. The context size is 634 tokens. This model uses an optimizer.",
                    "Our tested model contains 19 million parameters."),
                    "beta.pdf")]
        m=compare_evidence(docs,
            "GPT-1/2/3 的 number of trainable parameters、LN 位置、Context Window，dropout, optimizer")
        rows={r["id"]:r for r in m["rows"]}
        for ident in ("layer_norm","context_window","parameter_count"):
            self.assertTrue(all(c["hits"] for c in rows[ident]["cells"]))
        literal={r["label"]:r for r in m["rows"] if r["id"].startswith("literal")}
        self.assertEqual(set(literal),{"dropout","optimizer"})
        self.assertTrue(literal["dropout"]["cells"][0]["hits"])
        self.assertTrue(literal["optimizer"]["cells"][1]["hits"])
        self.assertTrue(all(c["match_scope"]=="lexical_only_subject_unresolved"
                            for c in rows["parameter_count"]["cells"]))
        self.assertNotIn("conclusion",m)
        self.assertTrue(any("不证明归一化在子层之前或之后" in note for note in m["notes"]))

    def test_exact_count_exposes_bounded_hidden_candidate_not_source_attribution(self):
        import fitz
        with fitz.open() as pdf:
            for i in range(4):
                page=pdf.new_page()
                page.insert_text((50,100),"The baseline uses 14 million parameters." if i<3
                                      else "Our model uses 27 million parameters.")
            a=parse_pdf(pdf.tobytes(),"four.pdf")
        b=parse_pdf(samples()[1],"unrelated.pdf")
        m=compare_evidence([a,b],"number of trainable parameters")
        c=m["rows"][0]["cells"][0]
        self.assertEqual((c["candidate_count"],c["hidden_count"],c["candidate_count_is_lower_bound"]), (4,1,False))
        self.assertEqual([h["page"] for h in c["hits"]],[1,2,3])
        self.assertTrue(all("baseline" in h["text"] for h in c["hits"]))
        self.assertEqual(c["match_scope"],"lexical_only_subject_unresolved")

    @unittest.skipUnless(all(os.getenv(f"EVIDENCE_DESK_PUBLIC_GPT{i}") for i in (1,2,3)),
                         "Optional GPT-1/2/3 originals not supplied")
    def test_public_gpt_reports_contain_anchored_candidates_and_unresolved_cells(self):
        paths=[Path(os.environ[f"EVIDENCE_DESK_PUBLIC_GPT{i}"]) for i in (1,2,3)]
        docs=[parse_pdf(p.read_bytes(),p.name) for p in paths]
        self.assertEqual([d["pages"] for d in docs],[12,24,75])
        self.assertEqual(docs[2]["arxiv"],{"id":"2005.14165","version":4})
        m=compare_evidence(docs,"GPT-1/2/3 参数量、LN 位置、Context Window 是如何演进的？")
        rows={r["id"]:r for r in m["rows"]}
        for cell,page,tokens in zip(rows["context_window"]["cells"],(5,4,8),(512,1024,2048)):
            self.assertTrue(any(h["page"]==page and str(tokens)+" tokens" in " ".join(h["text"].split())
                                for h in cell["hits"]))
        p=rows["parameter_count"]["cells"]
        self.assertEqual(p[0]["status"],"no_match")
        self.assertTrue(any(h["page"]==1 and "1.5B parameter" in h["text"] for h in p[1]["hits"]))
        self.assertTrue(any(h["page"]==1 and "175 billion" in h["text"] for h in p[2]["hits"]))
        self.assertTrue(all(c["match_scope"]=="lexical_only_subject_unresolved" for c in p))
        self.assertTrue(any(h["page"]==8 and "pre-normalization" in h["text"] for h in rows["layer_norm"]["cells"][2]["hits"]))
        self.assertNotIn("conclusion",m)

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
