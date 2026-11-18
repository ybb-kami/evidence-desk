"""Identify ruled tables in page rasters and map OCR words into cells."""

from bisect import bisect_right
from dataclasses import dataclass
import re

import numpy as np
from PIL import Image
from scipy import ndimage


@dataclass(frozen=True)
class _Rule:
    left: int
    right: int
    y: int


def _horizontal_rules(ink, dpi):
    minimum_length = max(12, round(dpi * 0.34), round(ink.shape[1] * 0.045))
    length = minimum_length | 1
    eroded = ndimage.minimum_filter1d(ink, length, axis=1, mode="constant", cval=0)
    opened = ndimage.maximum_filter1d(eroded, length, axis=1, mode="constant", cval=0)
    labels, _ = ndimage.label(opened)
    rules = []
    for bounds in ndimage.find_objects(labels):
        if bounds is None:
            continue
        ys, xs = bounds
        if ys.stop - ys.start <= max(3, round(dpi * 0.055)):
            rules.append(_Rule(xs.start, xs.stop, (ys.start + ys.stop) // 2))
    return sorted(rules, key=lambda rule: (rule.y, rule.left))


def _coextensive(first, second, dpi):
    width = first.right - first.left
    tolerance = max(dpi * 0.075, width * 0.055)
    return (
        abs(first.left - second.left) <= tolerance
        and abs(first.right - second.right) <= tolerance
    )


def _word_center(word):
    x0, y0, x1, y1 = word["bbox"]
    return (x0 + x1) / 2, (y0 + y1) / 2


def _table_words(words, bounds):
    left, top, right, bottom = bounds
    return [
        word for word in words
        if left <= _word_center(word)[0] < right
        and top <= _word_center(word)[1] < bottom
    ]


def _connected_borders(ink, first, second, dpi):
    tolerance = max(2, round(dpi * 0.025))
    for x in (first.left, first.right - 1):
        strip = ink[first.y : second.y + 1, max(0, x - tolerance) : x + tolerance + 1]
        if not strip.size or strip.any(axis=1).mean() < 0.88:
            return False
    return True


def _grid_columns(ink, rules, left, right, dpi, *, allow_open=False):
    """Return continuous vertical boundaries and whether a grid is visible."""
    top, bottom = rules[0].y, rules[-1].y
    if bottom <= top:
        return [], False
    tolerance = max(2, round(dpi * 0.02))
    start = max(0, left - tolerance)
    stop = min(ink.shape[1], right + tolerance)
    band = ink[top : bottom + 1, start:stop]
    # Text rarely occupies a fixed x coordinate over an entire row interval.
    for row in range(len(rules) - 1):
        if rules[row + 1].y - rules[row].y < max(5, round(dpi * 0.045)):
            return [], False
    row_coverage = np.array([
        band[a.y - top : b.y - top + 1].mean(axis=0)
        for a, b in zip(rules, rules[1:])
    ])
    visible = np.any(row_coverage >= 0.88, axis=0)
    margin = 2 * tolerance
    inner_visible = visible[left - start + margin : right - start - margin]
    if not inner_visible.any():
        return [], False
    continuous = np.all(row_coverage >= 0.88, axis=0)
    edges = np.flatnonzero(np.diff(np.r_[False, continuous, False]))
    columns = [start + (a + b) // 2 for a, b in zip(edges[::2], edges[1::2])]
    # Local dividers can indicate merged cells or a damaged grid. Their positions
    # cannot be extended across the whole table without inventing boundaries.
    if np.any(visible & ~ndimage.maximum_filter1d(continuous, 2 * tolerance + 1)):
        return [], True
    left_edge = abs(columns[0] - left) <= tolerance
    right_edge = abs(columns[-1] - right) <= tolerance + 1
    if len(columns) >= 3 and left_edge and right_edge:
        return columns, True
    if allow_open and len(columns) >= 2 and not left_edge and not right_edge:
        return [left, *columns, right], True
    return [], True



def _text_rows(words, dpi):
    if not words:
        return [], 0
    height = float(np.median([word["bbox"][3] - word["bbox"][1] for word in words]))
    tolerance = max(dpi * 0.018, height * 0.45)
    rows = []
    centers = []
    for word in sorted(words, key=lambda word: _word_center(word)[1]):
        _, y = _word_center(word)
        if rows and y - centers[-1] <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
            centers.append(y)
    return rows, height


def _has_separated_rows(rows, height, dpi):
    minimum_gap = max(dpi * 0.15, height * 1.75)
    separated = 0
    for row in rows:
        ordered = sorted(row, key=lambda word: word["bbox"][0])
        if any(b["bbox"][0] - a["bbox"][2] >= minimum_gap for a, b in zip(ordered, ordered[1:])):
            separated += 1
    return separated >= 2


def _text_columns(rows, left, right, height, dpi):
    """Find gutters supported by repeated OCR cells on both sides."""
    if len(rows) < 2:
        return []
    gap_minimum = max(5, round(dpi * 0.065), round(height * 0.75))
    occupied = np.zeros(right - left + 1, bool)
    support = np.zeros(right - left + 1, dtype=int)
    for row in rows:
        intervals = sorted((word["bbox"][0], word["bbox"][2]) for word in row)
        end = left
        for x0, x1 in intervals:
            start = max(left, x0)
            stop = min(right, x1)
            occupied[start - left : stop - left + 1] = True
            if end > left and start - end >= gap_minimum:
                margin = max(1, gap_minimum // 4)
                support[end + margin - left : start - margin - left + 1] += 1
            end = max(end, stop)
    supported = support >= max(2, int(np.ceil(len(rows) * 0.55)))
    supported_edges = np.flatnonzero(np.diff(np.r_[False, supported, False]))
    if any(occupied[a:b].all() for a, b in zip(supported_edges[::2], supported_edges[1::2])):
        return []
    eligible = supported & ~occupied
    edges = np.flatnonzero(np.diff(np.r_[False, eligible, False]))
    minimum_clear_width = max(3, round(height * 0.5))
    gutters = [
        left + (a + b) // 2 for a, b in zip(edges[::2], edges[1::2])
        if b - a >= minimum_clear_width
    ]
    return [left, *gutters, right]


def _make_cell(row, column, bounds, members, **spans):
    lines = {}
    for word in sorted(members, key=lambda word: (_word_center(word)[1], word["bbox"][0])):
        key = word.get("line_id") or round(_word_center(word)[1])
        lines.setdefault(key, []).append(word)
    text = "\n".join(
        " ".join(word["text"] for word in sorted(line, key=lambda word: word["bbox"][0]))
        for line in lines.values()
    )
    return {
        "row": row, "column": column, "bbox": bounds, "text": text,
        "status": "ocr_text" if text else "empty_or_unread",
        **spans,
    }


def _cells(words, columns, rows):
    by_cell = {}
    for word in words:
        x, y = _word_center(word)
        i, j = bisect_right(rows, y), bisect_right(columns, x)
        if 0 < i < len(rows) and 0 < j < len(columns):
            by_cell.setdefault((i, j), []).append(word)
    return [
        _make_cell(i, j, [left, top, right, bottom], by_cell.get((i, j), []))
        for i, (top, bottom) in enumerate(zip(rows, rows[1:]), 1)
        for j, (left, right) in enumerate(zip(columns, columns[1:]), 1)
    ]


def _sparse_row_boundaries(text_rows, columns, rules, partial_rules, height, dpi, *, wrapped_header=True):
    # Side-by-side header rules often delimit grouped columns. A single
    # rectangular header cannot reliably represent these groupings.
    unique_partial_y = {rule.y for rule in partial_rules}
    if len(unique_partial_y) < len(partial_rules):
        return []
    if wrapped_header and rules[1:-1] and len(text_rows) >= 3:
        header_limit = rules[1].y
        if max(_word_center(word)[1] for word in text_rows[1]) < header_limit < min(
            _word_center(word)[1] for word in text_rows[2]
        ):
            primary, continuation = (
                {bisect_right(columns, _word_center(word)[0]) for word in row}
                for row in text_rows[:2]
            )
            if len(primary) >= 2 and continuation < primary and len(continuation) == 1:
                text_rows = [text_rows[0] + text_rows[1], *text_rows[2:]]

    boundaries = [rules[0].y]
    for first, second in zip(text_rows, text_rows[1:]):
        upper_center = np.median([_word_center(word)[1] for word in first])
        lower_center = np.median([_word_center(word)[1] for word in second])
        upper_edge = max(_word_center(word)[1] for word in first)
        lower_edge = min(_word_center(word)[1] for word in second)
        separators = [
            rule.y for rule in [*rules[1:-1], *partial_rules]
            if upper_edge < rule.y < lower_edge
        ]
        if len(separators) == 1:
            boundaries.append(separators[0])
        elif lower_center - upper_center >= max(dpi * 0.045, height * 0.72):
            boundaries.append(int(np.ceil((upper_edge + lower_edge) / 2)))
        else:
            return []
    boundaries.append(rules[-1].y)
    return boundaries


def _header_runs(words, gap):
    runs = []
    for word in sorted(words, key=lambda word: word["bbox"][0]):
        x0, _, x1, _ = word["bbox"]
        if runs and x0 - runs[-1][1] < gap:
            runs[-1][1] = max(runs[-1][1], x1)
        else:
            runs.append([x0, x1])
    return runs


def _rule_bands(rules):
    bands = []
    for rule in sorted(rules, key=lambda rule: rule.y):
        if bands and rule.y - bands[-1][0].y <= 2:
            bands[-1].append(rule)
        else:
            bands.append([rule])
    return [sorted(band, key=lambda rule: rule.left) for band in bands]


def _grouped_cells(words, rules, partial, bounds, dpi):
    """Recognize two header tiers where partial rules delimit column groups."""
    left, top, right, bottom = bounds
    width = right - left
    bands = _rule_bands([*rules[1:-1], *partial])
    group_rules = next((band for band in bands if len(band) >= 2
                        and all(a.right < b.left for a, b in zip(band, band[1:]))
                        and sum(r.right - r.left for r in band) < width * .85), [])
    if not group_rules or group_rules[0].left - left < dpi * .20:
        return []
    split = group_rules[0].y
    header_end = next((band[0].y for band in bands if band[0].y > split
                       and band[0].left <= left + dpi * .06
                       and band[-1].right >= right - dpi * .06
                       and sum(r.right - r.left for r in band) >= width * .87), None)
    if header_end is None:
        return []

    stub_end = group_rules[0].left - max(1, round(dpi * .01))
    seams = [stub_end, *((a.right + b.left) // 2 for a, b in zip(group_rules, group_rules[1:])), right]
    leaf_rows, height = _text_rows(_table_words(words, [stub_end, split, right, header_end]), dpi)
    if len(leaf_rows) != 1:
        return []
    stub = _table_words(words, [left, top, stub_end, header_end])
    # A rule touching the stub label can produce a second, overlapping OCR word.
    stub = [
        word for word in stub if not any(
            other.get("confidence", 0) - word.get("confidence", 0) > 25
            and other["bbox"][0] <= word["bbox"][0] < word["bbox"][2] <= other["bbox"][2]
            and abs(_word_center(word)[1] - _word_center(other)[1]) < height * .25
            for other in stub
        )
    ]
    if len(_text_rows(stub, dpi)[0]) != 1:
        return []

    columns = [left, stub_end]
    groups = []
    for rule, (x0, x1) in zip(group_rules, zip(seams, seams[1:])):
        box = [x0, split, x1, header_end]
        leaf_words = _table_words(leaf_rows[0], box)
        leaves = _header_runs(leaf_words, max(dpi * .075, height * .95))
        title_words = _table_words(words, [x0, top, x1, split])
        if (len(_text_rows(title_words, dpi)[0]) != 1 or len(leaves) < 2
                or leaves[0][0] < rule.left or leaves[-1][1] > rule.right):
            return []
        groups.append((len(columns), leaves, title_words))
        columns.extend([*((a[1] + b[0]) // 2 for a, b in zip(leaves, leaves[1:])), x1])

    text_rows, height = _text_rows(_table_words(words, [left, header_end, right, bottom]), dpi)
    if not _has_separated_rows(text_rows, height, dpi):
        return []
    body_rules = [_Rule(left, right, header_end), *[rule for rule in rules if rule.y > header_end]]
    rows = _sparse_row_boundaries(text_rows, columns, body_rules, [], height, dpi, wrapped_header=False)
    if not rows:
        return []

    # Require independent body alignment to each leaf, in addition to its header.
    for start, leaves, _ in groups:
        for j, (x0, x1) in enumerate(leaves, start):
            center = (x0 + x1) / 2
            radius = (columns[j] - columns[j - 1]) * .40
            if sum(any(abs(_word_center(word)[0] - center) <= radius for word in row) for row in text_rows) < 2:
                return []

    def cell(row, column, box, members, **spans):
        return _make_cell(row, column, box, _table_words(members, box), **spans)

    cells = [cell(1, 1, [left, top, stub_end, header_end], stub, row_span=2)]
    for start, leaves, title_words in groups:
        cells.append(cell(1, start, [columns[start - 1], top, columns[start + len(leaves) - 1], split],
                          title_words, column_span=len(leaves)))
    for j, (x0, x1) in enumerate(zip(columns[1:], columns[2:]), 2):
        cells.append(cell(2, j, [x0, split, x1, header_end], leaf_rows[0]))

    for i, (y0, y1) in enumerate(zip(rows, rows[1:]), 3):
        row_words = text_rows[i - 3]
        cells.append(cell(i, 1, [left, y0, stub_end, y1], row_words))
        for start, leaves, _ in groups:
            x0, x1 = columns[start - 1], columns[start + len(leaves) - 1]
            members = _table_words(row_words, [x0, y0, x1, y1])
            runs = _header_runs(members, height * .90)
            gutter = columns[start]
            if len(leaves) == 2 and len(runs) == 1:
                first, last = runs[0]
                if (first < gutter - height * .25 and last > gutter + height * .25
                        and abs((first + last) / 2 - gutter) < (x1 - x0) * .14):
                    cells.append(cell(i, start, [x0, y0, x1, y1], members, column_span=2))
                    continue
            for j in range(start, start + len(leaves)):
                cells.append(cell(i, j, [columns[j - 1], y0, columns[j], y1], members))
    return cells


def _grouped_rail_cells(words, rules, rails, dpi):
    """Recover sparse subrows anchored by repeated result columns and group rules."""
    if len(rails) != 4 or len(rules) < 5:
        return []
    left, stub_end, result_start, right = rails
    top, header_end, first_end = [rule.y for rule in rules[:3]]

    # A dense first data row supplies leaf centers inside the ruled sections.
    reference = _table_words(words, [stub_end, header_end, right, first_end])
    rows, height = _text_rows(reference, dpi)
    if len(rows) != 1:
        return []
    minimum_pitch = max(height * 2, dpi * .14)
    left_centers = sorted(_word_center(word)[0] for word in reference if _word_center(word)[0] < result_start)
    right_centers = sorted(_word_center(word)[0] for word in reference if _word_center(word)[0] >= result_start)
    if len(left_centers) < 3 or len(right_centers) < 2:
        return []
    for centers in (left_centers, right_centers):
        if any(b - a < minimum_pitch for a, b in zip(centers, centers[1:])):
            return []

    def gutters(centers):
        return [round((a + b) / 2) for a, b in zip(centers, centers[1:])]

    columns = [left, stub_end, *gutters(left_centers), result_start, *gutters(right_centers), right]
    right_first = len(left_centers) + 2
    body_groups = []
    for upper, lower in zip(rules[1:], rules[2:]):
        bounds = [left, upper.y, right, lower.y]
        local = _table_words(words, bounds)
        outcomes, _ = _text_rows(_table_words(local, [result_start, upper.y, right, lower.y]), dpi)
        if not outcomes:
            return []
        # Genuine subrows each carry an independently aligned result in every
        # outcome column. Do not splice a centered group label into this order.
        for row in outcomes:
            occupied = {bisect_right(columns, _word_center(word)[0]) for word in row}
            if occupied != set(range(right_first, len(columns))):
                return []
        row_bands = _sparse_row_boundaries(
            outcomes, columns, [upper, lower], [], height, dpi, wrapped_header=False,
        )
        if not row_bands:
            return []
        stub = _table_words(local, [left, upper.y, stub_end, lower.y])
        if len(_text_rows(stub, dpi)[0]) > 1:
            return []
        body_groups.append((bounds, local, row_bands, stub))
    if sum(len(bands) > 2 and bool(stub) for _, _, bands, stub in body_groups) < 2:
        return []

    body_rows = []
    stubs = []
    index = 2
    for (_, upper, _, lower), local, bands, words_in_stub in body_groups:
        group_size = len(bands) - 1
        spans = {"row_span": group_size} if group_size > 1 else {}
        label = [word for word in words_in_stub if any(char.isalnum() for char in word["text"])]
        stubs.append(_make_cell(index, 1, [left, upper, stub_end, lower], label, **spans))
        for y0, y1 in zip(bands, bands[1:]):
            body_rows.extend(
                (index, col, [x0, y0, x1, y1], _table_words(local, [x0, y0, x1, y1]))
                for col, (x0, x1) in enumerate(zip(columns[1:], columns[2:]), 2)
            )
            index += 1

    header = [left, top, right, header_end]
    cells = _cells(_table_words(words, header), columns, [top, header_end]) + stubs
    # Re-read missing punctuation and fused spans from evidence crops through
    # the existing cell OCR retry. No text is carried from the reference row.
    numeric = re.compile(r"[-+]?\d+(?:\.\d+)?[kKmM%]?")
    fraction = re.compile(r"[-+]?\d+\.\d+")
    fractional_columns = {
        col for col in range(2, len(columns))
        if sum(
            any(fraction.fullmatch(word["text"]) for word in members)
            for _, column, _, members in body_rows if column == col
        ) >= 2
    }
    for row, col, box, members in body_rows:
        clean = [
            word for word in members if numeric.fullmatch(word["text"])
            and word.get("confidence", 100) >= 35
            and word["bbox"][0] >= box[0] - dpi * .02 and word["bbox"][2] <= box[2] + dpi * .02
            and (col not in fractional_columns or fraction.fullmatch(word["text"]))
        ]
        cells.append(_make_cell(row, col, box, clean if len(clean) == len(members) == 1 else []))
    return sorted(cells, key=lambda cell: (cell["row"], cell["column"]))


def _region(ink, rules, partial_rules, words, dpi):
    top, bottom = rules[0].y, rules[-1].y
    left = min(rule.left for rule in rules)
    right = max(rule.right for rule in rules)
    if bottom - top < max(12, round(dpi * 0.12)):
        return None
    bounds = [left, top, right, bottom + 1]
    local_words = _table_words(words, bounds)
    columns, vertical_evidence = _grid_columns(ink, rules, left, right, dpi, allow_open=True)
    closed = _connected_borders(ink, rules[0], rules[-1], dpi)
    if columns and closed and not partial_rules:
        rows = [rule.y for rule in rules]
        return {
            "type": "table", "bbox": bounds, "status": "structure_recognized",
            "method": "raster_grid", "cells": _cells(local_words, columns, rows),
        }

    if columns and not closed:
        # In open-sided grids the vertical rules determine columns. OCR can
        # separate rows when a horizontal rule is shared by several rows.
        tolerance = max(2, round(dpi * 0.02))
        local_words = [
            word for word in local_words
            if word["text"] not in {"|", "│"}
            or all(abs(_word_center(word)[0] - x) > tolerance for x in columns[1:-1])
        ]
        grouped = _grouped_rail_cells(local_words, rules, columns, dpi)
        if grouped:
            return {
                "type": "table", "bbox": bounds, "status": "partial_structure",
                "method": "vertical_rules_grouped_ocr_alignment", "cells": grouped,
            }
        text_rows, height = _text_rows(local_words, dpi)
        if _has_separated_rows(text_rows, height, dpi):
            row_bands = _sparse_row_boundaries(text_rows, columns, rules, [], height, dpi)
            if row_bands:
                return {
                    "type": "table", "bbox": bounds, "status": "structure_recognized",
                    "method": "vertical_rules_ocr_alignment",
                    "cells": _cells(local_words, columns, row_bands),
                }

    if partial_rules and not vertical_evidence:
        grouped = _grouped_cells(local_words, rules, partial_rules, bounds, dpi)
        if grouped:
            return {
                "type": "table", "bbox": bounds, "status": "structure_recognized",
                "method": "grouped_horizontal_rules_ocr_alignment", "cells": grouped,
            }

    text_rows, height = _text_rows(local_words, dpi)
    columns = _text_columns(text_rows, left, right, height, dpi)
    separated = _has_separated_rows(text_rows, height, dpi)
    if len(columns) < 3:
        broad = right - left >= ink.shape[1] * 0.20
        if not ((vertical_evidence and closed) or (broad and separated)):
            boxed = any(_connected_borders(ink, a, b, dpi) for a, b in zip(rules, rules[1:]))
            if not (broad and not boxed and not vertical_evidence and len(rules) >= 3):
                return None
    elif not vertical_evidence:
        row_bands = _sparse_row_boundaries(text_rows, columns, rules, partial_rules, height, dpi)
        if row_bands:
            return {
                "type": "table", "bbox": bounds, "status": "structure_recognized",
                "method": "horizontal_rules_ocr_alignment",
                "cells": _cells(local_words, columns, row_bands),
            }

    return {
        "type": "table", "bbox": bounds, "status": "detected_only",
        "method": "partial_raster_grid" if vertical_evidence else "horizontal_rules",
        "cells": [],
    }


def detect_tables(gray: Image.Image, words: list[dict], dpi: int) -> list[dict]:
    """Detect tables in pixel coordinates using rules and layout from page OCR."""
    pixels = np.asarray(gray.convert("L"))
    if not pixels.size:
        return []
    paper = float(np.median(pixels))
    ink = pixels < min(180, paper - 32)
    rules = _horizontal_rules(ink, dpi)
    results = []
    consumed = set()
    for i, first in enumerate(rules):
        if i in consumed:
            continue
        matches = [
            (j, rule) for j, rule in enumerate(rules[i + 1 :], i + 1)
            if j not in consumed and _coextensive(first, rule, dpi)
        ]
        if not matches:
            continue
        # A page may contain several tables at the same horizontal position.
        # A large intervening gap with no column-aligned OCR ends the current set.
        group = [(i, first)]
        first_border = None
        for index, (j, rule) in enumerate(matches):
            previous = group[-1][1]
            connected = _connected_borders(ink, previous, rule, dpi)
            if (
                first_border and not connected and index + 1 < len(matches)
                and _connected_borders(ink, rule, matches[index + 1][1], dpi)
                and not _table_words(words, [first.left, previous.y, first.right, rule.y])
            ):
                break
            if rule.y - previous.y > dpi * 0.55:
                between = _table_words(words, [first.left, previous.y, first.right, rule.y])
                rows, height = _text_rows(between, dpi)
                vertical = _grid_columns(ink, [previous, rule], first.left, first.right, dpi)[1]
                if not vertical and not _has_separated_rows(rows, height, dpi):
                    break
            if first_border is None:
                first_border = connected
            group.append((j, rule))
        if len(group) < 2:
            continue
        indices = {j for j, _ in group}
        last = group[-1][1]
        partial = [
            (j, rule) for j, rule in enumerate(rules[i + 1 : group[-1][0]], i + 1)
            if j not in indices and j not in consumed
            and first.y < rule.y < last.y
            and rule.left >= first.left - dpi * 0.03
            and rule.right <= first.right + dpi * 0.03
        ]
        result = _region(ink, [rule for _, rule in group], [rule for _, rule in partial], words, dpi)
        if result:
            results.append(result)
            consumed.update(indices)
            consumed.update(j for j, _ in partial)
    return results
