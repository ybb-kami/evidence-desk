# 本地多语种语义检索

使用 `intfloat/multilingual-e5-small` 的 int8 ONNX 权重，在 CPU 上分别编码检索问题和 PDF 原文段落。检索结果保留文献 SHA-256、物理页、页标签、块、段、原文和 OCR 来源。余弦分数衡量查询与段落的相似度，回答环节仍需核对命中段落能否支持结论。

在仓库根目录为运行工作台的 Python 安装依赖并下载模型。

```sh
python -m pip install -r scanpdf/semantic/requirements.txt
python -m scanpdf.semantic.install
```

模型和分词器合计约 129 MiB。安装命令通过 GitHub Git LFS 读取公开文件，根据固定对象的大小和 SHA-256 校验后原子写入 `.cache/semantic/`，无需账号或密钥。离线安装时，将已下载的 `model_quantized.onnx` 和 `tokenizer.json` 放到同一目录，然后执行 `python -m scanpdf.semantic.install --files /path/to/files`。可设置 `EVIDENCE_DENSE_MODEL_DIR` 共用已校验的模型目录。

检索过程在本机完成。首次检索加载并校验权重。首次请求涉及超过 750 个尚未编码的段落时，后台建立索引，请稍后重试同一范围。每次最多检索 12,000 段，进程内向量缓存限额 64 MiB。缓存由文件 SHA-256 和有序段落内容标识，新的 OCR 文本触发对应缓存重建。编码按模型约定使用 `query:` 和 `passage:` 前缀，最多使用每段前 512 个模型词元，返回的证据沿用 PDF 原始分段。

单篇文献达到 32 个有充分字母或文字内容的段落时，稠密通路使用这些段落，减少页边标记、图例碎片和数值行对正文语义排名的影响。每篇文献对相同原文去重。选定范围达到 32 段时，先按选定范围的向量均值对查询和段落做中心化，再计算余弦。关键词和表格检索仍可找到原有的短文本及数值证据。

`DenseIndex.score_texts(question, texts)` 可在前排证据中进一步比较最多 32 条原文摘录，每条最多 800 字。它复用同一 CPU 模型，按输入顺序返回余弦分数。

运行 `python -m unittest scanpdf.semantic.test_index -v` 检查缺失模型、OCR 更新、查询范围、后台索引和离线中文问题检索英文 PDF。安装权重与依赖后会运行真实模型测试。

源模型采用 MIT 许可。量化文件由 Xenova 转换，安装命令使用保留原模型标注的 GitHub 镜像固定提交。 [原模型](https://huggingface.co/intfloat/multilingual-e5-small) · [ONNX 导出](https://huggingface.co/Xenova/multilingual-e5-small) · [下载源](https://github.com/66lizhenhua66/multilingual-e5-small/tree/761b726dd34fb83930e26aab4e9ac3899aa1fa78)
