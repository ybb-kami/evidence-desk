# 本地维护者的四席 API 验收

截至北京时间 2026-11-18 22:52，维护者报告评委地址的首页和文库均为 200，页面没有新问答面板。那次验收模型请求为零。此前线上已验过免费原文定位。以下命令和接口用于下一次验收，云端未操作该主机。

## 先在现有评委页判断入口

打开「单篇问答与跨文献综述 · 逐句核对」。只有当前首页加载了 `/synthesis-panel.js` 才会出现这个面板。浏览器网络面板中，`/api/library` 的 `deepseek_enabled` 控制旧版「用 DeepSeek 核选原文」按钮。新四席路由保留 `model_bridge=None`，实测新面板可与 `deepseek_enabled:false` 同时出现。

若首页没有面板，先检查 8443 的活动反代后端及其四席启动入口是否采用 `judge_start.serve_four_seats`。确认已启动进程的模式为 `EVIDENCE_JUDGE_PROVIDER=approved_unix`。配置文件中的期望值不能代替活动进程与页面响应。

## 使用现有代理进程

本分支为原 `private_transport.py` 增加显式 `--provider-mode approved_unix`。在同一个既有代理服务的启动配置中使用下面的参数，保留唯一的账本和 socket。在评委网关进程内保持 `PrivateNetwork=yes`。网关和代理读取各自已核实的数字 UID，双方使用同一账本路径、预算配置、socket 和 provider mode。

```sh
python private_transport.py --provider-mode approved_unix \
  --ledger-config "$OPERATOR_CONFIG" --socket "$BROKER_SOCKET" --allow-uid "$GATEWAY_UID"
```

代理从自身进程的 `DEEPSEEK_API_KEY` 读取凭据。通过 systemd `LoadCredential` 管理的服务也可在同一个命令追加 `--credential-file %d/deepseek-api-key`。凭据文件要求绝对路径、普通文件、属主为服务用户或 root，组和其他用户无访问权限。网关及 worker 无需这项凭据。

启动先检查已核实的账本和政策，再构造同一个已审发送器。启动日志 `READY approved Unix provider broker` 表示本地监听就绪，没有发起模型请求。未指定模式仍启动离线模拟。历史两次库外请求尚无凭证时，沿用本地核账流程。[首次开账条件及操作](OPENING-RESERVE.md)

## 从原件到供应商回执

1. 用当前席位上传公开的 Attention v1，选择一篇，问 “What hardware was used for training?”。点「预览单篇问答」。`POST /api/synthesis/preview` 的 `status=ready`、`provider_mode=approved_unix` 表明此页选择了经批准的路由。此步只预览数据。若模式是 `offline_fixture`，填写离线验收结果。
2. 在当前授权、已核总额和凭据均就绪后，核对发送范围并从页面确认一次。`POST /api/synthesis/confirm` 的 JSON 返回 `generation_receipt`。受支持的原始元数据包括模型名、输入/输出/总 token 数及供应商请求 ID 的 SHA-256。没有完整元数据时会标为 `partial` 或 `unavailable`。账本预留和接口 200 不能代替供应商实扣或生成质量。
3. 在回答中核对训练硬件，点击引用，在物理第 7 页第 3 块核对「8 NVIDIA P100 GPUs」。保存脱敏回执、时间和页码；不要把 cookie、原始请求 ID 或密钥写入提交材料。若响应缺用量或请求结果未知，保留原预留并查供应商记录。

## 可直接用于当前演示说明

> 可上传 Attention v1，在「查找证据」检索 P100，并点击引文核对原 PDF 第 7 页的训练硬件。北京时间 11 月 18 日 22:52 的公网验收通过原文查证，DeepSeek 在线生成尚未验通。

仅在实机收到此轮真实回答、核对供应商回执及引用后更新在线生成状态。

维护者可在仓库根目录运行 `python integration/deepseek-discovery/browser_broker_receipt.py`，用注入的模型目录和完成响应重现回执展示，不需要密钥或外部模型服务。
