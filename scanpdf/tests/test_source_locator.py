"""Source links keep PDF versions and original page geometry independently verifiable."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

from PIL import Image
import pymupdf

import app
import source_locator
import workbench


def _scope(document, chunk, phrase=None):
    result = {"id": document["id"], **{key: chunk[key] for key in ("page", "block", "part")}}
    if phrase:
        start = chunk["text"].index(phrase)
        result.update(start=start, end=start + len(phrase))
    return result


def _pdf(text, *, rotated=False):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=620, height=780)
        room = page.insert_textbox(pymupdf.Rect(92, 113, 530, 650), text, fontsize=8)
        if room < 0:
            raise AssertionError("Test PDF text did not fit")
        page.insert_text((92, 690), "Independent paragraph below the evidence.", fontsize=8)
        if rotated:
            page.set_cropbox(pymupdf.Rect(58, 95, 565, 720))
            page.set_rotation(90)
        return pdf.tobytes()


class SourceLocatorTest(unittest.TestCase):
    def test_native_character_range_and_multi_part_on_rotated_crop(self):
        phrase = "unique second part citation"
        text = ("This paragraph contains many words from the first part of a long citation. " * 28
                + phrase + ". A final sentence follows it.")
        data = _pdf(text, rotated=True)
        with TemporaryDirectory() as folder:
            library = app.Library(Path(folder))
            identity = library.add(data, "rotated.pdf")["doc"]["id"]
            doc, = library.records([identity])
            chunk, = [row for row in doc["chunks"] if phrase in row["text"]]
            self.assertGreater(chunk["part"], 1)
            result = source_locator.locate(library, _scope(doc, chunk, phrase))
        self.assertEqual((result["width"], result["height"], result["rotation"]), (625, 507, 90))
        self.assertEqual(result["text"], chunk["text"])
        self.assertEqual(result["selection"]["precision"], "glyph")
        self.assertEqual(result["text"][result["selection"]["start"]:result["selection"]["end"]], phrase)
        actual, = result["selection"]["boxes"]
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            page = pdf[0]
            found, = page.search_for(phrase)
            expected = found * page.rotation_matrix
            image = Image.open(BytesIO(source_locator.render_image(page))).convert("L")
        for chosen, known in zip(actual, expected):
            self.assertAlmostEqual(chosen, known, places=3)
        x0, y0, x1, y1 = actual
        crop = image.crop((int(x0 * image.width / result["width"]), int(y0 * image.height / result["height"]),
                           int(x1 * image.width / result["width"]), int(y1 * image.height / result["height"])))
        self.assertLess(crop.getextrema()[0], 200)

    def test_archived_version_and_ambiguity(self):
        with TemporaryDirectory() as folder:
            library = app.Library(Path(folder))
            earlier = library.add(_pdf("First revision paired paired evidence."), "same-name.pdf")["doc"]["id"]
            later = library.add(_pdf("Second revision paired paired evidence."), "same-name.pdf")["doc"]["id"]
            library.set_archived(earlier, True)
            one, = library.records([earlier], archived=None)
            two, = library.records([later], archived=None)
            chunk = one["chunks"][0]
            archived = source_locator.locate(library, _scope(one, chunk))
            current = source_locator.locate(library, _scope(two, two["chunks"][0]))
            ambiguous = dict(chunk, text="paired")
            ambiguous.pop("offset")
            with patch.object(library, "records", return_value=[dict(one, chunks=[ambiguous])]):
                fallback = source_locator.locate(library, _scope(one, ambiguous, "paired"))
            with self.assertRaises(app.Bad) as invalid:
                source_locator.locate(library, dict(_scope(one, chunk), start=0, end=len(chunk["text"]) + 1))
        self.assertEqual(invalid.exception.code, 400)
        self.assertIn("First revision", archived["text"])
        self.assertIn("Second revision", current["text"])
        self.assertEqual(archived["doc"]["id"], earlier)
        self.assertEqual(archived["pdf_url"], f"/pdf/{earlier}#page=1")
        self.assertEqual(current["doc"]["id"], later)
        self.assertEqual(fallback["passage"]["precision"], "block")
        self.assertEqual(fallback["selection"]["boxes"], [])

    def test_archived_ocr_words_from_cached_rotated_page_frame(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.set_rotation(90)
            cached = {"physical_page": 1, "rotation": 90, "width_pt": page.rect.width,
                      "height_pt": page.rect.height, "cropbox": list(page.cropbox), "regions": [],
                      "lines": [{"line_id": "1.1.1", "text": "OCR evidence", "bbox_pt": [35, 90, 125, 102],
                                 "words": [{"text": "OCR", "bbox_pt": [35, 90, 57, 102]},
                                           {"text": "evidence", "bbox_pt": [62, 90, 125, 102]}]}]}
            data = pdf.tobytes()
        with TemporaryDirectory() as folder:
            library = workbench.ScanLibrary(Path(folder))
            identity = library.add(data, "scan.pdf")["doc"]["id"]
            recognized = {"source": {"sha256": identity}, "pages": [cached],
                          "render": {"frame": "rotated_visible_page_top_left"}}
            with patch.object(workbench, "recognize_pdf", return_value=recognized):
                library.recognize(identity, 1)
            library.set_archived(identity, True)
            doc, = library.records([identity], archived=None)
            request = _scope(doc, doc["chunks"][0], "evidence")
            result = source_locator.locate(library, request)
            with patch.object(source_locator, "_cached_ocr", return_value=dict(cached, rotation=0)):
                fallback = source_locator.locate(library, request)
        self.assertEqual(result["source"], "ocr")
        self.assertEqual(result["selection"], {"start": 4, "end": 12, "precision": "word",
                                             "boxes": [[62, 90, 125, 102]]})
        self.assertEqual(fallback["passage"], {"precision": "page", "boxes": []})
        self.assertEqual(fallback["selection"]["boxes"], [])

    def test_query_accepts_only_version_anchor_and_optional_offsets(self):
        reference = {"id": "a" * 64, "page": "1", "block": "2", "part": "3"}
        path = "/api/source?" + urlencode(reference)
        self.assertEqual(source_locator._query(urlsplit(path + "&start=0&end=3"), passage=True),
                         {"id": "a" * 64, "page": 1, "block": 2, "part": 3, "start": 0, "end": 3})
        for suffix in ("&start=0", "&page=2", "&text=private", "&start=-1&end=3"):
            with self.subTest(suffix=suffix), self.assertRaises(app.Bad) as gated:
                source_locator._query(urlsplit(path + suffix), passage=True)
            self.assertEqual(gated.exception.code, 400)
        table = urlsplit(f"/api/source?id={'a' * 64}&page=1&region=p1-r1&row=4&column=2")
        self.assertEqual(source_locator._query(table, passage=True),
                         {"id": "a" * 64, "page": 1, "region": "p1-r1", "row": 4, "column": 2})
        with self.assertRaises(app.Bad):
            source_locator._query(table._replace(query=table.query.replace("p1-r1", "p2-r1")), passage=True)
        with self.assertRaises(app.Bad):
            source_locator._query(table._replace(query=table.query + "&part=1"), passage=True)

    def test_native_cell_is_recomputed_from_original_page(self):
        with TemporaryDirectory() as folder, pymupdf.open() as pdf:
            page = pdf.new_page(width=420, height=480)
            page.insert_text((42, 68), "Table 1: Score by region")
            for x in (40, 180, 280):
                page.draw_line((x, 84), (x, 174))
            for y in (84, 114, 144, 174):
                page.draw_line((40, y), (280, y))
            for y, (name, value) in zip((102, 132, 162), (("Region", "Score"), ("North", "81.4"), ("South", "76.2"))):
                page.insert_text((46, y), name)
                page.insert_text((195, y), value)
            store = workbench.ScanLibrary(Path(folder))
            identity = store.add(pdf.tobytes(), "scores.pdf")["doc"]["id"]
            store.set_archived(identity, True)
            request = {"id": identity, "page": 1, "region": "p1-r1", "row": 3, "column": 2}
            result = source_locator.locate(store, request)
            with self.assertRaises(app.Bad) as missing:
                source_locator.locate(store, dict(request, row=300))
        self.assertEqual(missing.exception.code, 404)
        self.assertEqual((result["page"], result["row"], result["column"], result["text"]), (1, 3, 2, "76.2"))
        self.assertEqual(result["passage"]["precision"], "cell")
        box, = result["passage"]["boxes"]
        self.assertTrue(pymupdf.Rect(box).contains(pymupdf.Rect(195, 153, 204, 161)))


if __name__ == "__main__":
    unittest.main()
