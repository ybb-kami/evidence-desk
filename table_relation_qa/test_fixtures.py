from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from table_relation_qa.fixtures import generate
from table_relation_qa.run import baseline_current, grade, verify_oracle


def gold_shape(case):
    """Construct format example from ground truth; never use as an evaluated system."""
    positions={tuple(p) for p in case["expected"]["relation_cells"]}
    return {"id":case["id"],"sha256":case["sha256"],"status":case["expected"]["status"],
            "values":case["expected"]["values"],"citations":[
                {k:c[k] for k in ("page","table","row","column","text")} for c in case["cells"]
                if (c["row"],c["column"]) in positions]}


class FixtureQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary=TemporaryDirectory()
        cls.directory=Path(cls._temporary.name)
        cls.cases={c["id"]:c for c in generate(cls.directory)["cases"]}

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def test_every_truth_locator_matches_actual_pdf_and_bytes_are_reproducible(self):
        for c in self.cases.values():
            verify_oracle(self.directory,c)
            self.assertTrue(grade(self.directory,c,gold_shape(c))["relation_pass"])
        with TemporaryDirectory() as other:
            second={c["id"]:c["sha256"] for c in generate(other)["cases"]}
        self.assertEqual(second,{k:c["sha256"] for k,c in self.cases.items()})

    def test_values_without_source_wrong_column_or_negative_sign_do_not_pass(self):
        a=self.cases["positive"]
        b=self.cases["reordered_negative"]
        v=gold_shape(a); v["citations"]=[]
        self.assertFalse(grade(self.directory,a,v)["relation_pass"])
        v=gold_shape(b); v["values"]={**v["values"],"signed_difference":"2.25"}
        self.assertFalse(grade(self.directory,b,v)["relation_pass"])
        v=gold_shape(b); v["citations"][0]["column"]=2
        self.assertFalse(grade(self.directory,b,v)["valid_citations"])
        v=gold_shape(b); v["citations"][0]["page"]=2
        self.assertFalse(grade(self.directory,b,v)["relation_pass"])
        v=gold_shape(b); v["sha256"]=a["sha256"]
        self.assertFalse(grade(self.directory,b,v)["relation_pass"])

    def test_copying_original_contest_numbers_into_no_data_cases_cannot_pass(self):
        for key in ("missing_baseline_cell","missing_target_row","ambiguous_prior","no_target_scope"):
            c=self.cases[key]
            v=deepcopy(gold_shape(c)); v.update(
              status="matched",values={"model_score":"28.4","baseline_name":"Candle Net",
                                       "baseline_score":"26.36","signed_difference":"2.04"})
            result=grade(self.directory,c,v)
            self.assertFalse(result["relation_pass"])
            self.assertFalse(result["safe_abstention"])

    def test_empty_cell_must_be_expressly_located_for_missing_value_claim(self):
        case=self.cases["missing_baseline_cell"]
        reply=gold_shape(case)
        reply["citations"]=[c for c in reply["citations"] if c["text"]]
        score=grade(self.directory,case,reply)
        self.assertTrue(score["anchor_pass"])
        self.assertTrue(score["safe_abstention"])
        self.assertFalse(score["relation_pass"])

    def test_current_only_anchors_not_relations(self):
        reports=[grade(self.directory,c,baseline_current(self.directory,c)) for c in self.cases.values()]
        self.assertEqual(sum(r["anchor_pass"] for r in reports),6)
        self.assertFalse(any(r["relation_pass"] for r in reports))
        self.assertEqual(sum(r["safe_abstention"] for r in reports),4)


if __name__=="__main__":
    unittest.main()
