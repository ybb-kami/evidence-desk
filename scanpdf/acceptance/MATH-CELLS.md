# 扫描表格的数学式复核

表格结构和内容读数分别判断。单元格直接呈现需上下标或运算位置的渐近记号、根式，或单变量渐近记号的 OCR 置信度偏低时，标记 `math_unverified`。表格级结构状态、原 OCR 字符串与置信度、文件身份、物理页及单元格坐标均可回查。界面下拉框显示“数学式需核对原图”，选中后原图高亮，另列“原始 OCR（未核验）”。此前缓存的表在读取时获得同样标记，原数据库及既有引文坐标保留。

## 原件与独立栅格

固定公告扫描件 `1706.03762v1_img.pdf`，SHA-256 为 `64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d`。第 6 物理页表 1 仍有 5 行 4 列。整页 Tesseract 把首个复杂式 `O(n²·d)` 读成 `O(n? - d)`，原置信度为 87.05。对同格裁图按原尺寸、两倍及三倍重读仍给出同一错误字符串。新的扫描页标记 8 格，包括 6 个复合记号和 2 个低置信简单读数。Chromium 实际打开第 2 行第 2 列，核对原页 SHA 和高亮框 `[233.7, 138.6, 333.9, 150.6]` 点。原值只出现在未核验的明细中。

另建一页无文字层的三列运行时表。真实 OCR 给出的 `O(k - f)` 被标记为需核对，同表简单 `O(t)` 和实测样式的数值 `14.6` 保持现有读数。原公告表 2 的 31/31、表 3 的 57/57 结果、44/44 设置和 127/127 空位以及表 4 的 11/11 分数保持。该规则需要 OCR 先辨出相应记号，无法发现所有漏读符号，也没有通用数学式准确率。[浏览器、独立栅格和数值回执](results/math-table-scan.json)

## 可选 CPU 候选

复制原页同格像素并保留裁片 SHA 元数据，再用已校验权重的 RapidLaTeXOCR 在两种尺度生成独立 JSON 草稿。本次 8 格的两尺度字符串一致，人工对原图可确认平方、乘点及对数底的变化。两次运行使用同一公式模型，没有校准概率。输出始终保留未核验状态，工作台默认仍提示核对原图。权重与运行时来自既有公式模块。[四件权重 SHA 和依赖](../FORMULA_REVIEW.md)

```sh
pdf=/path/to/1706.03762v1_img.pdf
audit_dir=$(mktemp -d)
python scanpdf/acceptance/math_cells_browser.py "$pdf" --chromium /usr/bin/chromium
PYTHONPATH=scanpdf python -m scanpdf "$pdf" --pages 6 --output "$audit_dir/page6.json"
/path/to/formula-env/bin/python scanpdf/tools/math_table_candidates.py \
  "$pdf" "$audit_dir/page6.json" --page 6 --weights /path/to/weights --output "$audit_dir/candidates"
```
