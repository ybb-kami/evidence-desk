import json
import unittest
from unittest.mock import Mock, patch

from scanpdf.rerank.client import Reranker, RerankError


class RerankClientTests(unittest.TestCase):
    def response(self, payload, status=200):
        connection = Mock()
        connection.getresponse.return_value.status = status
        connection.getresponse.return_value.read.return_value = json.dumps(payload).encode()
        return connection

    def test_reorders_copies_and_keeps_ties_in_candidate_order(self):
        rows = [
            {"page": 2, "block": 4, "text": "First."},
            {"page": 2, "block": 5, "text": "Second."},
            {"page": 3, "block": 9, "text": "Third."},
        ]
        response = self.response({"results": [
            {"index": 2, "relevance_score": 0.8},
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.8},
        ]})
        with patch("scanpdf.rerank.client.HTTPConnection", return_value=response) as conn:
            ranked = Reranker(port=9988).rerank("Which paragraph?", rows)
        conn.assert_called_once_with("127.0.0.1", 9988, timeout=120)
        self.assertEqual([row["block"] for row in ranked], [5, 4, 9])
        self.assertEqual([row["page"] for row in ranked], [2, 2, 3])
        self.assertTrue(all("rerank_score" not in row for row in rows))
        _, path, payload, _ = response.request.call_args.args
        self.assertEqual(path, "/v1/rerank")
        self.assertEqual(json.loads(payload)["documents"], [row["text"] for row in rows])

    def test_rejects_incomplete_and_invalid_weights(self):
        rows = [{"text": "A."}, {"text": "B."}]
        for results in (
            [{"index": 0, "relevance_score": 1}],
            [{"index": 0, "relevance_score": 1}, {"index": 0, "relevance_score": 2}],
            [{"index": 0, "relevance_score": 1}, {"index": 1, "relevance_score": float("nan")}],
        ):
            with self.subTest(results=results), patch(
                "scanpdf.rerank.client.HTTPConnection", return_value=self.response({"results": results})
            ):
                with self.assertRaises(RerankError):
                    Reranker().rerank("Query", rows)

    def test_empty_candidate_pool_does_not_open_http_connection(self):
        with patch("scanpdf.rerank.client.HTTPConnection") as conn:
            self.assertEqual(Reranker().rerank("Query", []), [])
        conn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
