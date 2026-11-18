# 传智杯科研助手 · 扫描 PDF 解析

这个独立模块将 PDF 的可见页面渲染为灰度位图，在本机运行 Tesseract，然后输出带物理页码和位置的 UTF-8 JSON。适用于没有文字层的扫描件。导入方负责选择解析路径、文献去重、任务调度和展示。运行时无需模型下载、网络服务或密钥。

## 安装与运行

以下命令在仓库的 `scanpdf/` 目录运行。实测环境为 Linux、Python 3.12.14、Tesseract 5.5.0，Python 依赖版本见 `requirements.txt`。Debian/Ubuntu 的系统依赖为 `tesseract-ocr`、`tesseract-ocr-eng`，合成测试另需 `fonts-dejavu-core`。中文另装 `tesseract-ocr-chi-sim` 并使用 `--lang chi_sim+eng`。

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
tesseract --list-langs
python -m scanpdf ../pdf/1706.03762v1_img.pdf --output scan-result.json --crops scan-crops
```

默认以 240 DPI 做英文 OCR。可传 `--pages 1,3-5 --dpi 200` 缩小范围。页码从 1 开始，输出按物理页序排列。CLI 拒绝覆盖已有 JSON 或同名证据图。每次复测使用新的输出位置；中途失败时已写出的 PNG 可保留供排查，完整成功以退出码 0 和可读取的 JSON 为准。

## 接入和引用原文

现有项目可在后台任务中用独立虚拟环境的 Python 执行上述命令，工作目录设为模块目录，输入输出传绝对路径，用退出码判断成功。也可以在该目录或已将该目录加入 Python 模块路径的程序中直接调用：

```python
from scanpdf import parse_pdf

result = parse_pdf("/data/pdf/1706.03762v1_img.pdf", pages="2-4", dpi=240)
for page in result["pages"]:
    for region in page["regions"]:
        citation = (result["source"]["sha256"], page["physical_page"], region["bbox_pt"])
        print(citation, region["type"], region["status"])
```

`source.sha256` 标识所读文件的字节，`physical_page` 标识 PDF 里的页面序号。词、行、表格、单元格及公式区域都有 `bbox` 和 `bbox_pt`。二者均为左上原点的 `[左, 上, 右, 下]`，右下为裁剪边界。前者单位为渲染像素，后者是**应用 CropBox 和页面旋转后**的可见页面坐标，单位是点（1/72 英寸）；页面宽高也按此视图给出。默认 PDF 底部原点的原始用户坐标需要另行转换。与现有 PDF 阅读器对接时，按可见页面宽高等比例缩放即可在同一旋转视图中叠加。可选 `region.crop` 是相对证据图目录的 PNG 路径。区域的 `id` 在本次输出内可定位，调整 DPI 或解析版本可能改变它。

| 输出 | 已完成的处理 | 使用时应注意 |
|---|---|---|
| `lines[].words[]` | Tesseract 实际输出的词、框和置信度，按 OCR 行分组 | 置信度是引擎分数。空页或未读出词的页面会保留页码并记为 `no_words` |
| 表格 `structure_recognized` | 从完整网格线，或横线与重复列间隙推断行列，输出从 1 开始的行列号、单元格框及 OCR 文本 | `empty_or_unread` 表示空白或未读出。对于合并格，`row_span` 和 `column_span` 标在左上角单元格，省略值为 1。整页 OCR 漏掉的有墨迹单元格会裁剪重读 |
| 表格 `partial_structure` | 已推断部分行列与合并范围，例如多组变体表 | 可按单元格回看原图。部分列名和数值含 OCR 错误，指标问答不会自动以此类表作数值比较 |
| 表格 `detected_only` | 有表格区域证据，但行列划分不可靠 | `cells` 为空。跨行跨列、弱线框、无框表格和倾斜扫描可能漏检或误分 |
| 公式 `transcribed` | 高置信 OCR 文本通过简单单行算式语法与基线几何检查，输出纯文本 | 限于简单线性算式，仍可能有字符错误 |
| 公式 `detected_only` | OCR 布局、独立公式编号或分式几何辅助定位 | `text` 为 null。原页的 OCR 行可能仍有误读；请展示区域图。复杂公式、上下标、矩阵与通用 LaTeX 重建需要另行识别 |

## 与 evidence-desk 的接入点

主项目的 `engine.parse_pdf(data, name)` 返回 `id`（原文件 SHA-256）以及 `textless_pages`（缺少可索引文字的物理页序号）。宿主可在导入任务中将这些页号用逗号连接，传给扫描模块的 `--pages`。列表为空时即可跳过 OCR。扫描输入应为同一份原始 PDF；保存侧车 JSON 时校验 `source.sha256 == doc["id"]`，再按 `physical_page` 关联到阅读器。原有 `ocr_local.py` 仍可用于生成带文字层的 PDF。本分支提供以下本地网页入口，支持按需识别、检索和回到原图核对。

安装上文依赖后，在**主仓库根目录**运行：

```sh
python scanpdf/workbench.py --data ./data --port 8765
```

在浏览器打开 `http://127.0.0.1:8765`，导入 PDF，点击文献旁的「识别扫描页」，选择物理页并点击「识别本页」。扫描页展示 OCR 词框和置信度、公式候选、表格单元格和原页位置。返回文库后，可在原有检索框中搜索已识别的文字，通过命中卡片打开原 PDF 或回到 OCR 定位。原生文字页沿用原有阅读器。可用 `--lang chi_sim+eng` 选择已安装的中文和英文语言包。页面解析采用 240 DPI，可选择单页，也可点击「识别整篇待处理页」连续识别。整篇模式逐页保存，跳过原生文字页和已处理页。可在当前页完成后暂停，重新打开仍可继续剩余页。扫描坐标及置信度压缩后与索引一起写入本地数据库；单个文库的扫描页数据上限为 12 MiB，原件和索引继续使用宿主原有限额。

