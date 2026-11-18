# 证据台本地 RAG 运行与接力

青海大学袁瑞个人赛作品，报名系统队伍名 `26CZB0001406`。本次工作的验收目标是让评委导入新 PDF 后能提出新的中英文问题，得到可回到该版本物理页、原文块或表格单元格的回答。评审重点为赛题预置的版本去重、纯扫描 PDF、541 页教材，以及 6 个研究工作流。对检测框、OCR、表结构、模型候选、人工核验分别记录证据。

新代码在私有仓库独立分支 `feature/research-assistant-rag`。先前已交接的扫描版本在 `feature/scanpdf-parser`，SHA 为 `22cd8b7cd6a3d6340c809056cf0f927d966745d5`。此处为本机运行说明。服务器配置和评委入口由部署线管理。凭据只能使用运行时的私密配置。

## 供部署线程取用的已测基线

已从私有远端回读 `feature/research-assistant-rag` 历史中的 `5203688cf7d8b1f3340b63eeada92167855455bc`。部署候选和最终演示须各自记录 SHA。用已授权的 Git 连接在新目录获取，密钥由部署端环境变量注入。

```sh
git clone --branch feature/research-assistant-rag https://github.com/ybb-kami/evidence-desk.git evidence-judge
cd evidence-judge
git rev-parse HEAD
```

- 此基线的 Chromium 走实际上传和单篇证据追问。新建三页文献后，中文问答给出「37」并引用物理页 2；英文设备题给出 capacitive gauge 并引用页 3；设备序列号缺证据，返回拒答。服务端逐字回读字符范围。两正一负只说明这三个样本。[完整响应与耗时](acceptance/results/local-rag-new-document.json)
- 扫描样本的表 2 可从问答打开物理页 8 的行 12 列 2 与列 3，读数 28.4 和 41.0；两次跳转均核对单元格边框。公开 73 题检索页码命中 48/73，对方公开记录 68/73。原包 39 份的 15 道示例实例，12 个答全，2 个部分，1 个缺失；补充原包以外 GPT-2 后另 3 个答全。[扫描定位回放](acceptance/results/scan-qa-cell-links.json) · [评审要求与同输入对照](RUBRIC.md)
- 新评委文库单独设目录。单 PDF 上限 24 MiB、文库上限 50 份 / 128 MiB，39 份资料同时装入后剩约 2.27 MiB。上传体验需要单独的空文库。保持评委入口、进程、文库与原有监控和个人文档隔离。演示地址应直接打开科研助手。公网问答与官网提交不在上述本机验收内。

## 从干净环境运行

在新检出的仓库根目录使用 Linux x86-64 和 Python 3.12。系统需装 `tesseract-ocr`、`tesseract-ocr-eng`。额外中文 OCR 使用 `tesseract-ocr-chi-sim` 和 `--lang chi_sim+eng`。模型安装器各自核验固定来源和 SHA。逻辑、依赖和原始测试记录均可在 Git 中核对，模型和运行时二进制另行下载。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r scanpdf/requirements-rag.txt
.venv/bin/python -m scanpdf.semantic.install
.venv/bin/python scanpdf/bilingual/install_models.py
.venv/bin/python -m scanpdf.qa_reader.install .cache/qa-reader
.venv/bin/python scanpdf/local_llm/install.py --directory .cache/qwen-local
```

第一个本机终端运行生成模型。二进制、GGUF 和许可证的哈希见模型说明。Qwen 生成服务使用 4 个 CPU 线程，实测驻留内存约 2.43 GiB。完整工作台另需 PDF 解析、语义索引、抽取和翻译的内存。可在部署时只开启目标机器承载得起的组件。[Qwen 与 llama.cpp 安装记录](local_llm/README.md)

```sh
.cache/qwen-local/llama-b7940/llama-server \
  --model .cache/qwen-local/Qwen3-1.7B-Q4_K_M.gguf --alias evidence-local-qwen \
  --host 127.0.0.1 --port 39348 --threads 4 --n-gpu-layers 0 \
  --ctx-size 4096 --parallel 1 --chat-template-kwargs '{"enable_thinking":false}' --reasoning-budget 0
```

第二个终端仍在仓库根目录运行。使用新的专用文库目录。`/followup` 是单篇问答，文库中的“问这篇论文”也会选中相应版本。工作台和模型各自监听回环地址。

```sh
export EVIDENCE_QA_READER="$PWD/.cache/qa-reader/mobilebert_qa.tflite"
export EVIDENCE_LOCAL_QA_URL=http://127.0.0.1:39348/v1/chat/completions
export EVIDENCE_LOCAL_QA_MODEL=evidence-local-qwen
.venv/bin/python scanpdf/workbench.py --data ../evidence-rag-judge-data --port 8765
```

只运行基础提取与已识别表格问答时可用 `scanpdf/requirements.txt`。本机多语向量、翻译、读者及本地生成有各自依赖和可用状态。部署者选用 DeepSeek 时另按项目说明配置，外发按钮须由使用者逐次确认。冻结工作台的 Host/Origin 校验和单文库性质仍适用。[公网隔离条件](../deploy/README.md)

## 一次提问如何回答

1. 用户选定一个确定的 PDF 版本，或者进一步限定在一个已引用的段落。完整 SHA 决定来源文件。
2. 能以原件确定的作者、硬件、已标记超参数和表格行列先按原文作答。扫描表会区分缓存 OCR 和原图裁切重读。无法确定的数值保留缺口，可点击单元格核对。[表格问答案例脚本](acceptance/scan_qa_browser.py)
3. 对其他问题使用 BM25、词项覆盖与可选 OPUS-MT 查询翻译。已安装 multilingual-e5-small 时并行考虑跨语向量相似度，按排名融合候选。大型首次索引在后台运行，状态显示在页面。每个文库有自己的可校验向量缓存，PDF、文本、OCR 更新及预处理版本参与缓存身份。[语义索引](semantic/README.md)
4. 可选 MobileBERT 从原句抽取有精确字符起止的候选。其候选和分数不能证明问题可答。Qwen 只读取本次命中的短摘录。应用从服务器侧核对证据编号、所引原文和数字。模型回答仍可能误解同页的对象、单位或关系，应核对被引用的完整句子。[抽取模型及反例](qa_reader/README.md)
5. 点击“定位原文”能查看指定版本、物理页、文本块和可核验的字符框；归档后的已存版本仍可只读回查。表格定位重算原件坐标，缺少精确几何依据时退回可确定的范围。

## 复测

下列测试启动回环服务和专用临时文库。Chromium 验收需额外安装 `playwright`，并提供本机浏览器路径。第一项使用新生成的三页文献，检查两个有依据的问题和一个资料未报告的问题；第三项的 GPT-2 额外资料见赛题验收说明。

```sh
.venv/bin/python -m pip install playwright
.venv/bin/python scanpdf/acceptance/local_rag.py --chromium /usr/bin/chromium
.venv/bin/python scanpdf/acceptance/scan_qa_browser.py /path/to/pdf/1706.03762v1_img.pdf
.venv/bin/python scanpdf/acceptance/official_http.py /path/to/pdf --chromium /usr/bin/chromium
.venv/bin/python scanpdf/acceptance/synthesis_twenty.py /path/to/pdf --chromium /usr/bin/chromium
```

赛题原问、公开改写题和独立封存题的分母分别记录。原页命中、完整证据覆盖、答案字段、缺证据时能否拒答是不同指标；复核时应保留失败项和模型配置。正式视频和线上运行版本应注明实际代码提交，官网回执由提交线保存。
