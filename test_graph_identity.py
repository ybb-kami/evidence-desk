"""Regression tests for title extraction in same-font PDFs."""
import unittest

import fitz

from graph import extract_identity


class GraphIdentityTests(unittest.TestCase):
    visible_title = "A Lantern Inventory Across Six Imaginary Cities"
    visible_subheading = "An Unrelated Line About Clouds and Mountains"

    def identity(self, title):
        with fitz.open() as pdf:
            pdf.set_metadata({"title": title})
            page = pdf.new_page()
            page.insert_text((50, 70), self.visible_title)
            page.insert_text((50, 100), self.visible_subheading)
            page.insert_text((50, 145), "Fictional paper used to check identity only.")
            return extract_identity(page)

    def test_metadata_corroborates_first_visible_line(self):
        self.assertEqual(self.identity(self.visible_title)["title"], self.visible_title)

    def test_metadata_title_must_not_be_invented_or_select_later_line(self):
        for misleading in ("A Fake Title Not Visible Anywhere", self.visible_subheading):
            with self.subTest(misleading=misleading):
                self.assertIsNone(self.identity(misleading)["title"])


if __name__ == "__main__":
    unittest.main()
