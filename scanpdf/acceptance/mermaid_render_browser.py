"""Render exported observation graphs and numeric charts in offline Chromium."""

import argparse
import csv
from hashlib import sha256
from io import StringIO
import json
import math
from pathlib import Path
import re

from playwright.sync_api import sync_playwright

from corpus import workbench
from csv_trends_browser import save


MERMAID_VERSION = "11.12.0"
BUNDLE_SHA256 = "07e37dfa97b337ccc85365d57eddf99b9706f09db3b59b260d0333b23b343c4b"


def load(page, source, x, y, *, x_kind="numeric", group=None, units=("", "")):
    page.locator("#trend-file").set_input_files(source)
    page.select_option("#trend-x", label=x)
    page.select_option("#trend-y", label=y)
    page.select_option("#trend-kind", x_kind)
    if group:
        page.select_option("#trend-group", label=group)
    page.fill("#trend-x-unit", units[0])
    page.fill("#trend-y-unit", units[1])
    if x_kind == "numeric":
        page.check("#trend-connect")
    page.locator("#trend-draw").click()
    page.locator("#trend-preview img").wait_for()
    details = page.get_by_text("查看并导出 TikZ / Mermaid", exact=True)
    if not details.evaluate("node => node.parentElement.open"):
        details.click()


def csv_fixture(headers, rows):
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return {"name": "fixture.csv", "mimeType": "text/csv", "buffer": stream.getvalue().encode()}


def render(page, source, file):
    data = page.evaluate("""async source => {
      const parsed = await mermaid.parse(source);
      const {svg} = await mermaid.render('export-rendered', source);
      const preview = document.getElementById('diagram');
      preview.innerHTML = svg;
      const graphic = preview.querySelector('svg');
      return {type:parsed.diagramType, svg,
        nodes:Object.fromEntries([...graphic.querySelectorAll('g.node')].map(node => [
          node.id.replace(/^flowchart-(.+)-[0-9]+$/, '$1'), node.textContent])),
        edges:graphic.querySelectorAll('path.flowchart-link').length,
        unsafeElements:graphic.querySelectorAll('script, iframe, img, a[href]').length,
        labels:[...graphic.querySelectorAll('text')].map(node => node.textContent),
        chartPath:graphic.querySelector('.line-plot-0 path')?.getAttribute('d') || null,
        chartStroke:graphic.querySelector('.line-plot-0 path')?.getAttribute('stroke') || null};
    }""", source)
    file.write_text(data.pop("svg"), encoding="utf-8")
    assert data["unsafeElements"] == 0, data
    return data


def verify_observations(data, source):
    with source.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 32
    assert len(data["nodes"]) == 36 and data["edges"] == 45
    assert {data["nodes"][f"group{i}"] for i in range(3)} == {"cyl = 4", "cyl = 6", "cyl = 8"}
    for i, row in enumerate(rows, 1):
        label = data["nodes"][f"row{i}"]
        assert f'CSV {i + 1}' in label and f'= {row["hp"]} ·' in label and label.endswith(f'= {row["mpg"]}'), label


def verify_chart(data, source):
    with source.open(newline="", encoding="utf-8") as stream:
        series = [(int(row["time"]), int(row["value"])) for row in csv.DictReader(stream)]
    assert [x for x, _ in series] == list(range(1871, 1971))
    assert {"time (年)", "value (10⁸ m³)", "1970", "time (年) 1871–1970 / value (10⁸ m³)"} <= set(data["labels"]), data["labels"]
    path = data["chartPath"]
    assert path and not re.search(r"NaN|Infinity", path)
    assert data["chartStroke"].lower() == "#135e70", data["chartStroke"]
    points = [(float(px), float(py)) for px, py in re.findall(r"[ML](-?[0-9.]+),(-?[0-9.]+)", path)]
    assert len(points) == len(series) == 100
    min_i = min(range(len(series)), key=lambda i: series[i][1])
    max_i = max(range(len(series)), key=lambda i: series[i][1])
    min_y, max_y = series[min_i][1], series[max_i][1]
    bottom, top = points[min_i][1], points[max_i][1]
    first_pixel, last_pixel = points[0][0], points[-1][0]
    assert first_pixel < last_pixel and top < bottom
    for observed, (year, value) in zip(points, series, strict=True):
        x = first_pixel + (last_pixel - first_pixel) * (year - 1871) / 99
        y = bottom + (top - bottom) * (value - min_y) / (max_y - min_y)
        assert math.isclose(observed[0], x, abs_tol=0.002) and math.isclose(observed[1], y, abs_tol=0.002), observed
    return len(points)


