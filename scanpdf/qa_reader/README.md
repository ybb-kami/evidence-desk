# 本地 MobileBERT 原文问答候选

使用 TensorFlow 发布的 Apache 2.0 MobileBERT Question and Answerer。模型文件来自普通 Git 对象，固定到 `startlinglabs/ai-models` 提交 `e3497f95667121989884ecba69a4a46899dbc4ef`，大小 100,670,436 字节。模型及内嵌词表的 SHA‑256 均与 TensorFlow Lite Support 发布的 `mobilebert_with_metadata`、`mobilebert_vocab` 一致。Google Research 公布的可转 TFLite 问答 SavedModel 使用 SQuAD v1.1。本模型没有已核实的 SQuAD v2.0 拒答能力。[TensorFlow 固定哈希](https://github.com/tensorflow/tflite-support/blob/3a2efe787f41020a49871176d91aa3fc731c86bd/workspace9.bzl#L18) · [权重许可](https://github.com/tensorflow/tflite-support/blob/3a2efe787f41020a49871176d91aa3fc731c86bd/tensorflow_lite_support/cc/test/testdata/task/text/mobilebert_with_metadata.json#L67) · [Google Research 发布记录](https://github.com/google-research/google-research/blob/7117df5f368e1301e34bd014be9c93e24a4e73c6/mobilebert/README.md#L19) · [固定镜像](https://github.com/startlinglabs/ai-models/blob/e3497f95667121989884ecba69a4a46899dbc4ef/models/mobilebert.tflite)

```text
模型  e79d3c70108bbdee02da657b679349cab46dbb859a05b599c76b53d98e82f272
词表  07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3
```

从仓库根目录安装并复测。安装器可以用 `--source /path/to/mobilebert.tflite` 核验本地文件。模型权重存放在调用者选择的位置，推理只读取本地文件。

```sh
python -m pip install -r scanpdf/qa_reader/requirements.txt
python -m scanpdf.qa_reader.install /path/to/reader-model
python -m scanpdf.qa_reader.smoke /path/to/reader-model/mobilebert_qa.tflite
```

`SpanReader(model_path, threads=2, max_hits=4).read(question_en, hits, limit=4)` 可以跨请求复用，调用底层 CPU Interpreter 时串行。英文问题最多 64 个 WordPiece，每个候选片段最多推理两个重叠窗口。每个窗口使用 `input_ids`、`input_mask`、`segment_ids` 三个 `[1,384]` 的 `int32` 输入，读取 `start_logits`、`end_logits` 两个 `[1,384]` 的 `float32` 输出。函数返回原文候选、所属检索片段下标、物理页和块号，以及候选和上下文在该片段内的字符起止位置，结尾为开区间。上下文取自片段内包含候选的原句，受 PDF 文本块边界约束。

| 复测情形 | 实际输出 |
| --- | --- |
| 已给出周五读数和冷柜样本量 | 分别抽出 `37` 和 `58`，字符区间均回读原句。 |
| 未给出市长身份或电池价格 | 仍提出 `Adaria` 和 `two batteries made by Adaria in Wekalo`。相对 `[CLS]` 的分差约 `+18.52` 和 `+11.42`。 |
| 两份片段对同一船只给出不同船长 | 保留两条候选 `Hali Mora`、`Nera Sol` 和各自页码，分差约 `+39.51` 与 `+36.61`。 |

`span_score` 为候选的 start/end logit 之和，`null_score` 为同窗 `[CLS]` 位置的对应分数，`span_over_null` 为两者的差。分差未经校准，不能单独用于判定回答成立。调用方应保留原句和物理页，显示冲突候选，使用独立的证据规则确认问题所问实体、关系和答案类型是否由同一来源支持。规则无法确认时，输出待核实片段或保留无答案状态。上述合成用例只复测 CPU 推理、来源定位及已知失败方式。
