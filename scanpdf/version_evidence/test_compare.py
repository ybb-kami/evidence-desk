"""Regression cases for source spans and identity/semantic independence."""

from unittest import TestCase
from urllib.parse import parse_qs, urlsplit

import pymupdf

from engine import parse_pdf
from semantic import DenseUnavailable

from .compare import _align, compare, MAX_CHARS, MAX_PASSAGES


PARAGRAPHS = (
    "The open wetland sensors recorded water temperature every 20 minutes. We calibrated the probes separately in a laboratory water bath before deployment.",
    "A solar charger kept each station operating through the night. The devices wrote their measurements to a local memory card when the network was offline.",
    "All field notes were checked by a second observer before analysis. Researchers stored the resulting table alongside the archived weather log.",
)
PARAPHRASES = (
    PARAGRAPHS[2],
    "The instruments measured the temperature of the marsh water at 30 minute intervals. Before installation we verified every probe independently using a controlled laboratory water bath.",
    "Solar power supplied the measurement stations overnight. Whenever internet service failed, local storage on removable cards retained the logged sensor readings.",
)


def manuscript(title, abstract, paragraphs):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=620, height=760)
        page.insert_text((55, 72), title, fontsize=22)
        page.insert_text((55, 124), "Abstract", fontsize=13)
        assert page.insert_textbox((55, 142, 555, 205), abstract, fontsize=11) >= 0
        page.insert_text((55, 231), "1 Introduction", fontsize=13)
        for n, paragraph in enumerate(paragraphs):
            top = 251 + 65 * n
            assert page.insert_textbox((55, top, 555, top + 55), paragraph, fontsize=11) >= 0
        return pdf.tobytes()


def controlled_pdfs():
    return (
        manuscript("Monitoring Water Temperature in Wetlands",
                   "We measure changes in wetland water temperature using independent sensors and record the variation under field conditions.",
                   PARAGRAPHS),
        manuscript("Field Measurements of Marsh Thermal Change",
                   "Independent probes let us observe thermal variation in marsh water at the field site throughout the monitoring period.",
                   PARAPHRASES),
    )


class VersionEvidenceTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before, cls.after = (parse_pdf(pdf, f"record-{i}.pdf") for i, pdf in enumerate(controlled_pdfs()))

    def test_no_id_reorder_is_not_a_work_identifier(self):
        result = compare(self.before, self.after, semantic=False, dense=None)
        self.assertEqual(result["identity"]["status"], "unverified")
        self.assertEqual(result["semantic"]["state"], "disabled")
        self.assertEqual(result["title"]["change"], "changed_wording")
        self.assertEqual(result["abstract"]["change"], "changed_wording")
        matches = [row for row in result["passages"] if row["change"] == "same_wording"]
        self.assertEqual(len(matches), 1)
        self.assertGreater(matches[0]["left"]["block"], matches[0]["right"]["block"])
        self.assertTrue(all(row["cosine"] is None for row in result["passages"]))
        self.assertLessEqual(result["scope"]["left_sampled"], MAX_PASSAGES)

    def test_unavailable_model_preserves_source_spans(self):
        class Unavailable:
            def score_texts(self, question, texts):
                raise DenseUnavailable("index_preparing")

        result = compare(self.before, self.after, semantic=True, dense=Unavailable())
        self.assertEqual(result["semantic"]["state"], "unavailable")
        self.assertEqual(result["semantic"]["reason"], "index_preparing")
        self.assertEqual(result["passages"], compare(self.before, self.after, semantic=False, dense=None)["passages"])
        for row in [result["title"], result["abstract"], *result["passages"]]:
            for side, doc in [("left", self.before), ("right", self.after)]:
                if anchor := row[side]:
                    params = {k: v[0] for k, v in parse_qs(urlsplit(anchor["source_url"]).query).items()}
                    self.assertEqual(params["id"], doc["id"])
                    chunk = next(c for c in doc["chunks"] if (c["page"], c["block"], c["part"]) == (
                        int(params["page"]), int(params["block"]), int(params["part"])))
                    self.assertEqual(chunk["text"][int(params["start"]):int(params["end"])], anchor["text"])
                    self.assertLessEqual(len(anchor["text"]), MAX_CHARS)

    def test_near_ties_are_not_paired_and_distinct_number_tokens_survive(self):
        left = [{"text": "Measured water temperature after 20 minutes."}, {"text": "Checked controls weekly."}]
        right = [{"text": "We measured water temperature after 30 minutes."}, {"text": "Measured water temperature at noon."}]
        rows = _align(left, right, [[0.92, 0.915], [0.73, 0.71]])
        self.assertTrue(all(row["change"] == "unpaired" for row in rows))
        rows = _align(left, right, [[0.92, 0.85], [0.73, 0.71]])
        self.assertEqual(rows[0]["change"], "similar_candidate")
        self.assertEqual(rows[0]["number_tokens"], {"left": ["20"], "right": ["30"]})
