"""Evidence-grounded lookup across cached scan and native PDF table cells."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pymupdf

from scan_table_qa import answer_native_table, answer_scan_table, native_cell_box
from workbench import ScanLibrary, index_page


def scan_cell(row, column, text, **spans):
    left = 40 + (column - 1) * 100
    top = 110 + (row - 1) * 20
    return dict(row=row, column=column, text=text,
                status="ocr_text" if text else "empty_or_unread",
                bbox_pt=[left, top, left + spans.get("column_span", 1) * 100,
                         top + spans.get("row_span", 1) * 20], **spans)


def scan_line(block, y, text, *columns):
    return dict(line_id=f"{block}.1.1", bbox_pt=[45, y, 500, y + 8], text=text,
                words=[{"bbox_pt": [45 + i * 100, y, 85 + i * 100, y + 8]} for i in columns])


class ScanTableTests(unittest.TestCase):
    def setUp(self):
        cells = [scan_cell(1, 1, "System", row_span=2), scan_cell(1, 2, "BLEU", column_span=2),
                 scan_cell(1, 4, "Training Cost (FLOPs)", column_span=2), scan_cell(2, 2, "EN-IT"),
                 scan_cell(2, 3, "EN-ES"), scan_cell(2, 4, "EN-IT"), scan_cell(2, 5, "EN-ES"),
                 scan_cell(3, 1, "Example (base model)"), scan_cell(3, 2, "14.2"), scan_cell(3, 3, ""),
                 scan_cell(3, 4, "3.7 · 10¹⁰", column_span=2), scan_cell(4, 1, "Example (big)"),
                 scan_cell(4, 2, "14.9"), scan_cell(4, 3, "21.4"), scan_cell(4, 4, "2.1e11", column_span=2)]
        self.page = {"physical_page": 3, "lines": [scan_line(1, 91, "Table 7: Translation scores"),
                      scan_line(2, 154, "Example (base model) 14.2 3.7 · 10¹⁰", 0, 1, 3),
                      scan_line(3, 176, "Example (big) 14.9 21.4 2.1e11", 0, 1, 2, 3)],
                     "regions": [{"type": "table", "id": "p3-r1", "status": "structure_recognized",
                                  "bbox_pt": [40, 110, 540, 190], "cells": cells}]}
        self.doc = {"id": "a" * 64, "name": "example.pdf", "chunks": [], "textless_pages": [3], "text_chars": 0}
        index_page(self.doc, self.page)

    def page_info(self, docid, number):
        self.assertEqual(docid, self.doc["id"])
        self.assertEqual(number, 3)
        return {"page": self.page}

    def ask(self, question, anchor=None):
        return answer_scan_table(self, self.doc, question, anchor)

    def test_language_columns_and_same_column_calculation(self):
        result = self.ask("表七 Example 的基础版和大模型，英译意的BLEU相差多少？")
        self.assertIn("0.7 BLEU", result["answer"])
        cells = result["table_cells"]
        self.assertEqual([(cell["page"], cell["table"], cell["row"], cell["column"], cell["block"])
                          for cell in cells], [(3, 7, 3, 2, 2), (3, 7, 4, 2, 3)])
        self.assertEqual(cells[0]["bbox_pt"], [140, 150, 240, 170])
        self.assertTrue(any(hit["text"] == "Table 7: Translation scores" for hit in result["hits"]))

    def test_merged_flops_applies_across_the_two_named_directions(self):
        result = self.ask("What is Example base's training cost for English-to-Spanish in Table 7?")
        cells = result["table_cells"]
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["value"], "3.7 × 10^10 FLOPs")
        self.assertEqual(cells[0]["column_span"], 2)
        self.assertIn("EN-IT · EN-ES", cells[0]["column_label"])

    def test_blank_unknown_language_ambiguity_and_conflicting_coordinate(self):
        blank = self.ask("Table 7, Example base English to Spanish BLEU value")
        self.assertIsNone(blank["answer"])
        self.assertIsNone(blank["table_cells"][0]["value"])
        for question in ("Table 7 Example big Spanish to English BLEU score",
                         "Table 7 Example big BLEU score", "Table 7 row 4 column 4 BLEU",
                         "Table 7 row 4 column 2 Example base",
                         "Table 7 English to Italian BLEU of Example base and Other big"):
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertIsNone(result["answer"])
                self.assertTrue(result["gap"])
        self.assertIsNone(self.ask("Who wrote the document?"))

    def test_anchor_does_not_broaden_to_another_ocr_chunk(self):
        selected = next(chunk for chunk in self.doc["chunks"] if chunk["block"] == 2)
        within = self.ask("Table 7 Example base EN-IT BLEU", selected)
        outside = self.ask("Table 7 Example big EN-IT BLEU", selected)
        self.assertIsNotNone(within["answer"])
        self.assertIsNone(outside["answer"])
        self.assertEqual(outside["hits"], [])


class NativeTableTests(unittest.TestCase):
    def test_unruled_key_value_fields_and_original_units(self):
        with TemporaryDirectory() as directory, pymupdf.open() as pdf:
            page = pdf.new_page(width=480, height=420)
            page.insert_text((45, 45), "Equipment profile", fontsize=14)
            for y, key, value in ((94, "Component", "Specification"), (124, "Controller", "MICA-9"),
                                  (154, "Actuator", "Linear drive"), (184, "Power source", "18 V"),
                                  (214, "Protection", "Thermal relay")):
                page.insert_text((55, y), key)
                page.insert_text((268, y), value)
            store = ScanLibrary(Path(directory))
            docid = store.add(pdf.tobytes(), "profile.pdf")["doc"]["id"]
            doc = store.records([docid])[0]
            found = answer_native_table(store, doc, "On page 1, what controller and power source are listed?")
            self.assertEqual([cell["value"] for cell in found["table_cells"]], ["MICA-9", "18 V"])
            self.assertEqual([cell["value_kind"] for cell in found["table_cells"]], ["reported_text", "number"])
            for cell in found["table_cells"]:
                self.assertIsNone(cell["table"])
                self.assertEqual(native_cell_box(page, cell["region"], cell["row"], cell["column"])["text"], cell["text"])
            unscoped = answer_native_table(store, doc, "What controller and power source are listed?")
            self.assertEqual(unscoped["table_cells"], found["table_cells"])

    def test_known_condition_conflict_and_partial_row_fields(self):
        with TemporaryDirectory() as directory, pymupdf.open() as pdf:
            page = pdf.new_page(width=430, height=480)
            page.insert_text((42, 68), "Table 4: Comparison by data source")
            for x in (40, 123, 210, 290, 390):
                page.draw_line((x, 84), (x, 204))
            for y in (84, 114, 144, 174, 204):
                page.draw_line((40, y), (390, y))
            texts = (("System", "Dataset", "F1", "Latency (ms)"), ("Alpha", "Set-East", "81.4", "-"),
                     ("Beta", "Set-West", "76.2", "3.7"), ("Gamma", "Set-North", "80.1", "2.6"))
            for y, values in zip((102, 132, 162, 192), texts):
                for x, value in zip((46, 130, 218, 299), values):
                    page.insert_text((x, y), value)
            store = ScanLibrary(Path(directory))
            docid = store.add(pdf.tobytes(), "conditions.pdf")["doc"]["id"]
            doc = store.records([docid])[0]
            wrong = answer_native_table(store, doc, "In Table 4, what F1 does Alpha report on Set-West?")
            self.assertIsNone(wrong["answer"])
            self.assertTrue(wrong["gap"])
            partial = answer_native_table(store, doc, "What are Alpha's F1 and latency for Set-East in Table 4?")
            self.assertIn("81.4", partial["answer"])
            self.assertIn("Latency", partial["gap"])
            self.assertEqual([cell["value"] for cell in partial["table_cells"]], ["81.4", None])
            paired = answer_native_table(store, doc,
                "In Table 4, what F1 does Alpha report on Set-East and what latency does Beta report on Set-West?")
            self.assertEqual([(cell["row"], cell["column"], cell["value"]) for cell in paired["table_cells"]],
                             [(2, 3, "81.4"), (3, 4, "3.7 ms")])

    def test_native_hierarchical_caption_and_physical_cell(self):
        with TemporaryDirectory() as directory, pymupdf.open() as pdf:
            page = pdf.new_page(width=420, height=480)
            page.insert_text((42, 68), "Table 2.4: Measurements on two collections")
            for x in (40, 180, 280, 380):
                page.draw_line((x, 84), (x, 204))
            for y in (84, 114, 144, 174, 204):
                page.draw_line((40, y), (380, y))
            for y, row in zip((102, 132, 162, 192), (("Collection", "F1", "Latency"),
                                                   ("North", "81.4", "2.5 ms"),
                                                   ("South", "76.2", "3.7 ms"),
                                                   ("East", "80.1", "2.6 ms"))):
                for x, value in zip((46, 195, 295), row):
                    page.insert_text((x, y), value)
            store = ScanLibrary(Path(directory))
            docid = store.add(pdf.tobytes(), "collections.pdf")["doc"]["id"]
            doc = store.records([docid])[0]
            found = answer_native_table(store, doc, "In Table 2.4, what is the F1 of South?")
            self.assertIn("76.2", found["answer"])
            cell = found["table_cells"][0]
            self.assertEqual((cell["table"], cell["page"], cell["row"], cell["column"], cell["source"]),
                             ("2.4", 1, 3, 2, "native"))
            self.assertIn("block", cell)
            self.assertTrue(pymupdf.Rect(cell["bbox_pt"]).contains(pymupdf.Rect(195, 153, 204, 161)))
            located = native_cell_box(page, cell["region"], cell["row"], cell["column"])
            self.assertEqual((located["bbox_pt"], located["text"]), (cell["bbox_pt"], "76.2"))
            self.assertIsNone(native_cell_box(page, "p2-r1", 3, 2))
            self.assertIsNone(native_cell_box(page, cell["region"], 500, 2))
            missing = answer_native_table(store, doc, "In Table 2.5, what is the F1 of South?")
            self.assertIsNone(missing["answer"])
            self.assertTrue(missing["gap"])