def check_boundaries(page):
    failures = {
        "missing_y": ([[1, 2], [2, ""], [3, 6]], "缺失"),
        "missing_x": ([[1, 2], ["", 3], [3, 6]], "缺失"),
        "invalid": ([[1, 2], [2, "N/A"], [3, 6]], "无效"),
        "duplicate": ([[1, 2], [1, 3], [3, 6]], "重复"),
        "irregular": ([[1, 2], [2, 3], [4, 6]], "等距"),
        "scientific_notation": ([[1, "1e-10"], [2, 3], [3, 6]], "十进制"),
    }
    for case, (rows, message) in failures.items():
        load(page, csv_fixture(["x", "y"], rows), "x", "y")
        assert page.locator("#trend-mermaid-xy-source").is_hidden(), case
        assert message in page.locator("#trend-mermaid-xy-reason").text_content(), case
        assert page.locator("#trend-mermaid-code").input_value().startswith("flowchart LR"), case
    many = csv_fixture(["x", "y"], [[i, i] for i in range(1, 202)])
    load(page, many, "x", "y")
    assert page.locator("#trend-mermaid").is_disabled()
    assert "200" in page.locator("#trend-mermaid-status").text_content()
    assert "\nxychart\n" in page.locator("#trend-mermaid-xy-code").input_value()
    return [*failures, "flowchart_point_limit"]


