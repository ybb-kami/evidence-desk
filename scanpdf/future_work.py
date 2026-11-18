"""Group cited future-work wording and label the strength of later body evidence."""

from itertools import combinations
import re
import unicodedata

from local_review import FUTURE, _topic_terms


MAX_VECTOR_GROUPS = 12
VECTOR_THRESHOLD = 0.87
VISIBLE_PAIRS = 24
HAN = re.compile(r"[\u3400-\u9fff]+")
EVALUATION = re.compile(r"^(?:In our experiments?,?\s+)?(?:we|our (?:team|study))\s+"
                        r"(?:measured|evaluated|tested|ran|validated|benchmarked|compared|recorded|observed)\b"
                        r"|^我们(?:实测|测试了|评估了|测得)", re.I)
AUTHOR = re.compile(r"^(?:(?:in|for) (?:our )?future (?:work|research),?\s+)?"
                    r"(?:we (?:plan|will|leave|aim|intend)|our future (?:work|research))\b|"
                    r"^(?:我们(?:将|计划|拟)|本研究下一步)", re.I)
NUMBER_CONDITION = re.compile(
    r"\b(?:with|at|using|under|across)\s+([0-9][0-9,.]*)\s*(tokens?|gpus?|tpus?|samples?|"
    r"examples?|seeds?|epochs?|milliseconds?|seconds?|gigabytes?|gb)\b", re.I)
BENCHMARK_CONDITION = re.compile(
    r"\b(?:on|using|under)\s+(?:the\s+)?([A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+){0,2})"
    r"\s+((?:test|validation|training) (?:set|split)|benchmark|dataset|protocol)\b", re.I)
POLARITY = re.compile(r"\b(?:without|never|not|no)\b|无需|不使用|未采用", re.I)
FUTURE_ACTIONS = frozenset("characterize quantify measure test assess investigate evaluate testing measurement profile".split())


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("\u00ad", "")
    text = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", text)
    return " ".join(text.split()).rstrip(".。 ")


def topics(text):
    text = FUTURE.sub(" ", text)
    terms = set(_topic_terms(text)) - FUTURE_ACTIONS
    for match in HAN.findall(text):
        terms.update(match[offset:offset + 2] for offset in range(len(match) - 1))
    return terms


def conditions(text):
    numeric = {f"{value.replace(',', '')} {unit.casefold().rstrip('s')}"
               for value, unit in NUMBER_CONDITION.findall(text)}
    benchmarks = {normalize(f"{name} {kind}") for name, kind in BENCHMARK_CONDITION.findall(text)
                  if any(char.isupper() or char.isdigit() for char in name)}
    return sorted(numeric | benchmarks)


def _condition_relation(first, second):
    if bool(POLARITY.search(first["text"])) != bool(POLARITY.search(second["text"])):
        return "polarity_differs"
    left, right = set(first["conditions"]), set(second["conditions"])
    if left < right or right < left:
        return "conditions_incomplete"
    return "conditions_differ" if left != right else "review"


def _vector_pairs(groups, dense):
    if dense is None:
        return {}, "not_requested"
    if not dense.available():
        return {}, "unavailable"
    if len(groups) > MAX_VECTOR_GROUPS:
        return {}, "scope_limit"
    from semantic import DenseUnavailable

    scores = {}
    try:
        for index, row in enumerate(groups[:-1]):
            tail = groups[index + 1:]
            values = dense.score_texts(row["text"], [other["text"] for other in tail])
            scores.update({(row["id"], other["id"]): score for other, score in zip(tail, values)})
    except DenseUnavailable as exc:
        return {}, exc.reason
    return scores, "ready"


def group_proposals(follow_ups, evidence, papers, nodes, *, dense=None):
    papers_by_id = {paper["id"]: paper for paper in papers}
    importance = {node["id"]: node["pagerank"] for node in nodes}
    groups = {}
    for row in follow_ups:
        ref = row["direction"]
        original = evidence[ref]
        paper = papers_by_id[original["doc"]]
        text = original["text"]
        match = normalize(text)
        group = groups.setdefault(match, {"id": ref, "text": text, "members": [], "conditions": conditions(text)})
        group["members"].append({"ref": ref, "doc": paper["id"], "work": paper["work"], "date": row["date"],
                                 "by_author": bool(AUTHOR.search(text)), "state": row["state"],
                                 "pagerank": importance[paper["work"]]})
    ordered = list(groups.values())
    for group in ordered:
        group["works"] = len({member["work"] for member in group["members"]})
        group["topics"] = sorted(topics(group["text"]))
        group["pagerank"] = max(member["pagerank"] for member in group["members"])
    vector, vector_state = _vector_pairs(ordered, dense)
    candidates = []
    for first, second in combinations(ordered, 2):
        works = {member["work"] for group in (first, second) for member in group["members"]}
        if len(works) < 2:
            continue
        shared = set(first["topics"]) & set(second["topics"])
        minimum = min(len(first["topics"]), len(second["topics"]))
        overlap = len(shared) / max(1, minimum)
        vector_score = vector.get((first["id"], second["id"]), 0.0)
        if len(shared) >= 3 and overlap >= .6:
            method, score = "topic", overlap
        elif vector_score >= VECTOR_THRESHOLD:
            method, score = "e5", vector_score
        else:
            continue
        candidates.append({"refs": [first["id"], second["id"]], "method": method, "score": round(score, 4),
                           "terms": sorted(shared), "relation": _condition_relation(first, second)})
    candidates.sort(key=lambda row: (row["method"] != "topic", -row["score"], row["refs"]))
    ordered.sort(key=lambda row: (-row["works"], -row["pagerank"], int(row["id"][1:])))
    return {"groups": ordered, "candidates": candidates[:VISIBLE_PAIRS], "candidate_count": len(candidates),
            "vector": vector_state, "vector_limit": MAX_VECTOR_GROUPS,
            "normalized_duplicates": len(follow_ups) - len(ordered)}


def classify_later(proposal, excerpt):
    """Require explicit shared conditions before describing a reported experiment as same-scope."""
    requested, observed = topics(proposal), topics(excerpt)
    held, tested = conditions(proposal), conditions(excerpt)
    if (EVALUATION.search(excerpt) and not POLARITY.search(excerpt) and not POLARITY.search(proposal)
            and held and held == tested and len(requested) >= 3
            and requested <= observed):
        return "reported_same_scope"
    return "topic_candidate"
