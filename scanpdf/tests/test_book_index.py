import unittest
from unittest.mock import Mock

import workbench
from book_index import answer
from followup import answer_question
from rag_search import ranked_passages


def block(page, number, text, part=1, offset=0):
    return {"page": page, "block": number, "part": part, "offset": offset, "text": text}


class BookIndexTests(unittest.TestCase):
    def setUp(self):
        labels = [str(n) for n in range(1, 81)]
        labels[76:80] = ["63", "64", "65", "66"]
        self.doc = {"id": "b" * 64, "name": "Measurement handbook.pdf", "pages": 80, "page_labels": labels,
                    "chunks": [
                        block(61, 1, "Calorimeter, 710\nThis is an equipment description, 480"),
                        block(77, 1, "63\nBibliography"), block(77, 2, "Calorimeter, 700\nDark count, 27"),
                        block(78, 1, "Subject Index\n64"),
                        block(78, 2, "Beam, 15\nCalorimeter, 16–18,\n29\n短波滤波、１８－２１、３４\nCalorimeter offset, 8"),
                        block(79, 1, "Beam, 28\nDrift correction ........ 32, 40\nanode, ii–iv\nLaser, see optical cavity"),
                        block(80, 1, "Appendix A"), block(80, 2, "Calorimeter, 6\nAppendix subject, 17"),
                    ]}

    def ask(self, question, anchor=None):
        return answer(self.doc, question, anchor)

    def test_printed_ranges_wrap_and_original_offsets(self):
        result = self.ask('What printed pages does the back-of-book index list for "Calorimeter"?')
        item = result["book_index"]
        self.assertEqual(item["printed_pages"], ["16–18", "29"])
        self.assertEqual((item["physical_page"], item["page_label"]), (78, "64"))
        self.assertIn("PDF physical page 78 (page label 64)", result["answer"])
        hit, = result["hits"]
        self.assertEqual(hit["text"][hit["start"]:hit["end"]], "Calorimeter, 16–18,\n29")
        self.assertEqual(item["anchor"], {key: hit[key] for key in ("id", "page", "block", "part", "start", "end")})

    def test_chinese_term_with_fullwidth_locators_and_reverse_question(self):
        for question in ("教材书末索引将短波滤波指向哪些印刷页？", "短波滤波在书末索引中对应哪些印刷页？",
                         "书末索引中查询短波滤波的页码"):
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertEqual(result["book_index"]["printed_pages"], ["18–21", "34"])
                self.assertIn("PDF 物理第 78 页", result["answer"])

    def test_continuation_dots_and_roman_printed_labels(self):
        cases = {"Which page references does the index entry for Drift correction give?": ["32", "40"],
                 "Which pages does the index list for anode?": ["ii–iv"]}
        for question, expected in cases.items():
            with self.subTest(question=question):
                result = self.ask(question)["book_index"]
                self.assertEqual((result["physical_page"], result["printed_pages"]), (79, expected))

    def test_ambiguity_requires_original_anchor(self):
        question = "Which pages does the book index list for Beam?"
        unscoped = self.ask(question)
        self.assertIsNone(unscoped["answer"])
        self.assertIn("Multiple", unscoped["gap"])
        self.assertEqual([hit["page"] for hit in unscoped["hits"]], [78, 79])
        for hit, page in zip(unscoped["hits"], ("15", "28")):
            self.assertEqual(self.ask(question, hit)["book_index"]["printed_pages"], [page])
        wrong = self.ask(question, self.doc["chunks"][0])
        self.assertIsNone(wrong["answer"])
        self.assertIn("outside", wrong["gap"])

    def test_other_sections_unknown_term_and_cross_reference_do_not_masquerade_as_locators(self):
        for term in ("Dark count", "Laser", "Calor", "Appendix subject", "Missing"):
            with self.subTest(term=term):
                result = self.ask(f'What printed pages does the index list for "{term}"?')
                self.assertIsNone(result["answer"])
                self.assertEqual(result["hits"], [])
                self.assertNotIn("book_index", result)

    def test_topic_questions_and_unverified_book_index_do_not_supply_printed_pages(self):
        for question in ("Explain Calorimeter from the book index", "What is a calorimeter?",
                         "How does a Calorimeter work according to the index on page 78?"):
            self.assertIsNone(self.ask(question))
        book = self.doc | {"pages": 1, "chunks": [block(1, 1, "Beam, 75\nCalorimeter, 87")]}
        result = answer(book, "What pages are listed in the index for Calorimeter?")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["hits"], [])

    def test_explicit_final_page_does_not_jump_to_an_earlier_index_page(self):
        result = self.ask("教材末页索引将短波滤波指向哪些印刷页？")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["hits"], [])

    def test_overlapping_passages_deduplicate_but_truncated_labels_do_not_match(self):
        header = block(80, 1, "Index\n66")
        preceding = "Tungsten foil, 22\n"
        full = preceding + "Long negative ion trap, 34\nNoisy detector, 71\nXenon vessel, 102"
        start = full.index("negative")
        self.doc["chunks"][-2:] = [header, block(80, 2, full), block(80, 2, full[start:], 2, start)]
        unique = self.ask('What printed pages does the index list for "Noisy detector"?')
        self.assertEqual(unique["book_index"]["printed_pages"], ["71"])
        partial = self.ask('What printed pages does the index list for "negative ion trap"?')
        self.assertIsNone(partial["answer"])


