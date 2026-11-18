# 离线验收记录

## 项目自带样例

`python -m unittest -v test_acceptance`：3 项通过。验证了 PDF 导入、页码和原件字节、重启后持久化、改名重复识别，以及损坏 PDF 拒收。样例查询 `verify page numbering` 可以找到物理第 2 页关于文件内页序与印刷页码的区别。

## 独立文档

在临时数据库和本机回环服务中，导入了 TeX Live 自带的 B. Jackowski、J. M. Nowacki 的《Latin Modern Family of Fonts: The Technical Documentation》。本机文件为 `/usr/share/doc/fonts-lmodern/lm-info.pdf`，上游包清单列出 `doc/fonts/lm/lm-info.pdf`。文件未复制到项目，也未上传。实测文件 SHA-256：

```
5907f59e368762a3a2858a6826aab019d0accb367f1b8cc6062d472635579fe6
```

- 导入 26 个物理页，索引 2,822 个文本块。
- 查询 `more than 700 glyphs`，限定于这份 PDF，返回 1 条命中：物理第 3 页，第 3 块。“Each of the lm text fonts contains more than 700 glyphs.” 已用独立的 `pdftotext -f 3 -l 3 -layout` 核对该句，并核对了服务返回的 PDF 字节与原件一致。
- 查询 `quasarzxqvvnobaselocal` 返回 0 条，不生成替代答案。
- 限制：同一结果的相邻文本保留排版断行（例如 `Viet` 与 `namese` 分行）。命中适合定位；正式引文应打开原页复核。该验证只覆盖本地文本检索，不代表 OCR 或语义检索能力。

## 真实论文的版本与证据

输入是 Vaswani 等人《Attention Is All You Need》的官方 arXiv [v7](https://arxiv.org/pdf/1706.03762v7) 和 [v1](https://arxiv.org/pdf/1706.03762v1)。验收使用临时本地数据库，不随代码重分发 PDF。

- 先以 `Attention-current.pdf` 导入 v7，再以 `renamed-early-draft.pdf` 导入 v1。两者文件名和字节均不同。系统报告同一 arXiv 标识 `1706.03762`、文本相似度 0.720，建议核对后引用较新的 v7；两个文件均保留、可分别检索，返回的源 PDF 字节各自与官方输入一致。
- 分别限定 v7、v1 并查询 `NVIDIA P100 GPUs` 或 `Adam optimizer`。两版的物理第 7 页都找到训练使用一台配有 8 块 NVIDIA P100 GPU 的机器，以及 Adam 优化器。用 `pdftotext -f 7 -l 7 -layout` 独立核对两版。优化器的参考文献编号在 v7 是 [20]，在 v1 是 [18]；报告应注明版本。
- 仅输入“用了什么训练硬件和优化器”或无关词 `tungstenorbitunicornzxq`，两版分别返回 0 条。这说明离线检索依赖原文关键词，不会自动翻译中文查询或补写答案；同 arXiv 标识的版本提示也不等于能识别所有论文版本。
