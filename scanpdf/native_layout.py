"""Conservative alignment recovery for compact tables in native PDF pages."""

from bisect import bisect_right
from statistics import median

KEY_HEADERS = {"key", "field", "parameter", "hyperparameter", "configuration key", "setting", "option",
               "component", "property", "metric", "attribute", "variable"}
VALUE_HEADERS = {"value", "values", "specification", "description", "configuration", "setting", "result", "details"}


def _center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _box(words):
    return [min(word["bbox_pt"][0] for word in words), min(word["bbox_pt"][1] for word in words),
            max(word["bbox_pt"][2] for word in words), max(word["bbox_pt"][3] for word in words)]


def _text(words):
    return " ".join(word["text"] for word in sorted(words, key=lambda word: word["bbox_pt"][0]))


def _rows(words):
    if not words:
        return [], 0
    height = median(word["bbox_pt"][3] - word["bbox_pt"][1] for word in words)
    results = []
    for word in sorted(words, key=lambda word: _center(word["bbox_pt"])[1]):
        y = _center(word["bbox_pt"])[1]
        if not results or y - results[-1][0] > max(1.4, height * .44):
            results.append((y, [word]))
        else:
            results[-1][1].append(word)
    return results, height


def _runs(words, minimum_gap):
    runs = []
    right = 0
    for word in sorted(words, key=lambda word: word["bbox_pt"][0]):
        left, _, stop, _ = word["bbox_pt"]
        if not runs or left - right >= minimum_gap:
            runs.append([word])
            right = stop
        else:
            runs[-1].append(word)
            right = max(right, stop)
    return runs


def _words_in(words, bbox):
    left, top, right, bottom = bbox
    return [word for word in words
            if left <= (point := _center(word["bbox_pt"]))[0] < right and top <= point[1] < bottom]


def _cell(row, column, bbox, words, **spans):
    text = _text(words)
    return {"row": row, "column": column, "bbox_pt": [round(value, 4) for value in bbox],
            "text": text, "status": "native_text" if text else "empty_or_unread", **spans}


def _grid(rows, height, bbox, region_id, *, key_value=False):
    if not 3 <= len(rows) <= 140 or not height:
        return None
    left, top, right, bottom = bbox
    head_words = rows[0][1]
    headings = _runs(head_words, max(5, height * .58))
    if not 2 <= len(headings) <= 18 or any(len(run) > 8 for run in headings):
        return None
    if key_value and (len(headings) != 2 or _text(headings[0]).casefold() not in KEY_HEADERS
                      or _text(headings[1]).casefold() not in VALUE_HEADERS):
        return None
    names = [_box(run) for run in headings]
    centers = [_center(box)[0] for box in names[1:]]
    if len(centers) == 1:
        divider = (names[0][2] + names[1][0]) / 2
    else:
        divider = centers[0] - (centers[1] - centers[0]) / 2
    if not left < divider < centers[0] < right or names[0][2] > divider + 1:
        return None
    columns = [left, divider, *((a + b) / 2 for a, b in zip(centers, centers[1:])), right]
    if any(b - a < height for a, b in zip(columns, columns[1:])):
        return None
    row_edges = [max(top, _box(head_words)[1] - height * .3),
                 *((a[0] + b[0]) / 2 for a, b in zip(rows, rows[1:])),
                 min(bottom, _box(rows[-1][1])[3] + height * .3)]
    if len(row_edges) != len(rows) + 1 or row_edges[-1] <= row_edges[-2]:
        return None

    records = []
    full_rows = 0
    for _, words in rows[1:]:
        stub = [word for word in words if _center(word["bbox_pt"])[0] < divider]
        data = [word for word in words if _center(word["bbox_pt"])[0] >= divider]
        if not stub:
            records.append((stub, data, None, False))
            continue
        if (len(stub) > 14 or len(_text(stub)) > 110
                or (key_value and (len(data) > 16 or len(_text(data)) > 135))):
            return None
        if any(word["bbox_pt"][0] < divider - 1 and word["bbox_pt"][2] > divider + 1 for word in words):
            return None
        positions = {bisect_right(columns, _center(word["bbox_pt"])[0]) for word in data}
        group = _runs(data, max(6, height * .85))
        center = _center(_box(data))[0] if len(group) == 1 else None
        independent = center is not None and min(abs(center - lane) for lane in centers) <= height * .32
        full = positions == set(range(2, len(columns))) and (len(centers) == 1 or len(group) == len(centers))
        full_rows += int(full)
        records.append((stub, data, center, independent))
    if full_rows < 2 or (key_value and full_rows != len(records)):
        return None

    cells = [_cell(1, i, [a, row_edges[0], b, row_edges[1]], group)
             for i, (a, b, group) in enumerate(zip(columns, columns[1:], headings), 1)]
    value_center = (centers[0] + centers[-1]) / 2
    repeated_center = sum(bool(stub and center is not None and not independent
                               and abs(center - value_center) <= height * .34)
                          for stub, _, center, independent in records) >= 2
    for row, (stub, payload, center, independent) in enumerate(records, 2):
        upper, lower = row_edges[row - 1:row + 1]
        if not stub:
            if payload:
                cells.append(_cell(row, 1, [left, upper, right, lower], payload, column_span=len(headings)))
            continue
        cells.append(_cell(row, 1, [left, upper, divider, lower], stub))
        if repeated_center and center is not None and not independent and abs(center - value_center) <= height * .34:
            cells.append(_cell(row, 2, [divider, upper, right, lower], payload, column_span=len(centers),
                               span_evidence="repeated_center_across_columns"))
            continue
        for col, (start, stop) in enumerate(zip(columns[1:], columns[2:]), 2):
            members = [word for word in payload if start <= _center(word["bbox_pt"])[0] < stop]
            cells.append(_cell(row, col, [start, upper, stop, lower], members))
    return {"id": region_id, "type": "table", "status": "partial_structure", "method": "native_aligned_table",
            "bbox_pt": [left, row_edges[0], right, row_edges[-1]], "cells": cells}


