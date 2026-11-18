# DeepSeek 网页检索与抓取

DeepSeek 官方开源客户端 Harness 在提交 `ca33fd6843b205e279c7ccb6cb73a472c78f5760` 提供独立 Search 和 Fetch API 的协议、实现与测试。官方实施记录说明开发方于 2026-10-16 实测两个公开端点。此处仅实现 Bearer API 密钥方式。 [DeepSeek Search and Fetch 指南](https://api-docs.deepseek.com/guides/search/) · [官方 Harness 实现](https://github.com/deepseek-ai/deepseek-harness/tree/ca33fd6843b205e279c7ccb6cb73a472c78f5760/packages/web/web-deepseek-api) · [官方实测记录](https://github.com/deepseek-ai/deepseek-harness/blob/ca33fd6843b205e279c7ccb6cb73a472c78f5760/.agents/notes/implemented/feature/2026-10-15-deepseek-search-fetch-api.md)

| 操作 | 固定 HTTPS 请求 | 请求内容 | 返回内容 |
| --- | --- | --- | --- |
| Search | `POST https://api.deepseek.com/v1/tools/search` | 用户明确填写的 `query`、`count: 5`、`need_content: false` | `results[]` 的 HTTPS 来源、题名、摘要和日期 |
| Fetch | `POST https://api.deepseek.com/v1/tools/fetch` | Search 返回的首个合格来源 `url` | `content` 中的 Markdown，保留前 8,000 字并标记截断 |

上层按次取得用户同意，调用 `search(terms, authorize=ledger_authorize, fetch=…)`。每次 Search 或 Fetch 请求发出前由共用账本独立授权。仅选择 Fetch 且 Search 返回合格结果时执行 Fetch。回调拒绝会阻止对应请求。适配器只向 `api.deepseek.com` 发请求，拒绝 API 重定向，每次最多一搜一抓。检索词长度上限 128 字。应用不向检索 API 发送 PDF、页面片段、会话历史、原始聊天问题或本地文库。返回网页内容作为待核查的外部线索。

官方记录所引指南按带结果的成功请求计费，Search 和 Fetch 各为每千次人民币 5 元。预算可以保守按每次尝试各预留人民币 0.005 元。当前环境未读取实时价格表，无法确认美元报价、模型输入和输出 token 价格或账户折扣。 [官方计价页面](https://api-docs.deepseek.com/quick_start/pricing/) · [官方 Harness 计价依据](https://github.com/deepseek-ai/deepseek-harness/blob/ca33fd6843b205e279c7ccb6cb73a472c78f5760/.agents/notes/implemented/feature/2026-10-15-deepseek-search-fetch-api.md#consequences)

官方模型目录把 `deepseek-flash` 映射到显示名称 `DeepSeek-V4.1-Flash`。`1017` 是官方模型目录可提供的发布徽标，位于 `data[].client_metadata["deepseek-harness"].badge.text`。官方代码按该目录动态处理发布标记。 [官方模型 ID](https://github.com/deepseek-ai/deepseek-harness/blob/ca33fd6843b205e279c7ccb6cb73a472c78f5760/packages/llm/llm-deepseek/src/models.ts) · [官方模型徽标协议](https://github.com/deepseek-ai/deepseek-harness/blob/ca33fd6843b205e279c7ccb6cb73a472c78f5760/.agents/notes/implemented/feature/2026-10-22-listed-model-badges.md)

## 本机验收

```sh
python -m unittest -v scanpdf.discovery.test_deepseek_search
```

2026-11-18 在本机成功只读获取上述 DeepSeek 官方 GitHub 提交。访问用户提供的官方指南和计价页面，以及向两个服务端点发送无密钥 `HEAD`，均在代理 CONNECT 阶段返回 403。本机模拟协议验收通过请求授权顺序、精确载荷、固定域名、限长、拒绝跳转、拒绝非公开目标、预算拒绝及 Fetch 失败后的结果保留路径。本机实际付费调用待可访问官方端点且已授权的部署环境验证。
