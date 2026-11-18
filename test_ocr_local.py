import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import fitz

from ocr_local import create_derivative


def english_available():
    if os.name != "posix" or not shutil.which("tesseract"):
        return False
    try:
        result = subprocess.run(["tesseract", "--list-langs"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return "eng" in result.stdout.splitlines()


@unittest.skipUnless(english_available(), "requires local Tesseract with English trained data")
class LocalOCRTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / "source.pdf"
        self.output = Path(temp.name) / "source.ocr.pdf"

    def fixture(self, lines, image_only=True):
        with fitz.open() as vector, fitz.open() as raster:
            for line in lines:
                page = vector.new_page(width=520, height=300)
                if line:
                    page.insert_text((45, 100), line, fontsize=28, fontname="hebo")
                if image_only:
                    image = page.get_pixmap(dpi=150, alpha=False)
                    target = raster.new_page(width=520, height=300)
                    target.insert_image(target.rect, pixmap=image)
                    self.assertEqual(target.get_text().strip(), "")
            (raster if image_only else vector).save(self.source)

    def test_real_ocr_scan_with_unchanged_physical_page_order(self):
        self.fixture(["Minimum degree is twelve", "Maximum degree is forty"])
        create_derivative(self.source, self.output)
        with fitz.open(self.output) as derivative:
            self.assertEqual(len(derivative), 2)
            self.assertIn("derivative", derivative.metadata["title"])
            self.assertIn("minimum degree is twelve", derivative[0].get_text().lower())
            self.assertIn("maximum degree is forty", derivative[1].get_text().lower())
            self.assertEqual(derivative[1].rect, fitz.Rect(0, 0, 520, 300))

    def test_no_overwrite(self):
        self.output.write_bytes(b"original output")
        with self.assertRaisesRegex(ValueError, "exists"):
            create_derivative(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"original output")

    def test_refuses_past_native_text_and_empty_ocr(self):
        self.fixture(["Previously embedded text"], image_only=False)
        with self.assertRaisesRegex(ValueError, "already has text"):
            create_derivative(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.fixture([""])
        with self.assertRaisesRegex(ValueError, "found no text"):
            create_derivative(self.source, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
