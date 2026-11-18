# 5203688c 评委视频来源与复录说明

新版《证据台》评委视频录自 Chromium 实际操作界面，使用完整代码提交 `5203688cf7d8b1f3340b63eeada92167855455bc`。归档该提交后在两个独立的回环文库起服务。画面保持 1920×1080，时长 7 分 00.6 秒，普通话合成旁白、中文字幕。参赛信息为个人赛、青海大学、袁瑞、报名系统队伍名 `26CZB0001406`。[实际成片](evidence-desk-mandarin-1080p-5203688c.mp4) · [计时讲稿](mandarin-transcript-5203688c.md) · [技术校验](video-check-5203688c.json) · [代表性执行证据](video-proof-5203688c.json)

## 原件、音色及使用范围

- 英文新问法和原生表格使用项目原创虚构调查 PDF。五行耗时 CSV 为项目原创虚构数据。复录包包含固定样本生成脚本，文件 SHA‑256 见执行证据。
- 扫描页、双语原件与 20 篇综述来自固定赛题资料包镜像。录屏局部显示所核对的原页。复录包不附第三方 PDF 权重或文件。对外公开传播的授权范围请按竞赛及原论文条件核对。[固定资料包](https://github.com/CuiMiles/ResearchTrace/tree/52d11950da4f3da1dcb830d6212665eb89c0394f/evals/pdfs)
- 普通话旁白为本机运行的 Hexgrad Kokoro‑82M v1.0 `zf_xiaoxiao`（speaker ID 47），运行库为 sherpa‑onnx 2.0.1。所用 INT8 发布包附 Apache 2.0 许可。归档 SHA‑256 为 `72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653`，随附 LICENSE 为 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`。开场说明语音为本机合成，未用背景音乐。不附语音模型。[上游 Kokoro](https://github.com/hexgrad/kokoro) · [核验的 INT8 发行包](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_0.tar.bz2)

## 画面中的数据状态

上传的新 PDF、英文问法、原页高亮和原生定位现场执行。扫描原件提前导入，第 8 页在镜头内从无缓存状态开始识别，后面的扫描表查分复用刚生成的缓存。20 篇文献提前导入独立文库，图谱及综述在画面内计算。双语章节在同一冻结提交和同一原件上另录，现场设置术语保留并运行英中翻译。CSV 在当前浏览器载入、绘制与导出行映射。

问答实际使用 BM25、本机 multilingual‑E5‑small 和 MobileBERT。双语实际使用 OPUS‑MT。候选提取、术语保留和引文定位来源均见执行证据。录制没有启用本机 Qwen 生成或外部 DeepSeek 服务。语音自动反听可识别学校、个人赛、扫描数值与图谱数量，参赛者姓名的最终听感需人工试听。

## 复录

准备 Python 3.12、Chromium、FFmpeg、Playwright 1.62、该提交锁定的模型运行依赖和 Noto Sans CJK SC。Playwright 可用 `python -m playwright install ffmpeg` 准备录制编码器。也可以用已配置的 `PLAYWRIGHT_BROWSERS_PATH` 指向其本机安装目录。赛题原件、E5、MobileBERT、OPUS‑MT 和 Kokoro 资产各自按固定哈希准备，均放在交付包外。[复录源码](video-replay-5203688c.zip) · [模型与接入入口](../RAG_HANDOFF.md)

解压复录源码并进入该目录。先把下列 shell 变量设为自己的实际路径。`repo` 为所测仓库，`pdf_dir` 为赛题原件目录。`reader` 指向 MobileBERT 模型，`dense`、`translation` 指向模型目录。`kokoro` 是已经解压的发行包目录，`kokoro_archive` 是原始下载归档。

```sh
python -m pip install 'sherpa-onnx==2.0.1' 'playwright==1.62.0'
python voice.py --model "$kokoro" --archive "$kokoro_archive" --out voice
python replay.py --repo "$repo" --expected-sha 5203688cf7d8b1f3340b63eeada92167855455bc \
  --pdf-dir "$pdf_dir" --out raw --reader-model "$reader" --dense-models "$dense" \
  --translation-models "$translation" --voice voice/voice-manifest.json --record --pacing 1
python record-bilingual-retake.py --frozen raw --pdf-dir "$pdf_dir" \
  --translation-models "$translation" --out bilingual-retake
python assemble_inputs.py raw bilingual-retake selected-takes
python compose.py --raw selected-takes --narration voice --out video
python inspect_final.py video
```

本机依赖分开安装时，`replay.py` 和 `record-bilingual-retake.py` 可重复传入 `--runtime-path`。录制在临时文库执行，按次回放用时受设备负载影响。技术检查验证时长、分辨率、编码、整片解码、响度和最终 SHA‑256。原始录屏和暂存文库不属于成片交付。
