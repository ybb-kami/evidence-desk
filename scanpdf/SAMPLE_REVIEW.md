# 1706.03762v1_img.pdf 镜像样本实测

### 文件身份

ResearchTrace 的公开仓库包含 `科研小助手.rar` 的解包目录，维护者注明由用户提供，内有赛题说明和 39 份测试文献。固定提交中，完整目录和核心样本目录下的扫描件指向同一个 Git blob。下载得到 4,672,118 字节，SHA-256 为 `64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d`，与镜像清单一致。PyMuPDF 检查显示全书 15 页，每页一幅图像，均无可提取文字。博学谷及赛氪页面在本环境返回代理 403，未能用组委会直接发布的哈希作独立认证。其他仓库出现过字节不同的同名文件，复测须核对哈希。[资料包目录](https://github.com/CuiMiles/ResearchTrace/tree/52d11950da4f3da1dcb830d6212665eb89c0394f/evals/pdfs/科研小助手) · [镜像清单](https://github.com/CuiMiles/ResearchTrace/blob/52d11950da4f3da1dcb830d6212665eb89c0394f/evals/pdfs/core/manifest.json)

### 实测结果

使用 240 DPI、Tesseract 5.5.0 英文模型运行 CLI，本次全页解析用时 27.7 秒。15 页均有 OCR 输出。人工核对的可追溯文字包括第 1 页论文标题和第 7 页训练硬件 “8 NVIDIA P100 GPUs”。表中的 IoU 为输出框和人工核对的参照框的交并比，表格框覆盖表体，编号公式框包含公式及其右侧编号。阈值取 0.5。这些样本也用于调试算法，结果仅适用于这一文件和设置。

| 物理页 | 原图对象 | 输出状态 | 核对结果 | IoU |
|---|---|---|---|---|
| 6 | 表 1，层类型和复杂度 | structure_recognized | 5 行 × 4 列，保留两行文字的表头。部分复杂度表达式的上下标及符号有 OCR 错字 | 0.991 |
| 8 | 表 2，BLEU 与训练成本 | detected_only | 定位完整表体，保留证据图，单元格列表为空 | 0.989 |
| 9 | 表 3，Transformer 变体 | detected_only | 定位完整表体，未输出跨行分组结构 | 0.999 |
| 9 | 表 4，句法分析结果 | structure_recognized | 12 行 × 3 列，11 个数据行的评分均对齐。末行为 Dyer et al. (2016)、generative、93.3 | 0.997 |
| 3 | 编号公式 (1) | detected_only | 含分式的注意力公式与编号定位 | 0.970 |
| 5 | 编号公式 (2) | detected_only | 前馈网络公式与编号定位 | 0.899 |
| 7 | 编号公式 (3) | detected_only | 学习率公式与编号定位 | 0.856 |

此外，第 7 页无编号的注意力公式也返回区域。全页共输出 4 个表格区域、4 个公式区域；这 4 条公式的转写字段均为 null。正文 OCR、表格内容及行内数学式缺少逐字符人工标注，尚无全文准确率或全部公式召回率。

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
