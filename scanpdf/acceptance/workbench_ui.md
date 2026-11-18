# 工作台入口的桌面与窄屏交互验收

2026-11-18 以归档出的 `c94c628` 为基线，随后用同一提交上集成交互修复的工作树复测。浏览器为 Debian 上的 Chromium 156.0.8078.79，视口为 1280×900、390×844 和 320×740。每个视口独立启动工作台，使用空的临时文库。证据来自赛题包中的 Attention v7 和 BERT 原始 PDF。两次实际使用本机 OPUS-MT；单篇使用原文字段规则，Copilot 使用规则编排。本次没有外部网页请求。[复跑脚本](workbench_ui.py)

| 操作 | `c94c628` | 集成修复复测 |
| --- | --- | --- |
| 首页导航 | 三宽度整页均无横向溢出。390 和 320 像素下，品牌文字高 72 px、单行 24 px，实际分成三行。 | 手机宽度下品牌为一行。390/320 的首屏导入按钮前移 16/9 px；桌面位置稳定。 |
| 导入与单篇 | 首个 Tab 到跳过导航链接，Enter 可打开文件选择。损坏 PDF 只显示英文 `Not a PDF file.`。有效原件从卡片打开时已选定 Attention v7。 | 损坏 PDF 有中文重选提示，继续导入有效文件成功。 |
| 引文与回跳 | GPU 结论正确。链接打开第 7 物理页第 9 块，显示原文和块框。关闭新标签后，问答结果仍在。 | 高亮确切句子 `We trained our models on one machine with 8 NVIDIA P100 GPUs.`，原件页和问答上下文保持可核。 |
| 双语 | 桌面双栏和手机逐段成对显示，原文与译文的定位键可往返聚焦；原段截图成功。输入 999 后，页码框未还原，两栏和原件链接消失。 | 输入框支持 Enter 跳页。越界仍显示当前页、译文和裁图，并恢复页码、提示有效范围。另在两页素材验证三个宽度下可以用 Enter 跳到第 2 页，随后越界仍保留第 2 页；直接打开无效页链接会显示第 1 页和范围提示。 |
| 引文图 | 主页生成 2 个节点和 1 条实际引文；从图中回读 BERT PDF 第 11 页，字节 SHA-256 与该版本一致。 | 节点选择、引文和原件回查均通过。 |
| CSV | 缺横轴类型时原生校验聚焦到字段，未显示中文状态。390/320 的预览区宽 316/256 px，图宽 340 px，整页无横向溢出。 | 缺字段有中文状态。仅当预览区溢出时提示横向滚动，聚焦后方向键可验证滚动。图注仍定位 CSV 第 5 行，SVG 可下载，返回定位到首页的 CSV 区。 |
| Copilot 与跨篇 | 两份指定 PDF 成功进入规则编排、显示引用工具执行记录，返回主页。跨篇显示 Attention 和 BERT 各自摘录及独立解码器结论。 | 两条流程和三宽度返回均通过。 |

以下命令从当前仓库根目录执行。运行工作台的 Python 需要项目依赖与 OPUS-MT 运行时。调用脚本的 Python 需要 Playwright。本次三宽度从打开主页到最后返回均约 4.6–5.9 秒。用时包含浏览器操作与这一段真实翻译，文件选择由脚本提供。未测其他浏览器或实体手机的系统文件选择器。 [翻译本机配置](../bilingual/README.md)

```sh
audit_checkout=$(mktemp -d)
git archive c94c628 | tar -x -C "$audit_checkout"
EVIDENCE_TRANSLATION_MODELS=/path/to/opus-models \
  python scanpdf/acceptance/workbench_ui.py /path/to/contest-pdf \
  --checkout "$audit_checkout" --python /path/to/workbench-venv/bin/python

EVIDENCE_TRANSLATION_MODELS=/path/to/opus-models \
  python scanpdf/acceptance/workbench_ui.py /path/to/contest-pdf \
  --python /path/to/workbench-venv/bin/python --fixed
```
