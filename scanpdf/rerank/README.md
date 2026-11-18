# 本地检索重排试验

此目录提供 Qwen3-Reranker-0.6B CPU 客户端和一个独立的检索排序复测。模型比较问题与候选原文，返回重排分数。`Reranker.rerank(question, passages)` 沿原次序接收文献证据，保留文章、物理页、块号、分段和原 RRF 分数。结果增加 `rerank_score`，同分沿用输入次序。候选只能重新排序，未进入候选池的答案段无法补回。分数用于排序，不能当作事实支持概率。

## 安装与运行

安装脚本从公开 Docker Hub 注册表按固定 OCI manifest 安装模型与许可证。GGUF 约 1.20 GB，Apache-2.0。部署端可复用经过校验的 llama.cpp b7940 Linux x86-64 CPU 发布包，其中 `llama-server` 提供排序接口。[模型仓库](https://hub.docker.com/r/ai/qwen3-reranker) · [运行时来源](../local_llm/README.md)

| 固定工件 | SHA-256 |
|---|---|
| OCI manifest | `bcf5005067bd62a99a68d91e15f23bcc7f1d48706c81d7c97e4f7323e337c35c` |
| Qwen3-Reranker-0.6B F16 GGUF | `9c9c23f7de6dce710fe77bb698659d06179d42075b13834af260c3f2145af662` |
| 许可证层 | `609e2cb599f84aaa41d8ef29d8fdb04d164fab22e8d9292ca34a599d0f56a338` |
| 本机派生 Q4_K_M GGUF | `006462ec37ebdc64b898bd494131e844c58c4b1dd93e5227b3df644c92355c1a` |

```bash
ed_rerank_dir=~/.cache/evidence-desk-rerank
python -m scanpdf.rerank.install --directory "$ed_rerank_dir"
/path/to/llama-b7940/llama-quantize \
  "$ed_rerank_dir/Qwen3-Reranker-0.6B.gguf" \
  "$ed_rerank_dir/Qwen3-Reranker-0.6B-Q4_K_M.gguf" Q4_K_M 1
/path/to/llama-b7940/llama-server \
  --model "$ed_rerank_dir/Qwen3-Reranker-0.6B-Q4_K_M.gguf" --alias qwen3-reranker-0.6b \
  --host 127.0.0.1 --port 39357 --threads 1 --threads-batch 1 --threads-http 1 \
  --parallel 1 --ctx-size 4096 --n-gpu-layers 0 --pooling rank --rerank
```

实测派生文件为 396,476,128 字节。单线程量化耗时约 57 秒，启动后的 Q4_K_M 进程驻留内存约 1.23 GiB。用于推理的模型文件从 1.20 GB 减为约 0.40 GB。复测记录保留上游 F16 的来源与哈希。

```python
from scanpdf.rerank import Reranker

ranked = Reranker(port=39357).rerank(question, rrf_passages)
```

## 比较方式

六道开发题在查看任何排序结果前定稿，来自三份独立公开文档。三组分别考察中问英证、英语改写、同一物理页有语义相近的干扰段。文档的固定镜像地址、SHA-256、问题及人工定位的答案段在样本文件中。此试验未使用封存验收题。[开发样本](dev-cases.json)

基线用现有 E5 与 BM25 的 RRF 取前 8 条。重排接收这个列表的固定前缀。以物理页、块号和分段都匹配作为命中，统计 Top-1、Top-3、MRR，另计单线程重排新增耗时。两边用同一批候选。样本可检验安装与推理可行性，也能发现候选移位的具体正负例。六题不足以推断整套评测或长文档的准确率。

F16 在单核、低优先级并与其他推理共用 CPU 时，第一个 8 段请求超过 120 秒，未返回开发题排序。随后冻结同样的六题和标签，将重排范围收窄为 RRF 前 3 段，并由同一已校验 F16 权重派生 Q4_K_M。调整时尚未看到开发题的重排结果。前 3 段试验的 Top-3 只反映候选覆盖，排序效果看 Top-1 和 MRR。

按样本 JSON 的名称保存三份 PDF，然后在已配置 E5 模型的工作台环境中运行。

```bash
python -m unittest scanpdf.rerank.test_client
python -m scanpdf.rerank.benchmark --papers /path/to/pdfs \
  --e5 /path/to/verified-e5 --pool-limit 3 --timeout 300 \
  --cache /path/to/benchmark-e5-cache --report /path/to/new-rerank-report.json
```

## 真实结果

| 六题主试验 | RRF Top-1 | 加重排 Top-1 | RRF MRR → 重排 MRR | 新增耗时中位数 |
|---|---:|---:|---:|---:|
| 中问英证 | 0/2 | 0/2 | 0 → 0 | 6.72 秒 |
| 英语改写 | 1/2 | 1/2 | 0.50 → 0.50 | 12.48 秒 |
| 同页歧义 | 2/2 | 2/2 | 1.00 → 1.00 | 15.31 秒 |
| 合计 | 3/6 | 3/6 | 0.50 → 0.50 | 11.98 秒 |

三道未命中题的标注证据均未进入候选池，双方 Top-3 同为 3/6。六条自然问法均由现有的 RRF 函数回退至 E5 单路，严格 BM25 无候选。现有暖查询耗时 0.034–0.092 秒，单核 Q4_K_M 另增 5.92–17.25 秒。跨语第 2 题中，重排仍优先选择相关的小节标题，标题没有展开源句反转与短程依赖的关系。[逐题结果](results-q4.json)

另单独固定三个关键词短问诊断融合分支，三条均实际激活 BM25 和 E5。Top-1 从 3/3 降至 2/3，MRR 从 1.00 降至 0.83，Top-3 保持 3/3。`passage encoder FAISS` 的正确正文被移到第二位。新增耗时中位数 10.67 秒。本试验保留为独立组件，现有工作台排序继续使用原有流程。[双路样本](dev-fused-cases.json) · [双路结果](results-fused-q4.json)
