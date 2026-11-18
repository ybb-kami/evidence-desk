"""Exercise the installed Tesseract and the CLI with raster PDF inputs."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import fitz
from PIL import Image

from .fixture import create_fixture


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


DEFAULT_SAMPLE = Path(__file__).resolve().parents[2] / "pdf" / "1706.03762v1_img.pdf"
SUPPLIED_SAMPLE = os.environ.get("SCANPDF_OFFICIAL_SAMPLE")


class ScanPDFTests(unittest.TestCase):
    dpi = 200

    def run_cli(self, source: Path, directory: Path, *args: str) -> tuple[dict, Path]:
        output = directory / "evidence.json"
        crops = directory / "crops"
        completed = subprocess.run(
            [
                sys.executable, "-m", "scanpdf", str(source),
                "--output", str(output), "--dpi", str(self.dpi),
                "--crops", str(crops), *args,
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(output.read_text(encoding="utf-8")), crops

    def assert_source_evidence(self, result: dict, source: Path, page_count: int) -> None:
        with source.open("rb") as stream:
            expected_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        self.assertEqual(result["source"]["sha256"], expected_sha256)
        self.assertEqual(result["source"]["page_count"], page_count)

    def assert_bbox(self, item: dict, page: dict) -> None:
        pixel_box = item["bbox"]
        point_box = item["bbox_pt"]
        for box, width, height in (
            (pixel_box, page["width_px"], page["height_px"]),
            (point_box, page["width_pt"], page["height_pt"]),
        ):
            x0, y0, x1, y1 = box
            self.assertTrue(0 <= x0 < x1 <= width, box)
            self.assertTrue(0 <= y0 < y1 <= height, box)
        for index, (pixel, point) in enumerate(zip(pixel_box, point_box)):
            limit = page["height_pt"] if index % 2 else page["width_pt"]
            self.assertAlmostEqual(point, min(limit, pixel * 72 / self.dpi), delta=0.02)

    def assert_page_evidence(self, page: dict, width_pt: float, height_pt: float) -> None:
        self.assertAlmostEqual(page["width_pt"], width_pt)
        self.assertAlmostEqual(page["height_pt"], height_pt)
        self.assertAlmostEqual(page["width_px"], math.ceil(width_pt * self.dpi / 72), delta=1)
        self.assertAlmostEqual(page["height_px"], math.ceil(height_pt * self.dpi / 72), delta=1)
        for line in page["lines"]:
            self.assert_bbox(line, page)
            self.assertTrue(line["text"].strip())
            self.assertTrue(line["words"])
            for word in line["words"]:
                self.assertTrue(word["text"].strip())
                self.assert_bbox(word, page)
        for region in page["regions"]:
            self.assert_bbox(region, page)

    def assert_marker(self, page: dict, marker: str) -> None:
        matching_lines = [
            line for line in page["lines"] if _normalized(line["text"]) == marker
        ]
        self.assertEqual(len(matching_lines), 1, page["lines"])
        x0, y0, *_ = matching_lines[0]["bbox_pt"]
        self.assertAlmostEqual(x0, 50, delta=5)
        self.assertAlmostEqual(y0, 66, delta=5)

    def assert_evidence_image(self, region: dict, crops: Path) -> None:
        image_path = crops / region["crop"]
        self.assertTrue(image_path.resolve().is_relative_to(crops.resolve()))
        with Image.open(image_path) as image:
            image.load()
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)
            min_value, max_value = image.convert("L").getextrema()
            self.assertLess(min_value, max_value, "Evidence crop is blank")

    def test_image_only_pdf_with_table_formula_and_cropped_rotated_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "image-only.pdf"
            create_fixture(source)
            with fitz.open(source) as document:
                self.assertEqual(len(document), 3)
                for page in document:
                    self.assertFalse(page.get_text().strip())
                    self.assertTrue(page.get_images())
                self.assertEqual(document[2].rotation, 90)
                self.assertNotEqual(document[2].cropbox, document[2].mediabox)

            result, crops = self.run_cli(source, directory, "--lang", "eng")
            self.assert_source_evidence(result, source, 3)
            self.assertEqual([page["physical_page"] for page in result["pages"]], [1, 2, 3])
            first, second, third = result["pages"]
            for page in (first, second, third):
                self.assert_page_evidence(page, 600, 800)
            self.assert_marker(first, "first page evidence")
            self.assert_marker(second, "second page evidence")
            self.assert_marker(third, "third page evidence")

            tables = [region for region in first["regions"] if region["type"] == "table"]
            self.assertEqual(len(tables), 1, first["regions"])
            table = tables[0]
            self.assertEqual(table["status"], "structure_recognized")
            for actual, expected in zip(table["bbox_pt"], (50, 220, 550, 370)):
                self.assertAlmostEqual(actual, expected, delta=5)
            expected_cells = {(1, 1): "alpha", (1, 2): "beta", (2, 1): "gamma", (2, 2): "delta"}
            self.assertEqual(len(table["cells"]), len(expected_cells))
            self.assertEqual(
                {(cell["row"], cell["column"]): _normalized(cell["text"]) for cell in table["cells"]},
                expected_cells,
            )
            self.assert_evidence_image(table, crops)

            formulas = [region for region in first["regions"] if region["type"] == "formula"]
            self.assertEqual(len(formulas), 1, first["regions"])
            formula = formulas[0]
            if formula["status"] == "detected_only":
                self.assertIsNone(formula["text"])
            else:
                self.assertEqual(formula["status"], "transcribed")
                transcription = re.sub(r"\s+", "", formula["text"]).replace("·", "*")
                self.assertRegex(transcription, r"^E=m\*?c(?:²|\^2|\^\{2\})$")
            self.assertEqual(str(formula["equation_number"]).strip("()"), "1")
            x0, y0, x1, y1 = formula["bbox_pt"]
            self.assertTrue(150 < x0 < 190 and 450 < y0 < 485, formula)
            self.assertTrue(x1 > 250 and y1 > 475, formula)
            self.assert_evidence_image(formula, crops)

    def test_selected_range_retains_physical_page_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "image-only.pdf"
            create_fixture(source)
            result, _ = self.run_cli(source, directory, "--pages", "2-3")
            self.assert_source_evidence(result, source, 3)
            self.assertEqual([page["physical_page"] for page in result["pages"]], [2, 3])
            self.assert_marker(result["pages"][0], "second page evidence")
            self.assert_marker(result["pages"][1], "third page evidence")

    @unittest.skipUnless(SUPPLIED_SAMPLE or DEFAULT_SAMPLE.is_file(), "Official sample path not supplied")
    def test_supplied_official_sample_produces_locatable_ocr(self) -> None:
        # This run verifies the supplied file and locators. It has no transcription ground truth.
        source = Path(SUPPLIED_SAMPLE or DEFAULT_SAMPLE).expanduser().resolve()
        with tempfile.TemporaryDirectory() as temporary, fitz.open(source) as document:
            result, crops = self.run_cli(source, Path(temporary))
            self.assert_source_evidence(result, source, len(document))
            self.assertEqual(
                [page["physical_page"] for page in result["pages"]],
                list(range(1, len(document) + 1)),
            )
            first_page = " ".join(line["text"] for line in result["pages"][0]["lines"])
            self.assertIn("attention is all you need", _normalized(first_page))
            regions = [region for page in result["pages"] for region in page["regions"]]
            self.assertTrue({"table", "formula"}.issubset({r["type"] for r in regions}))
            for region in regions:
                self.assert_evidence_image(region, crops)
            for evidence, pdf_page in zip(result["pages"], document):
                self.assert_page_evidence(evidence, pdf_page.rect.width, pdf_page.rect.height)


if __name__ == "__main__":
    unittest.main()
