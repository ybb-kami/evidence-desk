# 扫描公式的 CPU 转写草稿

默认扫描解析仍以区域和原图为证据。需要审阅 LaTeX 时，可以另跑 RapidLaTeXOCR CPU 模型。它读入原有 PNG 区域裁剪，另存 JSON，保留原解析结果。每条草稿记录源 PDF 哈希（文件顶层）、物理页、区域点坐标、证据图文件名及 SHA-256。推理前仅在 OCR 唯一定位右侧公式编号时裁去编号，并记录实际推理框。

`source_scale` 和 `upsampled_2x` 分别对原尺度和放大两倍的图像运行模型。`variants_agree` 表示两次 LaTeX 文本完全相同。`calibrated_probability` 始终为 null，两次一致也可能同时误读。每条模型输出均为 `unverified_candidate`，请对照原图检查字符、上下标和结构后引用。

两种尺度的原始 LaTeX 都会保留。若两路只对等号左侧的多字母变量名有一处字形分歧，且扫描 OCR 在等号左侧唯一支持其中一项，可以给出校对候选。对于两路都写成 `_{-}` 的位置，只有同一公式框内的文字 OCR 也读到对应的含下划线词，且每次出现都能在词图中找到低位细横笔画，才建议把它写作 `\_`。缺少对应证据的差异继续并排显示。核对页展示完整扫描裁图、每个有证据的词图以及笔画框。候选和差异仍使用 `unverified_candidate` 状态。

## 在工作台核对

识别扫描页后，点击左侧的公式区域，再点击页首的「公式核对」。核对页会继承文献、物理页和选中的公式区域。也可直接在核对页切换文献、物理页或区域。页面始终能查看所选区域的原图与页码。配置了本机模型时，点击「识别这一条公式」可对该区域运行 CPU 模型，结果显示为「未核验候选」。

按下文准备虚拟环境和权重，然后用以下变量启动工作台。两个路径均应为绝对路径。未设置变量时，页面显示安装指引。权重文件在推理前通过 SHA-256 校验。

```sh
export EVIDENCE_FORMULA_PYTHON=/absolute/path/to/formula-env/bin/python
export EVIDENCE_FORMULA_WEIGHTS=/absolute/path/to/weights
python workbench.py
```

## 更新后复测

输入为同一份 15 页纯图像扫描件，SHA-256 为 `64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d`。RapidLaTeXOCR 0.0.9 和 ONNX Runtime 1.32.0 在 CPU 上运行，裁图来自扫描页的 240 DPI 渲染。复测保留两种原始输出及可复查的扫描裁图。对应电子稿用于人工核对。第 4 页另选了两条实际印刷公式，在推理前按扫描页定位裁切，作为转写样例。

| 扫描原图 | 原始结果 | 校对及人工核图 |
|---|---|---|
| 第 3 页 (1) | 两路相同 | 保留转置上标、分式、平方根和 `d_k` 下标，未触发字形改动 |
| 第 5 页 (2) | 两路相同 | 保留 `max` 的参数结构、权重与偏置下标，未触发字形改动 |
| 第 7 页 (3) | 原尺度首字母为 `I`，两倍图为 `l`。两路均把三处字面下划线写成下标减号 | 同图文字 OCR 支持 `lrate`。两个 `step_num` 与一个 `warmup_steps` 各有低位横笔画。校对候选保留三处字面下划线以及原输出的全部指数和运算结构 |
| 第 7 页无编号注意力式 | 两路相同 | 分子转置、分母根号和括号结构可辨，函数名与左括号仍共用字体分组 |
| 第 4 页 MultiHead 式 | 两路相同 | 人工核图确认 `head_1`、省略号及 `head_h` 对应，保留末尾小写 `h` |
| 第 4 页 `head_i` 投影式 | 两路不同 | 原尺度保留左侧 `i` 下标但少一个 `t`。两倍放大保留 `Attention`，左侧 `i` 读成基线字母。保留两份模型草稿供核对 |

