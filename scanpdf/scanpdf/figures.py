"""Locate captioned graphic regions on image-only PDF pages using OCR and ink geometry."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

import numpy as np
from PIL import Image
import pymupdf
from scipy.ndimage import maximum_filter1d, minimum_filter1d

from .ocr import recognize
from .tables import detect_tables

CAPTION = re.compile(r"^(?:Figure|Fig\.|图|圖)\s*([1-9]\d{0,3}(?:\.\d{1,3})?[a-z]?)\s*"
                     r"(?:[:：]\s*|[.\-–—]\s+)(\S.*)$", re.I)


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _captions(lines):
    for index, line in enumerate(lines):
        match = CAPTION.fullmatch(line['text'].strip())
        if not match:
            continue
        pieces = [line]
        height = line['bbox'][3] - line['bbox'][1]
        for following in lines[index + 1:index + 16]:
            last = pieces[-1]
            previous = last['bbox']
            box = following['bbox']
            gap = box[1] - previous[3]
            same_paragraph = following['line_id'].rsplit('.', 1)[0] == last['line_id'].rsplit('.', 1)[0]
            # Tesseract can split the short last line of a caption into a
            # separate block. Join it only after a close, aligned wrap.
            aligned_wrap = (not last['text'].rstrip().endswith(('.', '!', '?', '。', '！', '？'))
                            and 0 <= gap <= .55 * height and abs(box[0] - previous[0]) <= .35 * height
                            and previous[2] >= line['bbox'][2] - height)
            if (not (same_paragraph or aligned_wrap) or CAPTION.match(following['text'])
                    or not 0 <= gap <= 1.15 * height
                    or min(previous[2], box[2]) - max(previous[0], box[0]) < height):
                break
            pieces.append(following)
        yield {'label': match[1], 'caption': '\n'.join(piece['text'] for piece in pieces),
               'caption_bbox': _union([piece['bbox'] for piece in pieces]),
               'caption_lines': [piece['line_id'] for piece in pieces]}, height


def _layout_scope(caption, image_shape, line_height, lines):
    height, width = image_shape
    x0, y0, x1, _ = caption
    left, right = round(0.07 * width), round(0.93 * width)
    middle = width / 2
    if x1 < middle - line_height:
        right = round(middle + line_height / 2)
    if x0 > middle + line_height:
        left = round(middle - line_height / 2)
    top = max(round(.04 * height), y0 - round(.64 * height))
    # A preceding caption or a wide, sentence-like line delimits this panel.
    for line in lines:
        bx0, _, bx1, by1 = line['bbox']
        shared = min(right, bx1) - max(left, bx0)
        previous_caption = CAPTION.match(line['text']) and shared >= line_height
        paragraph_text = shared >= .67 * (right - left) and _sentence_line(line, line_height)
        if top < by1 < y0 and (previous_caption or paragraph_text):
            top = by1 + 2
    return left, top, right, y0 - 2


def _sentence_line(line, line_height):
    text = line['text']
    words = line.get('words', ())
    return (len(re.findall(r'\b[^\W\d_]{3,}\b', text)) >= 8
            and len(words) >= 9 and all(b['bbox'][0] - a['bbox'][2] < 3 * line_height
                                       for a, b in zip(words, words[1:])))


def _opening(ink, width, axis):
    kept = minimum_filter1d(ink, width, axis=axis, mode='constant', cval=0)
    return maximum_filter1d(kept, width, axis=axis, mode='constant', cval=0)


def _find_panel(nontext, original, caption, line_height, lines, dpi):
    left, top, right, stop = _layout_scope(caption, nontext.shape, line_height, lines)
    if stop - top < .27 * dpi or right - left < .38 * dpi:
        return None
    ink = nontext[top:stop, left:right]
    strokes = (_opening(ink, max(4, round(dpi * .22)), 1) |
               _opening(ink, max(4, round(dpi * .19)), 0))
    # Sparse page noise, tiny glyph remnants and isolated rule fragments do not
    # justify a full figure. Keep a group of substantial strokes near the caption.
    rows = np.flatnonzero(strokes.sum(axis=1) >= max(2, round(dpi / 80)))
    if not len(rows):
        return None
    splits = np.flatnonzero(np.diff(rows) > max(round(dpi * .38), line_height)) + 1
    groups = np.split(rows, splits)
    for group in reversed(groups):
        y0, y1 = int(group[0]), int(group[-1]) + 1
        if stop - (top + y1) > max(4 * line_height, .76 * dpi):
            break
        if y1 - y0 < .24 * dpi:
            continue
        occupied = np.flatnonzero(strokes[y0:y1].any(axis=0))
        x0, x1 = int(occupied[0]), int(occupied[-1]) + 1
        if x1 - x0 < .46 * dpi or strokes[y0:y1, x0:x1].sum() < .007 * dpi**2:
            continue
        # Recover adjacent labels and arrows erased by the OCR mask, retaining
        # only original pixels in the limited window surrounding these strokes.
        pad_x, pad_y = round(.40 * dpi), round(.43 * dpi)
        bx0, by0 = max(left, left + x0 - pad_x), max(top, top + y0 - pad_y)
        bx1, by1 = min(right, left + x1 + pad_x), min(stop, top + y1 + pad_y)
        local = original[by0:by1, bx0:bx1]
        ys = np.flatnonzero(local.sum(axis=1) >= 4)
        xs = np.flatnonzero(local.sum(axis=0) >= 4)
        margin = max(2, round(.02 * dpi))
        if len(ys) and len(xs):
            return [max(left, bx0 + int(xs[0]) - margin), max(top, by0 + int(ys[0]) - margin),
                    min(right, bx0 + int(xs[-1]) + 1 + margin), min(stop, by0 + int(ys[-1]) + 1 + margin)]
    return None


def _contains_prose(box, lines):
    paragraphs = Counter()
    for line in lines:
        bounds = line['bbox']
        width = bounds[2] - bounds[0]
        area = width * (bounds[3] - bounds[1])
        if (width >= .68 * (box[2] - box[0]) and _intersection(box, bounds) >= .8 * area
                and _sentence_line(line, bounds[3] - bounds[1])):
            paragraphs[line['line_id'].rsplit('.', 1)[0]] += 1
    return any(count >= 2 for count in paragraphs.values())


def detect_figures(image, lines, dpi, table_regions=()):
    """Return image-space panels and unresolved captions for one rendered page."""
    captions = sorted(_captions(lines), key=lambda item: (item[0]['caption_bbox'][1], item[0]['caption_bbox'][0]))
    if not captions:
        return [], []
    original = np.asarray(image.convert('L')) < 205
    nontext = original.copy()
    for line in lines:
        for word in line.get('words', ()):
            x0, y0, x1, y1 = word['bbox']
            nontext[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1] = False
    figures, unresolved = [], []
    for item, line_height in captions:
        caption = item['caption_bbox']
        box = _find_panel(nontext, original, caption, line_height, lines, dpi)
        if box is None:
            unresolved.append(dict(item, reason='insufficient_graphic_geometry'))
        elif box[2] - box[0] < .44 * (caption[2] - caption[0]):
            # A few strokes in a wider compound graphic can otherwise produce
            # an attractive but misleading crop of just one panel.
            unresolved.append(dict(item, reason='insufficient_panel_span'))
        elif any(_intersection(box, table['bbox']) for table in table_regions):
            unresolved.append(dict(item, reason='overlaps_table_region'))
        elif _contains_prose(box, lines) or any(_intersection(box, row['bbox']) for row in figures):
            unresolved.append(dict(item, reason='prose_or_duplicate_region'))
        else:
            figures.append(dict(item, bbox=box, method='ocr_caption_and_graphic_strokes'))
    return figures, unresolved


def scan_figures(source, *, pages=None, dpi=240, lang='eng', crops=None):
    """Return independent figure anchors for image-only pages, leaving scan regions unchanged."""
    from .parser import _lines, select_pages

    if not 72 <= dpi <= 600:
        raise ValueError('DPI must be between 72 and 600')
    if not re.fullmatch(r'[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*', lang):
        raise ValueError('Expected a Tesseract language name or combination')
    path = Path(source)
    with path.open('rb') as stream:
        sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    results = []
    crop_dir = Path(crops) if crops is not None else None
    if crop_dir is not None:
        crop_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(path) as pdf:
        if not pdf.is_pdf or pdf.needs_pass or not len(pdf):
            raise ValueError('A nonempty, unlocked PDF is required')
        total_pages = len(pdf)
        for number in select_pages(pages, total_pages):
            page = pdf[number - 1]
            page_record = {'physical_page': number, 'figures': [], 'unresolved_captions': []}
            results.append(page_record)
            if page.get_text().strip():
                page_record['status'] = 'skipped_native_text'
                continue
            rect = page.rect
            if math.ceil(rect.width * dpi / 72) * math.ceil(rect.height * dpi / 72) > 25_000_000:
                raise ValueError(f'Physical page {number} exceeds 25 megapixels; use a lower DPI')
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            color = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            gray = color.convert('L')
            words = recognize(gray, dpi, lang, 3)
            if not words:
                words = recognize(gray, dpi, lang, 11)
            lines = _lines(words)
            page_record.update(status='processed', width_px=pix.width, height_px=pix.height,
                               width_pt=rect.width, height_pt=rect.height)
            tables = detect_tables(gray, words, dpi) if next(_captions(lines), None) else []
            figures, unresolved = detect_figures(gray, lines, dpi, tables)
            for item in figures + unresolved:
                for name in ('caption_bbox', 'bbox'):
                    if name in item:
                        item[name + '_pt'] = [round(min(rect.height if i % 2 else rect.width, v * 72 / dpi), 4)
                                             for i, v in enumerate(item[name])]
                item.update(physical_page=number, source_sha256=sha256)
            for index, item in enumerate(figures, 1):
                item['id'] = f'p{number}-f{index}'
                if crop_dir is not None:
                    filename = f'{sha256[:16]}-{item["id"]}.png'
                    with (crop_dir / filename).open('xb') as stream:
                        color.crop(item['bbox']).save(stream, format='PNG')
                    item['crop'] = filename
            page_record.update(figures=figures, unresolved_captions=unresolved)
    return {'schema_version': 1, 'source': {'filename': path.name, 'sha256': sha256, 'page_count': total_pages},
            'render': {'dpi': dpi, 'lang': lang, 'frame': 'rotated_visible_page_top_left'},
            'scope': 'figure region and OCR caption', 'not_extracted': ['graphic_semantics', 'plot_data'],
            'pages': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--pages', help='1-based physical pages, e.g. 3,4,13')
    parser.add_argument('--dpi', type=int, default=240)
    parser.add_argument('--lang', default='eng')
    parser.add_argument('--output', type=Path, required=True, help='New UTF-8 JSON file')
    parser.add_argument('--crops', type=Path, help='Save the actual cropped page image for each detected figure')
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'Output already exists: {args.output}')
    try:
        data = scan_figures(args.input, pages=args.pages, dpi=args.dpi, lang=args.lang, crops=args.crops)
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write('\n')
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f'scanpdf.figures: {exc}\n')


if __name__ == '__main__':
    main()
