"""Verify that a direct result supports every explicitly asked part."""

import re

from scan_table_qa import (
    COMPARE, DIFFERENCE, QUERY_TERMS, _concepts, _directions, _find,
    _header_directions, _normal, _query_shape, _words, part_can_use_table,
)


_GENERIC_LABELS = QUERY_TERMS | {"model", "method", "parameter", "result", "setting", "metric", "none"}
_SCORE_ASK = re.compile(
    r"\b(?:how many|what (?:were|was|is))\s+(?:(?:the|its|model's)\s+)?bleu(?:\s+(?:points?|scores?))?\b"
    r"|\bbleu\s*(?:分数|分值|得分)?\s*(?:是|为)?\s*多少", re.I
)
_PRIOR_ASK = re.compile(
    r"^\s*what\s+(?:(?:was|is)\s+)?(?:the\s+)?(?:previous\s+|prior\s+|then\s+)?"
    r"(?:state[- ]of[- ]the[- ]art|best(?:\s+baseline)?|(?:prior|previous) (?:best|score))\b"
    r"|^\s*(?:(?:当时|以往|此前|之前)的?)\s*(?:state[- ]of[- ]the[- ]art|sota|最佳)(?:\s*模型|\s*分数)?\s*(?:是|有|为)?\s*多少",
    re.I,
)
_CHANGE_ASK = re.compile(
    r"^(?:by how much|how much (?:did|does|higher|lower|was the improvement))\b[^?;]{0,100}"
    r"\b(?:improv\w*|increas\w*|higher|lower|gain\w*|bleu)\b"
    r"|^(?:其|它|该模型)?\s*(?:提升|提高|增加|多|低|减少)(?:了)?\s*多少", re.I
)
_PRIOR_SUFFIX = re.compile(r"\s*(?:bleu\s+)?(?:model|baseline|score|points?)?\s*[?？。.]*\s*", re.I)


def _target_score(part):
    # English often puts the translation direction before the words BLEU points.
    # Removing only the explicitly recognized language direction retains the ask.
    from metric_qa import TASKS

    for pattern in TASKS.values():
        part = re.sub(pattern, "", part, flags=re.I)
    return _SCORE_ASK.search(" ".join(part.split()))


def _metric_covers(result, parts):
    from metric_qa import TASKS

    summary = result.get("metric_comparison", {})
    if summary.get("task") not in TASKS:
        return False
    asked = set()
    for part in parts:
        directions = {task for task, pattern in TASKS.items() if re.search(pattern, part, re.I)}
        other_metrics = _concepts(_words(part)) - {"bleu"}
        if not part_can_use_table(part) or directions - {summary["task"]} or other_metrics:
            return False
        if match := _PRIOR_ASK.search(part):
            role = "prior_score"
            # A related subtraction can occur in the same explicit clause.
            tail = re.split(r",?\s+and\s+|[，、]|以及", part[match.end():], maxsplit=1, flags=re.I)
            if not _PRIOR_SUFFIX.fullmatch(tail[0]) or (len(tail) > 1 and not (
                    _CHANGE_ASK.search(tail[1]) and summary.get("difference") is not None)):
                return False
        elif _CHANGE_ASK.search(part):
            role = "difference"
        elif _target_score(part):
            role = "target_score"
        else:
            return False
        if role in asked or summary.get(role) is None:
            return False
        asked.add(role)
    return True


def _part_cells(part, cells):
    query, table, page, row, column, _ = _query_shape(part)
    plain = _words(query)
    concepts = _concepts(plain)
    directions = _directions(_normal(query))
    matched, found_concepts, found_directions = set(), set(), set()
    for index, cell in enumerate(cells):
        if any(scope and cell.get(key) not in scope for key, scope in (
                ("table", table), ("page", page), ("row", row), ("column", column))):
            continue
        labels = [label for key in ("row_label", "column_label") for label in cell.get(key, "").split(" · ")]
        label_concepts = set().union(*(_concepts(_words(label)) for label in labels))
        label_directions = set().union(*(_header_directions(_normal(label)) for label in labels))
        named = any((literal := _words(label)) and literal not in _GENERIC_LABELS
                    and not literal.isdecimal() and _find(literal, plain) for label in labels)
        fields = (concepts & label_concepts) or (directions & label_directions)
        if not (row and column or fields or (not concepts and not directions and named)):
            continue
        matched.add(index)
        found_concepts.update(label_concepts)
        found_directions.update(label_directions)
    return matched if concepts <= found_concepts and directions <= found_directions else set()


def covers_parts(result, parts):
    """Keep a combined result only when its structured evidence covers each part."""
    if not result.get("answer") or result.get("gap"):
        return False
    method = result.get("answer_method")
    if method == "table_calculation":
        return _metric_covers(result, parts)
    if method not in {"scan_table", "native_table"}:
        return False
    cells = [cell for cell in result.get("table_cells", ()) if cell.get("value") is not None and cell.get("bbox_pt")]
    if len(cells) < len(parts):
        return False
    coverage = [_part_cells(part, cells) if part_can_use_table(part) and not (
        COMPARE.search(part) or DIFFERENCE.search(part)) else set() for part in parts]
    return all(indices - set().union(*(other for n, other in enumerate(coverage) if n != index))
               for index, indices in enumerate(coverage))
