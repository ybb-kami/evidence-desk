"""Replay marked mathematical table cells on a real scan and a new raster table."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

import pymupdf

from corpus import call, workbench


EXPECTED_SHA = "64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d"


def new_raster_table(path):
    rows = (("Method", "Work per run", "Score"), ("Alpha", "O(k - f)", "12.8"),
            ("Beta", "O(t)", "14.6"), ("Gamma", "O(d / v)", "16.4"))
    xs = (60, 252, 426, 568)
    with pymupdf.open() as vector, pymupdf.open() as raster:
        layout = vector.new_page(width=628, height=450)
        layout.insert_text((60, 90), "Table 1: Independent runtime study.", fontsize=14)
        for y in range(120, 321, 50):
            layout.draw_line((xs[0], y), (xs[-1], y), width=.8)
        for x in xs:
            layout.draw_line((x, 120), (x, 320), width=.8)
        for offset, row in enumerate(rows):
            for x, text in zip(xs, row):
                layout.insert_text((x + 12, 151 + offset * 50), text, fontsize=17)
        image = layout.get_pixmap(dpi=240, colorspace=pymupdf.csGRAY, alpha=False)
        page = raster.new_page(width=628, height=450)
        page.insert_image(page.rect, pixmap=image)
        assert not page.get_text().strip()
        raster.save(path, deflate=True, no_new_id=True)


def detect(port, source, physical):
    raw = source.read_bytes()
    docid = call(port, '/api/import?' + urlencode({'name': source.name}), raw, 'application/pdf')['doc']['id']
    assert docid == sha256(raw).hexdigest()
    output = call(port, '/api/scan', {'id': docid, 'page': physical})
    assert output == call(port, f'/api/scan/{docid}?page={physical}')
    return docid, raw, output['page']


def cell_at(table, row, column):
    return next(cell for cell in table['cells'] if cell['row'] == row and cell['column'] == column)


def real_case(browser, port, source):
    docid, raw, page = detect(port, source, 6)
    assert docid == EXPECTED_SHA
    [table] = page['regions']
    assert table['id'] == 'p6-r1' and table['status'] == 'structure_recognized'
    ambiguous = {(2, 2), (3, 2), (4, 2), (4, 4), (5, 2), (5, 4)}
    assert all(cell_at(table, *position)['status'] == 'math_unverified' for position in ambiguous)
    target = cell_at(table, 2, 2)
    assert target['text'] == '' and target['ocr_raw']['text'] == 'O(n? - d)'
    assert target['bbox_pt'] == [233.7, 138.6, 333.9, 150.6]
    assert cell_at(table, 3, 3)['text'] == 'O(n)'
    marked = [cell for cell in table['cells'] if cell['status'] == 'math_unverified']
    origin = f'http://127.0.0.1:{port}'
    tab = browser.new_page()
    try:
        tab.goto(origin + '/scan?' + urlencode({'id': docid, 'page': 6, 'region': table['id'], 'row': 2, 'column': 2}))
        detail = tab.locator('#detail')
        detail.locator('#table-cell').wait_for()
        assert detail.inner_text().startswith('数学式需核对原图')
        assert '原始 OCR（未核验） O(n? - d)' in detail.inner_text()
        select = detail.locator('#table-cell')
        assert select.input_value() == '2,2'
        assert 'O(n? - d)' not in select.locator('option[value="2,2"]').inner_text()
        actual = tab.locator('#mark').evaluate("node => [node.style.left,node.style.top,node.style.width,node.style.height].map(parseFloat)")
        x0, y0, x1, y1 = target['bbox_pt']
        expected = [100 * v / scale for v, scale in zip((x0, y0, x1-x0, y1-y0),
                    (page['width_pt'], page['height_pt'], page['width_pt'], page['height_pt']))]
        assert all(abs(a - b) < .001 for a, b in zip(actual, expected))
        assert tab.request.get(origin + '/pdf/' + docid).body() == raw
    finally:
        tab.close()
    return {'source_sha256': docid, 'physical_page': 6, 'table': table['id'], 'marked': marked,
            'unchanged_simple': cell_at(table, 3, 3)['text'], 'exact_original_and_highlight': True}


def run(browser, official):
    with workbench() as port, TemporaryDirectory(prefix='math-cells-raster-') as folder:
        official_result = real_case(browser, port, official)
        synth = Path(folder) / 'runtime-table.pdf'
        new_raster_table(synth)
        digest, _, page = detect(port, synth, 1)
        [table] = page['regions']
        flagged = cell_at(table, 2, 2)
        assert flagged['status'] == 'math_unverified' and flagged['ocr_raw']['text']
        assert cell_at(table, 3, 2)['text'] == 'O(t)'
        assert cell_at(table, 3, 3)['text'] == '14.6'
        return {'official': official_result, 'synthetic': {'source_sha256': digest, 'physical_page': 1,
                'marked': flagged, 'simple': cell_at(table, 3, 2), 'score': cell_at(table, 3, 3)}}


if __name__ == '__main__':
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('official_scan', type=Path)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    with sync_playwright() as play, play.chromium.launch(executable_path=args.chromium) as browser:
        print(json.dumps(run(browser, args.official_scan), indent=2, ensure_ascii=False))
