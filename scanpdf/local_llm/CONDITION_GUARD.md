# 数值答案的实验条件核验

《A Neural Probabilistic Language Model》第 11 页第 6 块在同一段报告了两组验证集读数。Brown corpus 对应 200,000 词，Associated Press (AP) News 对应 963,138 词。在未指明语料的情况下直接答 200,000 会隐藏两组数据的区别。指定 AP 的问题也曾收到引用 Brown 原句的 200,000 词回答。[公开论文与固定哈希](comparison-cases.json)

共享核验器在引文定位后检查数值所在分句，并把候选数值与命名语料、硬件、实验分组或设定值关联。问题明确限定条件时，引文必须支持该条件。问题未限定条件时，同一文档的已检索原文出现同字段、同单位和不同条件下不一致的读数，返回 `ambiguous_measurement` 并请用户限定条件。明确的对比和区间不触发这项歧义判定。实验摘要、跨块省略和未识别的命名模式仍可能缺少可解析的条件关系。现有字段、单位与引文核验继续运行。[核验器](../condition_support.py)

## 历史真实响应离线复放

九道公开题在两组历史真实响应上复放，共 18 条。复放校验四份论文的 SHA-256 和每次原请求的 SHA-256，随后只切换条件核验。实际保存的历史响应和生成参数继续保留。[原始模型输出](comparison-results.json)

| 案例 | 当前关闭条件核验 | 当前启用条件核验 |
| --- | --- | --- |
| 指定 AP 验证集 | 引用 Brown 原句，答 200,000 词 | `different_condition` |
| 未指定语料的验证集 | 答 200,000 | `ambiguous_measurement` |
| 其余 16 条真实响应 | 状态、原因和显示答案均一致 | 状态、原因和显示答案均一致 |

两处变化均来自历史 4B 响应。冻结版本的 AP 答案由引文绑定器挡下，现有绑定器正常识别排印连字后，这条响应能进入条件核验。独立测试也验证了类似语料场景中明确指定语料时，正确读数可以通过。历史复放读取已保存的响应。[完整逐条复放](condition-replay.json)

从仓库根目录复现。`--papers` 指向使用[清单](comparison-cases.json)中的文件名保存四份公开 PDF 的目录。

```sh
python scanpdf/local_llm/replay_condition_guard.py --papers /path/to/public-papers
PYTHONPATH="$PWD:$PWD/scanpdf" python -m unittest scanpdf.tests.test_condition_support
```
