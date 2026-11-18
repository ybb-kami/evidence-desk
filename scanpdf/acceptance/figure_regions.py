"""Replay OCR figure anchors on pinned paper pages and verify the saved pixels."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy
from PIL import Image, ImageChops, __version__ as pillow_version
import pymupdf
import scipy


ATTENTION = ('1706.03762v1_img.pdf', '64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d')
FLASH = ('2205.14135v2.pdf', 'ca7f9fda10b90fc05dd291a3accc85e9c1a4a860b99b31928dab03ed3fcb14e4')
BERT = ('N19-1423.pdf', '987545ffb087f1ece898142c403a516baeabeb70ce19089397fac6f7db12c3d4')
DPI = 240
RASTER_DPI = 220

# Points sampled across the artwork on the original page in the PDF point frame.
LANDMARKS = {
    'attention_architecture': [(208, 330), (407, 330), (344, 84)],
    'attention_two_panels': [(153, 74), (194, 142), (416, 190)],
    'flash_memory_attention': [(121, 153), (300, 99), (487, 158)],
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_source(directory, source):
    path = directory / source[0]
    if digest(path) != source[1]:
        raise ValueError(f'Unexpected published PDF hash for {path.name}')
    return path


def raster_page(path, page_number, target):
    with pymupdf.open(path) as source, pymupdf.open() as raster:
        original = source[page_number - 1]
        page = raster.new_page(width=original.rect.width, height=original.rect.height)
        pixels = original.get_pixmap(dpi=RASTER_DPI, colorspace=pymupdf.csRGB, alpha=False)
        page.insert_image(page.rect, pixmap=pixels)
        if page.get_text().strip() or len(page.get_images()) != 1:
            raise ValueError(f'Expected one raster and no text in {target.name}')
        raster.save(target, no_new_id=True, deflate=True)
    return target


def verify_page(case, source, page, fragments, output):
    from scanpdf.figures import _intersection
    from scanpdf.ocr import recognize
    from scanpdf.parser import _lines
    from scanpdf.tables import detect_tables

    assert page['status'] == 'processed', case
    [figure] = page['figures']
    assert not page['unresolved_captions'], case
    assert figure['id'] == f'p{page["physical_page"]}-f1', case
    assert figure['physical_page'] == page['physical_page'] and figure['source_sha256'] == digest(source), case
    assert all(fragment in ' '.join(figure['caption'].casefold().split()) for fragment in fragments), case
    assert figure['bbox'][3] < figure['caption_bbox'][1], case
    assert figure['bbox_pt'][3] - figure['bbox_pt'][1] <= .45 * page['height_pt'], case
    assert figure['bbox_pt'][2] - figure['bbox_pt'][0] <= .75 * page['width_pt'], case
    assert all(point in pymupdf.Rect(figure['bbox_pt']) for point in LANDMARKS[case]), case
    for field in ('bbox', 'caption_bbox'):
        x0, y0, x1, y1 = figure[field]
        assert 0 <= x0 < x1 <= page['width_px'] and 0 <= y0 < y1 <= page['height_px'], case
        assert figure[f'{field}_pt'] == [round(value * 72 / DPI, 4) for value in figure[field]], case

    with pymupdf.open(source) as pdf:
        physical = pdf[page['physical_page'] - 1]
        assert not physical.get_text().strip(), case
        pixels = physical.get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB, alpha=False)
        original = Image.frombytes('RGB', (pixels.width, pixels.height), pixels.samples)
    with Image.open(output / 'crops' / figure['crop']) as crop:
        assert ImageChops.difference(crop, original.crop(figure['bbox'])).getbbox() is None, case
    gray = original.convert('L')
    words = recognize(gray, DPI, 'eng', 3)
    boxes = [line['bbox'] for line in _lines(words) if line['line_id'] in figure['caption_lines']]
    assert len(boxes) == len(figure['caption_lines']), case
    assert all(_intersection(box, figure['caption_bbox']) == pymupdf.Rect(box).get_area() for box in boxes), case
    tables = detect_tables(gray, words, DPI)
    assert not any(_intersection(figure['bbox'], table['bbox']) for table in tables), case
    return {'case': case, 'figure': figure, 'crop_sha256': digest(output / 'crops' / figure['crop']),
            'pixel_exact': True, 'overlapping_table_regions': 0, 'visual_landmarks_pt': LANDMARKS[case]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scanpdf.figures import scan_figures

    args.output.mkdir(parents=True, exist_ok=False)
    original_attention = check_source(args.pdf_dir, ATTENTION)
    original_flash = check_source(args.pdf_dir, FLASH)
    original_bert = check_source(args.pdf_dir, BERT)
    flash = raster_page(original_flash, 2, args.output / '2205.14135v2-p2-raster.pdf')
    bert = raster_page(original_bert, 3, args.output / 'N19-1423-p3-raster.pdf')
    crops = args.output / 'crops'
    attention = scan_figures(original_attention, pages='3,4,8,13', dpi=DPI, crops=crops)
    flash_scan = scan_figures(flash, dpi=DPI, crops=crops)
    bert_scan = scan_figures(bert, dpi=DPI, crops=crops)
    [architecture, panels, tables, fine_strokes] = attention['pages']
    [memory_chart] = flash_scan['pages']
    [bert_page] = bert_scan['pages']
    positives = [
        verify_page('attention_architecture', original_attention, architecture, ('model architecture',), args.output),
        verify_page('attention_two_panels', original_attention, panels, ('scaled dot-product attention', 'multi-head attention'), args.output),
        verify_page('flash_memory_attention', flash, memory_chart,
                    ('flashattention uses tiling', 'fast on-chip sram', 'speedup on the attention computation.'), args.output),
    ]
    assert tables['status'] == 'processed' and not tables['figures'] and not tables['unresolved_captions']
    assert not fine_strokes['figures'] and not bert_page['figures']
    [uncertain_attention] = fine_strokes['unresolved_captions']
    [uncertain_bert] = bert_page['unresolved_captions']
    assert uncertain_attention['label'] == '3' and uncertain_attention['reason'] == 'insufficient_panel_span'
    assert uncertain_bert['label'] == '1' and uncertain_bert['reason'] == 'overlaps_table_region'
    assert 'long-distance dependencies' in uncertain_attention['caption'] and 'bbox' not in uncertain_attention
    assert 'parameters are fine-tuned' in uncertain_bert['caption'] and 'bbox' not in uncertain_bert
    [native] = scan_figures(original_flash, pages='2')['pages']
    assert native['status'] == 'skipped_native_text' and not native['figures']
    text = subprocess.check_output(['tesseract', '--version'], text=True).splitlines()[0]
    result = {'schema_version': 1, 'render': {'dpi': DPI, 'lang': 'eng', 'pymupdf': pymupdf.__version__,
              'pillow': pillow_version, 'tesseract': text, 'scipy': scipy.__version__, 'numpy': numpy.__version__},
              'sources': {'attention': attention['source'],
                          'flash_raster': {'original': FLASH[0], 'original_sha256': FLASH[1],
                                           'original_physical_page': 2, 'raster_dpi': RASTER_DPI, **flash_scan['source']},
                          'bert_raster': {'original': BERT[0], 'original_sha256': BERT[1],
                                          'original_physical_page': 3, 'raster_dpi': RASTER_DPI, **bert_scan['source']}},
              'positive': positives, 'paper_table_page': tables, 'unresolved_captions': [uncertain_attention, uncertain_bert],
              'native_exclusion': native}
    (args.output / 'results.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{len(positives)} pixel-verified figure crops, 2 unresolved captions, 1 table-only page, 1 native page omitted')


if __name__ == '__main__':
    main()
