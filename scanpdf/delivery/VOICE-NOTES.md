# 普通话配音版本与核验

## 评审推荐版

`evidence-desk-kokoro-mandarin-1080p.mp4` 使用本项目讲稿、项目实际浏览器录屏，以及本机合成的普通话解说。语音模型为 Hexgrad Kokoro-82M v1.0。此次使用 sherpa-onnx 发布的 INT8 多语种版本，选择模型元数据列出的中文音色 `zf_xiaoxiao`（speaker ID 47）。演示无需联网合成，交付文件中不包含模型权重。

模型发布者注明权重采用 Apache 许可，所用发行包附 Apache License 2.0。发行包 README 记载，`voices.bin` 由 Kokoro-82M 官方 voices 目录转换。此处仅列出模型与音色的来源、发行许可和成片用法，不作其他素材权利的推断。项目源码与录屏中展示论文的使用条件应分别核对。[模型发布者说明](https://github.com/hexgrad/kokoro) · [所用模型发行包](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_0.tar.bz2)

下载归档 SHA-256 为 `72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653`。归档中 LICENSE 的 SHA-256 为 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`。

## 对照试听依据

选择开场、扫描页和英中阅读三段，给三种语音方案输入同一讲稿。采用同一本机中文 SenseVoice 模型反听，按去标点、去空格后的 344 个参考字符计算 Levenshtein 距离。下表使用调整时长和响度前的语音。结果衡量这三段录音的机器可辨性，人工听感仍应由队伍最终审核。

| 合成方案 | 第 1 段 / 106 字 | 第 8 段 / 118 字 | 第 13 段 / 120 字 | 总编辑距离 / 字符数 |
| --- | ---: | ---: | ---: | ---: |
| Kokoro 中文 | 6 | 8 | 6 | 20 / 344（5.8%） |
| 既有 Piper 华燕 | 7 | 9 | 10 | 26 / 344（7.6%） |
| eSpeak NG 规则合成 cmn | 25 | 13 | 25 | 63 / 344（18.3%） |

规则合成测试使用 eSpeak NG 的普通话字典及共振峰合成。它可以辨明段落大意，三段对照里专名和术语误听更多。历史版 `evidence-desk-mandarin-1080p.mp4` 使用 Piper 华燕。华燕发行资料将其训练语料许可标为 Unknown。当前推荐提交 Kokoro 版，或使用参赛者自行录制的普通话。人工重录可使用[时间码讲稿](mandarin-transcript.md)。[eSpeak NG 项目说明](https://github.com/espeak-ng/espeak-ng) · [历史配音模型说明所在发行包](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-zh_CN-huayan-medium.tar.bz2)

## 成片技术核验

推荐版时长 375.675 秒，1920×1080 H.264，44.1 kHz AAC。全部 9,382 帧与音轨经 FFmpeg 完整解码；压缩视频流的 SHA-256 与历史版一致。成片综合响度 −16.0 LUFS，真峰值 −1.6 dBTP。MP4 文件 SHA-256 为 `d342500c3eebd69c03da68b05231fcd5021de9e7e639f8603e961d5b67c62527`。
