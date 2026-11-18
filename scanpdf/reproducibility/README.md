# 代码复现证据

从工作台进入「核对论文代码」，选择本地 PDF。页面列出在已索引原文中识别到的公开 GitHub 仓库地址，保留文件 SHA-256、物理页、文本块、摘录和原文字符范围。点击原文位置可查看原页高亮。没有完整地址时，可查阅当前已索引范围和待识别页数。

选择一条引用，再确认本次查询。服务以只读方式向 GitHub 公共 API 请求该仓库的元数据、提交和文件树。带完整提交 SHA 的引用使用该版本，其余引用使用核验时的默认分支。结果展示固定提交、核验时间、目录完整性、API 提供的仓库许可标记，以及指向该提交的实际文件链接。许可证、README、环境和依赖文件按名称列出。训练和评测文件是按文件名识别的入口候选。

证据范围为公开提交、可获取的目录及追踪文件路径。实验运行记录为空。仓库 API 的许可标记和具体提交中的许可证文件各有来源。文献引用也可能属于相关工作或外部依赖，来源摘录用于核对作者的表述。文本中只有展示标签或图标的链接可能需要从原 PDF 手工查看。

## 请求和缓存

- 每次检查的请求须带所选论文的本地引用标识和确认值。外部请求只使用公开仓库名和可选的公开提交 SHA。
- 请求使用 `api.github.com` 的固定 HTTPS 路径，采用 GET、无认证头和无重定向。单次连接或读取超时 6 秒，全程设定 22 秒预算，每个 JSON 响应上限 2 MB。大目录只在预算内回退到根目录，结果标明覆盖范围。
- 有效核验缓存一小时，失败缓存两分钟，最多保留最近 64 个结果。继续使用缓存时界面显示原核验时间。
- 每类最多显示六个按路径排列的文件，同时显示该类目录匹配数。仓库不可用、超时、拒绝、目录截断分别保留状态。

### 获取时快照

可以在同一个文库使用独立命令获取候选文件的快照。先导入论文 PDF，再取该原始文件的 SHA-256。`--list` 在本机返回论文已索引的公开引用。`--repository` 只选中该 PDF 的唯一仓库引用。有多个提交引用时，可用列表中的 `--reference` 标识明确选择。

```sh
python -m scanpdf.reproducibility.sync --data /path/to/library --document PDF_SHA256 --list
python -m scanpdf.reproducibility.sync --data /path/to/library --document PDF_SHA256 \
  --repository google-research/bert --confirm \
  --file license LICENSE --file readme README.md --file environment requirements.txt \
  --file training run_pretraining.py --file evaluation run_squad.py
```

`--confirm` 允许该次只读操作向 `github.com` 发送从所选论文核验的仓库名，并向 `raw.githubusercontent.com` 发送同一仓库、公告中的提交 SHA 和显式选取的仓库相对路径。两步均用固定 HTTPS GET、系统信任链、无认证和拒绝跳转。可使用配置的无凭据 HTTPS 代理。PDF、页面文字、查询内容、文库标识和本机路径留在本地。获取过程中把 UTF-8 文件当作字节核验，原始文件内容不保存在库内。快照只包含提交、公开来源、时间、候选文件大小、SHA-256、计算的 Git blob 标识及所检文件的结果。

首步读取 Git 引用公告，并核对默认分支和 HEAD 的一致性。论文明确引用提交时，独立命令只能读公告中展示的提交。其次只取得显式选取的最多十个文件。每个响应最多 256 KiB，单次网络操作超时六秒，累计尝试使用 30 秒时间预算。公开拒绝、重定向、频率限制或网络错误中止此轮并保留已有快照。单个候选返回 404 时会在报告中列出，其他成功选取的文件仍可入库。

