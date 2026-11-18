"""Real PDF/HTTP evidence for conservative later-reading candidate reporting."""
import unittest

import fitz

from engine import parse_pdf
from local_review import collection_review
from test_acceptance import LocalHTTPFixture

OLDER="A Survey of Lamps Along the Fictional Northern Trail"
LATER="An Inventory of Maps from a Fictional Harbor"
DIRECTION="Future work should examine adaptive spectral clustering for sparse sensors."
LEAD="Adaptive spectral clustering is evaluated on sparse sensors under colder conditions."
UNRELATED="Cold winds alter how painted labels remain attached to field equipment."


def pdf(title, text, *, stamp=None, additional_stamp=None, reference=None, only_bibliography_overlap=False):
    with fitz.open() as doc:
        doc.set_metadata({"title":title, "subject":"Original synthetic evidence fixture"})
        p=doc.new_page(width=612,height=792)
        p.insert_text((65,72),title,fontsize=18)
        p.insert_text((65,160),text,fontsize=10)
        if stamp:
            p.insert_text((25,650),stamp,fontsize=9,rotate=90)
        if additional_stamp:
            p.insert_text((47,650),additional_stamp,fontsize=9,rotate=90)
        if reference:
            p=doc.new_page(width=612,height=792)
            p.insert_text((65,72),"References",fontsize=14)
            p.insert_text((65,130),"Fictional Author, 2017. "+reference+".",fontsize=10)
            if only_bibliography_overlap:
                p.insert_text((65,170),"Fictional Author, 2017. "+LEAD,fontsize=10)
        return doc.tobytes()


def documents(*, earlier_stamp="arXiv:1706.12345v1 [cs.LG] 12 Jun 2017",
              later_stamp="arXiv:1801.23456v1 [cs.LG] 10 Jan 2018",
              later_text=LEAD, cite=True, reverse=False, additional_stamp=None, refs_only=False):
    older=pdf(OLDER,DIRECTION,stamp=earlier_stamp,reference=LATER if reverse else None)
    newer=pdf(LATER,later_text,stamp=later_stamp,additional_stamp=additional_stamp,
              reference=OLDER if cite and not reverse else None,only_bibliography_overlap=refs_only)
    return [(older,"first.pdf"),(newer,"actually-later.pdf")]


class FrontierTests(unittest.TestCase):
    def test_counterexamples_all_fail_closed(self):
        for condition in (
            {"cite":False},                     # lexical overlap only
            {"reverse":True},                   # citation arrow wrong way
            {"later_stamp":None},               # no verified time
            {"later_stamp":"arXiv:1801.23456 [cs.LG] 10 Jan 2018"}, # no version
            {"later_stamp":"arXiv:1801.23456v1 [cs.LG] 12 Jun 2017"}, # same date
            {"later_stamp":"arXiv:1801.23456v1 [cs.LG] 31 Jun 2017"}, # impossible date
            {"additional_stamp":"arXiv:1801.23456v1 [cs.LG] 11 Jan 2018"}, # conflicting
            {"later_text":UNRELATED},           # citation and date but no overlap
            {"later_text":"The sensors use adaptive schemes. Other clustering uses spectral peaks."},
            {"later_text":UNRELATED,"refs_only":True},  # overlap only in bibliography
        ):
            with self.subTest(condition=condition):
                report=collection_review([parse_pdf(data,name) for data,name in documents(**condition)])
                self.assertEqual(report["later_candidates"],[])
                self.assertGreater(len(report["future_mentions"]),0)

    def test_original_direction_remains_unmodified(self):
        report=collection_review([parse_pdf(data,name) for data,name in documents()])
        match,=report["later_candidates"]
        self.assertEqual(match["direction"],report["future_mentions"][0])
        self.assertEqual(match["direction"]["text"],DIRECTION)
        self.assertEqual(match["status"],"reading_lead_only")
        self.assertNotIn("resolved",match)
        self.assertIn("不应据此舍弃",report["limits"])


class FrontierHTTP(LocalHTTPFixture,unittest.TestCase):
    def test_five_anchors_retrieve_original_page_over_real_http(self):
        inputs=documents()
        library={}
        for data,name in inputs:
            summary=self.import_pdf(name,data)["doc"]
            library[summary["id"]]=data
        response=self.read_json("/api/local-review")
        self.assertEqual(len(response["edges"]),1)
        candidate,=response["later_candidates"]
        self.assertEqual(candidate["earlier_stamp"]["date"],"2017-06-12")
        self.assertEqual(candidate["later_stamp"]["date"],"2018-01-10")
        self.assertEqual(candidate["citation"]["page"],2)
        self.assertEqual(candidate["later_excerpt"]["text"],LEAD)
        self.assertGreaterEqual(len(candidate["matched_terms"]),3)
        for name in ("direction","citation","earlier_stamp","later_stamp","later_excerpt"):
            anchor=candidate[name]
            original=self.request("/pdf/"+anchor["source_document"]+"#page="+str(anchor["page"]))[0]
            self.assertEqual(original,library[anchor["source_document"]])
            with fitz.open(stream=original,filetype="pdf") as pdf_source:
                blocks=pdf_source[anchor["page"]-1].get_text("blocks")
                block=next(b for b in blocks if b[5]+1==anchor["block"] and b[6]==0)
                self.assertIn(" ".join(anchor["text"].split())," ".join(block[4].split()))

if __name__=="__main__":
    unittest.main()
