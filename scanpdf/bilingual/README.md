# 双语对照阅读

从本机工作台首页打开「双语精读」，选择活动文库中的 PDF、物理页码和翻译方向。点击段落按钮翻译整段，也可以先在同一段内划选文字。可整页翻译，或从当前页连续处理到末页。要读全篇，从第 1 页开始。右栏保存本次标签页中各文献、方向和页段的最近结果，跳页后可返回，刷新页面会清空。翻译时可停止，已经完成的段落保留。

每一行左右对应同一源段，滚动任一栏时两侧共同移动。「核对原 PDF 段落截图」直接从原文献的页面坐标渲染截图，可核对公式、字体及分栏位置。已在扫描工作台完成 OCR 的纯图像页也可阅读，原文侧标有 OCR。PDF 文字层或 OCR 的误读会传入机器翻译。此版本采用段落重排，尚未导出双语 PDF。

## 安装本地模型

以下命令从仓库根目录运行。Python 3.12、Linux x86_64 CPU 已验证。使用新虚拟环境，保留主项目的依赖约束。安装命令只下载软件和模型，工作台翻译只读取本机 PDF 和模型。在安装了原工作台运行依赖的虚拟环境中，也可以只追加安装双语模块的 requirements.txt。

```bash
python3 -m venv .venv-workbench
. .venv-workbench/bin/activate
python -m pip install -r scanpdf/requirements.txt -r scanpdf/bilingual/requirements.txt
python scanpdf/bilingual/install_models.py
python scanpdf/workbench.py --data ./data
```

默认模型目录为 `~/.local/share/evidence-desk/models`。可设置 `EVIDENCE_TRANSLATION_MODELS` 指定安装和读取位置。安装器从固定提交下载两份压缩包，逐份校验 SHA-256，只提取词表、模型、元数据及说明文件。已持有同哈希压缩包的机器可将它们命名为 `en-zh.zip` 和 `zh-en.zip`，用 `--archives <目录>` 离线安装。安装后两套模型文件合计约 162 MiB。部署源码未附模型权重。

| 方向 | 打包来源 | 压缩包 SHA-256 |
|---|---|---|
| 英译中 | [Argos 英中 1.9 镜像](https://github.com/sankarshan-kundu/argosmodels/tree/9b48df6c5e331600854eb0315dbf2aaf34a1f8a3) | `433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab` |
| 中译英 | [OPUS 中英 1.0 镜像](https://github.com/jiangwensm/OPUS-argosmodels/tree/10feffc6ca67d3870ed7a546b17ebdc16246dabc/1.0) | `1279759b6362817c14e70af4d3ca2056d8596b2ae9c691502b433b631c23194e` |

两份压缩包的 README 均将原始 OPUS-MT 模型署名为 Jörg Tiedemann 与 Santhosh Thottingal，列出论文 *OPUS-MT — Building open translation services for the World*（EAMT 2020）及原始模型的 CC BY 4.0 许可。安装时保留对应 README。[论文](https://aclanthology.org/2020.eamt-1.61/) · [许可证](https://creativecommons.org/licenses/by/4.0/)

若某一方向未安装，页面会尝试浏览器自带的 Translator API，首次使用可能下载浏览器语言包。浏览器缺少接口或语言对时，页面提示安装本机模型。两方向实测均使用本机 CTranslate2 CPU 模型。浏览器自带模型的覆盖和质量取决于浏览器。

## 原文保护与验收

对 PDF 数学字体、ASCII 等宽字体、OCR 公式区域，以及可识别的标记公式、行内代码和代码行，按源字符范围保留。若 PDF 字体缺乏语义标记，或版面复杂，读者可勾选「公式或代码整段保留」。原始页面截图可用于核对上下标、跨行或多栏结构。

```bash
python -m unittest scanpdf.bilingual.test_routes
node scanpdf/bilingual/test_reader.cjs
# 端到端测试另需 Python playwright 和 Chromium，以及赛题中的两份原始 PDF
python scanpdf/bilingual/check_browser.py --samples /path/to/contest-pdf --chromium /usr/bin/chromium
```

浏览器实测从临时文库导入原 PDF，在本机 HTTP 页面划选文字并运行真实模型。

| 文献与位置 | 核验结果 |
|---|---|
| 1706.03762v1，物理第 1 页，摘要第 1 句 | 得到中文译文。模型将 *convolutional neural networks* 误译为「转录神经网络」，应按原文理解为「卷积神经网络」。|
| 2501.12948v2，物理第 75 页，中文提示语 | 「请参考示例的格式，完成最后的测试题。」译为 “Please complete the final test with reference to the sample format.” |
| 1706.03762v1，物理第 4 页，多头注意力公式 | 对已抽取公式的划选范围逐字符相等，原 PDF 裁图可以打开。|
| 合成代码/公式页 | 整页翻译将 “Run the test.” 译为「运行测试。」；`print(value + 1)` 及 `x = 2 + 3` 未改变。|

两篇赛题资料中的抽查验证了真实选区、页面回链和模型调用。这次抽查没有评定整篇译文质量。专业术语、歧义指代以及公式周围的语句宜对照原页阅读。
