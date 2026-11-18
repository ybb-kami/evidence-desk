# 四席评委网关交接

代码基于单篇修复 `e6f7aa785610a5d98963ff903ab422deab615d65`。评委主机当前运行的版本由本地线程核为 `994abd4cfea4e427d7e8f5f44b36ea51d2295910`。本包只修改 `integration/deepseek-discovery/`。线上首页、原版 app.py、index.html、四席 worker 的文库和现存服务均由本地维护者管理。

## 20 元累计额度

项目的模型、独立 Search 和可选 Fetch 共用一个 SQLite 账本。人民币上限固定为 20 元。原账本使用整数纳美元，新配置的美元上限为 `floor(20 × 10⁹ / 每美元人民币汇率上界)`。旧预留、超时和结果未知的请求均占用额度，失败不退款。每个组至多一次模型、一次搜索、一次抓取，总组数仍限 100。项目预算和账号充值分别核对。

[随包汇率证据](fx_2026-11-18.json)记录了 2026-11-18 的公开市场快照、原始文件哈希、折算上界与有效期。每美元参考价 6.6662016 元，本次保守按 8.00 元折算，累计 2.50 美元，快照北京时间 11 月 19 日 0 时失效。账号结算汇率和平台收费需由持账户的一方另行核对。现行模型按完整输入窗口预扣 0.3151872 美元，搜索、抓取各 0.00075 美元。零历史预留下，完整组合最多 7 组；例如果已有 0.71 美元预留，最多再容纳 5 组。预留金额是上界，评委实机余额、充值和实扣未测。[当日参考数据](https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@2026-11-18/v1/currencies/usd.json)

旧配置继续识别原 0.71 美元指纹，专供读取与升级。新版收费入口要求人民币政策和有效的原位迁移凭证。首次真实联调由本地持有人按当前授权执行。用户在界面确认向供应商发送的 PDF 摘录或检索词，仅涉及这次发送的数据范围。

## 定位和升级原账本

1. 在本机受控服务配置中定位旧工厂的 `config.path` 或 `BudgetGate(db_path, ...)`。账本必须位于四席共享的持久目录。实际生产路径尚待本机线程核对。清点所有曾发出付费请求的入口、独立旧账本及后台实扣，使历史消费只计一次。禁止以临时席位库、空数据库或测试账本代替。如果存在未归属的外部账单，先核账再启用。
2. 停止全部旧收费发送器，等在途发送结束或确认进入未知状态。保留全部预留。用 SQLite 的 backup API 将活动库备份到独立、受限的新文件。将备份切到 DELETE journal、关闭连接、确认旁边没有 WAL/SHM/journal 文件，再对静态备份计算 SHA-256。源库保持原位。保留先前的授权、价格、配置和备份。
3. 旧 `GroupBudget` 三张表为 `conf/groups_/spend`。两份配置沿用已核实的 `path, approval_sha, price_sha, verified_epoch, expiry_epoch, seat_groups`。新配置使用新的授权记录摘要、本次有效的价格核验与 `cap_policy` 四字段（见汇率 JSON）。运维核对价格和汇率有效时间有交集。费用配置文件不含 API 密钥。用于重放的测试价格不能填入生产配置。
4. 在各独立服务均停写时，针对同一既有数据库执行以下迁移。它逐行比对历史及静态备份，同一事务更新指纹和审计凭证，保留原文件和每个 rid/gid。新的付款入口会拒绝一张刚创建的人民币账本。一次性出站票仍在排队时拒绝升级。以原政策启动的发送器在升级后不能继续送出请求。

```sh
cd integration/deepseek-discovery
python legacy_ledger.py upgrade-in-place --ledger "$ACTIVE_LEDGER" --snapshot "$STATIC_BACKUP" \
  --snapshot-sha256 "$BACKUP_SHA256" --previous-config "$OLD_CONFIG" --target-config "$NEW_CONFIG" --writers-stopped
python audit_group_ledger.py "$ACTIVE_LEDGER" --config "$NEW_CONFIG"
python legacy_ledger.py rollback-check --ledger "$ACTIVE_LEDGER" --migration-sha "$MIGRATION_SHA"
```

回滚应用时将 `EVIDENCE_JUDGE_PROVIDER` 设回 `off`，继续使用原四席阅读入口，并保留累计账本。迁移后的任何新增预留（含未获回执）均会阻止回退计费状态。回滚检查只读，不会恢复旧余额。原库为独立模型 `budget_config/budget_ledger`、单工具 `spend(id,seat,action,amount)` 或低价率旧模型时，用 `stage-legacy-review --help` 在静态备份上生成停用的对账副本。多账本重叠、人民币旧流水、未能核定的早期模型负债需人工核账，程序保持付费关闭。

