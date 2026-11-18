# 原创 PDF 表格关系对照验收

这套离线夹具只验证表格取证与关系键匹配，不实现 BLEU 比较、不替代官方原件，也不改工作台。所有系统、年份和表格均为合成测试；第 1 页的相似数字是明确标注的噪声。用与主仓库一致的 PyMuPDF 运行。

## 复现

从仓库根目录运行：

```sh
python -m unittest -v table_relation_qa.test_fixtures
python -m table_relation_qa.run /tmp/evidence-table-fixtures --generate-and-baseline
```

生成目录包括六份可直接翻阅的小 PDF、供候选程序读取的 `requests.json`，以及仅供评分器使用的 `manifest.json`。同一 PyMuPDF 环境重复生成 PDF 的 SHA-256 一致。评分器对照原 PDF 的页码、表题、各单元格坐标和文字（包括空格位），不是只比较预先写好的答案。

| 样本 | 设计挑战 | 应有行为 |
|---|---|---|
| `positive` | 法语任务、上一年、相似算法名同时出现 | 定位德语任务正确年份的指定模型和唯一 prior 行，计算正差 |
| `reordered_negative` | 数字列挪到最左，基线/目标行对调，原页和表号变化 | 同样的题面，按新布局取证，给负差而非照抄正值或只给绝对值 |
| `missing_baseline_cell` | 同任务 prior 已存在，但 BLEU 格为空，别的任务有分 | 明确缺值；以对应空格坐标作记录，不填零或拿别任务的值 |
| `missing_target_row` | 同名模型只出现在其它年/任务，或相似模型名 | 标明缺失目标行，不沿用同名或近似项 |
| `ambiguous_prior` | 同任务同年份有两个都标为 prior 的不同系统 | 无法选定唯一基线，不默认选最大、最先或最后 |
| `no_target_scope` | 表里只有相近任务/年份；首页还有看似正确的散文数字 | 无匹配，不生成任何分数或差值 |

## 同卷验收候选实现

本地与远端可共用同一生成目录或以 SHA-256 核对后生成。传给工具的输入仅是各份 PDF 和 `requests.json` 中的信息；不要把 `manifest.json`、固定页号或测试期望带进产品适配器。候选实现从原件推导页、表号和坐标，组织以下 JSON，评分器不运行任何候选脚本：

```json
{
  "cases": [
    {
      "id": "<来自 requests.json>",
      "sha256": "<被实际读取的 PDF 的哈希>",
      "status": "matched",
      "values": {
        "model_score": "<十进制分数>",
        "baseline_name": "<原文系统名称>",
        "baseline_score": "<十进制分数>",
        "signed_difference": "<目标减基线，不取绝对值>"
      },
      "citations": [
        {"page": 1, "table": 1, "row": 0, "column": 1, "text": "Task"}
      ]
    }
  ]
}
```

示例中的数字是结构示意，不是夹具位置。物理页、表号、数据行和列从 1 起，表头行索引为 0。引用 `text` 使用能在原格核对的内容（必要时只合并提取换行/空白）。`missing_value` 要给出缺失值所在格，文字用空字符串；`missing_row` 要给出所检查表范围的真实单元格，不能由“没搜到某句”推断表中没有。不能唯一定位时输出 `missing_row`、`missing_value`、`ambiguous_baseline`，或通用 `unresolved`；这些状态下 `values` 必须是空对象。所有夹具均须提交一条输出。

```sh
python -m table_relation_qa.fixtures /tmp/evidence-table-fixtures
# 运行候选实现。只输入生成的 PDF 和 requests.json，写出 candidate.json。
python -m table_relation_qa.run /tmp/evidence-table-fixtures --responses candidate.json
```

评分分别报告原件定位、关系验收和安全拒答。原件定位通过不表示关系求解通过；准确说出缺值/歧义仍须给可核对的源位置。“安全拒答”仅用于四个负例。有任一关系用例未过时，外部响应评分以非零退出。

## 当前主线基线

当前页级对象清单能定位六例的全部现有必需单元格（**6/6 原件定位**），但不提供依角色连接数据行、判定缺失理由或计算差值的 API（**0/6 关系验收**）。适配器因此仅返回 `unresolved`，没有在四个负例中声称得出答案（**4/4 安全拒答**）。这不是现有产品自动给出四种失败类型的证明。单测同时验证：只有正确数字但没注明来源、沿用旧页号、调换列或差值符号、将赛题常见数字硬套无数据表都会失败。
