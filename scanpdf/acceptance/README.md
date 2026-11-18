# 赛题资料包批量验收

在仓库根目录运行以下命令。输入为本地解开的 `科研小助手/pdf/`，来源和固定版本见[扫描样本记录](../SAMPLE_REVIEW.md)。测试通过本机 HTTP 操作单独的临时 SQLite 文库，无需部署或模型密钥。

```sh
# 已有文件会重新核验哈希；只下载缺少的文件。默认只取四个核心样本。
python scanpdf/acceptance/fetch_inputs.py "/path/to/科研小助手/pdf" --all
python scanpdf/acceptance/corpus.py "/path/to/科研小助手/pdf" --ocr
python test_longdoc.py "/path/to/科研小助手/pdf/UnderstandingDeepLearning_02_09_26_C.pdf"
```

批量测试先改名导入 v7、v1，验证提示新版、保留 v7 后恢复 v1；再按文件名导入剩余 PDF，核对各文件的字节哈希及页数。随后生成图谱，把每条边的来源按原件、物理页和块号回读，核对引文是否确实存在。输出 JSON 会列出每份输入、解析失败项、图谱节点、边及未匹配的书目条目。加上 `--ocr` 时，在同一个文库通过新工作台的 HTTP 接口逐页处理扫描件，并检查检索能否返回第 7 页的 GPU 段落。本次 15 页 OCR 完成后得到 282 个索引片段，包括按单元格裁剪重读的文字。

本次对照镜像清单逐份核验了 39 份 PDF（共 131,842,159 字节），39 份都成功进入同一文库。v1/v7 产生版本提示，相似度 0.720；图谱形成 38 个节点、75 条有向边，75 条来源片段都能回读。1,458 条参考记录未消歧为本库关系。图谱核对衡量出处可追溯性，尚缺人工金标来估计识别引用关系的召回率和精度。综述接口返回 12 条边的摘录和 12 条包含未来工作措辞的片段。

541 页教材得到 6,890 个可索引片段；第 440、476、541 物理页的三个预设查询均排名第一，Poppler 对照原页成功。原生提取未在第 2、4、10、12 页读出文字。该结果仅覆盖三个查询。中文检索在 Attention 两个版本的核对结果见[中文查询验收](qa.md)。

## 题面九道原问的 HTTP 与浏览器端复测

### 仅使用原赛题资料包

与[引擎直测](official_questions.md)分开统计。脚本在临时文库导入真实 PDF，五个单篇问题通过「证据追问」对应的 `/api/followup`，四个跨篇问题通过「跨篇问答」对应的 `/api/architecture`。问题保留题面措辞，移除 Adam 题的排版和省略论文名的旁注。两条命令分别直测 HTTP 和用 Playwright 实际操作 Chromium 页面。脚本输出原问、选定 PDF 的哈希、答案状态、命中条件及物理页块，并回读所链接的 PDF 字节。

```sh
python scanpdf/acceptance/official_http.py "/workspace/shared/contest-pdf" --show-hits
python scanpdf/acceptance/official_http.py "/workspace/shared/contest-pdf" --chromium /usr/bin/chromium
```

每条路径均复测 15 个实例。五个单篇问题和 BERT 对比各用 Attention v1、v7 跑两遍，三道 GPT 问题各跑一遍。**证据 12 个齐全、2 个部分命中、1 个缺失。** 12 个证据齐全的实例都有通过核验的结构化答案。第六作者的署名机构为多伦多大学，† 脚注另外说明工作是在 Google Brain 完成。BLEU 对照了表 2 的 EN-DE 列，Transformer (big) 28.4、先前最高 ConvS2S Ensemble 26.36，差值 2.04。

| GPT 原问 | 仅使用赛题资料包的覆盖 |
| --- | --- |
| 参数变化 | 部分。GPT-3 论文明确 1750 亿参数。GPT-1 原论文没有找到明确的总参数量，GPT-2 论文缺失。 |
| Layer Normalization 位置 | 缺失。产品返回了 GPT-3 第 8 页第 12 块，该段间接提到 GPT-2 的预归一化。没有取齐可支持两代比较的直接证据。 |
| 最大上下文 | 部分。GPT-3 第 8 页第 13 块明确上下文窗口为 2048 token。GPT-1 第 5 页第 5 块明确训练序列长 512 token，缺少 GPT-2 直接叙述的窗口比较。 |

