"""Reorder the very same PDF chunks using a loopback llama.cpp ranking endpoint."""

from http.client import HTTPConnection, HTTPException
import json
import math


MODEL_NAME = "qwen3-reranker-0.6b"
MAX_CANDIDATES = 32
MAX_CHARS_PER_PASSAGE = 2000


class RerankError(RuntimeError):
    """The local ranker is unavailable or returned an unusable result."""


class Reranker:
    def __init__(self, port=39357, timeout=120):
        self.port = port
        self.timeout = timeout

    def rerank(self, question, passages):
        """Return reordered copies with rerank_score and their original evidence fields."""
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise ValueError("Question must contain 1–500 characters.")
        if not passages:
            return []
        texts = [row["text"] for row in passages]
        if len(texts) > MAX_CANDIDATES or any(
            not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS_PER_PASSAGE
            for text in texts
        ):
            raise ValueError("Passages exceed the local reranker input limits.")
        body = json.dumps({
            "model": MODEL_NAME,
            "query": question,
            "documents": texts,
            "top_n": len(texts),
        }, ensure_ascii=False).encode()
        connection = HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            connection.request("POST", "/v1/rerank", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise RerankError(f"Local reranking returned HTTP {response.status}.")
            result = json.loads(response.read(1024 * 1024))
        except (OSError, HTTPException, ValueError) as error:
            raise RerankError("Could not read the local reranking response.") from error
        finally:
            connection.close()
        try:
            scores = {}
            for row in result["results"]:
                index, score = row["index"], row["relevance_score"]
                if (
                    type(index) is not int or index < 0 or index >= len(texts)
                    or index in scores or type(score) not in (float, int) or not math.isfinite(score)
                ):
                    raise ValueError("Invalid result index or score")
                scores[index] = score
            if len(scores) != len(texts):
                raise ValueError("Missing result index")
        except (KeyError, OverflowError, TypeError, ValueError) as error:
            raise RerankError("The ranking result does not match the candidate set.") from error
        order = sorted(range(len(passages)), key=lambda index: (-scores[index], index))
        return [dict(passages[index], rerank_score=scores[index]) for index in order]
