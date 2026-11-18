# 题面原问的证据检索核验

在仓库根目录运行以下命令。省略 `--show-hits` 可只看命中页块、排名和核验状态。

```bash
python scanpdf/acceptance/official_questions.py /workspace/shared/contest-pdf --show-hits
```

脚本直接把题面 1.2.1 的五个原问和 1.2.2 的第一个原问交给检索引擎。仅拼接题面换行，将 Adam 问题中分排的下标还原为 β₁、β₂，并移除该题关于省略论文名的旁注。五个单篇问题分别选定 Attention v1 和 v7，BERT 对比分别选择 v1+BERT 和 v7+BERT，共 12 个题目实例。BERT 使用资料库中的 ACL 论文 `N19-1423.pdf`。脚本输出三个 PDF 的 SHA-256。

`complete` 表示返回结果取齐规定原文，`partial` 表示只取到部分规定原文，`missing` 表示规定原文均未返回。每一项同时核对 PDF 身份、物理页、文本块、逐字来源和语义上下文。未满足规则的返回结果仍列在 `hits` 中。本次核验覆盖检索证据，生成答案、网页交互和未选定论文时的自动定位需要另行核验。

| 原问 | 必需的原页证据 | Attention v1 页:块 | Attention v7 页:块 |
| --- | --- | --- | --- |
| 第 6 作者及机构 | 按原页署名顺序，第六位是 Aidan N. Gomez，署名机构为 University of Toronto。† 脚注注明研究在 Google Brain 任职期间完成，另记为补充信息。 | 1:7（† 1:15） | 1:8（† 1:13） |
| Adam 超参数 | 同一 Adam 优化器段落明确 β₁ = 0.9 和 β₂ = 0.98。 | 7:5 | 7:11 |
| Base GPU | 训练段落将 Base 训练和 8 块 NVIDIA P100 GPU 联系起来。 | 7:3 | 7:9 |
| 英德 BLEU | §6.1 说明 Transformer (big) 得 28.4 且领先先前最佳超过 2.0。表 2 的英德语境和 ConvS2S Ensemble 行给出先前最佳 26.36，可计算差值 2.04。 | 7:19、8:1、8:9 | 8:16、8:1、8:9 |
| Layer Normalization | 参考文献条目列出 Jimmy Lei Ba、Jamie Ryan Kiros、Geoffrey E Hinton，年份为 2016。 | 10:7 | 10:13 |
| BERT 与原始 Transformer 对比 | Attention 给出独立的 Encoder 和 Decoder。BERT 在第 3 页第 26 块明确其多层双向 Transformer Encoder 基于 Vaswani 等人的实现。结合两篇原文可判断 BERT 架构省去了原始 Transformer 的独立 Decoder。 | 2:12、2:13 | 3:4、3:5 |

基线提交 `c90fa1b` 的检索引擎在 12 个实例上均未返回证据。可将该提交导出到临时目录，复制本核验脚本后执行同一命令。

本次改动后复跑的结果为 **8 个证据齐全、0 个部分命中、4 个缺失**。第 1、2、3、5 问在两个版本上均取齐必需证据，且对应原页块均排第一。第 1 问的 † 补充脚注没有返回。第 4 问与第 6 问在两个版本上均未返回结果，BERT 一侧也未返回。以上是对已知题目和固定 PDF 的定向回放，另外三道 GPT 多篇原问未纳入。该结果只衡量本次证据检索，无法估计盲测准确率。
