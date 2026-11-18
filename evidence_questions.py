"""Local concept-to-text retrieval. No answer generation or relation inference."""
import re
import unicodedata
from decimal import Decimal

from engine import find_evidence

MAX_PAPERS = 8
MAX_PER_CONCEPT = 4
BETAS = r"(?:β|beta)[ _-]?[12](?![a-z0-9])"
BLEU = r"\bbleu\b"
GPU = r"\b(?:gpus?|graphics processing units?|accelerators?|tpus?)\b"
OPT = r"\b(?:optimi[sz](?:er|ers|ation)|adamw?|sgd|stochastic gradient descent)\b"
LANG = {
    "en-fr": (r"英\s*(?:译|到|至|[-→])?\s*法|英文.{0,5}法文|英语.{0,5}法语|english\s*[- ]to\s*[- ]french", r"\benglish\s*[-– ]to\s*[-– ]french\b|\ben\s*[-→/]\s*fr\b"),
    "en-de": (r"英\s*(?:译|到|至|[-→])?\s*德|英文.{0,5}德文|英语.{0,5}德语|english\s*[- ]to\s*[- ]german", r"\benglish\s*[-– ]to\s*[-– ]german\b|\ben\s*[-→/]\s*de\b"),
}
CONCEPTS = [
    ("hardware", "训练硬件", r"训练.{0,5}硬件|硬件|显卡|图形处理器|(?<![a-z0-9])(?:gpus?|tpus?)(?![a-z0-9])|accelerator|training hardware",
     ("GPU", "graphics processing unit", "accelerator", "TPU")),
    ("optimizer", "优化器", r"优化器|优化算法|optimi[sz](?:er|ation)|(?<![a-z0-9])(?:adamw?|sgd)(?![a-z0-9])",
     ("optimizer", "Adam / AdamW", "SGD")),
    ("betas", "优化器 β 参数", BETAS + r"|β\s*参数|贝塔|动量系数",
     ("β1 / β2", "beta1 / beta2")),
    ("bleu", "BLEU 指标", r"(?<![a-z0-9])bleu(?![a-z0-9])",
     ("BLEU", "English-to-French / English-to-German（若问题指定）")),
]
CONTEXT_ONLY = re.compile(
    r"\b(?:no|not|never|cannot|neither|without|fail\w*|unable|didn['’]t|doesn['’]t|"
    r"could|would|may|might|expect\w*|aim\w*|hypoth\w*|predict\w*|"
    r"target\w*|goal\w*|threshold\w*|require\w*|intend\w*|need\w*|"
    r"improv\w*|gain\w*|increase\w*|reduc\w*|decreas\w*|difference\w*|"
    r"point\w*|higher|lower|minimum|maximum|about|approximately|at least|at most|up to)\b")
# Intentionally small grammar: only affirmative, explicit reporting phrases.
# Unsupported forms still appear in their full original excerpt.
NUMBER = r"(\d{1,3}(?:\.\d+)?)(?![\w%]|\.\d|[,/]\d)"
SCORE = re.compile(
    r"\b(?:achieves|achieved|obtains|obtained|reaches|reached|establishes|established)\s+"
    r"(?:(?:a|an|new|single-model|state-of-the-art|the)\s+){0,7}"
    r"(?:bleu score of\s+" + NUMBER + r"|" + NUMBER + r"\s+bleu(?:\s+score)?\b)")
DELIMITER = re.compile(r"(?<=[.!?])\s+(?=[a-z])|[;；]")
CLAUSE = re.compile(r"[、，,。；;!?！？/]+|\b(?:and(?: also)?|as well as)\b|以及|并且|和|及")


def _literal_queries(query):
    query = query.strip()
    variants = [query]
    # Preserve the original query and independently search unmapped clauses.
    for piece in CLAUSE.split(query):
        piece = piece.strip()
        # Separate conjunctions in common English questions can leave
        # interrogative scaffolding on the final constituent.
        if re.match(r"^(?:what|which)\b", query, re.I):
            piece = re.sub(r"^\s*(?:what|which)\s+(?:(?:is|are|was|were)\s+)?", "", piece, flags=re.I)
            piece = re.sub(r"\s+(?:was|were|is|are)\s+(?:used|reported|chosen|selected|specified)\s*$", "", piece, flags=re.I)
        if piece and piece != query and not any(re.search(c[2], _norm(piece)) for c in CONCEPTS):
            variants.append(piece)
    return list(dict.fromkeys(variants))[:1 + MAX_PER_CONCEPT]


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _sentences(text):
    return DELIMITER.split(text)


def _observed_scores(text, languages):
    for sentence in _sentences(text):
        tasks = [key for key, (_, matcher) in LANG.items() if re.search(matcher, sentence)]
        if (len(tasks) != 1 or (languages and tasks[0] not in languages)
                or CONTEXT_ONLY.search(sentence)):
            continue
        values = list(SCORE.finditer(sentence))
        # Two quantified reports in a sentence can refer to different models or conditions.
        if len(values) != 1:
            continue
        for m in values:
            value = m[1] or m[2]
            if 0 <= Decimal(value) <= 100:
                yield tasks[0], value


