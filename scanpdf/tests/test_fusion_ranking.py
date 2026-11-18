from unittest import TestCase
from unittest.mock import Mock, patch

from rag_search import ranked_passages


def hit(page, block):
    return {"id": "sample", "name": "sample.pdf", "page": page, "block": block,
            "part": 1, "text": f"Source on page {page}, block {block}."}


class FusionRankingTests(TestCase):
    def test_repeated_pages_yield_to_nearby_scores_without_dropping_passages(self):
        first, repeated, other = hit(3, 1), hit(3, 2), hit(6, 1)
        ranked = [first, repeated, other]
        dense = Mock()
        dense.search.return_value = ranked
        with patch("rag_search.retrieve", return_value=ranked):
            fused, _ = ranked_passages({"chunks": []}, "Which two steps were reported?", [], dense, limit=3)
            lexical, _ = ranked_passages({"chunks": []}, "Which two steps were reported?", [], limit=3)
        self.assertEqual([(row["page"], row["block"]) for row in fused], [(3, 1), (6, 1), (3, 2)])
        self.assertEqual([(row["page"], row["block"]) for row in lexical], [(3, 1), (3, 2), (6, 1)])

    def test_keyword_only_corroborates_an_existing_candidate(self):
        first, evidence, uncorroborated = hit(2, 1), hit(4, 1), hit(7, 1)
        candidates = [first, evidence]
        dense = Mock()
        dense.search.return_value = candidates
        with patch("rag_search.retrieve", return_value=candidates):
            fused, metadata = ranked_passages({"chunks": []}, "Where was the measurement made?",
                                               [uncorroborated, evidence], dense)
        self.assertEqual([row["page"] for row in fused], [4, 2])
        self.assertEqual(fused[0]["retrieval_ranks"]["keyword-corroboration"], 2)
        self.assertIn("keyword-corroboration", metadata["methods"])

    def test_paired_measurements_keep_priority_and_original_offsets(self):
        first, repeated, other = hit(3, 1), hit(3, 2), hit(6, 1)
        ranked = [other, first, repeated]
        dense = Mock()
        dense.search.return_value = ranked
        paired = [dict(first, start=0, end=4), dict(repeated, start=2, end=7)]
        with (patch("rag_search.retrieve", return_value=ranked),
              patch("rag_search.paired_measurements", return_value=paired)):
            result, _ = ranked_passages({"chunks": []}, "Which two measurements were reported?", [], dense)
        self.assertEqual([row["page"] for row in result], [3, 3, 6])
        self.assertEqual([(row["start"], row["end"]) for row in result[:2]], [(0, 4), (2, 7)])