def recover_region(words, region, captions):
    left, top, right, bottom = region["bbox_pt"]
    variants = [(top, bottom)]
    following = [line for line in captions if bottom <= line["bbox_pt"][1] <= bottom + 95
                 and min(right, line["bbox_pt"][2]) - max(left, line["bbox_pt"][0]) >= (right - left) * .45]
    if following:
        limit = min(line["bbox_pt"][1] for line in following)
        variants += [(top, limit - 2), (max(0, top - 15), limit - 2)]
    variants.append((max(0, top - 15), bottom))
    for a, b in variants:
        bbox = [left, a, right, b]
        rows, height = _rows(_words_in(words, bbox))
        if recovered := _grid(rows, height, bbox, region["id"]):
            return recovered
    return None


def recover_single_rules(words, rules, captions, covered, page_number):
    results = []
    for left, y, right in rules:
        occupied = [*covered, *(region["bbox_pt"] for region in results)]
        if any(box[0] <= left + 3 and box[1] <= y <= box[3] and box[2] >= right - 3 for box in occupied):
            continue
        nearby = [line for line in captions if 0 <= y - line["bbox_pt"][3] < 62
                  and min(right, line["bbox_pt"][2]) - max(left, line["bbox_pt"][0]) > (right - left) * .45]
        if not nearby:
            continue
        rows, height = _rows(_words_in(words, [left - 2, y - 22, right + 2, y + 120]))
        headers = [(center, group) for center, group in rows if center < y]
        if not headers:
            continue
        selected = [headers[-1]]
        for center, group in [entry for entry in rows if entry[0] > y]:
            if center - selected[-1][0] > height * 1.8 or len(group) < 2:
                break
            selected.append((center, group))
        if len(selected) < 3:
            continue
        bbox = [left - 2, y - 22, right + 2, selected[-1][0] + height]
        index = len(covered) + len(results) + 1
        if candidate := _grid(selected, height, bbox, f"p{page_number}-r{index}"):
            results.append(candidate)
    return results


def recover_unruled(words, page_number):
    rows, height = _rows(words)
    for index, (start, group) in enumerate(rows[:-3]):
        headings = _runs(group, max(5, height * .58))
        if len(headings) != 2 or _text(headings[0]).casefold() not in KEY_HEADERS or _text(headings[1]).casefold() not in VALUE_HEADERS:
            continue
        x0, x1 = (heading[0]["bbox_pt"][0] for heading in headings)
        aligned = [(start, group)]
        for center, other in rows[index + 1:]:
            lanes = _runs(other, max(14, height))
            if (len(lanes) != 2 or center - aligned[-1][0] > height * 2.8
                    or any(abs(lane[0]["bbox_pt"][0] - expected) > 2
                           for lane, expected in zip(lanes, (x0, x1)))):
                break
            aligned.append((center, other))
        if len(aligned) < 4:
            continue
        box = _box([word for _, group in aligned for word in group])
        bbox = [box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3]
        if candidate := _grid(aligned, height, bbox, f"p{page_number}-r1", key_value=True):
            return candidate
    return None
