# 1706.03762v1_img.pdf 镜像样本实测

### 文件身份

ResearchTrace 的公开仓库包含 `科研小助手.rar` 的解包目录，维护者注明由用户提供，内有赛题说明和 39 份测试文献。固定提交中，完整目录和核心样本目录下的扫描件指向同一个 Git blob。下载得到 4,672,118 字节，SHA-256 为 `64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d`，与镜像清单一致。PyMuPDF 检查显示全书 15 页，每页一幅图像，均无可提取文字。博学谷及赛氪页面在本环境返回代理 403，未能用组委会直接发布的哈希作独立认证。其他仓库出现过字节不同的同名文件，复测须核对哈希。[资料包目录](https://github.com/CuiMiles/ResearchTrace/tree/52d11950da4f3da1dcb830d6212665eb89c0394f/evals/pdfs/科研小助手) · [镜像清单](https://github.com/CuiMiles/ResearchTrace/blob/52d11950da4f3da1dcb830d6212665eb89c0394f/evals/pdfs/core/manifest.json)

### 实测结果

使用 PyMuPDF 1.26.6、240 DPI 和 Tesseract 5.5.0 英文模型解析扫描文件。15 页均有整页 OCR 输出。人工核对的可追溯文字包括第 1 页论文标题和第 7 页训练硬件 “8 NVIDIA P100 GPUs”。表中的 IoU 为输出框和参照框的交并比，表格框覆盖表体，编号公式框包含公式及其右侧编号。定位阈值为 0.5。官方文件用于开发与回归检查。

| 物理页 | 原图对象 | 输出状态 | 核对结果 | IoU |
|---|---|---|---|---|
| 6 | 表 1，层类型和复杂度 | structure_recognized | 5 行 × 4 列，保留两行文字的表头。部分复杂度表达式的上下标及符号有 OCR 错字 | 0.991 |
| 8 | 表 2，BLEU 与训练成本 | structure_recognized | 两层表头、5 个末级列、10 个数据行。冻结的 17 个 BLEU、14 个科学记数法单元格全部与原表相符。基线直接解析为 16/31 | 0.989 |
| 9 | 表 3，Transformer 变体 | partial_structure | 20 行 × 13 列，保留 A–D 四个跨行标签范围。右侧 57 个结果由 43/57 提升为 57/57，左侧设置由 43/44 提升为 44/44，127 个原表空位均保留。部分数学表头仍需对照原图 | 0.999 |
| 9 | 表 4，句法分析结果 | structure_recognized | 12 行 × 3 列，11 个评分均与原表相符。末行为 Dyer et al. (2016)、generative、93.3 | 0.997 |
| 3 | 编号公式 (1) | detected_only | 含分式的注意力公式与编号定位 | 0.970 |
| 5 | 编号公式 (2) | detected_only | 前馈网络公式与编号定位 | 0.899 |
| 7 | 编号公式 (3) | detected_only | 学习率公式与编号定位 | 0.856 |

此外，第 7 页无编号的注意力公式也返回区域。全页共输出 4 个表格区域、4 个公式区域；默认解析的 4 条公式转写字段均为 null。可选本地模型的候选另存，见[公式核对](FORMULA_REVIEW.md)。正文 OCR、表格内容及行内数学式缺少逐字符人工标注，尚无全文准确率或全部公式召回率。

### 冻结数值验收和独立页面

在修改解析器前固定输入、数值参考点和计分方法，运行基线 `5203688cf7d8b1f3340b63eeada92167855455bc`。数值参考点取自发布版的文字层并核对版面。正式扫描件以 4,672,118 字节的图像版原件为输入。两个独立论文页在 220 DPI 下光栅化，另存为仅含一张图像的 PDF，再由同一解析器以 240 DPI 读取。冻结脚本检查原件和光栅像素的 SHA-256。[冻结参考](acceptance/numeric_scan_gold.json) · [基线与改进结果](acceptance/results/numeric-scan.json)

数值检查要求唯一单元格框覆盖参考点，且每个实值单元格只覆盖一个数值参考点。对比前仅统一 Unicode 形式、空白和等价乘号。漏读、串格、原空位错误填值分别计数。首个独立样本的形状在改动解析器前按出版页图复核，500M 和 1.3B 是首列内的两条独立标签行，正确形状为 8 行 × 10 列。数值与计分规则沿用原冻结定义。

| 独立光栅页 | 基线 | 改进后 |
|---|---|---|
| 2406.14491v2，物理第 5 页表 1 | 8 行 × 10 列，45/45 | 8 行 × 10 列，45/45，IoU 0.996 |
| 2206.07682v2，物理第 26 页表 2 | 37 行 × 3 列，101/108 | 37 行 × 4 列，107/108，IoU 0.998。36 个 E 格式科学记数法均准确。唯一严格文本差异来自原表写作 `[sic] 199B` 的注记 |

### 数值证据和已有扫描库

数值列依据重复的数值形态确认。解析器依次采用两种裁切图重读，结果一致且达到置信阈值时写入数值。科学记数法的上标候选须由原图确认印刷位置，再分别识别尾数、底数与指数。单元格保存整页初读 `ocr_raw`、OCR 置信度、裁切框和各次尝试 `numeric_ocr`。高置信度的初读可在无相反读数时保留。无法确认时输出 `numeric_unresolved`，原图证据仍可查阅。

工作台区域修订号为 2。已识别页面通过「更新本页区域」重新计算表格，保留原有 OCR 行、词、渲染坐标和文库索引。原先由单元格裁切生成的引文随页保存初次文字与原矩形。用已有扫描库实测第 8、9 页从修订号 1 更新为 2。第 9 页原先可定位的 41 条裁切引文在刷新、重启和重复请求后均高亮同一位置。英德 BLEU 题面仍得到 28.4、既有最高 26.36 与差值 2.04。

复杂表头、低质量或缺少分隔线的表格仍可能只定位表区或保留部分结构。OCR 置信数值用于筛选裁切结果。以上计数来自固定范围的实值与空白，无法估计开放域表格准确率。

### 复现

先按 README 安装依赖。在 `scanpdf/` 下执行以下命令会从固定版本下载样本，核验哈希，运行真实 OCR，再对参照框逐项评分。下载的 PDF 和解析产物保存在临时目录内。

```sh
set -eu
run_dir=$(mktemp -d)
pdf="$run_dir/1706.03762v1_img.pdf"
curl -fL 'https://raw.githubusercontent.com/CuiMiles/ResearchTrace/52d11950da4f3da1dcb830d6212665eb89c0394f/evals/pdfs/core/1706.03762v1_img.pdf' -o "$pdf"
printf '%s  %s\n' 64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d "$pdf" | sha256sum -c -
python -m scanpdf "$pdf" --dpi 240 --lang eng --output "$run_dir/result.json" --crops "$run_dir/crops"
python tools/review_1706_scan.py "$run_dir/result.json"
SCANPDF_OFFICIAL_SAMPLE="$pdf" python -m unittest discover -s tests -t . -v
```

把扫描件和两篇独立论文原件放在同一目录，从仓库根目录执行下面的命令。验收脚本保存扫描证据 JSON、原图区块 PNG 及数值对照摘要。

```sh
set -eu
pdf_dir=/path/to/contest-pdf
run_dir=$(mktemp -d)
python scanpdf/acceptance/numeric_scan.py --pdf-dir "$pdf_dir" --output "$run_dir/current"
mkdir "$run_dir/baseline-code"
git archive 5203688cf7d8b1f3340b63eeada92167855455bc scanpdf/scanpdf | tar -x -C "$run_dir/baseline-code"
python scanpdf/acceptance/numeric_scan.py --pdf-dir "$pdf_dir" --output "$run_dir/baseline" \
  --parser-dir "$run_dir/baseline-code/scanpdf"
```
