# CSV 趋势图导出验收

从工作台首页的「本地 CSV 图表」进入「按变量绘图与核对趋势」。导入 UTF-8 CSV 后，选择横轴及其类型、目标数值列、单位和分组。勾选「连接同组可比的相邻数值点」即可显示符合条件的线段。导出项包括 SVG、300 dpi PNG、图注、逐行 JSON 映射、TikZ、Mermaid 观测关系图和符合条件的 Mermaid 数值趋势图。

数值横轴按 X 升序。已知 X 的缺失或无效 Y 和重复 X 会断开连线。组内存在未知 X 时只画点，首末差留空。首末差使用唯一且可见的最小和最大 X 对应的 Y。每个峰值保留所有并列观测的 CSV 起始行，详细映射包含未绘制记录和原因。类别按首次出现排列。数据和图注描述观测范围，原始行序只用于追溯。

TikZ 与 SVG 共享绘图坐标和标记。用 XeLaTeX、TikZ、fontspec 和 standalone 编译 `.tex`。本机安装 Noto Sans CJK SC 时可自动用于中文标签。Mermaid 观测关系图保存分组、原始观测及已选择的相邻关系。数值坐标与比例可在 SVG 或 TikZ 中复查。关系图最多 200 个点，源码最多 48,000 字符。

常规 `xychart` 只用于明确勾选连线的单一数值系列。选中范围必须有至少两个点，并能由 Mermaid 11.12 的数值轴从首末 X 逐点复现全部位置。存在重复、缺失、无效值、不同间距或无法用普通十进制表示的数值时，界面解释原因并提供适用的观测关系图、SVG 或 TikZ。分组系列可单独选择，导出标题保留组名，横纵轴保留明确的单位。[Mermaid XY Chart 语法](https://mermaid.js.org/syntax/xyChart.html)

## 获取验收输入

以下命令下载两份公开 CSV 和一个固定版本的 npm 发布包。依赖文件放在独立临时目录，应用和仓库无需附带 Mermaid。该 npm 命令只取发布包，不执行安装脚本。

```sh
figure_check=$(mktemp -d)
curl -fsS https://raw.githubusercontent.com/vincentarelbundock/Rdatasets/master/csv/datasets/mtcars.csv -o "$figure_check/mtcars.csv"
curl -fsS https://raw.githubusercontent.com/vincentarelbundock/Rdatasets/master/csv/datasets/Nile.csv -o "$figure_check/Nile.csv"
npm --cache "$figure_check/npm-cache" pack mermaid@11.12.0 --pack-destination "$figure_check" --silent
(
  cd "$figure_check" || exit
  printf '%s\n' \
    '4d6dfa9b029dbcc15333a6e3598803fae8ae7c4d08605040b1706e6367695058  mtcars.csv' \
    'd0452bea38c61e796a4eeb950bf91d20fb5c7f13d5822eadf5990fe54f9c8d07  Nile.csv' \
    '93492e4d609be83653a41c435f4cbd7e74419f7fa6b4fc90a8c983195f64af82  mermaid-11.12.0.tgz' | sha256sum -c - || exit
  tar -xzf mermaid-11.12.0.tgz package/dist/mermaid.min.js
  printf '%s\n' '07e37dfa97b337ccc85365d57eddf99b9706f09db3b59b260d0333b23b343c4b  package/dist/mermaid.min.js' | sha256sum -c -
)
```

[mtcars 数据字典](https://stat.ethz.ch/R-manual/R-devel/library/datasets/html/mtcars.html) · [Nile 数据字典](https://stat.ethz.ch/R-manual/R-devel/library/datasets/html/Nile.html) · [Mermaid npm 发布页](https://www.npmjs.com/package/mermaid/v/11.12.0)

## 离线复现

在项目根目录运行下列命令。浏览器测试使用本机 Chromium 和 Python Playwright。`csv_trends_browser.py` 另需 XeLaTeX 及前述宏包。`--chromium` 和 `--xelatex` 可指定二进制位置。

```sh
node test_csv_stats.cjs
node scanpdf/test_csv_trends.cjs
python scanpdf/acceptance/csv_trends_browser.py "$figure_check/mtcars.csv" "$figure_check/exports"
python scanpdf/acceptance/mermaid_render_browser.py \
  "$figure_check/mtcars.csv" "$figure_check/Nile.csv" \
  "$figure_check/package/dist/mermaid.min.js" "$figure_check/mermaid-exports" \
  --original-mmd "$figure_check/exports/csv-variable-mermaid.mmd"
```

浏览器从首页打开工作台、导入 CSV 并导出源码。应用页面仅访问回环地址，实际 Mermaid 渲染在独立的离线上下文中使用本地 SHA-256 已验证的 bundle，开启严格安全模式。验收用浏览器实际生成的 SVG 核对 mtcars 的组标签、观测节点和连线数，并检查含保留字符的图标签和缺失点。Nile 的年份和流量用于独立复核渲染折线的每个坐标。另覆盖缺失、重复、乱序、非等距、科学记数法与错误 Mermaid 源码。

2026-11-18 的 Chromium 实测中，原导出和本轮 mtcars 导出均渲染为 36 个节点和 45 条边。保留字符样例的引号、方括号、尖括号、反斜杠及中文标签按原值显示。Nile 1871–1970 年的 100 个折线顶点逐点核对通过，横轴标出年，纵轴标出 (10^8\,\mathrm{m}^3)。工作台与渲染上下文均无公网请求。[实测结果](results/csv-mermaid-chromium.json)
