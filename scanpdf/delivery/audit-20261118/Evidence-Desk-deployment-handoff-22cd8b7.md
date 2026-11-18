# 证据台部署交接 · 已测候选

2026 年 11 月 18 日，青海大学袁瑞个人赛作品，报名系统队伍名 26CZB0001406。

## 交接版本与入口

私有远端 `ybb-kami/evidence-desk` 的可获取分支为 `feature/scanpdf-parser`。本轮用 `git ls-remote --heads` 核对其完整 SHA 为 `22cd8b7cd6a3d6340c809056cf0f927d966745d5`。远端 main 指向 `a6b6d27be19aa0e4f23a5a94ce151242d43fc251`。部署线应把评审版本锁定到上述功能分支的完整提交，或记录其经过独立复测的整合提交。[冻结源码](https://github.com/ybb-kami/evidence-desk/tree/22cd8b7cd6a3d6340c809056cf0f927d966745d5)

以下示例适用于全新的工作目录。以已有只读权限拉取。从新检出的仓库根目录运行。实例数据使用新建目录，示例命令只服务于本地预验。Git bundle 备用文件名为 `Evidence-Desk-individual-26CZB0001406-22cd8b7.bundle`，本轮用 `git bundle verify` 验证有完整的 30 次提交历史及同 SHA 分支。

```sh
git clone --branch feature/scanpdf-parser https://github.com/ybb-kami/evidence-desk.git evidence-desk-22cd8b7
cd evidence-desk-22cd8b7
git checkout --detach 22cd8b7cd6a3d6340c809056cf0f927d966745d5
git rev-parse HEAD
python3.12 -m venv .venv
.venv/bin/python -m pip install -r scanpdf/requirements.txt
tesseract --list-langs
.venv/bin/python scanpdf/workbench.py --port 8765 --data ../evidence-judge-data-22cd8b7
```

Linux 及 Python 3.12，基础 Python 依赖见 `scanpdf/requirements.txt`，包括固定版本的 PyMuPDF、Pillow、NumPy、SciPy。系统安装 Tesseract 5 与英文语言包；中文 OCR 另装简体中文语言包并加 `--lang chi_sim+eng`。默认按页使用本机 OCR。数据库为专用数据目录中的 `library.sqlite`。主目录 `app.py` 是较早的轻量入口，整合扫描、双语、追问和综述的入口是 `scanpdf/workbench.py`。[工作台说明](https://github.com/ybb-kami/evidence-desk/blob/22cd8b7cd6a3d6340c809056cf0f927d966745d5/scanpdf/README.md)

整篇本地翻译另装 `scanpdf/bilingual/requirements.txt` 并运行 `scanpdf/bilingual/install_models.py` 获取和校验双向权重。公式候选可按 `scanpdf/FORMULA_REVIEW.md` 配置独立模型环境。基础上传、OCR、定位及本交接中的原题回放无需模型密钥。DeepSeek 由部署侧按现有私密配置决定是否启用，启动时需显式加 `--enable-deepseek`；同一冻结部署的真实调用、界面回答和引用回跳需要一起验收。[本地翻译](https://github.com/ybb-kami/evidence-desk/blob/22cd8b7cd6a3d6340c809056cf0f927d966745d5/scanpdf/bilingual/README.md) · [公式模型](https://github.com/ybb-kami/evidence-desk/blob/22cd8b7cd6a3d6340c809056cf0f927d966745d5/scanpdf/FORMULA_REVIEW.md)

## 实际验收

| 输入或操作 | 结果 |
|---|---|
| 赛题包 PDF 入库 | 冻结记录中 39/39 份均通过 HTTP 导入。v7 后传改名的 v1 会提示版本选择，保留及恢复可用。本轮检索复验另在空临时库通过 HTTP 导入 14 份同哈希原件，扫描件 15 页全部重新 OCR。 |
| 评委可点回原页的问答 | 本轮 Chromium 重跑原题，15 个实例中 12 个答案完整有据，2 个证据部分、1 个无证据；三题均缺原包以外的 GPT-2 全文。补入已校验的 GPT-2 PDF 后，3 个补充实例均答全。两次输出与冻结版逐项 JSON 完全一致。 |
| 单篇例子 | Attention v1 的第六作者 Aidan N. Gomez 和 University of Toronto 在物理第 1 页。8 块 NVIDIA P100 在第 7 页。英德 BLEU 为 28.4，既有模型最佳 26.36，相差 2.04，可点回第 8 页表 2。脚本核对文件哈希、实际 PDF 路由与页面内全部出处链接。 |
| 扫描及长书 | 扫描图有 3 张已恢复结构的表，1 张部分恢复的表。4 个复杂公式的默认结果为区域定位。541 页原件 6,890 个索引块，已有尾部原文查询记录。|
| 新问法 | 本轮 73 个公开单篇可答题只有 7 个的前五片段找到了全部标注物理页。10 个扫描表问题为 0/10。该项衡量页码检索，尚缺通用问答质量验收。 |

由仓库根目录复测。脚本使用独立临时数据目录和回环端口。浏览器验收另需 Playwright 与 Chromium，长书验收脚本另用 Poppler 的 pdftotext。路径均可替换。

```sh
python scanpdf/acceptance/official_http.py /path/to/pdf --chromium /usr/bin/chromium
python scanpdf/acceptance/official_http.py /path/to/pdf --gpt2 /path/to/GPT2-course.pdf --chromium /usr/bin/chromium
python scanpdf/acceptance/corpus.py /path/to/pdf --ocr
```

[原题逐题回放](https://github.com/ybb-kami/evidence-desk/blob/22cd8b7cd6a3d6340c809056cf0f927d966745d5/scanpdf/acceptance/results/questions-contest.json) · [GPT-2 补测](https://github.com/ybb-kami/evidence-desk/blob/22cd8b7cd6a3d6340c809056cf0f927d966745d5/scanpdf/acceptance/results/questions-extra-gpt2.json)

## 公网交接条件

本轮在临时工作台实际发送请求。本机入口 GET 返回 200；给同一进程发送评审域名的 Host，GET 返回 403；本机 Host 加外部 HTTPS Origin，POST 也返回 403。普通转发即便打开首页，上传仍可能失败。应用按单一活动文库设计。冻结仓库的公网指导区分预计算静态展示与可实时上传的隔离部署。[入口校验代码](https://github.com/ybb-kami/evidence-desk/blob/22cd8b7cd6a3d6340c809056cf0f927d966745d5/scanpdf/workbench.py) · [仓库部署说明](https://github.com/ybb-kami/evidence-desk/blob/22cd8b7cd6a3d6340c809056cf0f927d966745d5/deploy/README.md)

- 评委入口应专属科研助手。动态上传需要独立服务身份、独立文库、实际对外域名的同源验证及受控的解析资源。应用端口继续绑定回环。多位评委可上传个人文件时，应隔离会话与存储。实际登录方式按赛题提供给评委。
- 需要配置新入口时，由当前部署线负责。外部浏览器最终应能打开作品、上传一份新 PDF、提出新问题、点回该 PDF 的物理页，并验证不能读取另一个空间的文献。静态预演仅能覆盖阅读与预生成结果。
- 文库默认单文件 24 MiB、最多 50 份、原文总量 128 MiB。39 份原包合计 131,842,159 字节，若全部预装于同一文库，只余 2,375,569 字节。部署端可把完整预置库和评委新上传空间分开；否则一份普通大小的新论文就可能超限。反代上传限制还需容纳原始 541 页教材的 22,344,992 字节。
- 本轮未验收公网动态上传、该候选公网 DeepSeek 问答、真实外部下载入库、全文学术译文质量及未知问题的生成准确率。评委专用 URL、授权情况和实际部署 SHA 以负责部署的线程完成外部验证时的记录为准。此次只做本地交接与核验，未访问或操作 ETH 服务。
