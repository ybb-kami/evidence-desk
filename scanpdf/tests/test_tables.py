import unittest

from PIL import Image, ImageDraw

from scanpdf.tables import detect_tables


def word(text, x, y, width=40):
    return {
        "text": text,
        "bbox": [x, y, x + width, y + 12],
        "line_id": str(y),
        "confidence": 94,
    }


class TableDetectionTests(unittest.TestCase):
    def setUp(self):
        self.page = Image.new("L", (720, 800), 246)
        self.draw = ImageDraw.Draw(self.page)

    def test_grid_recovers_both_columns_and_keeps_empty_cells(self):
        for x in [80, 220, 420]:
            self.draw.line((x, 90, x, 180), fill=75, width=2)
        for y in [90, 130, 180]:
            self.draw.line((80, y, 420, y), fill=75, width=2)
        ocr = [word("Measure", 90, 101), word("Value", 240, 101), word("Count", 90, 145)]

        (table,) = detect_tables(self.page, ocr, dpi=144)

        self.assertEqual(table["status"], "structure_recognized")
        self.assertEqual(table["method"], "raster_grid")
        self.assertEqual(
            [(c["row"], c["column"], c["text"], c["status"]) for c in table["cells"]],
            [
                (1, 1, "Measure", "ocr_text"),
                (1, 2, "Value", "ocr_text"),
                (2, 1, "Count", "ocr_text"),
                (2, 2, "", "empty_or_unread"),
            ],
        )

    def test_booktabs_partial_header_rule_uses_ocr_alignment(self):
        for x0, x1, y in [(85, 560, 110), (235, 540, 147), (85, 560, 244)]:
            self.draw.line((x0, y, x1, y), fill=67, width=2)
        ocr = [
            word("Model", 98, 121), word("Precision", 265, 121, 60), word("Recall", 428, 121),
            word("Baseline", 98, 160, 55), word("0.81", 279, 160), word("0.74", 433, 160),
            word("Proposed", 98, 201, 65), word("0.93", 279, 201), word("0.86", 433, 201),
        ]

        (table,) = detect_tables(self.page, ocr, dpi=144)

        self.assertEqual(table["status"], "structure_recognized")
        self.assertEqual(table["method"], "horizontal_rules_ocr_alignment")
        self.assertEqual([c["text"] for c in table["cells"]], [
            "Model", "Precision", "Recall",
            "Baseline", "0.81", "0.74",
            "Proposed", "0.93", "0.86",
        ])
        self.assertEqual(table["cells"][0]["bbox"][3], 148)

    def test_sparse_table_keeps_multiline_heading_and_overlapping_word_boxes(self):
        for y in [90, 158, 245]:
            self.draw.line((80, y, 560, y), fill=60, width=2)
        ocr = [
            word("Sample", 95, 105), word("Mean", 273, 105), word("Standard", 433, 105),
            word("deviation", 433, 131),
            word("Control", 95, 169), word("12.4", 273, 171), word("0.2", 433, 172),
            word("Treatment", 95, 184), word("14.6", 273, 182), word("0.3", 433, 185),
        ]

        (table,) = detect_tables(self.page, ocr, dpi=144)

        self.assertEqual(table["status"], "structure_recognized")
        self.assertEqual([c["text"] for c in table["cells"]], [
            "Sample", "Mean", "Standard\ndeviation",
            "Control", "12.4", "0.2",
            "Treatment", "14.6", "0.3",
        ])

    def test_open_sides_use_continuous_vertical_dividers_and_ocr_rows(self):
        for x in [230, 415]:
            self.draw.line((x, 90, x, 220), fill=0, width=2)
        for y in [90, 135, 220]:
            self.draw.line((80, y, 560, y), fill=0, width=2)
        ocr = [
            word("Sample", 95, 105), word("Protocol", 290, 105), word("Score", 460, 105),
            word("Control", 95, 145), word("Method A", 290, 145), word("15", 460, 145),
            word("Treatment", 95, 180), word("Method B", 290, 180), word("29", 460, 180),
            word("|", 224, 145, 12),
        ]

        (table,) = detect_tables(self.page, ocr, dpi=144)

        self.assertEqual(table["method"], "vertical_rules_ocr_alignment")
        self.assertEqual([c["text"] for c in table["cells"]], [
            "Sample", "Protocol", "Score", "Control", "Method A", "15", "Treatment", "Method B", "29",
        ])

    def test_grouped_header_has_anchored_spans_and_preserves_empty_cells(self):
        for y in [80, 300, 370]:
            self.draw.line((80, y, 640, y), fill=0, width=2)
        for x0, x1, y in [(245, 415, 130), (432, 615, 130), (80, 214, 180), (239, 640, 180)]:
            self.draw.line((x0, y, x1, y), fill=0, width=2)
        ocr = [
            word("Sample", 95, 122),
            word("Accuracy", 295, 101, 65), word("Run time", 488, 101, 65),
            word("Left", 255, 145, 45), word("Right", 349, 145, 44),
            word("Left", 451, 145, 35), word("Right", 553, 145, 45),
            *[
                word(value, x, y, 30)
                for y in [200, 233]
                for x, value in [(95, "Trial"), (260, "32"), (355, "46"), (453, "83"), (558, "98")]
            ],
            word("Incomplete", 95, 266, 90), word("62", 255, 266),
            word("Pooled", 95, 318, 62), word("78", 255, 318), word("56", 349, 318),
            word("1.23", 499, 318, 42),
        ]

        (table,) = detect_tables(self.page, ocr, dpi=144)
        cells = {(c["row"], c["column"]): c for c in table["cells"]}

        self.assertEqual(table["method"], "grouped_horizontal_rules_ocr_alignment")
        self.assertEqual(cells[1, 1]["text"], "Sample")
        self.assertEqual(cells[1, 1]["row_span"], 2)
        self.assertEqual(cells[1, 2]["text"], "Accuracy")
        self.assertEqual(cells[1, 2]["column_span"], 2)
        self.assertEqual(cells[1, 4]["text"], "Run time")
        self.assertEqual(cells[1, 4]["column_span"], 2)
        self.assertNotIn((2, 1), cells)
        self.assertEqual([cells[2, j]["text"] for j in [2, 3, 4, 5]], ["Left", "Right", "Left", "Right"])
        self.assertEqual(cells[5, 3]["status"], "empty_or_unread")
        self.assertEqual(cells[5, 3]["text"], "")
        self.assertEqual(cells[6, 4]["text"], "1.23")
        self.assertEqual(cells[6, 4]["column_span"], 2)
        self.assertNotIn((6, 5), cells)

    def test_grouped_header_and_long_body_gap_keeps_full_extent(self):
        for y in [80, 300, 430]:
            self.draw.line((80, y, 640, y), fill=0, width=2)
        self.draw.line((230, 125, 404, 125), fill=0, width=2)
        self.draw.line((450, 125, 625, 125), fill=0, width=2)
        ocr = [
            word("Measure", 96, 104), word("First group", 264, 104), word("Second group", 477, 104),
            *[
                word(value, x, y)
                for y in [167, 211, 255, 329, 385]
                for x, value in [(96, "Sample"), (243, "12"), (353, "17"), (463, "13"), (563, "21")]
            ],
        ]

        (table,) = detect_tables(self.page, ocr, dpi=144)

        self.assertEqual(table["status"], "detected_only")
        self.assertEqual(table["bbox"], [80, 81, 641, 432])

    def test_stacked_single_column_diagram_nodes_are_not_tables(self):
        ocr = []
        for y, name in [(90, "Input"), (145, "Transform"), (200, "Output")]:
            self.draw.rectangle((235, y, 357, y + 31), outline=0, width=2)
            self.draw.line((295, y + 31, 295, y + 55), fill=0, width=2)
            ocr.append(word(name, 260, y + 8, 70))

        self.assertEqual(detect_tables(self.page, ocr, dpi=144), [])

    def test_spanning_text_can_leave_sparse_structure_unresolved(self):
        for y in [90, 145, 260]:
            self.draw.line((80, y, 560, y), fill=55, width=2)
        ocr = [
            word("Measure", 90, 115), word("Grouped headings", 220, 115, 280),
            word("Control", 90, 175), word("12", 235, 175), word("13", 420, 175),
            word("Sample", 90, 215), word("14", 235, 215), word("15", 420, 215),
        ]

        (table,) = detect_tables(self.page, ocr, dpi=144)

        self.assertEqual(table["status"], "detected_only")
        self.assertEqual(table["cells"], [])

    def test_grid_with_missing_vertical_boundary_does_not_invent_cells(self):
        for x in [80, 420]:
            self.draw.line((x, 90, x, 180), fill=0, width=2)
        self.draw.line((220, 130, 220, 180), fill=0, width=2)
        for y in [90, 130, 180]:
            self.draw.line((80, y, 420, y), fill=0, width=2)

        (table,) = detect_tables(self.page, [], dpi=144)

        self.assertEqual(table["status"], "detected_only")
        self.assertEqual(table["cells"], [])

    def test_prose_between_two_rules_is_not_a_table(self):
        for y in [110, 220]:
            self.draw.line((85, y, 560, y), fill=67, width=2)
        ocr = [
            word("word", x, y, 35)
            for y, shift in [(125, 0), (149, 3), (173, 6)]
            for x in range(92 + shift, 535, 42)
        ]

        self.assertEqual(detect_tables(self.page, ocr, dpi=144), [])

    def test_two_same_width_tables_with_a_prose_gap_stay_separate(self):
        ocr = []
        for top in [80, 202]:
            for x in [80, 220, 420]:
                self.draw.line((x, top, x, top + 90), fill=0, width=2)
            for y in [top, top + 40, top + 90]:
                self.draw.line((80, y, 420, y), fill=0, width=2)
            ocr.extend([word("Measure", 90, top + 13), word("Value", 240, top + 13)])

        tables = detect_tables(self.page, ocr, dpi=144)

        self.assertEqual(len(tables), 2)
        self.assertTrue(all(table["status"] == "structure_recognized" for table in tables))


if __name__ == "__main__":
    unittest.main()