## 同一入口及私网路径

保留实机的 `demo_slots(DEFAULT_APP, run_dir/'sessions', 4, loopback_origin)`。下面的命令在一份脱敏本机副本上生成待审核补丁。使用维护者从目标实机独立核过的哈希。此前本机副本摘要不能替代这一步。生成器仅向标准输出写 diff。

```sh
python entry_patch.py "$ENTRY_COPY" --expect-sha256 "$ENTRY_SHA256" > judge-entry.patch
```

新副本将 `serve(slots, ...)` 改为 `serve_four_seats(serve, slots, ...)`，保留原有 origin、匿名模式、会话轮换、3600 秒 TTL 和 `model_bridge=None`。将本包 `integration/deepseek-discovery` 置于该网关的 Python 模块搜索路径（保留原 worker 启动目录）。模式 `off` 调回原 gateway；`offline_fixture` 使用本包候选 gateway 处理新端点；将来核准的 `approved_unix` 要求同一迁移账本和经核实的外部代理。`judge_start.py` 使用限定包名导入候选 gateway，避免命中运维进程已载入的旧 `gateway`。

网关沿用 `PrivateNetwork=yes`。接线时设置 `EVIDENCE_JUDGE_PROVIDER`、`EVIDENCE_JUDGE_BUDGET_CONFIG`、`EVIDENCE_JUDGE_SOCKET` 和数字 `EVIDENCE_JUDGE_BROKER_UID`。后两项标识文件系统 AF_UNIX 套接字及代理 UID。套接字目录由代理用户或 root 管理，不得组写；两进程仅共享所需的套接字和唯一账本目录，按最小权限访问同一 SQLite 和 journal。相互核对 Linux SO_PEERCRED。各席 PDF 与浏览器 cookie 保留在原有运行环境。

网关先预留精确发送字节及金额。代理用同一原账本核对模型/工具、请求编号、字节哈希、有效政策和固定价格，在事务中消耗一次性出站票后才发送。限制满、版本核验失败、断线或超时不会自动重试。网关和 worker 无需网络密钥。默认代理命令仅启动离线假供应商。将来获准的外部代理进程可用 `UnixProviderBroker(..., ledger=..., provider_mode="approved_unix", approved_sender=broker_sender.approved_sender(key))`，由该进程从受控运行环境读 key。真实模型 POST 前验证官方目录里的 `deepseek-flash`、Flash 展示名和 `1017` 标记。工具仅访问固定 DeepSeek 目的地，Fetch 限定同席已确认的学术结果。禁止同时启用旧 `ModelBridge`、旧直连工厂或其他单独记账的工作台实例。

## 免费复跑

Linux、CPython 3.12、PyMuPDF 1.26.6、tokenizers 0.22.1。浏览器复测用 Playwright 1.62.0 和系统 Chromium 156。官方分词资产已随仓库提供，程序校验原始哈希。安装依赖可在独立 venv 完成。以下命令从仓库根目录运行；默认测试自行生成 PDF。提供预置目录时，集成套件使用其中的 Attention v1 和 ACL 版 BERT 原件。

```sh
python -m pip install -r requirements.txt -r integration/deepseek-discovery/requirements-judge-test.txt
python -m unittest discover -s integration/deepseek-discovery -v
EVIDENCE_TEST_PDF_DIR="$PDF_DIR" python -m unittest discover -s integration/deepseek-discovery -v
python integration/deepseek-discovery/browser_single_paper.py
python integration/deepseek-discovery/browser_single_paper.py --attention "$PDF_DIR/1706.03762v1.pdf"
python integration/deepseek-discovery/netns_smoke.py
```

最后一项需要 Linux util-linux `unshare` 和允许创建用户及网络命名空间的本地测试环境。它启动两个不同网络命名空间，隔离客户端只有 lo，模型请求经文件系统套接字发送到假供应商。运行时产生的账本和文库都在临时目录。

四席浏览器复测从上传按钮开始，经 `POST /api/import`、`/api/synthesis/preview`、`/api/synthesis/confirm` 和 `GET /pdf/<sha256>#page=N` 检查物理页与字节。搜索/抓取使用 `/api/discovery/preview` 和 `/api/discovery/confirm`。`0534cf6` 的旧工作台脚本另需 `/api/model-status`、`/followup`、`/api/followup`、`/source`、`/api/source` 和可选的 `/copilot`、`/api/copilot` 流。该脚本针对另一套 UI；四席分支的浏览器证据随本包附上。[Chromium 验收脚本](browser_single_paper.py)
