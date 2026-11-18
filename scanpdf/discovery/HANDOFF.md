# Copilot 网页线索到论文原页

Copilot 把 DeepSeek Search 返回的链接单列为「公开网页线索」。官方 arXiv、ACL Anthology 和 JOSS 链接或对应 DOI 可生成「在论文发现中核对」入口。交接链接只带规范 DOI 或 arXiv 编号，显式的 arXiv 版本后缀会保留。第三方网页中的 DOI 字符串不会生成下载入口。文库原文定位仍来自选中的本地 PDF。

打开交接页只读取本机信息。JOSS 题录缺失时，用户可逐次确认将此 DOI 发往 JOSS 官方出版仓库。工作台验证题录的期刊、DOI 和出版版 CC BY 4.0 许可，并记录 XML 的 SHA-256。arXiv 和 ACL 链接可在出版方核对题名及版本。之后还需单独确认下载 PDF，使用现有原件 SHA-256、去重和物理页入口。本流程没有新增 DeepSeek 付费调用。

## 2026-11-18 Chromium 对照

| 同一个隔离 Search 结果和 Fetch 失败响应 | 基线 `0534cf6` | 当前交接 |
| --- | ---: | ---: |
| 保留的网页线索 | 2 | 2 |
| 出现在 Copilot 的论文核对入口 | 0 | 1 |
| 测试账本中的 Search / Fetch 预留 | 1 / 1 | 1 / 1 |

隔离响应同时返回 JOSS 正式链接和非出版方链接，旧版和新版均执行用户明示的一搜一抓。抓取失败仍显示两条 Search 线索。当前只有正式链接产生交接入口。此对照借助测试传输，未对 DeepSeek 发送请求；账本为临时测试账本。单元测试另覆盖工具重复请求去重、外部网页与文库证据隔离、伪造域名、DOI 语法、版本保留和两次确认。[DeepSeek 官方接口与网络限制](DEEPSEEK-SEARCH.md)

真正的出版方验收从空文库和空题录开始。打开核对页时未提交题录或 PDF 请求。分别确认后，从 JOSS 官方仓库获得 Michael Waskom 的 *seaborn: statistical data visualization*，DOI `10.21105/joss.03021`。XML SHA-256 为 `0ff4b751af9628029436b82ea72d575b5409063a46e5232dd66a2b7713ce05c6`。PDF 共 4 个物理页、150,825 字节，SHA-256 为 `163e1f3a1c502b0cc9005144811a69e03104cf009b75b028cba6dd6b725fef0e`。工作台物理第 1 页文本包含作者机构，原页 PNG 与独立从该 PDF 渲染的字节相同，PNG SHA-256 为 `5d9b7de3307941aa95a00fef09bc75c8188b0c3a17a64c8970d252d60e950409`。本次未实联 arXiv 或 ACL Anthology。 [JOSS 题录](https://raw.githubusercontent.com/openjournals/joss-papers/master/joss.03021/10.21105.joss.03021.crossref.xml) · [出版 PDF](https://raw.githubusercontent.com/openjournals/joss-papers/master/joss.03021/10.21105.joss.03021.pdf)

## 复跑

从项目根目录使用装有 Playwright 的 Python 和 Chromium 执行。第一条命令会访问 JOSS 出版仓库。只有环境依赖系统 HTTPS 代理时才需要前置的环境变量。[浏览器验收脚本](test_handoff_browser.py)

```sh
EVIDENCE_JOSS_USE_SYSTEM_PROXY=1 python scanpdf/discovery/test_handoff_browser.py
before=$(mktemp -d)
git archive 0534cf65898bf638f1778e4a5f486ce941c2813e | tar -x -C "$before"
python scanpdf/discovery/test_handoff_browser.py --repo "$before" --before
PYTHONPATH=.:scanpdf:scanpdf/tests python -m unittest discovery.test_handoff test_research_workflow
```
