import unittest
from unittest.mock import patch

import fitz

from engine import duplicates, find_evidence, parse_pdf


def make_pdf(page_texts, *, title=None, labels=None, password=None, chinese=False):
    with fitz.open() as pdf:
        for value in page_texts:
            page = pdf.new_page(width=650, height=1100)
            if value:
                inserted = page.insert_textbox(fitz.Rect(20, 20, 630, 1080), value,
                                               fontsize=11, fontname="china-s" if chinese else "helv")
                assert inserted >= 0
        if title:
            pdf.set_metadata({"title": title})
        if labels:
            pdf.set_page_labels(labels)
        opts = {"encryption": fitz.PDF_ENCRYPT_AES_256, "owner_pw": "local-owner",
                "user_pw": password} if password else {}
        return pdf.tobytes(**opts)


class EngineTests(unittest.TestCase):
    def test_cites_physical_page_and_printed_label(self):
        labels = [{"startpage": 0, "style": "r"}, {"startpage": 2, "style": "D"}]
        doc = parse_pdf(make_pdf(["Cover.", "The experiment measured pollen survival.", ""],
                                 labels=labels), "Source.pdf")
        self.assertEqual(doc["pages"], 3)
        self.assertEqual(doc["textless_pages"], [3])
        hits = find_evidence([doc], "Where did the experiment measure pollen survival?")
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]["page"], hits[0]["page_label"]), (2, "ii"))
        self.assertEqual(hits[0]["text"], "The experiment measured pollen survival.")
        self.assertEqual(hits[0]["name"], "Source.pdf")

    def test_image_only_pdf_is_explicitly_textless(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 5, 5), False)
            pix.clear_with(0)
            page.insert_image(fitz.Rect(20, 20, 120, 120), pixmap=pix)
            doc = parse_pdf(pdf.tobytes(), "there-is-no-text.pdf")
        self.assertEqual(doc["textless_pages"], [1])
        self.assertEqual(doc["chunks"], [])
        self.assertEqual(find_evidence([doc], "there is no text"), [])

    def test_empty_search_and_missing_evidence_do_not_match(self):
        doc = parse_pdf(make_pdf(["The experiment measured pollen survival."]), "X.pdf")
        for query in ["resistance microscopy", "pollen microscopy", "the a how",
                      "x" * 501, ""]:
            with self.subTest(query=query[:20]):
                self.assertEqual(find_evidence([doc], query), [])

    def test_phrase_beats_same_unordered_keywords(self):
        doc = parse_pdf(make_pdf(["survival after pollen collection.", "pollen survival."]), "X.pdf")
        self.assertEqual(find_evidence([doc], "pollen survival")[0]["page"], 2)

    def test_chinese_evidence(self):
        doc = parse_pdf(make_pdf(["本文讨论图论中的匹配问题。"], chinese=True), "中文.pdf")
        self.assertEqual(find_evidence([doc], "匹配问题")[0]["page"], 1)
        self.assertEqual(find_evidence([doc], "燃烧实验"), [])

    def test_parse_rejects_invalid_protected_and_limits(self):
        for data in [b"x", b"%PDF-1.4\nbroken",
                     make_pdf(["secret"], password="test")]:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                parse_pdf(data, "bad.pdf")
        good = make_pdf(["A sentence.", "A second sentence."])
        for constant in ("MAX_BYTES", "MAX_PAGES", "MAX_CHARS", "MAX_CHUNKS"):
            with self.subTest(constant=constant), patch(f"engine.{constant}", 1):
                with self.assertRaises(ValueError):
                    parse_pdf(good, "large.pdf")

    def test_exact_bytes_even_when_filename_changes(self):
        data = make_pdf(["Short content."])
        first, second = parse_pdf(data, "Original.pdf"), parse_pdf(data, "Renamed.pdf")
        self.assertEqual(duplicates(second, [first])[0]["reason"], "identical_bytes")

    def test_filename_is_not_evidence_of_duplication(self):
        first = parse_pdf(make_pdf(["The experiment measured pollen survival."]), "same.pdf")
        second = parse_pdf(make_pdf(["A survey of coral reef depths."]), "same.pdf")
        self.assertEqual(duplicates(second, [first]), [])
        blank1 = parse_pdf(make_pdf([""], title="A"), "A.pdf")
        blank2 = parse_pdf(make_pdf([""], title="B"), "B.pdf")
        self.assertEqual(duplicates(blank1, [blank2]), [])

    def test_unchanged_text_and_possible_version_are_distinguished(self):
        body = " ".join(f"finding{j:03d}" for j in range(100))
        first = parse_pdf(make_pdf([body]), "Paper.pdf")
        rewrapped = parse_pdf(make_pdf([body], title="Different metadata"), "Copy.pdf")
        version = parse_pdf(make_pdf([body.replace("finding010", "amendment")]), "Draft.pdf")
        candidates = duplicates(first, [version, rewrapped])
        self.assertEqual([row["reason"] for row in candidates],
                         ["identical_extracted_text", "possible_version"])
        self.assertLess(candidates[1]["score"], 1.0)

    def test_exact_hash_large_content_unchanged(self):
        body = " ".join(f"finding{j:03d}" for j in range(200))
        first = parse_pdf(make_pdf([body]), "Paper.pdf")
        second = parse_pdf(make_pdf([body], title="Alternate"), "Copy.pdf")
        self.assertGreater(len(first["chunks"]), 1)
        self.assertEqual(duplicates(first, [second])[0]["reason"], "identical_extracted_text")
        self.assertEqual(find_evidence([first], "finding198")[0]["part"], 2)


    def test_margin_markers_rank_renamed_v7_and_v1_in_either_direction(self):
        body = " ".join(f"finding{j:03d}" for j in range(250))
        def stamped(name, version, passage):
            with fitz.open() as pdf:
                page = pdf.new_page(width=650, height=1100)
                page.insert_text((22, 645), f"arXiv:2305.12345v{version} [math.CO] 2 May 2023",
                                 rotate=90, fontsize=13)
                assert page.insert_textbox(fitz.Rect(44, 47, 627, 1050), passage, fontsize=10) >= 0
                return parse_pdf(pdf.tobytes(), name)

        newer = stamped("arbitrary.pdf", 7, body)
        older = stamped("not-a-versioned-file-name.pdf", 1,
                        body.replace("finding217", "subsequent"))
        self.assertEqual(newer["arxiv"], {"id": "2305.12345", "version": 7})
        self.assertGreater(len(older["chunks"]), 2)
        for first, second in [(newer, older), (older, newer)]:
            match = duplicates(first, [second])[0]
            self.assertEqual(match["reason"], "possible_version")
            self.assertIn("v7", match["advice"])
            self.assertIn("两份文件均保留", match["advice"])

        cited = parse_pdf(make_pdf(["We used results of arXiv:2305.12345v1 in another field.",
                                   "arXiv:2305.12345v7 [math.CO] 2 May 2023"]), "See_2305.12345v1.pdf")
        self.assertIsNone(cited["arxiv"])
        self.assertEqual(duplicates(newer, [cited]), [])
        false_stamp = stamped("false-stamp.pdf", 9,
                             " ".join(f"unrelated{j:03d}" for j in range(250)))
        self.assertEqual(duplicates(newer, [false_stamp]), [])


if __name__ == "__main__":
    unittest.main()
