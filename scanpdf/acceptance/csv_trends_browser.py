"""Exercise Chromium with the published mtcars CSV and verify its exported data independently."""

import argparse
import csv
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import struct
import subprocess
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import pymupdf
from playwright.sync_api import sync_playwright

from corpus import workbench


NS = "{http://www.w3.org/2000/svg}"


def read_rows(source):
    with source.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        previous = reader.fieldnames and reader.line_num
        rows = []
        for index, record in enumerate(reader, 1):
            rows.append((index, previous + 1, record))
            previous = reader.line_num
    return rows


def exact_reference(number, line, row, x_column):
    return {"dataRow": number, "sourceLine": line, "x": int(row[x_column]), "y": float(Decimal(row["mpg"]))}


def x_center(element):
    if element.tag == NS + "circle":
        return float(element.attrib["cx"])
    if element.tag == NS + "rect":
        return float(element.attrib["x"]) + float(element.attrib["width"]) / 2
    return float(re.match(r"M(-?[0-9.]+) ", element.attrib["d"])[1])


def verify_numeric(source, files):
    rows = read_rows(source)
    assert len(rows) == 32 and "hp" in rows[0][2]
    digest = sha256(source.read_bytes()).hexdigest()
    result = json.loads(files["mapping"].read_text())
    assert result["source"]["sha256"] == digest and len(result["rows"]) == len(rows)
    assert result["source"]["x"]["unit"] == "hp" and result["source"]["y"]["unit"] == "mpg (US)"
    assert result["source"]["group"]["column"] == "cyl" and result["orientation"] == "ascending_numeric_x"
    for exported, (number, line, row) in zip(result["rows"], rows, strict=True):
        for key, value in exact_reference(number, line, row, "hp").items():
            assert exported[key] == value, (key, exported, row)
        assert (exported["xCell"], exported["yCell"], exported["group"], exported["plotted"]) == (
            row["hp"], row["mpg"], row["cyl"], True)

    groups, segments = {}, set()
    for exported in result["groups"]:
        group = exported["group"]
        subset = [(number, line, row) for number, line, row in rows if row["cyl"] == group]
        by_x = {}
        for number, line, row in subset:
            by_x.setdefault(int(row["hp"]), []).append((number, line, row))
        ordered = sorted(by_x)
        assert {row["x"] for row in exported["duplicates"]} == {x for x in ordered if len(by_x[x]) > 1}
        first, last = by_x[ordered[0]], by_x[ordered[-1]]
        if len(first) == len(last) == 1:
            expected_delta = Decimal(last[0][2]["mpg"]) - Decimal(first[0][2]["mpg"])
            assert math.isclose(exported["endpoints"]["deltaY"], float(expected_delta), abs_tol=1e-12)
            assert exported["endpoints"]["first"] == exact_reference(*first[0], "hp")
            assert exported["endpoints"]["last"] == exact_reference(*last[0], "hp")
        else:
            expected_delta = None
            assert exported["endpoints"] is None
        max_y = max(Decimal(row["mpg"]) for _, _, row in subset)
        peak_rows = [exact_reference(number, line, row, "hp") for number, line, row in subset if Decimal(row["mpg"]) == max_y]
        assert exported["peak"]["value"] == float(max_y) and exported["peak"]["observations"] == peak_rows
        valid_links = {(by_x[a][0][0], by_x[b][0][0]) for a, b in zip(ordered, ordered[1:])
                       if len(by_x[a]) == len(by_x[b]) == 1}
        reported_links = {(s["from"]["dataRow"], s["to"]["dataRow"]) for s in exported["segments"]}
        assert reported_links == valid_links
        segments.update(valid_links)
        groups[group] = {"points": len(subset), "peak": float(max_y), "peak_lines": [r["sourceLine"] for r in peak_rows],
                         "endpoint_delta": float(expected_delta) if expected_delta is not None else None,
                         "duplicates": len(exported["duplicates"]), "segments": len(valid_links)}

    svg = ElementTree.parse(files["svg"]).getroot()
    assert svg.attrib["width"] == "183mm" and svg.find(".//" + NS + "image") is None
    width = float(svg.attrib["viewBox"].split()[2])
    x_min = min(int(row["hp"]) for _, _, row in rows)
    x_max = max(int(row["hp"]) for _, _, row in rows)
    marks = {int(node.attrib["data-row"]): node for node in svg.iter() if "data-row" in node.attrib}
    assert len(marks) == 32
    for number, line, row in rows:
        mark = marks[number]
        assert (mark.attrib["id"], int(mark.attrib["data-source-line"]), int(mark.attrib["data-x"]),
                Decimal(mark.attrib["data-y"])) == (f"observation-{number}", line, int(row["hp"]), Decimal(row["mpg"]))
        expected_x = 80 + (width - 38 - 80) * (int(row["hp"]) - x_min) / (x_max - x_min)
        assert math.isclose(x_center(mark), expected_x, abs_tol=0.0001)
    assert {(int(node.attrib["data-from-row"]), int(node.attrib["data-to-row"])) for node in svg.iter()
            if "data-from-row" in node.attrib} == segments
    caption = files["caption-file"].read_text()
    assert result["caption"] in caption and digest in caption
    assert result["caption"] in svg.find(NS + "desc").text
    png = files["png"].read_bytes()
    width_px, height_px = struct.unpack(">II", png[16:24])
    assert (width_px, height_px) == (round(183 * 300 / 25.4), round(result["figure"]["heightMm"] * 300 / 25.4))
    assert png[37:41] == b"pHYs" and struct.unpack(">IIB", png[41:50]) == (11811, 11811, 1)
    return {"rows_checked": len(rows), "sha256": digest, "png_dimensions": [width_px, height_px], "groups": groups}


