# 本地生成式问答复测

使用 Qwen3-1.7B Q4_K_M 与 llama.cpp 在 Linux x86-64 上运行 CPU 推理。复测从项目原有的 `engine.find_evidence` 获取公开 PDF 的页段，再调用本机模型生成回答和引文。安装需要 Python 3.12、约 1.2 GB 磁盘空间，以及 GitHub Releases 和 Docker Hub 公共 OCI 注册表的下载权限。

```bash
ed_llm_dir=~/.cache/evidence-desk-local-llm
python scanpdf/local_llm/install.py --directory "$ed_llm_dir"
"$ed_llm_dir/llama-b7940/llama-server" \
  --model "$ed_llm_dir/Qwen3-1.7B-Q4_K_M.gguf" \
  --host 127.0.0.1 --port 39348 --threads 4 --n-gpu-layers 0 \
  --ctx-size 4096 --parallel 1 \
  --chat-template-kwargs '{"enable_thinking":false}' --reasoning-budget 0
```

模型安装文件约 1.11 GB，CPU 运行时压缩包约 24.5 MB。脚本按固定 OCI manifest 获取 GGUF，校验原始大小和 SHA-256，校验成功后再保留文件。Docker Hub 使用仅用于下载该公开仓库的匿名令牌。启动后可访问 `http://127.0.0.1:39348/health` 和 OpenAI 兼容的 `/v1/chat/completions`。四线程复测期间观察到模型进程的驻留内存约 2.43 GiB。

| 文件或记录 | SHA-256 |
|---|---|
| Qwen3-1.7B Q4_K_M GGUF | `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897` |
| Docker OCI manifest | `5eef42cd04fdde10a06e082f030545f8c29ec182e0f304a9af9815e106e2ec5c` |
| llama.cpp b7940 Linux x86-64 压缩包 | `1b05844f4570c630fa1eb08483ef629bf92939712cab426cb269b5dcaccbfbd7` |

Docker 公共 manifest 记录 GGUF 源为 `unsloth/Qwen3-1.7B-GGUF`，配置声明 Apache-2.0。llama.cpp b7940 的提交为 `e6e934c5ea1540522171ec91eee05d37909286d1`，运行时归档附 MIT 许可证。[Docker 模型仓库](https://hub.docker.com/r/ai/qwen3) · [Qwen3 发布说明](https://qwenlm.github.io/blog/qwen3/) · [llama.cpp 发布和校验值](https://github.com/ggml-org/llama.cpp/releases/expanded_assets/b7940)

## 用真实 PDF 验证回答和引文

在已安装工作台依赖的 Python 环境中运行。

```bash
python scanpdf/local_llm/smoke.py /path/to/1706.03762v1.pdf
```

复测脚本固定赛题包原件 SHA-256，在本机读取 PDF，每次取最多 4 个检索段落，先发编号原文，再发问题。中英文分别使用原生语言提示。请求用 `temperature=0`、`seed=42`、`max_tokens=200`，通过 JSON Schema 约束 `status`、`answer`、`source` 和 `quote`。正例校验事实值、物理页与块号，并查验模型引用的字符确实出现在对应检索段落里。负例提供与训练设备相关的真实段落，该段包含 GPU 型号和数量，未记录序列号。精确提示词、请求体与验证逻辑见复测脚本。[复测脚本](smoke.py)

| 问题 | 模型输出 | 原文和位置 | 实测耗时 |
|---|---|---|---:|
| How many NVIDIA P100 GPUs were used for training? | 8 | “8 NVIDIA P100 GPUs”，物理第 7 页块 3 | 1.67 秒 |
| 训练 beta1 beta2是多少？ | 训练 beta1 和 beta2 是 0.9 和 0.98。 | “β1 = 0.9, β2 = 0.98”，物理第 7 页块 5 | 4.18 秒 |
| What serial number is reported for the NVIDIA P100 GPUs? | `status=insufficient`，`source=0`，答案及引文均为空 | 向模型提供了物理第 7 页块 3 | 1.30 秒 |

表中是已启动模型的一次顺序运行，输出速度约 24–25 token/s。用安装脚本另起一个实例后，三条响应内容相同，耗时依次为 28.98、7.75、1.60 秒。输入长度、缓存和 CPU 负载会影响实际延迟。测试覆盖已知位置的两个正例和一个缺失事实的反例，未衡量开放题准确率或端到端产品表现。结构约束保证字段形式，引文和事实仍需校验。此前的自由文本提示曾在模型拒答时附上无效引文编号。
