# 公开文献发现与获取

启动扫描工作台并打开 `/download`。输入公开论文题名或参考文献信息，勾选 Crossref 检索确认。带受支持 DOI 的 ACL Anthology 题录会显示「选择开放 PDF」。选择题录后，核对题名和 DOI，再单独确认从 ACL Anthology 下载并导入当前文库。例如，赛题中的 *BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding* 对应 DOI `10.18653/v1/N19-1423`。

下载请求仅发往 ACL Anthology、arXiv 或 Journal of Open Source Software（JOSS）官方出版仓库的固定 HTTPS 地址。ACL 支持 `10.18653/v1/` 下的标准卷号和论文编号，由工作台生成出版方 PDF 路径。arXiv 也可通过公开编号单独下载，带版本的编号可定位指定版本，例如 `1706.03762v1`。下载拒绝重定向，响应大小上限为 24 MiB，总请求限时 18 秒。导入按原件 SHA-256 去重，已归档重复项保留其归档状态。下载记录包含请求的 DOI 或 arXiv 编号、公开来源地址、UTC 下载时间及 SHA-256。PDF 请求仅发送下载所需的公开编号。

其他 Crossref 记录可通过 DOI 查看书目信息。若运行环境无法连通服务，可通过工作台手动导入已有 PDF。

## 从科研 Copilot 核对论文线索

在 Copilot 填写公开检索词。若网页结果含有受支持的 arXiv、ACL Anthology 或 JOSS 官方论文链接，点击「在论文发现中核对」打开对应的编号和出版方入口。已有 JOSS 题录可直接查阅，缺少时可以单独同意将公开 DOI 发给 JOSS 官方出版仓库。确认下载后，PDF 进入当前文库并提供物理原页入口。[浏览器前后对照](HANDOFF.md)

## 用 JOSS 出版题录按题名获取 PDF

用 JOSS 官方出版仓库的 Crossref XML 准备本机题录。准备程序可接收多个公开 JOSS DOI，核对期刊、DOI 和出版版本的 CC BY 4.0 许可，保留原始 XML 供复查。默认保存在 `~/.local/share/evidence-desk/joss-catalog`，也可在准备和启动工作台前一并设置 `EVIDENCE_JOSS_CATALOG` 选择目录。

从项目根目录执行以下命令，准备三条正式出版题录并打开工作台。

```sh
(cd scanpdf && python -m discovery.joss 10.21105/joss.03020 10.21105/joss.03021 10.21105/joss.03022)
python scanpdf/workbench.py --data ./data
```

打开 `/download`，将题录来源切换到「JOSS 本机出版题录」，输入公开题名 `seaborn statistical data visualization` 并查找。选择开放 PDF 后确认下载。页面给出本地 PDF 物理第 1 页和带 SHA-256 的下载记录。题名查询在本机已缓存的题录范围内完成。PDF 从 JOSS 官方出版仓库下载，路径由选定的 DOI 生成。

若该环境需经系统 HTTPS 代理访问官方仓库，准备题录和启动工作台时可设置 `EVIDENCE_JOSS_USE_SYSTEM_PROXY=1`。该设置仅用于 JOSS 固定官方仓库，支持无凭据的系统 HTTPS 代理。

### 浏览器实联复现

以下命令使用 Playwright 和 Chromium，在临时题录及文库中复现全过程。连通出版仓库需要代理的环境可在命令前添加 `EVIDENCE_JOSS_USE_SYSTEM_PROXY=1`。

```sh
python scanpdf/acceptance/live_discovery.py \
  --seed 10.21105/joss.03020 10.21105/joss.03021 10.21105/joss.03022 \
  --query 'seaborn statistical data visualization' --expected-doi 10.21105/joss.03021 \
  --page 1 --anchor 'Center for Neural Science, New York University'
```

2026-11-18 实联中，官方仓库的三份 XML 和所选 PDF 均返回 HTTP 200。Chromium 从这三条已缓存题录按题名命中 Waskom 的论文并完成入库。工作台返回的 PDF 为 150,825 字节、4 个物理页，SHA-256 为 `163e1f3a1c502b0cc9005144811a69e03104cf009b75b028cba6dd6b725fef0e`。验收核对了物理第 1 页的作者机构和原页渲染。该环境的 Crossref API 与 ACL Anthology PDF 地址连接失败。[JOSS 出版原件](https://raw.githubusercontent.com/openjournals/joss-papers/master/joss.03021/10.21105.joss.03021.pdf)

离线单元测试使用模拟响应覆盖题录许可、确认、固定路径和代理边界。实联验收独立运行。

## 获取论文所引用的文献

在 `/download` 选择一篇活动文库论文并查看参考文献。页面从本地索引读取引文，列出原页、引文内容和状态。仅含一个明确 arXiv 编号或受支持 ACL DOI 的条目可勾选。没有受支持编号或编号有歧义的条目标记为「未能解析」，可打开原页复核。部分 PDF 的排版或缺少文字层可能使本地索引无法切分全部引文。

一次同意可以获取多条，按界面列表顺序逐篇下载。只向对应出版方发送公开编号。当前篇结束后可以停止处理，已完成的结果保存在本地，未执行的条目保持待获取。重新勾选失败或未找到的条目并确认后可重试。重复 PDF 依原件摘要去重，逐项结果可在同一来源论文下重新查看。

## 定期追踪 arXiv

在 `/download` 的「定期追踪 arXiv」中，选择一个公开分类和每天或每周刷新，明确同意后保存订阅。工作台启动后才执行订阅，首次订阅立即尝试一次。发往 arXiv 的内容仅为所选公开分类及固定的题录查询参数。文库相关度和代码仓库线索在本地计算与显示。

订阅、上次尝试、上次成功刷新、最近结果与下次到期时间保存在当前文库。失败后以 15 分钟起步退避，最多等待 6 小时，成功后恢复原频率。重启时只处理一次到期任务。点击「停止定期刷新」后保留结果和时间，清除下次到期，重启后也保持停止。

模拟时钟覆盖首次订阅、日/周间隔、重启、失败退避与停止。工作台实联测试发送公开分类 `cs.CL` 时未连接到 arXiv，界面状态已记录该次失败和下次重试时间，随后验证了停止。实联获取最新题录尚未验证。
