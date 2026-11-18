"""The 20 invented PDFs exercise graph mechanics, not real-corpus accuracy."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

import fitz

from test_acceptance import LocalHTTPFixture

TITLES = [
    "A Register of Lanterns on the Western Ridge",
    "Cloud Patterns in the Copper Archipelago",
    "A Survey of Painted Doors Along the River",
    "Tidal Clocks and the East Harbor Archive",
    "A Manual of Rain Maps for Quiet Valleys",
    "Winter Notes from the Glasshouse Observatory",
    "Road Markers of the Inland Salt Route",
    "A Catalogue of Kites in the Northern Market",
    "Mist Bells Across the Lower Granite Fields",
    "Garden Borders of the Southern Lighthouse",
    "Bird Sketches at the Three Stone Bridges",
    "A Register of Boats in an Imaginary Estuary",
    "An Atlas of Shadow Patterns in Cedar Lane",
    "The Folded Map of the Sunday Clocktower",
    "Two Notebooks from an Imaginary Rail Station",
    "Two Notebooks from an Imaginary Rail Station",
    "Terraces and Water Channels in a Paper City",
    "The Color of Windows in the Snow Quarter",
    "A Field Guide to Footpaths on Orange Island",
    "Pebble Patterns in an Empty Courtyard",
]
# Human-defined truth is independent of what the recognizer emits.
REFS = {1:(0,), 2:(0,), 3:(1,2), 4:(2,), 5:(4,), 6:(3,), 7:(2,6),
        8:(7,), 9:(4,8), 10:(9,), 11:(10,), 12:(1,)}
# One deliberately unsupported but genuine bibliography citation uses an alternate heading.
SEMANTIC_EDGES = {(src,dst) for src,targets in REFS.items() for dst in targets}
ELIGIBLE_EDGES = SEMANTIC_EDGES - {(12,1)}
DOI_TARGET = 10
CONFLICTING_DOI = "10.0000/synthetic-graph-fixture-10"


def reference(title):
    return "Fictional Author, 2024. " + title + "."


def synthetic_pdf(index):
    with fitz.open() as pdf:
        title = TITLES[index]
        pdf.set_metadata({"title":title, "subject":"Original synthetic test record; not a publication"})
        first = pdf.new_page()
        first.insert_text((50,70),title,fontsize=19)
        first.insert_text((50,120),f"Synthetic test record {index:02d}. All places, works and claims are invented.")
        first.insert_text((50,148),f"Fictional sample {index:02d}: a local artifact for testing citation mechanics.")
        if index == DOI_TARGET:
            first.insert_text((50,172),"doi: "+CONFLICTING_DOI)
        if index in (13,19):
            # Title text in body, without a bibliography, is not a citation edge.
            first.insert_text((50,197),reference(TITLES[0]))
        entries = [reference(TITLES[referent]) for referent in REFS.get(index,())]
        if index == 4:
            entries.append(reference(TITLES[2])) # duplicate anchor; one edge
        if index == 13:
            entries.append(reference("Uncollected Drawings from a Faraway Tramway"))
        if index == 16:
            entries.append(reference(TITLES[14]))  # ambiguous between 14 and 15
        if index == 17:
            entries.append(reference(TITLES[17]))  # self reference
        if index == 18:
            entries.append(reference(TITLES[0])+" doi: "+CONFLICTING_DOI) # disagreeing ID and title
        if entries:
            page = pdf.new_page()
            page.insert_text((50,80),"Sources Consulted" if index == 12 else "References")
            for offset, line in enumerate(entries):
                page.insert_text((50,130+45*offset),line,fontsize=9)
        return pdf.tobytes()


def independent_page_text(pdf_bytes, physical_page):
    return subprocess.check_output(
        ["pdftotext","-f",str(physical_page),"-l",str(physical_page),"-","-"],
        input=pdf_bytes,
    ).decode("utf-8")


def normalized(text):
    return " ".join(text.split())


class GraphTwenty(LocalHTTPFixture, unittest.TestCase):
    def measure(self):
        originals, id_to_index = {}, {}
        poppler = bool(shutil.which("pdftotext"))
        for i in range(len(TITLES)):
            data = synthetic_pdf(i)
            doc = self.import_pdf(f"synthetic-citation-{i+1:02d}.pdf",data)["doc"]
            self.assertEqual(doc["id"],hashlib.sha256(data).hexdigest())
            originals[doc["id"]] = data
            id_to_index[doc["id"]] = i
        graph = self.read_json("/api/graph")
        self.assertEqual(len(graph["nodes"]),20)
        self.assertEqual(set(id_to_index),{node["id"] for node in graph["nodes"]})
        observed = {(id_to_index[edge["source"]],id_to_index[edge["target"]]) for edge in graph["edges"]}
        self.assertEqual(len(graph["edges"]),len(observed))
        anchors = 0
        for edge in graph["edges"]:
            self.assertEqual(edge["page"],2)
            self.assertIsInstance(edge["block"],int)
            target = TITLES[id_to_index[edge["target"]]]
            self.assertIn(target,edge["text"])
            source_document = edge["source_document"]
            original,_headers = self.request("/pdf/"+source_document)
            self.assertEqual(original,originals[source_document])
            # Use independent Poppler where present; also validate exact block with MuPDF.
            with fitz.open(stream=original,filetype="pdf") as source:
                blocks = source[edge["page"]-1].get_text("blocks")
                blk = next(b for b in blocks if b[5]+1==edge["block"] and b[6]==0)
                self.assertIn(normalized(edge["text"]),normalized(blk[4]))
            if poppler:
                self.assertIn(target, normalized(independent_page_text(original, edge["page"])))
            anchors += 1
        unresolved = graph["unresolved_by_reason"]
        self.assertGreaterEqual(unresolved.get("no_local_match",0),1)
        self.assertEqual(unresolved.get("ambiguous"),1)
        self.assertEqual(unresolved.get("conflicting"),1)
        self.assertEqual(unresolved.get("same_work"),1)
        self.assertIn(12,{id_to_index[paper] for paper in graph["no_bibliography_detected"]})
        self.assertIn(19,{id_to_index[paper] for paper in graph["no_bibliography_detected"]})
        isolated = [id_to_index[node["id"]] for node in graph["nodes"]
                    if not node["indegree"] and not node["outdegree"]]
        self.assertIn(19,isolated)
        def score(truth):
            return {"TP":len(observed&truth),"FP":len(observed-truth),"FN":len(truth-observed)}
        return {"input_documents":len(originals),"works":len(graph["nodes"]),
                "semantic":score(SEMANTIC_EDGES),"eligible":score(ELIGIBLE_EDGES),
                "unresolved":unresolved,"independently_locatable_edges":anchors,
                "poppler_page_cross_checks":anchors if poppler else 0,
                "isolated_nodes":len(isolated),"expected_missed_note":"Alternate bibliography heading Sources Consulted"}

    def test_twenty_original_synthetic_citation_fixtures(self):
        report = self.measure()
        self.assertEqual(report["semantic"],{"TP":14,"FP":0,"FN":1})
        self.assertEqual(report["eligible"],{"TP":14,"FP":0,"FN":0})
        self.assertEqual(report["independently_locatable_edges"],14)


def public_works(v7,v1,bert):
    fixture = GraphTwenty()
    try:
        fixture.setUp()
        originals = [Path(path).read_bytes() for path in (v7,v1,bert)]
        hashes = [hashlib.sha256(data).hexdigest() for data in originals]
        assert hashes == [
            "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
            "0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476",
            "5692a5514787a8c6727b4ff3b726a3385798bc68e12138d1d4af83947e2acf6e",
        ], "requires the previously qualified public PDF bytes"
        for name,data in zip(("attention-v7.pdf","attention-v1.pdf","bert.pdf"),originals):
            fixture.import_pdf(name,data)
        graph = fixture.read_json("/api/graph")
        assert len(graph["nodes"])==2 and len(graph["edges"])==1
        edge, = graph["edges"]
        assert (edge["source"],edge["target"])==(hashes[2],hashes[0])
        attention = next(node for node in graph["nodes"] if node["id"]==hashes[0])
        assert {doc["id"] for doc in attention["documents"]}==set(hashes[:2])
        assert edge["page"]==11 and "attention is all you need" in normalized(edge["text"]).lower()
        source,_headers = fixture.request("/pdf/"+edge["source_document"])
        assert source==originals[2]
        assert re.search(r"attention is all you need",normalized(independent_page_text(source,11)),re.I)
        return {"public_pdfs":3,"distinct_works":2,"directions":["BERT → Attention"],
                "verified_anchor":{"page":edge["page"],"block":edge["block"],"method":edge["method"]},
                "unresolved":graph["unresolved_by_reason"]}
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--public",nargs=3,metavar=("V7","V1","BERT"))
    opts = args.parse_args()
    fixture = GraphTwenty()
    try:
        fixture.setUp()
        print(json.dumps(fixture.measure(),indent=2))
    finally:
        fixture.doCleanups()
    if opts.public:
        print(json.dumps(public_works(*opts.public),indent=2,ensure_ascii=False))
