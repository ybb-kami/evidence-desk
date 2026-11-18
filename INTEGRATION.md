# 两条线如何并入同一版本

本仓从本机单人证据工作台继续开发；用户的另一 Codex 同时迭代 feature/scanpdf-parser。两者共享 a6b6d27。不要从网页“复制所有文件”覆盖另一路的入口，不能把来源头写进文档当成真正合并 Git 历史。

1. 在有该私有仓授权的干净克隆中 fetch 两个真实分支。若主线来自 bundle，先用 git bundle verify 核实完整性，再将其 main fetch 为独立临时分支，如 `git fetch /path/evidence-desk.bundle refs/heads/main:refs/heads/desk-reviewed`；远端实际分支名以当次 `git ls-remote --heads origin` 为准。
2. 从所选整合基线新建 integration 分支，先 `git merge-base` / `git log --left-right` / `git diff --stat` 审阅差异。用普通非快进合并保留两侧父提交；如果需要选择性适配，也如实记录来源提交、原文件、改动和未采用部分。不要 force、不要把未运行的外部模块标成已验、不要把模型权重/密钥/真实用户 PDF 进仓。
3. 重点冲突：app.py 的同源验证与 HTTP 接口、index.html、engine.py、图谱身份/参考条目、本机 OCR 二次导入 vs 另一线的 OCR 原件索引。scanpdf/ 可能已经以明示来源的快照出现在主线，两侧重复文件需逐个 diff。两种“翻译”方案不能共占同一组 /api/bilingual/ URL。使用用户已实现的本地 CTranslate2 版本，未安装模型时明确提示，不改用未经同意的在线服务。
4. 核验本机回归 + 各来源原件 opt-in + 统一 12 道原题（原句/同一 PDF 哈希）+ 独立数据。测试数不等于 12 题涵盖率，也不等于 3.5 真实部署验收。合并并授权 push 后比对远端完整 SHA；不要以本机 HEAD 代替远端。
