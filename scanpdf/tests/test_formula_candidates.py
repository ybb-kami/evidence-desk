from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from tools.formula_candidates import _load_model, _WEIGHTS, generate_candidates


def fixture():
    return {"pages": [{
        "physical_page": 2,
        "lines": [{"words": [
            {"bbox": [10, 20, 40, 30], "text": "x=y"},
            {"bbox": [80, 20, 84, 30], "text": "("},
            {"bbox": [84, 20, 89, 30], "text": "4"},
            {"bbox": [90, 20, 94, 30], "text": ")"},
        ]}],
        "regions": [
            {"id": "p2-r1", "type": "formula", "status": "detected_only",
             "equation_number": "4", "bbox": [10, 20, 94, 35],
             "bbox_pt": [3, 6, 28.2, 10.5], "crop": "region.png"},
            {"id": "p2-r2", "type": "table"},
        ],
    }]}


class FormulaCandidateTests(unittest.TestCase):
    def test_stable_output_is_a_draft_and_preserves_source(self):
        scan = fixture()
        original = deepcopy(scan)
        dimensions = []

        def predict(encoded):
            with Image.open(BytesIO(encoded)) as image:
                dimensions.append(image.size)
            return "x=y", 0.1

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "region.png"
            Image.new("L", (84, 15), 255).save(path)
            candidate, = generate_candidates(scan, temp, predict)
            self.assertEqual(candidate["evidence_crop_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(scan, original)
        self.assertEqual(dimensions, [(78, 23), (148, 38)])
        self.assertEqual(candidate["recognition_bbox_px"], [10, 20, 80, 35])
        self.assertEqual(candidate["status"], "unverified_candidate")
        self.assertEqual(candidate["region_status"], "detected_only")
        self.assertTrue(candidate["confidence"]["variants_agree"])
        self.assertIsNone(candidate["confidence"]["calibrated_probability"])

    def test_keeps_both_disagreeing_drafts(self):
        scan = fixture()
        scan["pages"][0]["lines"] = []
        outputs = iter(["a=b", "a=6"])
        with tempfile.TemporaryDirectory() as temp:
            Image.new("L", (84, 15), 255).save(Path(temp) / "region.png")
            row, = generate_candidates(scan, temp, lambda _: (next(outputs), 0))
        self.assertFalse(row["trimmed_ocr_equation_number"])
        self.assertFalse(row["confidence"]["variants_agree"])
        self.assertEqual(row["variants"]["source_scale"]["latex"], "a=b")
        self.assertEqual(row["variants"]["upsampled_2x"]["latex"], "a=6")

    def test_rejects_crop_path_outside_directory(self):
        scan = fixture()
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            crops = parent / "crops"
            crops.mkdir()
            Image.new("L", (84, 15), 255).save(parent / "region.png")
            scan["pages"][0]["regions"][0]["crop"] = "../region.png"
            with self.assertRaisesRegex(ValueError, "inside the crop directory"):
                generate_candidates(scan, crops, lambda _: self.fail("unexpected inference"))

    def test_rejects_changed_model_files_before_starting_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in _WEIGHTS.values():
                (Path(temp) / name).write_bytes(b"incorrect download")
            with self.assertRaisesRegex(ValueError, "do not match the verified"):
                _load_model(temp)


if __name__ == "__main__":
    unittest.main()