def _rank(concept, text, wanted):
    metric = bool(re.search(BLEU, text))
    opt = bool(re.search(OPT, text))
    beta_count = len(set(re.findall(BETAS, text)))
    if concept == "hardware" and re.search(GPU, text):
        model = bool(re.search(r"\b(?:nvidia|amd|intel|tesla|[pavhl]\d{2,4}|rtx\s*\d{3,4})\b", text))
        train = bool(re.search(r"\btrain\w*\b|\b(?:machine|cluster|server)\b", text))
        return 1 + 3*model + 2*train + 2*bool(re.search(r"\b(?:machine|cluster|server)\b", text))
    if concept == "optimizer" and opt:
        return 1 + 2*bool(beta_count) + 2*bool(re.search(r"\b(?:used|use|train\w*)\b", text))
    if concept == "betas" and beta_count:
        return 1 + beta_count + 4*opt
    if concept == "bleu" and metric:
        has_task = any(re.search(LANG[key][1], text) for key in wanted)
        if wanted and not has_task:
            return 0
        candidates = list(_observed_scores(text, wanted))
        return 1 + 4*bool(candidates) + 2*has_task
    return 0


def locate_question(docs, query):
    """Retrieve original local excerpts per document; never choose an answer."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError("请输入 1 到 500 字的检索问题。")
    if not docs or len(docs) > MAX_PAPERS:
        raise ValueError("请选择 1 到 8 篇活动文献。")
    normalized = _norm(query)
    wanted = {key for key, (hint, _) in LANG.items() if re.search(hint, normalized)}
    concepts = [{"id": k, "label": label, "terms": aliases}
                for k, label, pattern, aliases in CONCEPTS if re.search(pattern, normalized)]
    papers, observations = [], []
    literal_queries = _literal_queries(query)
    for doc in docs:
        hits = {}
        if concepts:
            buckets = {c["id"]: [] for c in concepts}
            for c in doc["chunks"]:
                text = _norm(c["text"])
                for key in buckets:
                    score = _rank(key, text, wanted)
                    if score:
                        buckets[key].append((-score, c["page"], c["block"], c.get("part", 1), c))
            for key, rows in buckets.items():
                picked = set()
                for _, page, block, part, chunk in sorted(rows, key=lambda row: row[:4]):
                    if len(picked) >= MAX_PER_CONCEPT:
                        break
                    pos = (page, block, part)
                    if (page, block) in picked:
                        continue
                    picked.add((page, block))
                    labels = doc.get("page_labels", [])
                    row = hits.setdefault(pos, {**chunk, "id": doc["id"], "name": doc["name"],
                        "page_label": labels[page - 1] if page <= len(labels) else str(page),
                        "concept_ids": []})
                    row["concept_ids"].append(key)
                    if key == "bleu":
                        for task, value in dict.fromkeys(_observed_scores(_norm(chunk["text"]), wanted)):
                            observations.append({"value": value, "task": task, "id": doc["id"], "page": page, "block": block})
        literal_count = 0
        for literal in literal_queries:
            if literal_count >= MAX_PER_CONCEPT:
                break
            for row in find_evidence([doc], literal):
                pos = (row["page"], row["block"], row.get("part", 1))
                if pos in hits:
                    continue
                if literal_count >= MAX_PER_CONCEPT:
                    break
                hits[pos] = {**row, "concept_ids": []}
                literal_count += 1
        papers.append({"id": doc["id"], "name": doc["name"], "arxiv": doc.get("arxiv"), "hits": list(hits.values())})
    notes = ["以下是按文献显示的候选原文，非自动答案或跨篇推理。未命中不代表文献没有记载。"]
    if concepts:
        notes.append("固定概念词表和原词检索并行，不是全文翻译；每篇每概念最多显示 4 个原文块，另可显示最多 4 个原词命中。")
    else:
        notes.append("未匹配固定概念词表，已按输入原词检索。复杂问句请改用原文术语。")
    if observations:
        notes.append("数值仅来自正文中语式明确、与语言任务同句的候选报告；仍需核对原文的模型、版本和条件，未标注的摘录也可能含数值。")
    for task in sorted({item["task"] for item in observations}):
        if len({Decimal(item["value"]) for item in observations if item["task"] == task}) > 1:
            notes.append(("英译法" if task == "en-fr" else "英译德") +
                         "的候选摘录出现不同 BLEU 数值；它们可能来自不同段落、版本或条件，不选定唯一分数。")
    return {"status": "evidence_found" if any(p["hits"] for p in papers) else "insufficient_evidence",
            "query": query.strip(), "concepts": concepts, "papers": papers, "notes": notes, "observations": observations}
