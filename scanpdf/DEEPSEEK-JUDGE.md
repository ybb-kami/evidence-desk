# 证据台评委版运行交接

产品提交 `90f8f04` 的实测已单独汇总。[本轮 18 项考察点、公开同题对照和新盲测](acceptance/REVIEW-90f8f04.md)

青海大学袁瑞，个人赛，报名编号 `26CZB0001406`。参赛材料为技术 PDF、可编辑源稿和普通话演示视频。本次优化代码位于私有分支 `improve/grounded-retrieval`。

## 在独立目录启动

在新目录克隆该分支，在仓库根目录使用 Linux、Python 3.12、Tesseract 及英文语言包。扫描中文时另装简体中文语言包。以下命令安装解析与检索依赖。多语向量检索可用 `.venv/bin/python -m scanpdf.semantic.install` 安装固定模型。工作台使用自己的本地文库。完整 39 份资料接近文库容量上限，评委交互上传可使用单独文库或只预装演示子集。

```sh
git clone --branch improve/grounded-retrieval https://github.com/ybb-kami/evidence-desk.git evidence-judge
cd evidence-judge
git rev-parse HEAD
python3.12 -m venv .venv
.venv/bin/python -m pip install -r scanpdf/requirements-rag.txt
.venv/bin/python scanpdf/workbench.py --data ../evidence-judge-data --port 8765 --enable-deepseek
```

启动后打开本机 `http://127.0.0.1:8765/`。模型状态在 `/api/model-status`。在“科研 Copilot”内勾选本次模型授权，或只填公开检索词做联网检索。在“证据追问”可选定文献或从原页跳到指定段落。部署方需将科研助手的独立评委入口指向工作台，并遵循现有 Host／Origin 校验。使用专属进程和文库，外网复核应覆盖无密码框的首页、PDF 上传和带出处的回答。现有 ETH 监控由其原工作区管理。[隔离配置](../deploy/README.md) · [容器说明](container/README.md)

## 从作品页面调用

启动上面的工作台后，在首页上传 PDF，打开「证据追问」，勾选本次允许 Flash，提交一个需要原文回答的问题。页上显示本次回答的来源。能够直接从原表或原句核出的字段会走本地处理。需要验证模型时，可使用下面的独立验收，它会向工作台上传新样本，并检查模型返回和原件定位。科研 Copilot 内的公开检索词触发 Search；仅选择抓取时才会请求 Fetch。

使用根目录 `app.py`、另一条整合分支的默认网关或静态导出的作品页，会进入各自不同的流程。此版 Flash 和 Search 的配置和验收针对 `scanpdf/workbench.py`。服务端启动时读取环境变量，页面的 `configured` 字段表示本机配置状态，真实调用的证明是本次请求的供应商响应与对应的来源校验。

## 模型、搜索和预算

唯一生成服务是 DeepSeek。聊天请求使用官方模型名 `deepseek-flash`。付费生成前访问官方模型目录，核对展示名 `DeepSeek-V4.1-Flash` 与发布标记 `1017`。账号返回其他版本、缺乏发布标记或版本无法核验时停止。工作台不会启动 Qwen。逐次同意的 PDF 问答只发送问题与有界摘录；独立 Search 只接收用户填写的公开检索词。Fetch 可选择读取首个合格的公开结果。网页资料带 URL，PDF 证据保留文件指纹与物理页定位。[官方协议出处与验证范围](discovery/DEEPSEEK-SEARCH.md)

在部署端的私密环境配置同一 `DEEPSEEK_API_KEY`。下列美元单价需要依账号最新官方账单或价格页核实后填写；输入单价取可能适用的最高价，包含缓存未命中。不能确定单价时该类付费操作停止。名称与单位如下。

| 环境变量 | 单位 |
|---|---|
| `DEEPSEEK_INPUT_USD_PER_MILLION` | 每一百万输入 token 的美元上界 |
| `DEEPSEEK_OUTPUT_USD_PER_MILLION` | 每一百万输出 token 的美元上界 |
| `DEEPSEEK_SEARCH_USD_PER_CALL` | 每次独立搜索的美元上界 |
| `DEEPSEEK_FETCH_USD_PER_CALL` | 每次独立网页抓取的美元上界 |
| `EVIDENCE_BUDGET_DB` | 可选的持久 SQLite 文件绝对路径；上级目录须已存在 |

同一演示的各工作进程必须共用同一持久账本，默认位于文库的 `deepseek-budget.sqlite3`。按配置的单价保守预留，总额 0.71 美元，最多 100 个付费轮次、100 次模型调用；每轮至多两次模型调用（规划、生成）、一次 Search 和一次 Fetch。重启保留已用额度，网络失败后的预留也继续占用。实际可用轮数取决于费用上限，其他应用使用同一密钥产生的费用不归本账本管理。此预算覆盖 `scanpdf/workbench.py` 及其接管的旧付费路由。[DeepSeek 价格](https://api-docs.deepseek.com/quick_start/pricing/)

## 验收与材料

```sh
.venv/bin/python scanpdf/acceptance/deepseek_live.py --protocol-only
.venv/bin/python scanpdf/acceptance/deepseek_live.py --with-search
.venv/bin/python scanpdf/acceptance/official_http.py /path/to/pdf
```

第一项在无凭据环境验证 HTTP 协议，第二项使用临时文库，须先把 EVIDENCE_BUDGET_DB 设为与正式演示共用的持久账本绝对路径；在精确型号、凭据和费率等条件具备时进行真实验收；此脚本使用新生成的公开样本文档。阅读和扫描的浏览器复验另需 Playwright 和 Chromium。本云端未持有 DeepSeek 密钥，未产生实际模型、搜索或抓取费用。前期本地模型的历史成绩与本次 Flash 验收分开报告。[模型验收记录](acceptance/DEEPSEEK-ACCEPTANCE.md) · [扫描图版验收](acceptance/FIGURE-REGIONS.md) · [同一套公开题对照](acceptance/PUBLIC-REPLAY-49fb098.md)

[13 页技术说明](delivery/technical-report-zh-49fb098.pdf) · [可编辑源稿](delivery/technical-report-source-49fb098.zip) · [7 分钟普通话视频，录制版 5203688](delivery/evidence-desk-mandarin-1080p-5203688c.mp4)

补充材料对应产品提交 `90f8f04`。三页核验摘要列出 18 个评分考察点的实测与缺口，62.5 秒录屏展示教材原页定位，片中使用本地证据。 [核验摘要 PDF](delivery/verification-90f8f04.pdf) · [教材真实操作补充视频](delivery/long-book-90f8f04.mp4) · [录制与文件校验](delivery/long-book-90f8f04.json)
