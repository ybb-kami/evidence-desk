"""Single, explicit, local OCR job with a private temporary source and derivative."""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading

from ocr_local import MAX_BYTES, MAX_OUTPUT, create_derivative


class OCRJobError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _filename(source_name):
    basename = source_name.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^\w .()\-]", "_", re.sub(r"(?i)\.pdf$", "", basename)).strip(" .")
    return (base[:110] or "document") + ".ocr.pdf"


class LocalOCRJobs:
    """The current job is retained until the next start or application close.

    No archive, persistent job log, network operation or automatic library
    import. The caller resolves the ID to original bytes in its active library.
    """
    def __init__(self, *, worker=create_derivative):
        self._guard = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._directory = None
        self._state = {"status": "idle"}
        self._worker = worker
        self._closed = False

    def status(self):
        with self._guard:
            return dict(self._state)

    def start(self, source_id, source_name, original):
        if os.name != "posix":
            raise OCRJobError(501, "当前系统不支持此本地 OCR。请在 Linux 或 macOS 使用。")
        if not isinstance(original, bytes) or not original.startswith(b"%PDF-"):
            raise OCRJobError(400, "未找到可处理的 PDF 原件。")
        if len(original) > MAX_BYTES:
            raise OCRJobError(413, "扫描件超过本地 OCR 的 15 MiB 输入限制。")
        with self._guard:
            if self._closed:
                raise OCRJobError(503, "应用正在关闭，不能启动 OCR。")
            if self._state["status"] == "running":
                raise OCRJobError(409, "已有 OCR 任务在运行。请等待完成。")
            old = self._directory
            self._directory = Path(tempfile.mkdtemp(prefix="evidence-ocr-"))
            cancel = threading.Event()
            job_id = secrets.token_hex(16)
            previous = self._state
            self._state = {"status": "running", "job_id": job_id,
                           "source_id": source_id, "source_name": source_name}
            thread = threading.Thread(
                target=self._run, args=(self._directory, original, cancel, _filename(source_name)),
                name="evidence-local-ocr", daemon=True,
            )
            try:
                thread.start()
            except RuntimeError:
                shutil.rmtree(self._directory, ignore_errors=True)
                self._directory, self._state = old, previous
                raise OCRJobError(503, "无法启动本地 OCR 工作进程。") from None
            self._thread, self._cancel = thread, cancel
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
            return dict(self._state)

    def _run(self, directory, original, cancel, filename):
        source, output = directory / "source.pdf", directory / "derivative.ocr.pdf"
        try:
            source.write_bytes(original)
            del original
            self._worker(source, output, cancel_event=cancel)
            if cancel.is_set() or not output.is_file() or output.stat().st_size > MAX_OUTPUT:
                raise ValueError("OCR stopped or produced no valid output.")
        except ValueError as exc:
            update = {"status": "failed", "message": str(exc)[:350]}
        except Exception:
            update = {"status": "failed", "message": "本机 OCR 未完成。原件未改动。"}
        else:
            update = {"status": "succeeded", "filename": filename}
        finally:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass
        if update["status"] != "succeeded":
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
        with self._guard:
            if not self._closed:
                self._state.update(update)

    def result(self, job_id):
        with self._guard:
            if self._closed or self._state.get("job_id") != job_id:
                raise OCRJobError(404, "未找到此 OCR 结果。新任务会替换上一次的临时结果。")
            if self._state["status"] != "succeeded":
                raise OCRJobError(409, "OCR 没有可下载的副本。")
            try:
                data = (self._directory / "derivative.ocr.pdf").read_bytes()
            except OSError:
                raise OCRJobError(404, "本机临时 OCR 副本已不存在。") from None
            return data, self._state["filename"]

    def close(self):
        with self._guard:
            if self._closed:
                return
            self._closed = True
            self._cancel.set()
            thread, directory = self._thread, self._directory
        if thread is not None:
            thread.join()
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
