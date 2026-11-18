# DeepSeek Flash 独立验收

验收固定使用 `DeepSeek-V4.1-Flash-1017`。脚本生成一份公开虚构的两页 PDF，并启动本机工作台。每次使用新临时文库，工具规划只测试引文图，页码问答核对实际模型引文的文档 ID、物理页、文本块和字形定位，缺字段题检查工作台是否留空并给出缺口。请求关闭出版元数据授权，未选择 Search 或 Fetch，也不读取封存题集。验收输出只记录状态、通过条件、耗时和合成 PDF 的 SHA‑256。

在仓库根目录使用已安装工作台依赖的 Python 环境运行。付费实测读取当前进程已配置的 `DEEPSEEK_API_KEY`，并要求 `EVIDENCE_BUDGET_DB` 指向正式演示共用的账本绝对路径。模型配置由 `/api/model-status` 复核，脚本会明确区分无凭据阻塞和实时通过。

```bash
python scanpdf/acceptance/deepseek_live.py --protocol-only
python scanpdf/acceptance/deepseek_live.py --out /tmp/evidence-deepseek-live.json
```

`--protocol-only` 只测试无凭据时的本地 HTTP 合约。它移除子进程的供应商凭据。实测向工作台发出三次带模型同意的请求，服务启动参数为 `--enable-deepseek`。`model_calls_requested` 统计发给工作台的模型同意请求。

当前环境没有供应商凭据，实时验收记录为 `blocked`，模型同意请求为 0。独立合成 PDF、原页字形定位和拒绝错误页码的本地检查已通过。当前无凭据协议检测通过，`GET /api/model-status` 返回 HTTP 200，显示 `configured:false`。两次追问保留本地结果，并报告 `model_status:not_configured`。[实时验收记录](results/deepseek-flash-live.json) · [协议验收记录](results/deepseek-flash-protocol.json)

## 本地 HTTP 与 transport stub

```bash
python scanpdf/acceptance/deepseek_stub_http.py
```

脚本以实际工作台 HTTP 入口运行，并替换供应商传输、阻断进程对外连接。Search-only 路径实际只向 stub 传递明确输入的公开检索词，合成私料标记未传递。Flash 工具规划、单篇问答的页 2 原件字形定位和缺字段均通过。账本满 0.71 USD 后，额外付费调用为 0，追问返回 HTTP 503。当前实测另记录一次额度核验前的免费模型元数据请求。此项用于验证本地请求行为，线上供应商请求为 0。[本地事务测试记录](results/deepseek-flash-stub-http.json)