第 7 页 (3) 的三处笔画在 240 DPI 页图的半开像素框分别为 `[888,969,904,971]`、`[1104,969,1120,971]`、`[1335,969,1352,971]`。`lrate` 对应 OCR 框为 `[544,943,608,967]`。该条两尺度一致性依然为 `false`，增强候选由扫描图证据逐处核对。裁紧空白和 1.5 倍放大均保留原始下划线误读，二值化还曾引入 `min` 字形误读，因此这次仍保留现有的双尺度原图输入。第 4 页两条另选样例只检查转写，未通过工作台自动定位。以上是六条抽核结果，未估计通用召回率或准确率。

## 更新前镜像基线

使用[镜像样本](SAMPLE_REVIEW.md)的物理第 3、5、7 页，以 240 DPI 裁剪，再使用 RapidLaTeXOCR 0.0.9、ONNX Runtime 1.32.0 的 CPU 后端推理。逐条在原图检查字符及结构，并对照官方 [arXiv v1](https://arxiv.org/pdf/1706.03762v1) 的排版。官方电子稿只用于核对，模型输入为扫描图。

| 原图 | 两种尺度一致 | 人工核对 |
|---|---|---|
| 第 3 页公式 (1) | 是 | 转置上标、分式、平方根及根号内的下标均正确 |
| 第 5 页公式 (2) | 是 | 两组权重和偏置的下标、变量、加号及 max 的参数结构均正确 |
| 第 7 页公式 (3) | 否 | 指数及主要运算结构可辨。首字母在大写 I 和小写 l 间变化，两条草稿都把变量名中的字面下划线写成了下标减号 |
| 第 7 页无编号注意力式 | 是 | 分子转置、无下标的根号分母和嵌套括号保留，两个函数名和左括号进入了同一个字体分组 |

本次抽核的三条编号式有两条字符及结构符合原图。样本量为三条，结果仅说明该次试验的输出，无法估计通用准确率。复杂式的默认解析状态仍保留 `detected_only`。

## 复现

在 `scanpdf/` 运行，主扫描环境需按 [README](README.md) 安装。公式候选模型使用单独的 Python 3.12 虚拟环境，推理时读本地权重和裁剪图。首次下载约 179 MB 权重。项目不附带模型文件。[上游模型发布页](https://github.com/RapidAI/RapidLaTeXOCR/releases/tag/v0.0.0) · [PyPI 包](https://pypi.org/project/rapid-latex-ocr/0.0.9/)

```sh
set -eu
pdf=/path/to/1706.03762v1_img.pdf
trial=$(mktemp -d)
python -m scanpdf "$pdf" --pages 3,4,5,7 --dpi 240 --lang eng \
  --output "$trial/result.json" --crops "$trial/crops"

python3.12 -m venv "$trial/formula-env"
"$trial/formula-env/bin/pip" install rapid-latex-ocr==0.0.9 onnxruntime==1.32.0 requests
mkdir "$trial/weights"
release=https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0
for file in image_resizer.onnx encoder.onnx decoder.onnx tokenizer.json; do
  curl -fL "$release/$file" -o "$trial/weights/$file"
done
(
  cd "$trial/weights"
  sha256sum -c <<'SUMS'
e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d  image_resizer.onnx
01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d  encoder.onnx
bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072  decoder.onnx
1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019  tokenizer.json
SUMS
)
"$trial/formula-env/bin/python" tools/check_formula_quality.py "$pdf" "$trial/result.json" \
  --crops "$trial/crops" --weights "$trial/weights" --output "$trial/formula-audit"
python -m unittest tests.test_formula_candidates tests.test_formula_symbols tests.test_formula_review
```

复测目录包含候选 JSON 和对应扫描裁图。第 4 页补抽图在 JSON 中标记为 `manual_crop`。
