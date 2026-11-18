"""Combine independent rankings of the same passages within one selected document."""
from collections import Counter, defaultdict

from comparison_retrieval import paired_measurements
from hybrid_retrieval import retrieve, retrieve_candidates, retrieve_phrase_candidates
from semantic import DenseUnavailable


def _key(hit):
    return hit["id"], hit["page"], hit["block"], hit.get("part", 1)


def _select(ranked, scores, limit, *, diversify):
    keys = sorted(ranked, key=lambda key: ("paired-measurements" not in ranked[key]["retrieval_ranks"], -scores[key]))
    if not diversify:
        return keys[:limit]
    pages = Counter()
    selected = []
    while keys and len(selected) < limit:
        def priority(key):
            paired = "paired-measurements" in ranked[key]["retrieval_ranks"]
            score = scores[key] * (1 - 0.12 * min(pages[key[:2]], 2)) if not paired else scores[key]
            return paired, score

        key = max(keys, key=priority)
        keys.remove(key)
        selected.append(key)
        pages[key[:2]] += 1
    return selected


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
    paired = paired_measurements(doc, question)
    if paired:
        rankings.append(paired)
        methods.append("paired-measurements")
    # Reciprocal rank fusion compares order, not raw BM25 and cosine scales.
    # A unique key also keeps OCR and native results scoped to the returned PDF.
    ranked = {}
    scores = defaultdict(float)
    for method, hits in zip(methods, rankings):
        for rank, hit in enumerate(hits, start=1):
            key = _key(hit)
            if key not in ranked:
                ranked[key] = dict(hit, retrieval_ranks={})
            elif method == "paired-measurements":
                ranked[key].update(start=hit["start"], end=hit["end"])
            ranked[key]["retrieval_ranks"][method] = rank
            scores[key] += 1 / (20 + rank)
    hybrid = bool(semantic_hits and (sparse or expanded or original))
    if hybrid and "keyword" not in methods:
        # Only corroborate already-considered passages. This lexical signal
        # cannot independently introduce a hit or replace the sparse channel.
        method = "keyword-corroboration"
        for rank, hit in enumerate(original[:2 * limit], start=1):
            key = _key(hit)
            if key in ranked:
                ranked[key]["retrieval_ranks"][method] = rank
                scores[key] += 0.35 / (20 + rank)
        if any(method in hit["retrieval_ranks"] for hit in ranked.values()):
            methods.append(method)
    hits = []
    # At most two passages containing both named subjects and the requested
    # repeated unit precede the broader topical matches.
    for key in _select(ranked, scores, limit, diversify=hybrid):
        hits.append(dict(ranked[key], score=round(scores[key], 6)))
    return hits, {"methods": methods, "dense": semantic_status}