赛题包包含 GPT-1 和 GPT-3 原文，未包含 GPT-2 报告。上述三道 GPT 问题均通过跨篇界面提交完整原问，三题均未返回结构化答案。

### 另加 GPT-2 课程镜像补测

OpenAI 仓库所链的报告在当前环境返回 HTTP 403。补测采用公开课程仓库固定提交中的报告，正文为 OpenAI 作者发布的 *Language Models are Unsupervised Multitask Learners*。报告为 582,775 字节、24 页，SHA-256 为 `d9d852e2894556e73f53cb22b7c605a9643d6f0b19bf604b429ed6192fa24f4e`，与第二个公开阅读清单仓库中的文件逐字节一致。[官方报告链接](https://d4mucfpksywv.cloudfront.net/better-language-models/language-models.pdf) · [补测所用课程镜像](https://github.com/NLPcourse/Representation/blob/6c063e520f4496834d7a944fade39bfa6d2c5cd4/Alternative%20Reading%20List/language%20models%20are%20unsupervised%20multitask%20learners.pdf) · [独立镜像提交](https://github.com/Pooret/Andrej-Kaparthy-Reading-List/tree/295574d236bb97cf4796e81e0a0d7d6a5bf9e942)

```sh
python scanpdf/acceptance/official_http.py "/workspace/shared/contest-pdf" --gpt2 /path/to/GPT2-course.pdf --show-hits
python scanpdf/acceptance/official_http.py "/workspace/shared/contest-pdf" --gpt2 /path/to/GPT2-course.pdf --chromium /usr/bin/chromium
```

这组只补测三道 GPT 原问。HTTP 与浏览器两条路径均为 **3 个证据齐全，3 个结构化答案通过**。核对了页面显示的引文、链接和原 PDF 字节哈希。

| GPT 原问 | 补测直接证据与结论 |
| --- | --- |
| 参数变化 | GPT-2 第 4 页第 9–11 块为表 2 列头、原报参数值和表题，第 15 块把最小模型与原 GPT 对应、最大模型命名为 GPT-2。结合 GPT-1 第 5 页第 5 块的架构参数和 GPT-3 第 1 页第 11 块，支持约 117M（由 GPT-2 报告的等价模型推得）、原报 1542M、1750 亿。 |
| Layer Normalization 位置 | 人工查看 GPT-1 第 4 页图 1 的左图，箭头显示残差相加后归一化。GPT-2 第 4 页第 8 和 12 块说明在原 GPT 架构基础上把 LayerNorm 移到每个子块输入，并增加最后一个自注意力块之后的归一化。 |
| 最大上下文 | GPT-1 第 5 页第 5 块报告 512 token 训练序列，GPT-2 第 4 页第 13 块明确将窗口从 512 增至 1024，GPT-3 第 8 页第 13 块明确为 2048。 |

原包和补测分开统计。这是固定原问与固定 PDF 的产品路径回放，无法估计盲测准确率。

额外核对了 OpenAI 发布的实现。GPT-1 的 `block()` 在残差相加后调用归一化，`n_ctx` 默认 512。GPT-2 在 attention 和 MLP 前调用归一化，最后还有 `ln_f`，`n_ctx` 默认 1024。GPT-2 官方模型卡列出的最大模型为 15 亿参数，仓库 README 同时提示早期报告部分规模数字存在误差。这些代码和模型卡证据未计入 PDF 问答命中。[GPT-1 实现](https://github.com/openai/finetune-transformer-lm/blob/a69b5c43b0452462890bca8ff92fb75dee9290cf/train.py#L140) · [GPT-2 实现](https://github.com/openai/gpt-2/blob/9b63575ef42771a015060c964af2c3da4cf7c8ab/src/model.py#L123) · [GPT-2 模型卡](https://github.com/openai/gpt-2/blob/9b63575ef42771a015060c964af2c3da4cf7c8ab/model_card.md)

四篇文献的综述来源可另行用 `python scanpdf/acceptance/synthesis_browser.py /path/to/pdf` 复测。浏览器从工作台进入综述页，全选后核对所返回的 4 个摘要、1 条引文及 2 条未来工作摘录。七条均与对应 PDF 原块一致。可选模型生成仅通过协议测试。
