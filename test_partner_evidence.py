"""Exact-source tests for two narrow partner-origin retrieval options."""
import os
from pathlib import Path
import unittest

from engine import parse_pdf
from partner_byline import author_blocks
from partner_evidence import SOURCE_SHA
from test_v02_acceptance import fixture
from workflow import run_workflow


class PartnerEvidence(unittest.TestCase):
    def test_reference_candidate_comes_from_reference_section_not_mention(self):
        raw=fixture("New Evidence on Paper Lantern Designs",
                    "We often mention an Anemometer Algorithm in the background.",
                    "Anemometer Algorithm. S. Researcher. Journal of Wind Studies")
        doc=parse_pdf(raw,"fixture.pdf")
        result=run_workflow([doc],"请列出关于 “Anemometer Algorithm” 的参考文献（References）的完整作者和年份。",
                            lambda ident: raw)
        rows=result["papers"][0]["evidence"]
        self.assertTrue(any(row["page"]==2 and "partner_reference_entry" in row["concept_ids"] for row in rows))
        self.assertFalse(any(row["page"]==1 and "partner_reference_entry" in row["concept_ids"] for row in rows))
        self.assertEqual(result["source_methods"][0]["revision"],SOURCE_SHA)
        self.assertEqual(result["claim_support"],"not_assessed")
        for e in rows:
            self.assertTrue(any(all(e[k]==chunk[k] for k in ("page","block","part","offset","text"))
                                for chunk in doc["chunks"]))

    def test_byline_fails_closed_if_first_page_layout_ambiguous(self):
        title=(10,10,500,40,"Synthetic Author Ordinal Layout",0,0)
        abstract=(100,180,300,200,"Abstract",4,0)
        y_shifted=(30,95,115,150,"Different Layout",1,0)
        self.assertEqual(author_blocks([title,y_shifted,abstract],title[4]),[])

    def test_reference_alternate_source_not_used_after_hash_mismatch(self):
        raw=fixture("New Evidence on Paper Lantern Designs","Anemometer Algorithm",
                    "Anemometer Algorithm. S. Researcher. Journal of Wind Studies")
        doc=parse_pdf(raw,"fixture.pdf")
        result=run_workflow([doc],'References for Anemometer Algorithm',lambda ident:raw+b"x")
        self.assertEqual(result["status"],"insufficient_evidence")
        self.assertFalse(result["source_methods"])
        self.assertEqual(result["papers"][0]["source_status"],"hash_mismatch")

    @unittest.skipUnless(os.getenv("EVIDENCE_DESK_PUBLIC_ATTENTION_V7"),"Optional arXiv source not supplied")
    def test_actual_visual_sixth_author_and_complete_reference(self):
        source=Path(os.environ["EVIDENCE_DESK_PUBLIC_ATTENTION_V7"])
        raw=source.read_bytes(); doc=parse_pdf(raw,source.name)
        wanted=[
            ("论文Attention Is All You Need的第6作者是谁，在哪个公司/机构工作？",
             1,8,"Aidan N. Gomez","partner_author_ordinal"),
            ("请列出论文中引用的关于 “Layer Normalization” 的参考文献（References）的完整作者和年份。",
             10,13,"Jimmy Lei Ba","partner_reference_entry"),
        ]
        for query,page,block,literal,tag in wanted:
            with self.subTest(tag=tag):
                r=run_workflow([doc],query,lambda _:raw)
                self.assertEqual(r["source_methods"][0]["revision"],SOURCE_SHA)
                self.assertTrue(any(h["page"]==page and h["block"]==block and literal in h["text"]
                                    and tag in h["concept_ids"] for h in r["papers"][0]["evidence"]))
                self.assertEqual(r["claim_support"],"not_assessed")
        wrong=run_workflow([doc],"论文Not the Current Title的第6作者是谁？",lambda _:raw)
        self.assertFalse(wrong["source_methods"])


if __name__=="__main__":
    unittest.main()
