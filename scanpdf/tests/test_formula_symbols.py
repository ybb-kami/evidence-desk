import unittest

from PIL import Image, ImageDraw

from tools.formula_symbols import review_latex


def word(text, bbox, confidence=90):
    return {"text": text, "bbox": bbox, "confidence": confidence}


def fixture(stroke_y=35):
    image = Image.new("L", (160, 42), 255)
    ink = ImageDraw.Draw(image)
    ink.rectangle([81, 12, 91, 25], fill=0)
    ink.rectangle([98, stroke_y, 114, stroke_y + 1], fill=0)
    ink.rectangle([119, 12, 129, 25], fill=0)
    words = [
        word("rate", [1, 5, 35, 32]),
        word("=", [40, 22, 55, 24]),
        word("val_part", [60, 5, 145, 40], confidence=0),
    ]
    return {"lines": [{"words": words}]}, image, [0, 0, 160, 42]


class SymbolReviewTests(unittest.TestCase):
    def test_corrobates_a_name_and_low_underscore(self):
        source = r"R a t e=v a l_{-}p a r t^{-2}"
        magnified = r"r a t e=v a l_{-}p a r t^{-2}"
        review = review_latex(source, magnified, *fixture())
        self.assertEqual(review["latex"], r"r a t e=val\_part^{-2}")
        name, separator = review["checks"]
        self.assertEqual(name["ocr_text"], "rate")
        self.assertEqual(name["bbox_px"], [1, 5, 35, 32])
        self.assertEqual(separator["strokes_px"], [[98, 35, 115, 37]])
        self.assertEqual(review["differences"][0]["source"], "R")

    def test_word_without_a_low_stroke_does_not_replace_subscript_minus(self):
        raw = "v a l_{-}p a r t"
        review = review_latex(raw, raw, *fixture(stroke_y=20))
        self.assertIsNone(review["latex"])
        self.assertEqual(review["checks"], [])

    def test_duplicate_ocr_boxes_do_not_support_two_occurrences(self):
        page, image, box = fixture()
        page["lines"].append({"words": [page["lines"][0]["words"][-1]]})
        raw = "v a l_{-}p a r t + v a l_{-}p a r t"
        review = review_latex(raw, raw, page, image, box)
        self.assertIsNone(review["latex"])
        self.assertEqual(review["checks"], [])

    def test_no_guess_when_scales_disagree_on_an_exponent(self):
        source = "x=v a l_{-}p a r t^{-2}"
        magnified = "x=v a l_{-}p a r t^{-3}"
        review = review_latex(source, magnified, *fixture())
        self.assertIsNone(review["latex"])
        self.assertEqual(review["checks"], [])
        self.assertEqual(review["differences"][0]["source"], "2")
        self.assertEqual(review["differences"][0]["magnified"], "3")

    def test_right_side_word_does_not_select_left_side_glyph(self):
        page, image, box = fixture()
        page["lines"][0]["words"][0]["bbox"] = [58, 5, 95, 33]
        review = review_latex("R a t e=x", "r a t e=x", page, image, box)
        self.assertIsNone(review["latex"])


if __name__ == "__main__":
    unittest.main()
