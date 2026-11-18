# 证据台的本机 Docker 复现

在 Linux x86-64、Docker Engine 和 BuildKit 上运行。从仓库根目录构建，入口先把运行源码和公开演示 PDF 复制到临时净目录。镜像使用固定摘要的 Python 3.12 Debian Bookworm 基础镜像、项目锁定的 Python 直接依赖，以及 Debian 的 Tesseract 英文和简体中文语言包。正式 PDF、本地文库和 E5、翻译、问答及公式候选权重由运行者单独提供。

```sh
python3 scanpdf/container/build.py
```

在需要 HTTPS 检查代理的环境中，可把已有的可信 CA 合集作为 BuildKit secret 交给构建步骤。CA 只在包安装步骤可见。托管 Docker 可以显式选择本机套接字。

```sh
python3 scanpdf/container/build.py \
  --host unix:///var/run/docker.sock \
  --ca-bundle /etc/ssl/certs/ca-certificates.crt
```

## 本机启动与文库

```sh
docker volume create evidence-desk-local-data
docker run --rm --init --network=host \
  --mount type=volume,source=evidence-desk-local-data,target=/data \
  evidence-desk-rag:local
```

在同一 Linux 主机的浏览器访问 `http://127.0.0.1:8765/`。工作台固定绑定 `127.0.0.1`，Host 只接受当前端口的 `127.0.0.1` 或 `localhost`，写请求仍校验 Origin。使用 Linux host 网络使宿主机直接访问这个回环服务。关停容器后，命名卷保留原 PDF、SQLite 和语义索引。可以在镜像名后追加 `--port 18765` 或 `--lang chi_sim+eng`。

如果需要直接在宿主机管理文库，使用专用目录代替命名卷。例如先运行 `mkdir -p "$PWD/../evidence-desk-local-data"`，再使用 `--mount type=bind,src="$PWD/../evidence-desk-local-data",dst=/data`。默认容器用户是 UID/GID `10001`。使用普通 rootful Docker 时，可指定宿主机的非 root UID/GID，例如 `--user "$(id -u):$(id -g)"`，使其读写本人目录。rootless Docker 的 UID 映射由守护进程配置决定。托管环境实测 `--user 0:0` 可读取私有模型目录，但向绑定数据目录写入的 `0700` 向量缓存由映射后的宿主账号所有。使用命名卷可以让 Docker 管理这类数据的访问与清理。

该配方用于同机评审和复现。不同网络命名空间、宿主机以外或经评委隔离代理访问需要单独验证真实的 Host、Origin、会话及文库隔离。

## 可选模型挂载

在宿主机准备一个独立目录。安装器校验固定的公开模型来源；安装完成后，可把整个目录只读挂入 `/models`。以下命令使用新建的专用目录。

```sh
export EVIDENCE_MODEL_ROOT="$PWD/../evidence-desk-models"
mkdir -p "$EVIDENCE_MODEL_ROOT"
python3 -m scanpdf.semantic.install --output "$EVIDENCE_MODEL_ROOT/dense"
python3 scanpdf/bilingual/install_models.py --output "$EVIDENCE_MODEL_ROOT/translation"
python3 -m scanpdf.qa_reader.install "$EVIDENCE_MODEL_ROOT/qa-reader"
```

```sh
docker run --rm --init --network=host \
  --mount type=volume,source=evidence-desk-local-data,target=/data \
  --mount type=bind,src="$EVIDENCE_MODEL_ROOT",dst=/models,readonly \
  -e EVIDENCE_DENSE_MODEL_DIR=/models/dense \
  -e EVIDENCE_TRANSLATION_MODELS=/models/translation \
  -e EVIDENCE_QA_READER=/models/qa-reader/mobilebert_qa.tflite \
  evidence-desk-rag:local
```

容器用户需要具备对应模型目录的读取权限。只启用部分模型时，设置对应环境变量即可。已有离线文件可通过各安装器的 `--files`、`--archives`、`--source` 重新校验。评委生成模型由工作台通过 DeepSeek Flash 调用，配置方式见评委交接。 [E5](../semantic/README.md) · [OPUS-MT](../bilingual/README.md) · [MobileBERT](../qa_reader/README.md) · [评委交接](../DEEPSEEK-JUDGE.md)

公式候选推理需要用 `python3 scanpdf/container/build.py --formula --tag evidence-desk-rag:formula` 构建可选版本。RapidLaTeXOCR 使用独立 Python 环境，兼容其 NumPy 1.x 依赖。将已校验的权重目录置于挂载内，例如 `/models/formula`，运行时设置 `EVIDENCE_FORMULA_PYTHON=/opt/evidence-formula/bin/python` 和 `EVIDENCE_FORMULA_WEIGHTS=/models/formula`。基础镜像已含本地公式区检测与图像核对。 [扫描工作台与公式权重说明](../README.md)

## 验收

对专用验证文库运行以下脚本。它会导入演示 PDF、检索第 2 页、校验原件字节与引文图像，并检查错误 Host 和 Origin 的 403 响应。重新挂载同一数据卷后再运行，`sample_already_in_library` 应为 `true`。

```sh
python3 scanpdf/container/smoke.py
```

已挂载 E5 权重时，可以使用比赛原件验证真实模型推理。提供扫描 v1 PDF 后，同时检查 Tesseract OCR 和来源坐标。

```sh
python3 scanpdf/container/smoke.py \
  --attention /path/to/1706.03762v7.pdf \
  --scan /path/to/1706.03762v1_img.pdf
```

跨篇问答的浏览器验收可从本机源码运行。用真实 Attention 两个版本逐个核对英法 BLEU 单元格，同时检查 BERT 缺少训练 GPU 的证据和同条件样本的差值。 [跨篇验收脚本](../acceptance/architecture_general.py)

在 Linux x86-64、rootless Docker Engine 29.9.2 上实际构建并启动了基本镜像与公式镜像。基本镜像以 UID `10001` 首启和重启；重挂同一命名卷后仍能回读同 SHA 的演示原件。只读模型挂载实测 E5 返回 Attention v7 第 6 页第 5 块，MobileBERT 进入 `ready`，OPUS-MT 完成英译中。扫描版 v1 的第 7 页第 3 块可回查 OCR 词框。公式版通过分离的 NumPy 1.26.4 环境推理，返回扫描 v1 的 `p7-r1` 未校对候选。同机容器验收涵盖这两个镜像，公网代理与 DeepSeek 凭据需在评委环境单独验收。
