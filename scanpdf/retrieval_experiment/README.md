# 同页扩展检索实验

**两种同页扩展都未提高最终摘录的完整率，保留现有检索排序。** 三组均召回 12 道题的证据页。页级融合让更多支持文字进入候选，但最后的摘录选择没有留下相应文字。实验针对单篇检索与上下文选择组件，覆盖 3 份实际论文、12 道题、26 条逐字支持及 8 条条件干扰标注。预先规则和集成门槛见 [实验协议](PROTOCOL.md)。

| 指标 | 原排序 | 同页邻块 | 页级融合 |
| --- | ---: | ---: | ---: |
| 最终上下文包含全部支持 | 3/12 | 3/12 | 3/12 |
| 同页多块题最终上下文包含全部支持 | 2/7 | 2/7 | 2/7 |
| 最终上下文包含的逐条支持 | 11/26 | 10/26 | 10/26 |
| 8 块候选包含全部支持 | 8/12 | 8/12 | 9/12 |
| 8 块候选包含的逐条支持 | 18/26 | 18/26 | 22/26 |
| 前 5 不重复物理页包含证据页 | 12/12 | 12/12 | 12/12 |
| 入选的预标条件干扰 | 2/8 | 2/8 | 2/8 |
| 非金标物理页的最终摘录 | 40/96 | 40/96 | 40/96 |
| 每题最终字符数中位数 | 1088.5 | 1088.5 | 1095.5 |

六道中文题均使用了本机中译英扩展，三组均没有在最终上下文中保留全部支持的中文题。三道最终证据完整的英文题为 S3、D4 和 R1。每题都用了 8 个摘录槽，单题字符数最多 1411，低于 2600 字符上限。重复摘取同一块的句子和跨块证据的竞争都值得单独检查。

- D2 的同页邻块找回改进 DDPM 物理页 4 块 37 的 `0.999`，最终只保留同页块 38 的调度曲线文字。
- 页级融合为 D3 和 R4 找回了缺失块，分别为改进 DDPM 物理页 5 块 46 和 SAC 物理页 6 块 54。两个新块没有贡献预标支持给最终上下文。
- D1 的基线候选含改进 DDPM 物理页 3 块 6 的双数值对照，两种扩展都将其挤出候选。当前的候选尾部替换会牺牲某些条件精确匹配。

2026-11-18，基线组件版本为 `49fb098`，PyMuPDF 1.26.6。使用 `multilingual-e5-small` CPU ONNX 与现有 BM25、词面回退和本机中英翻译。12 题都有真实 E5，11 题另使用严格 BM25 或其扩展分支，D1 单独使用 E5。一次运行的初始检索中位耗时 192 ms，同页邻块与页级融合的额外选块分别为 0.47 ms 和 1.60 ms。提前对三份文献构建向量各用 4.59 秒、1.91 秒和 2.70 秒。每题的候选页序、块、摘录区间与缺证见 [实测明细](results.json)。

## 复跑

先准备三份 PMLR 正式原件。可从 `questions.json` 的 `publisher` 或对应出版方 GitHub Pages 仓库获取，脚本对每份 PDF 校验 SHA-256。PDF 与模型权重均位于独立数据目录。

```bash
pdf_dir="$(mktemp -d /tmp/evidence-page-experiment.XXXXXXXX)"
curl -fLsS https://raw.githubusercontent.com/mlresearch/v119/gh-pages/chen20j/chen20j.pdf -o "$pdf_dir/simclr.pdf"
curl -fLsS https://raw.githubusercontent.com/mlresearch/v139/gh-pages/nichol21a/nichol21a.pdf -o "$pdf_dir/diffusion.pdf"
curl -fLsS https://raw.githubusercontent.com/mlresearch/v80/gh-pages/haarnoja18b/haarnoja18b.pdf -o "$pdf_dir/sac.pdf"
PYTHONPATH=.:scanpdf EVIDENCE_TRANSLATION_MODELS=/path/to/local/translation/models \
  python -m retrieval_experiment.run --pdfs "$pdf_dir" --expect-translation
```

复跑需要可用的本机 E5 权重与中译英模型。脚本会新建临时向量缓存，核对原件哈希以及所有原文字串和物理页块。数据准备完成后，评测使用本机推理和本地 PDF。模型安装方式见 [语义检索](../semantic/README.md) 和 [双语精读](../bilingual/README.md)。
