# DeepSeek Flash 独立验收

验收固定使用 `DeepSeek-V4.1-Flash-1017`。脚本生成一份公开虚构的两页 PDF，并启动本机工作台。每次使用新临时文库，工具规划只测试引文图，页码问答核对实际模型引文的文档 ID、物理页、文本块和字形定位，缺字段题检查工作台是否留空并给出缺口。默认关闭出版元数据、Search 与 Fetch。可单独启用公开论文搜索。验收输出只记录状态、通过条件、耗时和合成 PDF 的 SHA‑256。

在仓库根目录使用已安装工作台依赖的 Python 环境运行。付费实测读取当前进程已配置的 `DEEPSEEK_API_KEY`，并要求 `EVIDENCE_BUDGET_DB` 指向正式演示共用的账本绝对路径。模型配置由 `/api/model-status` 复核，脚本会明确区分无凭据阻塞和实时通过。

```bash
python scanpdf/acceptance/deepseek_live.py --protocol-only
python scanpdf/acceptance/deepseek_live.py --out /tmp/evidence-deepseek-live.json
```

如需同时验证指定的 Flash 与 Search，可将第二条命令替换为下列命令。它使用配置好的共享账本和费率，发送三次模型授权请求和一个固定的公开论文检索词。只有搜索结果含目标 arXiv 论文原文地址才通过来源检查。Fetch 调用数为零；模型耗时、来源定位和缺字段拒答按项报告。缺密钥返回 blocked（退出码 2）；上游异常、通用空答或查到其他论文均不能充作通过。输出文件须使用新路径。

```bash
python scanpdf/acceptance/deepseek_live.py --with-search --out /tmp/evidence-flash-search-live.json
```

可使用以下命令在无凭据环境回归子进程配置、拒答判定和公开搜索验收逻辑。测试搜索响应由隔离的本地进程提供。

```bash
python scanpdf/acceptance/test_deepseek_live.py
```

`--protocol-only` 只测试无凭据时的本地 HTTP 合约。它移除子进程的供应商凭据。实测向工作台发出三次带模型同意的请求，服务启动参数为 `--enable-deepseek`。`model_calls_requested` 统计发给工作台的模型同意请求。

当前环境没有供应商凭据，实时验收记录为 `blocked`，模型同意请求为 0。独立合成 PDF、原页字形定位和拒绝错误页码的本地检查已通过。当前无凭据协议检测通过，`GET /api/model-status` 返回 HTTP 200，显示 `configured:false`。两次追问保留本地结果，并报告 `model_status:not_configured`。[实时验收记录](results/deepseek-flash-live.json) · [协议验收记录](results/deepseek-flash-protocol.json)

## 本地 HTTP 与 transport stub

锁版 `b69cca3` 在干净目录没有 E5 时，这份新 PDF 的两个英文问题均未召回证据。当前代码在没有向量结果、强词项和既有命中时，可用原文中连续的三个问题词筛选候选，保留物理页与块号。相同两个问题在关闭 E5 时已可定位物理第 2 页，模拟供应商路径也通过了原文与缺字段检查。此样本只验证英文词组可从原件检出。中英文语义检索可加载本地 E5，设置 `EVIDENCE_DENSE_MODEL_DIR` 指向权重绝对路径并安装相应 Python 依赖。[E5 安装说明](../semantic/README.md)

```bash
python scanpdf/acceptance/deepseek_stub_http.py
```

脚本以实际工作台 HTTP 入口运行，并替换供应商传输、阻断进程对外连接。Search-only 路径实际只向 stub 传递明确输入的公开检索词，合成私料标记未传递。Flash 工具规划、单篇问答的页 2 原件字形定位和缺字段均通过。账本满 0.71 USD 后，额外付费调用为 0，追问返回 HTTP 503。最终复测中，账本满额后额外模型目录查询也为 0。此项用于验证本地请求行为，线上供应商请求为 0。[本地事务测试记录](results/deepseek-flash-stub-http.json)

## 在当前评委服务上走一遍浏览器

在运行该服务的机器上，从源码目录执行。把 `8765` 改成已经运行的工作台端口。脚本连接该回环服务，沿用服务当前的文库和预算账本，通过首页导入一份公开虚构 PDF。它从「问这篇论文」进入，逐次勾选模型授权，检查第 2 页回答及其原件定位，再检查缺字段回执。缺配置或浏览器时会在提交模型请求之前返回 `blocked`。

```bash
python scanpdf/acceptance/judging_browser_live.py --url http://127.0.0.1:8765
```

若本轮也需要验证 DeepSeek Search，首次运行命令时追加 `--with-search`。它只提交一次原论文的公开检索词。文献选择、模型编排和读取首条网页均取消勾选，脚本也不打开检索结果中的网络链接。核心验收最多提交两次追问，含搜索时另提交一次搜索。每个测试问题只发送一次。

`--chromium` 可以指向 Chrome 或 Chromium。省略时优先查找系统浏览器，再尝试 Playwright 已安装的 Chromium。在 Windows 上可以按以下方式传入路径。

```powershell
python scanpdf/acceptance/judging_browser_live.py --url http://127.0.0.1:8765 --chromium "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

机器缺少 Playwright 或可启动的浏览器时，可以用自己的浏览器打开同一回环地址手工核验。用随测试脚本生成的两页 `judging-fictional-harmonics.pdf` 上传并进入「问这篇论文」。先授权并询问 `What type of box held the silver eddy-current paddle during the harmonics check?`，核对模型回执与第 2 页原件。再单独授权并询问 `What is the manufacturer’s serial number on the silver eddy-current paddle?`，核对是否明确报告证据不足及未给出序列号。依赖已安装时可从源码目录生成同一演练文件。

```bash
python -c "import sys; from pathlib import Path; sys.path.insert(0, 'scanpdf/acceptance'); import deepseek_live; Path('judging-fictional-harmonics.pdf').write_bytes(deepseek_live.fixture())"
```