class BookIndexRetrievalFallbackTests(unittest.TestCase):
    def setUp(self):
        self.doc = {"id": "a" * 64, "name": "Laboratory reference.pdf", "pages": 30,
                    "page_labels": [str(page) for page in range(1, 31)], "chunks": [
                        block(6, 1, "The hash index architecture uses a keyed lookup and closed addressing."),
                        block(9, 1, "漂移校正的正文介绍与束流监测采用独立实验。 Drift correction and beam monitoring use separate procedures."),
                        block(30, 1, "Subject Index\n30"),
                        block(30, 2, "Drift correction, 12–14\nBeam monitoring, 16\nQuartz tubes, 18, 23"),
                        block(30, 3, "漂移校正, 12–14\n束流监测, 16\n石英管, 18, 23"),
                    ]}
        self.store = Mock()
        self.store.records.return_value = [self.doc]
        self.store.dense = None
        self.store.passage_reader.candidates.return_value = ([], "disabled", None)
        self.generator = Mock()
        self.generator.answer.return_value = {"status": "disabled"}

    def ask(self, question, anchor=None):
        return answer_question(self.store, {"id": self.doc["id"], "question": question, "anchor": anchor},
                               generator=self.generator)

    def _ordinary_candidates(self, question, doc=None):
        doc = self.doc if doc is None else doc
        return ranked_passages(doc, question, workbench.engine.find_evidence([doc], question))

    def test_unquoted_multipart_english_and_chinese_keep_ranked_candidates_only(self):
        questions = (
            ("Can you check the alphabetical index and tell me where it points for drift correction and beam monitoring?", 2),
            ("请查看末尾的索引，告诉我漂移校正和束流监测分别有哪些印刷页。", 3),
        )
        for question, index_block in questions:
            with self.subTest(question=question):
                result = self.ask(question)
                candidates, retrieval = self._ordinary_candidates(question)
                self.assertEqual(result["answer_method"], "book_index")
                self.assertEqual(result["index_status"], "unresolved")
                self.assertIsNone(result["answer"])
                self.assertTrue(result["gap"])
                self.assertEqual((result["hits"], result["retrieval"]), (candidates, retrieval))
                self.assertEqual((result["hits"][0]["page"], result["hits"][0]["block"]), (30, index_block))
        self.generator.answer.assert_not_called()
        self.store.passage_reader.candidates.assert_not_called()

    def test_ordinary_index_topic_uses_ordinary_retrieval(self):
        question = "Which page introduces the hash index architecture?"
        self.assertIsNone(answer(self.doc, question))
        self.assertIsNone(answer(self.doc, "Which memory page stores active index entries?"))
        self.assertIsNone(answer(self.doc, "数据库索引中哪几页被缓存？"))
        result = self.ask(question)
        self.assertNotEqual(result.get("answer_method"), "book_index")
        self.assertEqual([(hit["page"], hit["block"]) for hit in result["hits"]], [(6, 1)])

    def test_unresolved_anchor_stays_in_selected_index_passage(self):
        question = "Can you check the alphabetical index and tell me where it points for drift correction and beam monitoring?"
        result = self.ask(question, [30, 2, 1])
        chunk = next(chunk for chunk in self.doc["chunks"] if (chunk["page"], chunk["block"]) == (30, 2))
        candidates, _ = self._ordinary_candidates(question, dict(self.doc, chunks=[chunk]))
        self.assertTrue(candidates)
        self.assertEqual(result["hits"], candidates)
        self.assertIsNone(result["answer"])
        outside = self.ask(question, [9, 1, 1])
        self.assertEqual(outside["hits"], [])
        self.assertIn("outside", outside["gap"])

    def test_exact_unique_entry_still_returns_original_span(self):
        result = self.ask("What printed pages does the book index list for Quartz tubes?")
        self.assertEqual(result["book_index"]["printed_pages"], ["18", "23"])
        self.assertNotIn("retrieval", result)
        hit, = result["hits"]
        self.assertEqual(hit["text"][hit["start"]:hit["end"]], "Quartz tubes, 18, 23")


if __name__ == "__main__":
    unittest.main()
