# 科研 Copilot 的可观测工作流

评委版的跨网工具与配置见[DeepSeek 运行交接](DEEPSEEK-JUDGE.md)。下文中的本地生成与 JOSS 工具结果来自早期冻结版本。

从工作台首页进入「科研 Copilot」。选定 1 至 6 份活动论文，提交问题。服务先核对版本范围，可请本机 Qwen 从本次允许的工具中选择操作。模型通过 OpenAI 兼容协议的 `message.tool_calls` 返回工具名与受限参数。服务核验后调用函数，补齐用户明确要求的步骤，并在查不到证据且存在未索引页时追加一次扫描覆盖核查。模型没有配置、忙、超时或输出无效时，页面标明由规则编排，保留可用的本机处理。

工具包括单篇定位问答、跨篇核对、局部引文与作者展望、扫描覆盖和 JOSS 出版方题录。用户勾选外部查询并在问题中输入单一支持的公开 DOI 时，才向 JOSS 固定地址发送该标识。工具不能自行选择文件系统路径、任意网络地址、其他文库或写操作。运行界面显示调用、耗时、来源条数和缺口；规则或模型选择工具的来源也可查。该工作流使用一个可选规划模型与确定性工具链。

本机生成路径读取模型的 SSE content 增量，页面暂显 answer 字段。引文校验后用结果替换预览；拒答、截断或中断时移除草稿。服务只转发显示字段。页面用 `textContent` 展示论文内容与答案。对只有结构化表格计算的任务，页面可直接显示核对后的单元格。模型生成的内容仍需对照原句理解。

## 复现

在 [RAG 环境](RAG_HANDOFF.md) 中安装 Playwright 和 Chromium。以下脚本新建测试文库，真实上传两份临时 PDF 并从首页进入 Copilot。第一项只使用本机规则。第二项用配置好的本机 Qwen 推理。加 `--publisher` 时会真实查询公开 JOSS 题录。

```sh
.venv/bin/python scanpdf/acceptance/copilot_browser.py --mode rules --chromium /usr/bin/chromium
.venv/bin/python scanpdf/acceptance/copilot_browser.py --mode real --publisher --chromium /usr/bin/chromium
```

| 操作 | 一次实际 Chromium 结果 |
|---|---|
| 无可检索文字的输入 | 单篇问答返回缺口后，追加扫描覆盖工具，定位到需识别的物理页 1。 |
| 两篇 PDF 的局部引文网络 | Qwen 返回 `citation_graph` 的真实函数调用，工具核到 2 个节点、0 条库内边，页面提示无法判断基石论文。 |
| 新文献记录的校准仪器 | Qwen 输出 quartz transducer，浏览器收到并显示 4 个实际内容增量，随后替换为已核对答复。引文逐字匹配物理页 1，此次 3 条原文链接均实际打开。 |
| 核实 JOSS 的 seaborn 论文 | Qwen 返回 `publication_metadata` 调用，真实出版方响应提供 DOI、题名、作者、许可验证依据与元数据 SHA。 |

这几项在同一 Linux 环境用单线程 Qwen3-1.7B Q4_K_M 实测。一次回放分别耗时 0.12、1.85、8.35、14.34 秒，含浏览器核查。首次加载、并发负载和网络延迟会改变实际时间。另一次有明显资源竞争的初试在 25 秒工具选择时限内未完成，界面回退到规则。上表只评估操作链，完整 RAG 质量另以封存题、公开题和原件验收核对。[浏览器逐事件实测](acceptance/results/copilot-local-browser.json) · [工具实现](research_workflow.py) · [模型流解析](model_stream.py)
