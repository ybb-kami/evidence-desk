# 首次启用四席 API 的历史预留

本补丁叠在 `f183a7ad6818f2a985abad613d77412625bcbd66` 上，继续使用原四席网关、私网 Unix 代理和累计 20 元政策。它处理两笔 Windows/WSL 实际请求早于首个正式 SQLite 账本的情况。默认启动状态仍为 `off`。建账命令只做本地文件操作。

## 本地接手时的事实

本地线程报告官方模型目录和余额查询均返回 HTTP 200，公网此前在免费模式点开了 Attention v1 第 7 页。云端可读资料中尚未找到两笔旧调用对应的供应商 usage、实扣金额、完整出站次数或执行参数。旧冒烟代码包含 `max_tokens=1200`，执行记录不足以将该值归到两笔旧请求。余额快照本身无法确定项目历史支出。[旧冒烟代码](https://github.com/ybb-kami/evidence-desk/blob/4d05cb80fa2abf8e0297d39ede8c35218aa108c1/tools/smoke_deepseek_app.py)

先由本地在服务器、WSL 及 Windows 确认是否曾启用正式账本，并冻结已有收费发送器。若已找到正式账本，请沿用该库；还需单独核对两笔库外费用，计入前保持付费关闭。此命令会拒绝接管已有库、SQLite 旁文件和已有的首次开账记录。

## 三类可核依据

每份记录注明覆盖的历史执行（`direct-test`、`app-smoke`）、包含重试的出站次数上界、是否覆盖所有结果未知及未结算的尝试。一次执行只能列入一个覆盖范围。数值为正，金额使用十进制字符串。源文件保留在受控目录，文件名为 `<内容 SHA256>.proof`。原始提示、答文、账户信息与凭据均无需上传 Git。

| `kind` | 本地维护者核对的来源 | 程序校验 |
|---|---|---|
| `provider_billing_upper` | 覆盖两个时段的账单或可核对的项目金额上界，可按 CNY 或 USD。用完整期间的较大合计覆盖项目时，确认结算范围和剩余未知请求 | 核源文件哈希、覆盖范围、金额精度及币种，费用单列为历史预留 |
| `provider_usage_upper` | 每次请求单独的供应商响应，可只保留 `id/model/usage`。运维确认当时的型号、价格、额外费和全部尝试均已覆盖 | 读取并逐项复核 prompt/completion/total tokens 与互异的请求 ID，按经核对的峰价计算。每个响应只能覆盖一笔。若同一轮还有结果未知的重试，可对该轮整体改用有依据的保守上界 |
| `request_cost_upper` | 两次真实执行各自的请求日志或能绑定到该次运行的参数，以及当时适用的价表。若能确认型号、重试上界和供应商最大可计费输出，也可采用较大的已核上界 | 每次至少按完整输入窗口预留，计算输入、输出及额外费用后乘出站次数上界 |

对于通用账单和运行日志，人工负责核准其真实性、完整性、账户归属和字段含义。文件哈希用于绑定核准过的材料。程序可直接核对所支持的供应商 usage JSON 字段。

若有证据证明两次各仅发一次 Flash，完整输入上界均为 1,048,576，全部可计费输出上限均为 1,200，峰价分别为每 token 300 和 1,200 纳美元且无附加费用，则公式给出 **632,025,600 纳美元**。这是条件算例。历史参数缺失时不得采用这个数、浏览器测试的样例金额或零开账。若两次有不同输出上界，可分列两份记录。

## 供本地线程执行

从 `integration/deepseek-discovery` 运行。以下是按供应商账单填写 `records.json` 的形状。占位内容必须由本地可核证据替换。`includes_all_attempts_and_pending` 在实际确认覆盖后设置，次数为整数。

```json
[{
  "kind": "provider_billing_upper",
  "runs": ["direct-test", "app-smoke"],
  "source_sha256": "<脱敏账单文件的 SHA256>",
  "attempts_upper": "<核对后填写整数>",
  "includes_all_attempts_and_pending": true,
  "currency": "CNY",
  "amount_upper": "<可核的合计上界>"
}]
```

另外两类的字段可直接对照提交内的 `test_opening_reserve.py`。请求参数路径需填写 `model_release`、`pricing_source_sha256`、`input_tokens_per_attempt_upper`、`output_tokens_per_attempt_upper`、`input_nano_usd_per_token_upper`、`output_nano_usd_per_token_upper`、`other_nano_usd_per_attempt_upper`。供应商 usage 路径沿用型号和价格字段，将请求窗口换成 `prompt_tokens`、`completion_tokens` 和 `usage_covers_all_billable_tokens=true`。程序按整数和精确分数计算，纳美元以 10⁻⁹ USD 为单位。

```sh
python opening_reserve.py prepare --ledger "$ACTIVE_LEDGER" --upper-rate "$VERIFIED_FX_UPPER" \
  --records "$PRIVATE_RECORDS_JSON" --proof-dir "$PRIVATE_PROOFS" --out "$OPENING_JSON"
python opening_reserve.py inspect --evidence "$OPENING_JSON" --proof-dir "$PRIVATE_PROOFS"
```

`prepare` 只生成新的脱敏摘要，并将其绑定到唯一的绝对规范账本路径。`inspect` 只读，二者均输出 `evidence_sha256` 和算出的上界。在已核实的运维 JSON 加上 `opening_evidence_sha256`（取上述值），沿用原有 `path, approval_sha, price_sha, verified_epoch, expiry_epoch, seat_groups, cap_policy`。服务端路径和汇率须与摘要一致。实际执行者确认本项目此前没有其他正式账本、旧发送器已停后，使用以下命令。

```sh
python opening_reserve.py initialize --config "$OPERATOR_CONFIG" --evidence "$OPENING_JSON" \
  --proof-dir "$PRIVATE_PROOFS" --no-existing-project-ledger --writers-stopped
python audit_group_ledger.py "$ACTIVE_LEDGER" --config "$OPERATOR_CONFIG"
```

历史上界写入单独的 `opening_reserve`，不产生历史 `spend`、虚构请求号或待发送票据。读审计的 `historical_opening_reserve`、`total_reserved_nano_usd`、`remaining_nano_usd`。其中累计预留包含开账金额和后来所有新预留。每个历史尝试保守占总组数的一组。人民币记录在汇率更新时向上取整，预留不会低于首次登记值。已确认的旧费用也以非退还预留方式计入，审计不会声称看到供应商实扣。

项目使用同一专用持久目录及唯一账本。同目录的 `.evidence-desk-opening-authority.json` 锁定开账证据、路径和文件身份。保留账本、该侧文件、原摘要和备份。相同配置重复执行只回读已生效记录。文件丢失、复制、篡改或首次写入中断时停付核账。不要通过另一个目录、主机或重新生成证据来重建余额。该机制保护正常并发、重启和误复制，掌握主机存储和配置的管理员仍须保管独立审计回执。

审计确认后，原入口继续使用同一 `private_runtime` 和 Unix 代理。开账配置只准使用已有的单次出站票据通道。运维者从评委页面实际提问后，单独核对供应商请求回执、对应预算记录与原文定位。保留历史的后续续期仍使用 `renew-policy-in-place` 并保留同一 `opening_evidence_sha256`。[原四席接线和续期](FOUR-SEAT-HANDOFF.md)

## 免费复跑及参赛材料

```sh
python -m unittest discover -s . -v
python browser_single_paper.py --opening-fixture
python browser_single_paper.py --opening-fixture --attention "$PDF_DIR/1706.03762v1.pdf"
```

浏览器回归会创建临时测试账本，显示离线模拟供应商。合成财经输入仅用于检验算法和接线。产品提交材料位于固定分支提交 `67db185cee1023420b9a84f02699f00d205f33fc`，该次录制早于开账补丁。[普通话演示视频](https://github.com/ybb-kami/evidence-desk/blob/67db185cee1023420b9a84f02699f00d205f33fc/scanpdf/delivery/evidence-desk-mandarin-1080p-5203688c.mp4) · [13 页技术 PDF](https://github.com/ybb-kami/evidence-desk/blob/67db185cee1023420b9a84f02699f00d205f33fc/scanpdf/delivery/technical-report-zh-49fb098.pdf) · [3 页核验补充](https://github.com/ybb-kami/evidence-desk/blob/67db185cee1023420b9a84f02699f00d205f33fc/scanpdf/delivery/verification-90f8f04.pdf)