工作台选择相应 PDF 与仓库引用时，会展示已经保存在此库的「获取时快照」。读取快照只访问本地 SQLite。它按论文 SHA 和具体引用存储，每库最多 64 条。只有再次明确运行获取命令时才更新，没有后台刷新。页面展示取得时间、固定原始字节链接和每个文件的 SHA-256。公告的来源对应可变仓库状态，原始文件链接中的提交保持固定。许可证与入口依据本次取得的文件，目录完整性、许可法律判断和实验运行属于其他核对步骤。实时 API 的按钮及其逐次确认独立保留。

## 实测

使用扫描工作台的真实 HTTP 接口和 Chromium 查阅四份公开论文。BERT `N19-1423.pdf` 的公开地址在物理第 2 页第 5 块，T5 `1910.10683v4.pdf` 的发布仓库在物理第 1 页第 19 块。两处引用的字符选择均定位到原 PDF。勾选之前无核验请求，提交后确认状态清空。第二次确认后读取第一次核验的缓存。

实际公共 API 调用在当前环境遇到 `CONNECT 403`，两份论文均展示不可用状态。另用相同 BERT 原件和受控 REST 响应，复测提交固定、五类目录文件和 SHA 持久链接的渲染。代理拒绝独立于这项受控成功验证。

Attention v1 的已索引原文没有可提取的公开 GitHub 仓库地址。Switch Transformer 原文有可定位地址，受控超时实验保留原文来源并显示超时。六项单位测试覆盖跨行和连字 URL、来源约束与逐次确认、缓存重启、固定文件链接、部分目录和只读网络请求。

```sh
PYTHONPATH=scanpdf python -m unittest discover -s scanpdf/reproducibility -p 'test_*.py' -v
run_dir=$(mktemp -d)
python scanpdf/reproducibility/check_browser.py --pdf-dir /path/to/contest-pdf --output "$run_dir/controlled"
python scanpdf/reproducibility/check_browser.py --pdf-dir /path/to/contest-pdf --output "$run_dir/live" --live
```

`--live` 表示同意浏览器为 BERT、T5 各核对一次公共 GitHub API。命令写出本地核验 JSON 和页面截图。

### 公网获取与真实浏览器重放

```sh
run_dir=$(mktemp -d)
python -m scanpdf.reproducibility.check_snapshot_browser \
  --pdf-dir /path/to/contest-pdf --output "$run_dir/verified" \
  --confirm-public --expect-live-forbidden
```

该脚本在新临时文库取得 BERT 快照，分别复核每个固定链接的原始字节及 SHA-256，再启动独立工作台进程。Chromium 查看物理第 2 页原文标记和快照。勾选后单独发起正常的 GitHub REST 核对，`--expect-live-forbidden` 用于核验本环境实际遇到的 403。允许 REST 的环境可省略最后一项。在临时目录保存带来源、哈希、实测状态的 JSON 和浏览器截图。

2026-11-18 实测从 BERT 原文第 2 页第 5 块定位 `google-research/bert`。Git 引用公告的默认分支为 `master`，当次固定提交为 `eedf5716ce1268e56f0a50264a88cafad334ac61`。五个候选文件均成功获取并逐个回读验证。工作台的快照卡片保持可见，实时 REST 在本环境明确显示 403，第二次确认读取对应失败缓存。浏览器页面只发出回环请求。[实测 JSON](../acceptance/results/repository-snapshot-bert.json)

### Docker 文件

现有 `COPY scanpdf/ ./scanpdf/` 已覆盖运行所需的 `scanpdf/reproducibility/__init__.py`、`references.py`、`github.py`、`snapshot.py`、`sync.py`、`routes.py`、`index.html` 和 `reader.js`，以及 `workbench.py` 的现有分发路径。运行独立快照命令和工作台时指向同一挂载文库 `/data`。这些运行文件只需镜像中已有的 Python 和根应用依赖。Git CLI、Playwright、Chromium、固定 PDF、浏览器复测脚本及生成的证据 JSON 属于验收环境。
