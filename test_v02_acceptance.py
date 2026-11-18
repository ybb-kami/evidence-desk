"""Offline tests across the actual local HTTP boundary, with original fixtures."""
import unittest
from urllib.error import HTTPError

import fitz

from test_acceptance import LocalHTTPFixture

TARGET_TITLE = "An Inventory of Lantern Patterns Across Imaginary Places"
SOURCE_TITLE = "A Catalogue of Clouds in a Fictional Archipelago"


def fixture(title, body, reference=None):
    """Original synthetic PDF used only to exercise source tracking."""
    with fitz.open() as pdf:
        pdf.set_metadata({"title": title})
        first = pdf.new_page()
        for y, line in [
            (72, title),
            (100, "Synthetic Evidence Desk test fixture. Not a publication."),
            (150, body),
        ]:
            first.insert_text((58, y), line)
        if reference:
            second = pdf.new_page()
            second.insert_text((58, 80), "References")
            second.insert_text((58, 135), "Fictional Example, 2025. " + reference + ".")
        return pdf.tobytes()


class EvidenceDeskV02Acceptance(LocalHTTPFixture, unittest.TestCase):
    def import_pair(self):
        target = self.import_pdf(
            "synthetic-a.pdf", fixture(TARGET_TITLE, "Only the target describes an amber lantern.")
        )["doc"]
        source = self.import_pdf(
            "synthetic-b.pdf", fixture(SOURCE_TITLE, "Only the source describes fog banks.", TARGET_TITLE)
        )["doc"]
        return source, target

    def test_graph_uses_reference_direction_and_verifiable_location(self):
        self.assertEqual(self.read_json("/api/graph"), {"nodes": [], "edges": []})
        source, target = self.import_pair()
        graph = self.read_json("/api/graph")
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertEqual((edge["source"], edge["target"]), (source["id"], target["id"]))
        self.assertEqual(edge["page"], 2)
        self.assertIsInstance(edge["block"], int)
        self.assertIn(TARGET_TITLE, edge["text"])
        original, _ = self.request("/pdf/" + source["id"])
        with fitz.open(stream=original, filetype="pdf") as source_pdf:
            self.assertIn(TARGET_TITLE, source_pdf[1].get_text())

    def test_compare_represents_a_paper_with_no_evidence(self):
        source, target = self.import_pair()
        result = self.read_json("/api/compare", {
            "query": "amber lantern", "ids": [source["id"], target["id"]],
        })
        papers = {paper["id"]: paper for paper in result["papers"]}
        self.assertEqual(set(papers), {source["id"], target["id"]})
        self.assertEqual(papers[source["id"]]["name"], source["name"])
        self.assertEqual(papers[source["id"]]["hits"], [])
        hits = papers[target["id"]]["hits"]
        self.assertTrue(hits)
        self.assertTrue(any(hit["page"] == 1 and "amber lantern" in hit["text"] for hit in hits))

    def test_discover_without_explicit_consent_rejects_without_network(self):
        # Do not send consent=True here: this suite must never request live discovery.
        for payload in ({"query": "Synthetic local fixture"},
                        {"query": "Synthetic local fixture", "consent": False}):
            with self.subTest(payload=payload):
                with self.assertRaises(HTTPError) as rejected:
                    self.read_json("/api/discover", payload)
                with rejected.exception as response:
                    self.assertIn(response.code, (400, 403))
                    self.assertTrue(response.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