def run(mtcars, nile, bundle, output, chromium, original):
    assert sha256(bundle.read_bytes()).hexdigest() == BUNDLE_SHA256, "Incorrect Mermaid bundle SHA-256"
    output.mkdir(parents=True, exist_ok=True)
    with workbench() as port, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, args=["--no-sandbox", "--disable-background-networking"])
        base = f"http://127.0.0.1:{port}/"
        attempts, errors = [], []
        app = browser.new_page(accept_downloads=True, viewport={"width": 1150, "height": 925})
        app.on("pageerror", lambda error: errors.append(str(error)))

        def local_only(route):
            url = route.request.url
            if url.startswith(("http://", "https://")) and not url.startswith(base):
                attempts.append(url)
                route.abort()
            else:
                route.continue_()

        app.route("**/*", local_only)
        app.goto(base)
        app.get_by_role("link", name="按变量绘图与核对趋势").click()
        app.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base.rstrip("/"))

        renderer = browser.new_page(offline=True, viewport={"width": 1250, "height": 830})
        renderer.on("pageerror", lambda error: errors.append(str(error)))
        renderer.on("request", lambda req: attempts.append(req.url) if req.url.startswith(("http://", "https://")) else None)
        renderer.set_content('<main id="diagram"></main>')
        renderer.add_script_tag(path=bundle.resolve())
        renderer.evaluate('mermaid.initialize({startOnLoad:false,securityLevel:"strict",htmlLabels:true})')

        load(app, mtcars, "hp", "mpg", group="cyl", units=("hp", "mpg (US)"))
        assert app.locator("#trend-mermaid-xy-source").is_hidden()
        mtcars_graph = save(app, "mermaid", output, "mtcars-").read_text(encoding="utf-8")
        rendered_observations = render(renderer, mtcars_graph, output / "mtcars-mermaid.svg")
        verify_observations(rendered_observations, mtcars)
        if original:
            baseline = render(renderer, original.read_text(encoding="utf-8"), output / "original-mtcars-mermaid.svg")
            verify_observations(baseline, mtcars)

        reserved_x, reserved_y, group_col = 'X "位置" [s]', '响应 <Y> & #', '组 {{}%}'
        group_name = '甲 "Q" <tag> [A] # `value` \\ &'
        load(app, csv_fixture([reserved_x, reserved_y, group_col], [[1, 2, group_name], [2, "", group_name], [3, 6, group_name]]),
             reserved_x, reserved_y, group=group_col)
        assert "缺失" in app.locator("#trend-mermaid-xy-reason").text_content()
        source = save(app, "mermaid", output, "reserved-").read_text(encoding="utf-8")
        reserved = render(renderer, source, output / "reserved-mermaid.svg")
        assert len(reserved["nodes"]) == 4 and reserved["edges"] == 3, reserved
        assert reserved["nodes"]["group0"] == f"{group_col} = {group_name}"
        assert reserved["nodes"]["row1"] == f"CSV 2 · {reserved_x} = 1 · {reserved_y} = 2"
        assert reserved["nodes"]["row3"] == f"CSV 4 · {reserved_x} = 3 · {reserved_y} = 6"

        load(app, csv_fixture([reserved_x, reserved_y, group_col], [[8, 7, group_name], [2, 2, group_name], [5, 6, group_name]]),
             reserved_x, reserved_y, group=group_col, units=("秒", "伏"))
        source = save(app, "mermaid-xy", output, "reserved-").read_text(encoding="utf-8")
        reserved_chart = render(renderer, source, output / "reserved-xychart-mermaid.svg")
        assert f"{reserved_x} (秒)" in reserved_chart["labels"] and f"{reserved_y} (伏)" in reserved_chart["labels"]
        assert any(group_name in label for label in reserved_chart["labels"])

        load(app, nile, "time", "value", units=("年", "10⁸ m³"))
        chart = app.locator("#trend-mermaid-xy-code").input_value()
        app.locator("#trend-mermaid-xy-copy").click()
        app.wait_for_function("expected => navigator.clipboard.readText().then(text => text === expected)", arg=chart)
        assert save(app, "mermaid-xy", output, "nile-").read_text(encoding="utf-8") == chart
        rendered_chart = render(renderer, chart, output / "nile-xychart-mermaid.svg")
        chart_points = verify_chart(rendered_chart, nile)
        renderer.locator("#diagram").screenshot(path=output / "nile-mermaid-preview.png")
        boundaries = check_boundaries(app)
        invalid_parsed = renderer.evaluate('''async () => {
          try { await mermaid.parse('xychart\\n line [1, ??, 3]'); return true; }
          catch { return false; }
        }''')
        assert not invalid_parsed
        assert not attempts and not errors, (attempts, errors)
        browser.close()
    return {"renderer": f"Mermaid {MERMAID_VERSION}", "bundle_sha256": BUNDLE_SHA256,
            "chromium_network": "offline for render; workbench loopback only", "external_requests": len(attempts),
            "mtcars_sha256": sha256(mtcars.read_bytes()).hexdigest(), "mtcars_rendered_nodes": len(rendered_observations["nodes"]),
            "mtcars_rendered_edges": rendered_observations["edges"], "original_export_replayed": bool(original),
            "reserved_character_labels_exact": True, "reserved_graph_rendered_nodes": len(reserved["nodes"]),
            "reserved_graph_rendered_edges": reserved["edges"], "nile_sha256": sha256(nile.read_bytes()).hexdigest(),
            "nile_chart_vertices_verified": chart_points, "nile_axis_units": ["年", "10⁸ m³"],
            "fallback_cases": [*boundaries, "malformed_mermaid"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mtcars", type=Path)
    parser.add_argument("nile", type=Path)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--original-mmd", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.mtcars, args.nile, args.bundle, args.output, args.chromium, args.original_mmd),
                     ensure_ascii=False, indent=2))
