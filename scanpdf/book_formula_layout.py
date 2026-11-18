"""Verify an exercise formula against the original page's character baselines."""

from functools import lru_cache
from hashlib import sha256
import re

import pymupdf

from source_locator import MAX_GEOMETRY_CHARS, _start, _text_units


_SCRIPT = {
    'sup': str.maketrans('0123456789+-=()in', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ'),
    'sub': str.maketrans('0123456789+-=()aeox', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓ'),
}


def _script(value, direction):
    converted = value.translate(_SCRIPT[direction])
    if all(before != after for before, after in zip(value, converted)):
        return converted
    return ('^{' if direction == 'sup' else '_{') + value + '}'


def _render(chars, units):
    ink = [(char, line, glyph) for char, (line, glyph) in zip(chars, units) if not char.isspace()]
    if not ink or len({line for _, line, _ in ink}) != 1 or any(glyph is None for _, _, glyph in ink):
        return None
    equals = [glyph for char, _, glyph in ink if char == '=']
    if not equals:
        return None
    size, _, (_, baseline) = equals[0]
    if size <= 0 or any(abs(glyph[0] - size) > 0.12 * size or abs(glyph[2][1] - baseline) > 0.08 * size for glyph in equals):
        return None

    output, pending = [], []
    direction = None
    last_base = None
    for char, (_, glyph) in zip(chars, units):
        if not char.isspace():
            font, flags, (x, y) = glyph
            delta = (y - baseline) / size
            ratio = font / size
            reduced = 0.3 < ratio <= 0.88
            vertical = ('sup' if reduced and delta <= -0.13 else
                        'sub' if reduced and delta >= 0.085 else None)
            if vertical:
                if last_base is None or x < last_base or vertical == 'sub' and flags & pymupdf.TEXT_FONT_SUPERSCRIPT:
                    return None
                if direction and vertical != direction:
                    output.append(_script(''.join(pending), direction))
                    pending.clear()
                direction = vertical
                pending.append(char)
                continue
            if ratio <= 0.88 or abs(delta) > 0.075 or flags & pymupdf.TEXT_FONT_SUPERSCRIPT:
                return None
            last_base = x if char.isalnum() or char in '])}' else None
        else:
            last_base = None
        if direction:
            output.append(_script(''.join(pending), direction))
            pending.clear()
            direction = None
        output.append(char)
    if direction:
        output.append(_script(''.join(pending), direction))
    return ' '.join(''.join(output).split())


def _original_formula(page, block, hit, label):
    candidate = re.compile(r'\s+'.join(re.escape(term) for term in label.split()))
    text = hit['text'][hit['start']:hit['end']]
    positions = list(candidate.finditer(text))
    if len(positions) != 1:
        return None
    begin, end = (hit['start'] + coordinate for coordinate in positions[0].span())
    textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_BLOCKS)
    blocks = [entry for entry in textpage.extractBLOCKS() if entry[6] == 0]
    if sum(len(entry[4]) for entry in blocks) > MAX_GEOMETRY_CHARS:
        return None
    plain = next((entry[4] for entry in blocks if entry[5] + 1 == block), None)
    raw = next((entry for entry in textpage.extractRAWDICT()['blocks']
                if entry['type'] == 0 and entry['number'] + 1 == block), None)
    if plain is None or raw is None:
        return None
    original, units = _text_units(
        ((char['c'], (span['size'], span['flags'], char['origin']) if not char['c'].isspace() else None)
         for span in line['spans'] for char in span['chars']) if line['dir'] == (1.0, 0.0)
        else ((char['c'], None) for span in line['spans'] for char in span['chars'])
        for line in raw['lines']
    )
    offset = _start(original, hit) if original == plain.strip() else None
    if offset is None:
        return None
    start, stop = offset + begin, offset + end
    display = _render(original[start:stop], units[start:stop])
    return (display, dict(hit, start=begin, end=end)) if display else None


def formula_reader(load_pdf, expected_id):
    """Load the original lazily. Return a checked formula and its exact source span."""
    @lru_cache(maxsize=1)
    def original():
        data = load_pdf() if load_pdf else None
        return data if isinstance(data, bytes) and sha256(data).hexdigest() == expected_id else None

    def read(value, hit):
        if hit.get('source') == 'ocr':
            return None
        try:
            data = original()
            if data is not None:
                with pymupdf.open(stream=data, filetype='pdf') as pdf:
                    if 1 <= hit['page'] <= len(pdf):
                        return _original_formula(pdf[hit['page'] - 1], hit['block'], hit, value)
        except (OSError, RuntimeError, ValueError, KeyError):
            return None
        return None

    return read