def verify_codes(source, files, xelatex):
    rows = read_rows(source)
    source_map = json.loads(files["mapping"].read_text())
    tex = files["tikz"].read_text()
    assert "\\begin{tikzpicture}" in tex and "\\end{tikzpicture}" in tex
    assert len(re.findall(r"^% Observation \d+, CSV line \d+, X=", tex, re.M)) == len(rows)
    for number, line, row in rows:
        assert f'% Observation {number}, CSV line {line}, X="{row["hp"]}", Y={float(row["mpg"]):g}\n' in tex
    source_connections = {f'{s["from"]["dataRow"]} {s["to"]["dataRow"]}' for group in source_map["groups"]
                          for s in group["segments"]}
    assert set(re.findall(r"^% Segment records (\d+ \d+)$", tex, re.M)) == source_connections
    with TemporaryDirectory(prefix="evidence-csv-tex-") as build:
        args = [xelatex, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                "-output-directory", build, str(files["tikz"].resolve())]
        compiled = subprocess.run(args, capture_output=True, text=True, timeout=45)
        assert compiled.returncode == 0, compiled.stdout[-4000:]
        pdf_path = files["tikz"].with_suffix(".pdf")
        pdf_path.write_bytes((Path(build) / pdf_path.name).read_bytes())
        assert "Missing character:" not in compiled.stdout, compiled.stdout[-2000:]
    with pymupdf.open(pdf_path) as pdf:
        assert len(pdf) == 1
        page = pdf[0]
        assert math.isclose(page.rect.width * 25.4 / 72, 183, abs_tol=0.2)
        assert math.isclose(page.rect.height * 25.4 / 72, source_map["figure"]["heightMm"], abs_tol=0.2)
        assert not page.get_images() and len(page.get_drawings()) >= len(rows)
        assert "mpg (US)" in page.get_text() and "hp" in page.get_text()
        page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25)).save(pdf_path.with_name("tikz-preview.png"))

    # Validate the emitted flowchart subset without a third-party browser renderer.
    mermaid = files["mermaid"].read_text().splitlines()
    assert mermaid[0] == "flowchart LR"
    nodes, edges = {}, set()
    identifiers = r"(?:observations|group[0-9]+|row[0-9]+)"
    for line in mermaid[1:]:
        line = line.strip()
        if not line or line.startswith("%%"):
            continue
        node = re.fullmatch(rf'{identifiers}\["([^"<>\[\]`\\]*)"\]', line)
        if node:
            ident = line.split("[", 1)[0]
            assert ident not in nodes
            nodes[ident] = re.sub(r"#(\d+);", lambda match: chr(int(match[1])), node[1])
        else:
            edge = re.fullmatch(rf"({identifiers}) --- ({identifiers})", line)
            assert edge, line
            edges.add(edge.groups())
    assert len(nodes) == 1 + len(source_map["groups"]) + len(rows)
    assert all(left in nodes and right in nodes for left, right in edges)
    assert {(int(left[3:]), int(right[3:])) for left, right in edges if left.startswith("row")} == {
        tuple(map(int, edge.split())) for edge in source_connections}
    group_ids = {label.split(" = ")[1]: node for node, label in nodes.items() if node.startswith("group")}
    for number, line, row in rows:
        assert nodes[f"row{number}"] == f'CSV {line} · hp = {row["hp"]} · mpg (US) = {row["mpg"]}'
        assert (group_ids[row["cyl"]], f"row{number}") in edges
    return {"tikz_vector_pdf": pdf_path.name, "mermaid_nodes": len(nodes), "mermaid_edges": len(edges),
            "mermaid_verification": "offline syntax and source data reconciliation"}


