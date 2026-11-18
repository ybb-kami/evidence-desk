"""Offline HTTP coverage of revision anchors and reversible archive operations."""
import hashlib
from pathlib import Path
import re
import subprocess
import unittest
from urllib.error import HTTPError

import fitz

from test_acceptance import LocalHTTPFixture


def document(line, tagline=None, trimmed=False):
    with fitz.open() as pdf:
        pdf.set_metadata({"subject": tagline or ""})
        page = pdf.new_page()
        page.insert_text((45, 45), "Evidence Desk synthetic revision specimen")
        if not trimmed:
            for i in range(12):
                page.insert_text((45, 70 + i*18),
                                 f"Unchanging reference block {i:02d}: only the exact source text is indexed.")
        page.insert_text((45, 330), line)
        return pdf.tobytes()


class RevisionAcceptance(LocalHTTPFixture, unittest.TestCase):
    def library_ids(self, endpoint="/api/library"):
        return {row["id"] for row in self.read_json(endpoint)["docs"]}

    def http_error(self, status, path, payload):
        with self.assertRaises(HTTPError) as raised:
            self.read_json(path, payload)
        with raised.exception as response:
            self.assertEqual(response.code, status)

    def test_compare_source_anchors_reverse_direction_and_excludes_no_source(self):
        left_data = document("Only this edition mentions the blue lantern.")
        right_data = document("Only this edition mentions the amber lantern.")
        left = self.import_pdf("earlier.pdf", left_data)["doc"]["id"]
        right = self.import_pdf("later.pdf", right_data)["doc"]["id"]
        self.assertEqual(self.library_ids(), {left, right})
        for before, after, words in [(left, right, ("blue", "amber")), (right, left, ("amber", "blue"))]:
            result = self.read_json("/api/revisions", dict(before_id=before, after_id=after))
            self.assertEqual((result["before"]["id"], result["after"]["id"]), (before, after))
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["counts"]["replaced"], 1)
            change, = result["changes"]
            for side, docid, expected in zip(("before", "after"), (before, after), words):
                anchor, = change[side]
                self.assertIn(expected + " lantern", anchor["text"])
                self.assertEqual(anchor["page"], 1)
                self.assertGreater(anchor["block"], 0)
                data = self.request("/pdf/" + docid)[0]
                with fitz.open(stream=data, filetype="pdf") as pdf:
                    self.assertIn(anchor["text"], pdf[0].get_text())
        self.http_error(400, "/api/revisions", dict(before_id=left, after_id=left))
        self.http_error(404, "/api/revisions", dict(before_id=left, after_id="0"*64))

    def test_archive_keeps_source_bytes_and_original_url_after_restart(self):
        old_data = document("Only this edition mentions the blue lantern.", "Old file")
        new_data = document("Only this edition mentions the blue lantern.", "New file")
        old = self.import_pdf("old.pdf", old_data)["doc"]["id"]
        new = self.import_pdf("new.pdf", new_data)["doc"]["id"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.library_ids(), {old, new})
        self.assertEqual(self.library_ids("/api/archive"), set())
        decision = self.read_json("/api/supersede", dict(keep_id=new, archive_id=old))
        self.assertEqual(decision["archived_doc"]["id"], old)
        self.assertEqual(self.library_ids(), {new})
        self.assertEqual(self.library_ids("/api/archive"), {old})
        self.assertEqual(self.read_json("/api/search", dict(query="blue lantern", ids=[old]))["hits"], [])
        self.assertEqual(self.request("/pdf/" + old)[0], old_data)
        repeated = self.import_pdf("renamed-old.pdf", old_data)
        self.assertTrue(repeated["existing"] and repeated["archived"])
        self.http_error(409, "/api/supersede", dict(keep_id=old, archive_id=new))

        self.stop_server()
        self.start_server()
        self.assertEqual(self.library_ids(), {new})
        self.assertEqual(self.library_ids("/api/archive"), {old})
        self.assertEqual(self.request("/pdf/" + old)[0], old_data)
        rev = self.read_json("/api/revisions", dict(before_id=old, after_id=new))
        self.assertEqual(rev["changes"], [])
        self.assertFalse(self.read_json("/api/restore", dict(id=old))["archived"])
        self.assertEqual(self.library_ids(), {old, new})
        self.assertTrue(self.read_json("/api/archive", dict(id=new))["archived"])
        self.assertEqual(self.library_ids("/api/archive"), {new})

    def test_unrelated_replacement_is_rejected_and_textless_is_explicit(self):
        a = self.import_pdf("a.pdf", document("One paper describes lanterns.", trimmed=True))["doc"]["id"]
        with fitz.open() as blank:
            blank.new_page()
            b = self.import_pdf("empty.pdf", blank.tobytes())["doc"]["id"]
        self.http_error(409, "/api/supersede", dict(keep_id=a, archive_id=b))
        self.assertEqual(self.library_ids(), {a, b})
        outcome = self.read_json("/api/revisions", dict(before_id=a, after_id=b))
        self.assertEqual(outcome["status"], "insufficient_text")
        self.assertEqual(outcome["text_coverage"]["after_textless_pages"], 1)


def public_acceptance(v7, v1):
    """Opt-in using already acquired public arXiv PDFs (no network)."""
    originals = [Path(v7).read_bytes(), Path(v1).read_bytes()]
    hashes = [
        "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
        "0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476",
    ]
    assert [hashlib.sha256(data).hexdigest() for data in originals] == hashes
    fixture = RevisionAcceptance()
    try:
        fixture.setUp()
        before = fixture.import_pdf("Attention-current.pdf", originals[0])["doc"]["id"]
        response = fixture.import_pdf("renamed-early-draft.pdf", originals[1])
        after = response["doc"]["id"]
        hint = next(row for row in response["duplicates"] if row["id"] == before)
        assert (hint["arxiv_id"], hint["existing_version"], hint["new_version"]) == ("1706.03762", 7, 1)
        assert fixture.library_ids() == {before, after}
        result = fixture.read_json("/api/revisions", dict(before_id=before, after_id=after))
        assert result["before"]["id"] == before and result["after"]["id"] == after
        assert result["status"] == "ok" and result["counts"]["replaced"]
        for side, marker, file in (("before", "20", v7), ("after", "18", v1)):
            phrase = f"We used the Adam optimizer [{marker}]"
            passage = next(anchor for change in result["changes"] for anchor in change[side]
                           if phrase in anchor["text"])
            page = str(passage["page"])
            text = subprocess.check_output(
                ["pdftotext", "-f", page, "-l", page, "-layout", file, "-"], text=True)
            assert phrase in re.sub(r"\s+", " ", text)
        fixture.read_json("/api/supersede", dict(keep_id=before, archive_id=after))
        assert fixture.library_ids() == {before}
        assert fixture.library_ids("/api/archive") == {after}
        for docid, data in zip(hashes, originals):
            assert fixture.request("/pdf/" + docid)[0] == data
        fixture.stop_server()
        fixture.start_server()
        assert fixture.library_ids("/api/archive") == {after}
        fixture.read_json("/api/restore", {"id": after})
        assert fixture.library_ids() == {before, after}
        return {"similarity": hint["score"], "counts": result["counts"], "truncated": result["truncated"],
                "independently_verified": "Both editions PDF page 7: Adam [20] -> [18]",
                "both_source_bytes_verified": True, "archive_restart_restore_verified": True}
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) == 3:
        print(json.dumps(public_acceptance(*sys.argv[1:]), indent=2))
    else:
        unittest.main()
