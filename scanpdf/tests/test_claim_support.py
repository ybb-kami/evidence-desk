import unittest

import workbench
from claim_support import _scalars, check
from evidence_context import MAX_SOURCE_CHARS, MAX_SOURCES, select_excerpts


def findings(text, quote, answer):
    a=text.index(quote)
    return [dict(page=1, block=1, text=text)], {"text":answer,"answer_start":a,"answer_end":a+len(quote),"hit_index":0}


class ClaimSupportTests(unittest.TestCase):
    def test_model_identifier_is_not_a_reported_measurement(self):
        self.assertEqual(_scalars("Juniper-17 and Orchid‑4 were compared."), [])
        self.assertEqual(_scalars("Juniper-17 reached -3 Hz."), [("-3 Hz", "hz")])

    def test_splits_and_fields(self):
        text="The test split contained 95 annotated items. The validation split contained 71 annotated items."
        self.assertEqual(check("How many annotated items were in the validation split?", "", *findings(text,"95 annotated","95")), "different_condition")
        self.assertIsNone(check("How many annotated items were in the validation split?", "", *findings(text,"71 annotated","71")))
        self.assertEqual(check("What was the instrument serial number?", "", *findings(text,"95","95")), "field_missing")

    def test_ambiguous_value_requires_selected_condition(self):
        text=("The sensor sampling frequency during heating was 92 Hz. "
              "The sensor sampling frequency during cooling was 67 Hz.")
        self.assertEqual(check("What was the sensor sampling frequency?", "", *findings(text,"92 Hz","92 Hz")), "ambiguous_measurement")
        self.assertIsNone(check("What was the sensor sampling frequency during heating?", "", *findings(text,"92 Hz","92 Hz")))

    def test_question_ranked_excerpt_is_not_clipped_to_the_start_of_a_passage(self):
        text=("Calibration plates were stored in drawers at the field site. "*30
              +"The sensor sampling frequency during heating was 92 Hz.")
        hits=[dict(text=text),dict(text="Design of experiments and methods for image segmentation. "*18)]
        sources=select_excerpts(hits,query="What was the sensor sampling frequency during heating?")
        self.assertIn("92 Hz",sources[1][2])
        self.assertLessEqual(sum(len(s) for _,_,s in sources.values()),MAX_SOURCE_CHARS)
        self.assertLessEqual(len(sources),MAX_SOURCES)
        for i,a,s in sources.values():
            self.assertEqual(hits[i]["text"][a:a+len(s)],s)


if __name__=="__main__":
    unittest.main()
