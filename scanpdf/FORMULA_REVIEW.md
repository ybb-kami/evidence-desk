# 扫描公式的 CPU 转写草稿

默认扫描解析仍以区域和原图为证据。需要审阅 LaTeX 时，可以另跑 RapidLaTeXOCR CPU 模型。它读入原有 PNG 区域裁剪，另存 JSON，保留原解析结果。每条草稿记录源 PDF 哈希（文件顶层）、物理页、区域点坐标、证据图文件名及 SHA-256。推理前仅在 OCR 唯一定位右侧公式编号时裁去编号，并记录实际推理框。

`source_scale` 和 `upsampled_2x` 分别对原尺度和放大两倍的图像运行模型。`variants_agree` 表示两次 LaTeX 文本完全相同。`calibrated_probability` 始终为 null，两次一致也可能同时误读。每条模型输出均为 `unverified_candidate`，请对照原图检查字符、上下标和结构后引用。

## 在工作台核对

识别扫描页后，点击左侧的公式区域，再点击页首的「公式核对」。核对页会继承文献、物理页和选中的公式区域。也可直接在核对页切换文献、物理页或区域。页面始终能查看所选区域的原图与页码。配置了本机模型时，点击「识别这一条公式」可对该区域运行 CPU 模型，结果显示为「未核验候选」。

按下文准备虚拟环境和权重，然后用以下变量启动工作台。两个路径均应为绝对路径。未设置变量时，页面显示安装指引。权重文件在推理前通过 SHA-256 校验。

```sh
export EVIDENCE_FORMULA_PYTHON=/absolute/path/to/formula-env/bin/python
export EVIDENCE_FORMULA_WEIGHTS=/absolute/path/to/weights
python workbench.py
```

## 镜像实测

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
python -m scanpdf "$pdf" --pages 3,5,7 --dpi 240 --lang eng \
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
"$trial/formula-env/bin/python" tools/formula_candidates.py "$trial/result.json" \
  --crops "$trial/crops" --weights "$trial/weights" --output "$trial/formula-candidates.json"
python -m unittest tests.test_formula_candidates
```
