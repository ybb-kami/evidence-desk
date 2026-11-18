"""Grouped BLEU extraction regression. Public files are strictly opt-in."""
import argparse
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import fitz

from engine import parse_pdf
from partner_metric import _normal, answer_metric

QUESTION = "Transformer (big) 在英德翻译中的 BLEU 分数比当时的最佳模型提升多少？"


def fixture():
    """A native two-tier grouped table with deliberately different task values."""
    with fitz.open() as pdf:
        p=pdf.new_page(width=612,height=792)
        p.insert_text((80,55),"A synthetic grouped translation table.",fontsize=16)
        p.insert_text((120,99),"Table 5: BLEU for EN-DE and EN-FR compared with previous systems.",fontsize=10)
        p.insert_text((126,124),"Model",fontsize=10)
        p.insert_text((286,118),"BLEU",fontsize=10)
        p.insert_text((414,118),"Cost",fontsize=10)
        for x,word in zip((258,329,400,472),("EN-DE","EN-FR","EN-DE","EN-FR")):
            p.insert_text((x,135),word,fontsize=10)
        for y, *values in [
            (155,"Moon Arc [1]","20","60","14","17"),(174,"Candle Net [2]","32.50","47","18","26"),
            (193,"Fern Pool [3]","30","82","11","17"),(212,"Transformer (big)","35.75","50","12","20")]:
            for x,value in zip((125,258,329,400,472),values):
                p.insert_text((x,y),value,fontsize=10)
        for y in (108,140,217):
            p.draw_line((119,y),(515,y),width=.6)
        for xa,xb in ((246,370),(392,515)):
            p.draw_line((xa,123),(xb,123),width=.6)
        return pdf.tobytes()


def check_clip(data,match):
    with fitz.open(stream=data,filetype="pdf") as source:
        for cell in match["cells"]:
            page=source[cell["page"]-1]
            actual=page.get_text(clip=fitz.Rect(cell["bbox_pt"])*page.derotation_matrix)
            assert _normal(actual)==_normal(cell["text"]),(cell,actual)


class PartnerMetricTests(unittest.TestCase):
    def test_unrelated_and_unclear_queries_do_not_guess(self):
        raw=fixture();doc=parse_pdf(raw,"synthetic.pdf")
        self.assertIsNone(answer_metric(doc,"What date was it released?",raw))
        both=answer_metric(doc,"BLEU improvement for EN-DE and EN-FR",raw)
        self.assertIsNone(both["answer"])
        self.assertIn("明确一个",both["gap"])
        bad=answer_metric(doc,QUESTION,b"not original")
        self.assertIsNone(bad["answer"]);self.assertEqual(bad["hits"],[])
        self.assertIn("哈希",bad["gap"])

    def test_missing_optional_detector_is_an_explicit_gap(self):
        raw=fixture();doc=parse_pdf(raw,"synthetic.pdf")
        with patch.dict("sys.modules",{"partner_tables":None}):
            result=answer_metric(doc,QUESTION,raw)
        self.assertIsNone(result["answer"])
        self.assertIn("可选",result["gap"])

    @unittest.skipUnless(all(importlib.util.find_spec(n) for n in ("PIL","numpy","scipy")),
                         "optional native-table dependencies unavailable")
    def test_grouped_two_task_layout_computes_signed_differences_and_exact_cells(self):
        raw=fixture();doc=parse_pdf(raw,"synthetic.pdf")
        de=answer_metric(doc,QUESTION,raw)["comparison"]
        fr=answer_metric(doc,QUESTION.replace("英德","英法"),raw)["comparison"]
        self.assertEqual([de["score"],de["baseline"],de["difference"]],["35.75","32.50","3.25"])
        self.assertEqual([fr["score"],fr["baseline"],fr["difference"]],["50","82","-32"])
        self.assertEqual(de["baseline_models"],["Candle Net"])
        self.assertEqual(fr["baseline_models"],["Fern Pool"])
        for row in (de,fr):
            self.assertEqual(row["source_document"],doc["id"])
            check_clip(raw,row)
        self.assertNotEqual(next(cell["column"] for cell in de["cells"] if cell["role"]=="target_score"),
                            next(cell["column"] for cell in fr["cells"] if cell["role"]=="target_score"))


def public_replay(paths):
    for path in paths:
        raw=path.read_bytes();doc=parse_pdf(raw,path.name)
        result=answer_metric(doc,QUESTION,raw)
        row=result.get("comparison")
        assert row,result
        assert (row["page"],row["score"],row["baseline"],row["difference"])==(8,"28.4","26.36","2.04")
        assert row["baseline_models"]==["ConvS2S Ensemble"]
        check_clip(raw,row)
        print(path.name, result["answer"], len(row["cells"]), "source cells checked")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--public",type=Path,nargs=2)
    opts=cli.parse_args()
    if opts.public:
        public_replay(opts.public)
    else:
        unittest.main(argv=[__file__])