def save(page, name, folder, prefix=""):
    with page.expect_download() as download:
        page.locator(f"#trend-{name}").click()
    value = download.value
    path = folder / (prefix + value.suggested_filename)
    value.save_as(path)
    return path


def run(source, output, chromium, xelatex):
    output.mkdir(parents=True, exist_ok=True)
    with workbench() as port, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        page = browser.new_page(accept_downloads=True, viewport={"width": 1050, "height": 900})
        errors, outbound = [], []
        base = f"http://127.0.0.1:{port}/"
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: outbound.append(request.url)
                if request.url.startswith(("http://", "https://")) and not request.url.startswith(base) else None)
        page.goto(base)
        page.get_by_role("link", name="按变量绘图与核对趋势").click()
        page.locator("#trend-file").set_input_files(source)
        page.select_option("#trend-x", label="hp")
        page.select_option("#trend-y", label="mpg")
        page.select_option("#trend-kind", "numeric")
        page.select_option("#trend-group", label="cyl")
        page.fill("#trend-x-unit", "hp")
        page.fill("#trend-y-unit", "mpg (US)")
        page.fill("#trend-row-unit", "车型")
        page.check("#trend-connect")
        page.locator("#trend-draw").click()
        page.locator("#trend-preview img").wait_for()
        page.locator("#trend-result").screenshot(path=output / "numeric-preview.png")
        paths = {name: save(page, name, output) for name in ("svg", "png", "caption-file", "mapping")}
        page.get_by_text("查看并导出 TikZ / Mermaid", exact=True).click()
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base.rstrip("/"))
        for name in ("tikz", "mermaid"):
            code = page.locator(f"#trend-{name}-code").input_value()
            page.locator(f"#trend-{name}-copy").click()
            page.wait_for_function("value => navigator.clipboard.readText().then(text => value === text)", arg=code)
            paths[name] = save(page, name, output)
            assert paths[name].read_text() == code
        observed = verify_numeric(source, paths)
        observed.update(verify_codes(source, paths, xelatex))

        page.select_option("#trend-x", label="gear")
        page.select_option("#trend-kind", "category")
        assert page.is_disabled("#trend-connect")
        page.select_option("#trend-focus", label="4")
        page.fill("#trend-x-unit", "")
        page.select_option("#trend-width", "90")
        page.locator("#trend-draw").click()
        page.locator("#trend-preview img").wait_for()
        page.locator("#trend-result").screenshot(path=output / "category-preview.png")
        category_map = json.loads(save(page, "mapping", output, "category-").read_text())
        svg = ElementTree.parse(save(page, "svg", output, "category-")).getroot()
        expected = [(number, row["gear"]) for number, _, row in read_rows(source) if row["cyl"] == "4"]
        assert category_map["focus"] == "4" and category_map["connect"] is False and svg.attrib["width"] == "90mm"
        assert [(item["dataRow"], item["x"]) for item in category_map["rows"] if item["plotted"]] == expected
        assert [item["category"] for item in category_map["groups"][0]["categories"]] == list(dict.fromkeys(label for _, label in expected))
        assert {node.text for node in svg.iter(NS + "text")} >= {label for _, label in expected}
        assert all("data-from-row" not in node.attrib for node in svg.iter())
        assert "首末差" not in category_map["caption"] and "上升" not in category_map["caption"]
        assert not errors and not outbound, (errors, outbound)
        browser.close()
        return {**observed, "category_rows_checked": len(expected), "http_requests_to_external_sites": len(outbound)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--xelatex", default="xelatex")
    args = parser.parse_args()
    print(json.dumps(run(args.csv, args.output, args.chromium, args.xelatex), ensure_ascii=False, indent=2))
