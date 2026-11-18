"""Combine independent rankings of the same passages within one selected document."""
from collections import defaultdict

from hybrid_retrieval import retrieve, retrieve_candidates, retrieve_phrase_candidates
from semantic import DenseUnavailable


def ranked_passages(doc, question, original, dense=None, *, limit=8, recall_bm25=True):
    sparse = retrieve(doc, question, limit=2 * limit)
    semantic_status = "disabled"
    semantic_hits = []
    if dense is not None:
        try:
            semantic_hits = dense.search([doc], question, limit=2 * limit)
            semantic_status = "ready"
        except DenseUnavailable as exc:
            semantic_status = exc.reason
    rankings = []
    methods = []
    expanded = []
    # When E5 cannot corroborate broader matches, require a contiguous query
    # phrase in the original passage. The returned passage is still a candidate.
    if recall_bm25 and not sparse and not original:
        recall = retrieve_candidates if semantic_hits else retrieve_phrase_candidates
        expanded = recall(doc, question, limit=2 * limit)
    if sparse:
        rankings.append(sparse)
        methods.append("bm25")
    elif expanded:
        rankings.append(expanded)
        methods.append("bm25-recall" if semantic_hits else "bm25-phrase")
    elif original:
        rankings.append(original)
        methods.append("keyword")
    if semantic_hits:
        rankings.append(semantic_hits)
        methods.append("multilingual-e5-small")
    # Reciprocal rank fusion compares order, not raw BM25 and cosine scales.
    # A unique key also keeps OCR and native results scoped to the returned PDF.
    ranked = {}
    scores = defaultdict(float)
    for method, hits in zip(methods, rankings):
        for rank, hit in enumerate(hits, start=1):
            key = (hit["id"], hit["page"], hit["block"], hit.get("part", 1))
            if key not in ranked:
                ranked[key] = dict(hit, retrieval_ranks={})
            ranked[key]["retrieval_ranks"][method] = rank
            scores[key] += 1 / (20 + rank)
    hits = []
    for key in sorted(ranked, key=lambda k: -scores[k])[:limit]:
        hits.append(dict(ranked[key], score=round(scores[key], 6)))
    return hits, {"methods": methods, "dense": semantic_status}
