# 离线验收记录

## 项目自带样例

`python -m unittest -v test_acceptance`：3 项通过。验证了 PDF 导入、页码和原件字节、重启后持久化、改名重复识别，以及损坏 PDF 拒收。样例查询 `verify page numbering` 可以找到物理第 2 页关于文件内页序与印刷页码的区别。

## 独立文档

在临时数据库和本机回环服务中，导入了 TeX Live 自带的 B. Jackowski、J. M. Nowacki 的《Latin Modern Family of Fonts: The Technical Documentation》。本机文件为 `/usr/share/doc/fonts-lmodern/lm-info.pdf`，上游包清单列出 `doc/fonts/lm/lm-info.pdf`。文件未复制到项目，也未上传。实测文件 SHA-256：

```
5907f59e368762a3a2858a6826aab019d0accb367f1b8cc6062d472635579fe6
```

- 导入 26 个物理页，索引 2,822 个文本块。
- 查询 `more than 700 glyphs`，限定于这份 PDF，返回 1 条命中：物理第 3 页，第 3 块。“Each of the lm text fonts contains more than 700 glyphs.” 已用独立的 `pdftotext -f 3 -l 3 -layout` 核对该句，并核对了服务返回的 PDF 字节与原件一致。
- 查询 `quasarzxqvvnobaselocal` 返回 0 条，不生成替代答案。
- 限制：同一结果的相邻文本保留排版断行（例如 `Viet` 与 `namese` 分行）。命中适合定位；正式引文应打开原页复核。该验证只覆盖本地文本检索，不代表 OCR 或语义检索能力。
