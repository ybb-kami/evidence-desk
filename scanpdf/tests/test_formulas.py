import unittest

from PIL import Image, ImageDraw

from scanpdf.formulas import detect_formulas


def word(text, x, y, width, height=12, confidence=96, line="math"):
    return {"text": text, "bbox": [x, y, x + width, y + height],
            "line_id": line, "confidence": confidence}


def raster(words=()):
    image = Image.new("L", (500, 270), 255)
    draw = ImageDraw.Draw(image)
    for item in words:
        left, top, right, bottom = item["bbox"]
        draw.rectangle([left + 1, top + 1, right - 2, bottom - 2], fill=20)
    return image


class FormulaDetectionTests(unittest.TestCase):
    def setUp(self):
        self.words = [word("x", 195, 70, 8), word("=", 220, 73, 8, 6),
                      word("y", 238, 70, 8)]

    def test_baseline_equation_and_number(self):
        words = [*self.words, word("(3.1)", 435, 70, 30)]
        self.assertEqual(detect_formulas(raster(words), words, 240, []), [{
            "type": "formula", "bbox": [195, 70, 465, 82],
            "status": "transcribed", "method": "ocr_baseline_arithmetic",
            "text": "x = y", "equation_number": "3.1",
        }])

    def test_low_confidence_produces_region_without_text(self):
        self.words[-1]["confidence"] = 62
        result, = detect_formulas(raster(self.words), self.words, 240, [])
        self.assertEqual(result["status"], "detected_only")
        self.assertIsNone(result["text"])

    def test_equation_number_in_a_separate_ocr_line(self):
        words = [*self.words, word("(7)", 430, 69, 19, line="label")]
        result, = detect_formulas(raster(words), words, 240, [])
        self.assertEqual(result["equation_number"], "7")
        self.assertEqual(result["bbox"], [195, 69, 449, 82])

    def test_ink_missing_from_ocr_prevents_transcription(self):
        image = raster(self.words)
        ImageDraw.Draw(image).rectangle([208, 68, 214, 73], fill=0)
        result, = detect_formulas(image, self.words, 240, [])
        self.assertEqual(result["status"], "detected_only")

    def test_prose_and_page_numbers_are_excluded(self):
        words = [word("where", 150, 70, 35), *self.words,
                 word("(2)", 435, 70, 30),
                 word("(73)", 238, 230, 23, line="footer")]
        self.assertEqual(detect_formulas(raster(words), words, 240, []), [])

    def test_formula_like_table_value_is_excluded(self):
        tables = [{"bbox": [100, 30, 420, 130]}]
        self.assertEqual(detect_formulas(raster(self.words), self.words, 240, tables), [])

    def test_stacked_fraction_is_detected_without_ocr(self):
        image = raster()
        draw = ImageDraw.Draw(image)
        draw.rectangle([226, 70, 258, 71], fill=0)
        draw.rectangle([234, 56, 239, 64], fill=0)
        draw.rectangle([244, 56, 249, 64], fill=0)
        draw.rectangle([238, 76, 246, 86], fill=0)
        result, = detect_formulas(image, [], 240, [])
        self.assertEqual(result["method"], "stacked_fraction_layout")
        self.assertEqual(result["status"], "detected_only")
        self.assertIsNone(result["text"])
        self.assertEqual(result["bbox"], [226, 56, 259, 87])

    def test_explicit_exponent_is_detected_without_transcription(self):
        self.words[0]["text"] = "x^2"
        result, = detect_formulas(raster(self.words), self.words, 240, [])
        self.assertEqual(result["status"], "detected_only")


if __name__ == "__main__":
    unittest.main()
