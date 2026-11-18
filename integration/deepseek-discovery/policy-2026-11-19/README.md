# 11 月 19 日四席网关人民币累计 20 元计价数据

本目录提供 [政策数据](policy.json) 和[汇率源文件](fx-usd-2026-11-18.json)。可以单独下载这两个文件。无需合并或替换服务器启动代码。

北京时间 11 月 19 日 00:10:21 复取的最新汇率文件仍标记 11 月 18 日，每美元 6.6662016 元。继续按 8.00 元折算，项目累计 20 元对应 2.50 美元。该汇率是政策选择，美元结算时须能覆盖实际折算及相关费用。价格和汇率的最晚失效时间均为北京时间 11 月 20 日 00:00，届时失效。项目历史支出、预扣及结果未知的请求继续占额。

### 本地可直接使用的字段

`policy.json.runtime_fields_available` 给出了当日的 `expiry_epoch` 和完整 `cap_policy`。源文件可用下列命令离线核对。第一列应等于 `cap_policy.fx_source_sha256`。

```sh
sha256sum fx-usd-2026-11-18.json
```

### 补全当日官方价依据

DeepSeek 官方 Harness 记录中，独立 Search 和 Fetch 各按每千次有结果的成功请求 5 元计价。公开模型目录给出的 Flash 非高峰美元报价为每百万 token 缓存命中 0.003、未命中 0.15、输出 0.60，高峰上浮至两倍。该目录引用 10 月 19 日读取的官网资料。本次云端无法读取官网实时价目。JSON 中保留了两份资料的固定提交、完整文件 SHA-256 和证据性质。[官方 Search/Fetch 记录](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70496aa02986ca6a820c5d497e816b69d7e/.agents/notes/implemented/feature/2026-10-15-deepseek-search-fetch-api.md#consequences) · [Flash 目录原始记录](https://github.com/anomalyco/models.dev/blob/d893237face842c5a4c918009d50c8e33b7c6523/providers/deepseek/models/deepseek-flash.toml)

本地能读取官网时，按账户结算币种读取**全天最高适用价格**。核对 Flash 文本输入（含未命中）、全部输出（含推理）以及两项独立工具。下面是现有网关预扣所能覆盖的单价上限。

| 账户计价币种 | 输入 / 百万 token | 输出 / 百万 token | Search / 千次 | Fetch / 千次 |
|---|---:|---:|---:|---:|
| 人民币 | 2.40 | 9.60 | 6.00 | 6.00 |
| 美元 | 0.30 | 1.20 | 0.75 | 0.75 |

人民币上限来自预扣金额乘以 8.00。美元工具上限来自网关每次预扣 750000 纳美元。账号适用费率超出任一对应上限时，现行预算计算不足以覆盖该项。汇率只用于折算；按人民币标价的工具直接与人民币上限比较。[官方中文价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) · [官方美元价格](https://api-docs.deepseek.com/quick_start/pricing/) · [独立 Search 和 Fetch 指南](https://api-docs.deepseek.com/guides/search/)

核实各项均在上限内后，用覆盖这三项的当日官方计价快照生成 `price_sha`，以当地实际复核时刻的 Unix 秒填写 `verified_epoch`。若需两份源文件，可分别计算原文 SHA-256，将 URL、哈希、结算币种、确认的峰值、单位和复核时间记录在同一公开计价凭证文件，再对该文件原始字节取 SHA-256。有效期起点取价格、汇率两者复核时间之较晚者。若只掌握本包中的历史价格资料，两个待核字段保持缺省。

配置中只采用原账本对应的路径、授权摘要、席位设置和既有的历史预留凭证摘要。本包不提供历史费用金额或任何新的开户依据，也不修改现行模型目录核验。[同账本续期说明](../FOUR-SEAT-HANDOFF.md)