主页的「证据追问」可先按选定文献检索，再在某条原文摘录中继续提问。每轮显示原文页和所用范围。更换文献会清空会话。「返回全文范围」可在同篇文献中改问其他段落。对 Adam 的两个 β 值，只在摘录含两个可辨认的赋值时显示读数。扫描文字缺失标记时会保留原页链接供核对。

下列对照可在 `scanpdf/` 下复现。主项目解析器将对照件的两页都列入 `textless_pages`。以 240 DPI 实测，扫描模块在物理第 1 页读到标题 “From question to passage”，第 2 页读到 “A test you can reproduce”，两行均返回原页位置。

```sh
trial=$(mktemp -d)
python tools/rasterize_proxy.py ../sample.pdf "$trial/sample-img.pdf" --dpi 150
python -m scanpdf "$trial/sample-img.pdf" --output "$trial/evidence.json" --dpi 240
```

## 复现与官方样本验收

```sh
python -m unittest discover -s tests -t . -v
# 原件放在主仓库 pdf/ 时自动纳入；其他路径可显式指定
SCANPDF_OFFICIAL_SAMPLE=/data/pdf/1706.03762v1_img.pdf python -m unittest -v tests.test_e2e.ScanPDFTests.test_supplied_official_sample_produces_locatable_ocr
```

常规测试生成三页无文字层 PDF，并通过真实 CLI 验证三页的文字、选页后的原页码、旋转 90° 且裁切过的页面定位、2×2 网格的四个单元格内容、编号公式状态和证据图。表格与公式的单元测试另外覆盖三线表、分隔线残缺、跨列歧义、上下堆叠的分式、正文干扰和低置信回退。在提供镜像样本时，同一套件还会验证镜像文件的七处参照框和一行真实表格数据。

官方原件验收会检查文件哈希、全部物理页、首页论文标题、两类区域是否出现及其证据图。该检查可以暴露回归，检测到候选区域仍需人工对照。用 CLI 保留完整输出后，按页核对原图中的表格和公式，记录漏检与误检；对识别成功的区域另核单元格行列、文字及公式字符。报告时保留源 SHA-256、DPI、语言、页码、位置和识别状态。15 页资料包镜像的来源、哈希、下载命令与逐页抽核结果见[镜像样本实测](SAMPLE_REVIEW.md)。

`tools/rasterize_proxy.py INPUT.pdf OUTPUT.pdf --dpi 150` 可从用户提供的电子版生成独立的纯图像对照件，并打印输入输出哈希。它生成的文件仅用于开发对照。赛题第 3.5 条里的版本去重和 500+ 页整书处理须由宿主系统端到端验收；本模块支持按页段调用，每次调用会在内存中汇总所选页的结构化结果。

### 复现交付包

在已签出的参赛分支、仓库根目录运行 `python scanpdf/delivery/package_review.py evidence-desk-review.zip`。脚本读取当前提交中已追踪的推荐 MP4、技术 PDF、讲稿、音色来源和双语样例，生成带逐文件 SHA-256 与源代码提交链接的新 ZIP。已有同名文件会保留。填写团队身份、评委访问及线上地址的位置见包内清单。
