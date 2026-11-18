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

## v0.1 历史验证界限（v0.2 更新见下）

页面 JavaScript 通过语法校验，实际 HTTP 请求通过。测试环境不允许 Chromium 创建启动所需的本机 socket，即使按权限流程重试亦未通过，因此尚无真实浏览器点击、手机布局验收或演示录像。DeepSeek 只完成离线协议测试与拒绝无授权调用的测试，不宣称已在线联调。OCR 通过真实本地 Tesseract 对原创双页图像 PDF 的测试；官方扫描件、541 页书籍未测。

## v0.2：541 页实物、引用方向与原页

从公开作者版文件 `UnderstandingDeepLearning_02_09_26_C.pdf`（22,344,992 字节，SHA-256 `f8237d393163900fa8e43210e680a3f987b45ccac7750b372e156fae3df0bf32`）启动临时本地 HTTP 服务，实测导入 541 页、6,890 个可检索片段。一次测量导入 2.18 秒，服务峰值常驻内存约 165 MiB；不是速度承诺。三条查询分别命中 PDF 第 440、476、541 页，印刷页码 426、462、527，均排第 1。末页命中是索引条目，不是正文论据。Poppler 对照定位；取回 PDF 与原始 SHA-256 相同。复跑命令：`python test_longdoc.py /path/to/UnderstandingDeepLearning_02_09_26_C.pdf`，需要 Linux 和 Poppler。不假定与赛方私发压缩包相同。

公开 BERT 论文（arXiv:1810.04805）与 Attention 的 v1/v7 两版一起建图：得到两个作品节点；BERT → Attention 的匹配依据是完整题名，BERT 第 11 页第 23 块可回看，已用独立 pdftotext 核对原页。120 条无本地匹配的参考条目没有生成边。此小样本不代表普适准确率。

## v0.2 最终离线回归

36 项 Python 单元及 HTTP 集成测试通过。用 Node VM 的模拟 DOM 执行真实界面事件处理函数，连接临时本地服务，验证导入、按篇结果、B→A 引文和第 2 页来源。Crossref 成功、失败及迟到响应使用受控模拟结果；未验证用户设备上的实时 Crossref。真实 Chromium 尝试启动时被环境拒绝创建 socket，未完成实际浏览器点击或布局验收。DeepSeek 未实联。以上均不代表整套资格或评分。
