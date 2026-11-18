"""Exercise source integrity, excerpt privacy and reproducible workflow trace."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import unittest

from engine import parse_pdf
from test_acceptance import LocalHTTPFixture
from test_v02_acceptance import fixture
from workflow import run_workflow


def sample(name, report, previous=None):
    data = fixture("Original Workflow Fixture for " + name,report,previous)
    return data,parse_pdf(data,name+".pdf")


class Workflow(unittest.TestCase):
    def test_original_reextract_checks_page_block_part_offset_and_order(self):
        raw,a = sample("A","Our model used an A100 GPU.")
        rawb,b = sample("B","Only clouds and lanterns.")
        call_ids=[]
        def pdf(ident):
            call_ids.append(ident)
            return {a["id"]:raw,b["id"]:rawb}[ident]
        r = run_workflow([b,a],"哪种 GPU",pdf)
        self.assertEqual(r["schema"],"evidence-desk-workflow-v1")
        self.assertEqual([p["id"] for p in r["papers"]],[b["id"],a["id"]])
        self.assertEqual(call_ids,[a["id"]])
        self.assertEqual(r["papers"][0]["status"],"no_match")
        hits=r["papers"][1]["evidence"]
        self.assertTrue(hits)
        for e in hits:
            self.assertTrue(any(all(e[k]==c[k] for k in ("page","block","part","offset","text"))
                                for c in a["chunks"]))
        self.assertEqual([s["id"] for s in r["steps"]],["plan","retrieve","verify","summarize"])
        self.assertEqual(r["steps"][2]["verified_count"],len(hits))
        self.assertEqual(json.loads(json.dumps(r,ensure_ascii=False)),r)

    def test_corrupted_index_does_not_export_unverified_text(self):
        raw,a=sample("A","The authentic encoder is described normally.")
        corrupt=deepcopy(a)
        body=next(c for c in corrupt["chunks"] if "authentic encoder" in c["text"])
        body["text"]="The encoder is fake; UNVERIFIED_PRIVATE_INDEX_TEXT."
        r=run_workflow([corrupt],"encoder",lambda ident:raw)
        self.assertEqual(r["status"],"insufficient_evidence")
        self.assertEqual(r["papers"][0]["evidence"],[])
        self.assertEqual(r["papers"][0]["failures"][0]["reason"],"text_or_offset_mismatch")
        self.assertNotIn("UNVERIFIED_PRIVATE_INDEX_TEXT",json.dumps(r))
        self.assertEqual(r["steps"][-1]["status"],"refused_no_verified_evidence")

    def test_wrong_hash_and_wrong_offset_fail_closed(self):
        raw,a=sample("A","The Encoder uses a stack of two test layers.")
        rawb,_=sample("B","The Encoder uses a stack of two test layers.")
        r=run_workflow([a],"encoder",lambda ident:rawb)
        self.assertEqual(r["papers"][0]["source_status"],"hash_mismatch")
        self.assertTrue(all(x["reason"]=="source_hash_mismatch" for x in r["papers"][0]["failures"]))
        moved=deepcopy(a)
        for c in moved["chunks"]:
            if "Encoder" in c["text"]:
                c["offset"] += 1
        r=run_workflow([moved],"encoder",lambda ident:raw)
        self.assertEqual(r["status"],"insufficient_evidence")
        self.assertTrue(all(x["reason"]=="text_or_offset_mismatch" for x in r["papers"][0]["failures"]))

    def test_empty_refuses_without_opening_unrelated_documents(self):
        _,a=sample("A","The original contains seabirds only.")
        result=run_workflow([a],"utterlyunmatchabletoken",lambda _:self.fail("should not request original"))
        self.assertEqual(result["status"],"insufficient_evidence")
        self.assertEqual(result["steps"][2]["status"],"skipped_no_candidates")
        self.assertEqual(result["papers"][0]["source_status"],"not_read_no_candidates")

    @unittest.skipUnless(os.getenv("EVIDENCE_DESK_PUBLIC_ATTENTION_V7") and os.getenv("EVIDENCE_DESK_PUBLIC_BERT"),
                         "Optional original PDFs not supplied")
    def test_real_cross_paper_sources_remain_separate(self):
        raw=[Path(os.environ[k]).read_bytes() for k in ("EVIDENCE_DESK_PUBLIC_ATTENTION_V7","EVIDENCE_DESK_PUBLIC_BERT")]
        pdfs={hashlib.sha256(b).hexdigest():b for b in raw}
        docs=[parse_pdf(b,f"test-{i}.pdf") for i,b in enumerate(raw)]
        r=run_workflow(docs,"编码器；参数量",pdfs.get)
        for paper in r["papers"]:
            self.assertEqual(paper["status"],"verified_evidence")
            self.assertTrue(any(e["page"]==3 and "encoder" in e["concept_ids"] for e in paper["evidence"]))
            self.assertEqual(paper["failures"],[])
        self.assertNotIn("conclusion",r)


class WorkflowHTTP(LocalHTTPFixture,unittest.TestCase):
    def test_http_workflow_preserves_scope_and_runs_in_order(self):
        raw,_=sample("A","The Encoder uses a stack of two test layers.")
        rawb,_=sample("B","Unselected private PDF.")
        a=self.import_pdf("chosen.pdf",raw)["doc"]["id"]
        self.import_pdf("unrelated.pdf",rawb)
        r=self.read_json("/api/workflow",{"query":"encoder","ids":[a]})
        self.assertEqual([p["id"] for p in r["papers"]],[a])
        self.assertNotIn("unrelated.pdf",json.dumps(r))
        self.assertEqual(r["status"],"verified_evidence")


if __name__ == "__main__":
    unittest.main()
