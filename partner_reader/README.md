# 证据台主应用的本机双语桥接

本目录复用协作者交付的提交 `abbb714` 中 `scanpdf/bilingual/` 的
`routes.py`、`local.py`、`reader.html`、`reader.js`、`export.js`、
`requirements.txt` 和前端测试。不是采用项目根部其他版本的 `bilingual/`。
翻译算法 CTranslate2 / SentencePiece、段落/选区、原文保护、截图和离线
HTML 导出源自协作者实现。没有带入下载器、安装脚本或权重。

局部适配：使用主 Handler 注入的错误类型以及现有活动文库；未读取上游
ScanLibrary 的专有 OCR 索引。原生文本与已明确导入的 OCR 文字层可读；
原扫描无文字时不伪造结果。主应用自己的 OCR 衍生件在阅读器标作 OCR，
仍应与原扫描对应物理页对照。模型缺失时保留原文和截图、翻译请求返回
503；没有浏览器 Translator 自动回退。缺失依赖不影响主应用。翻译采用
上游 420 token 限制，并以 409 拒绝同时进入的推理。

## 可选本地配置

在已经备有这些组件的环境中，本地依赖为 `ctranslate2==4.6.3` 与
`sentencepiece==0.2.1`。`EVIDENCE_TRANSLATION_MODELS` 指向部署者
事先取得并验证的模型目录；未配置时读取
`~/.local/share/evidence-desk/models`。各方向 `en-zh` 与 `zh-en`
分别要求 `sentencepiece.model`、`model/model.bin`、`model/config.json`
及 `model/shared_vocabulary.json`。目录检测仅证明文件存在，不能代替
模型加载和真实推理验收。主应用和阅读页都不会联网取得模型或依赖。

## 验收与边界

```sh
python -m unittest -v partner_reader.test_bridge
node partner_reader/test_reader.cjs
```

桥接测试使用临时本机 HTTP 及真实 PyMuPDF 源页和裁图、双向模拟引擎，
检查原件不变、空扫描不识别、可选 OCR 衍生件、归档拒绝、403/400/404/503。
Node 测试验证 Unicode 选区、原文保护、术语、HTML 转义及旧译段失效。
本执行环境没有双向权重或推理依赖，不宣称在主应用跑通了真实双向翻译。
