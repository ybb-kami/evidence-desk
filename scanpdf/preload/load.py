"""Select local PDFs and preserve the active library's normal import rules."""

from collections import Counter
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import stat
from time import monotonic

import app
import engine


V7 = "1706.03762v7.pdf"
V1 = "1706.03762v1.pdf"
SCAN = "1706.03762v1_img.pdf"
BOOK = "UnderstandingDeepLearning_02_09_26_C.pdf"
DEMO_FILES = (V7, V1, SCAN, BOOK, "N19-1423.pdf", "language_understanding_paper.pdf", "2005.14165v4.pdf")
UPLOAD_BYTES = min(app.UPLOAD_LIMIT, engine.MAX_BYTES)
_UNSAFE_NAME = re.compile(r"[\\/\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def capacity(library):
    with closing(sqlite3.connect(library.db)) as db:
        db.execute("BEGIN")
        docs, size, characters, archived = db.execute(
            "SELECT COUNT(*), coalesce(sum(length(pdf)),0), coalesce(sum(length(record)),0), "
            "coalesce(sum(EXISTS(SELECT 1 FROM archive WHERE archive.id=docs.id)),0) FROM docs"
        ).fetchone()
        has_scans = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_pages'").fetchone()
        scan_bytes = db.execute("SELECT coalesce(sum(length(result)),0) FROM scan_pages").fetchone()[0] if has_scans else 0
    return {
        "active_docs": docs - archived, "archived_docs": archived, "document_limit": app.MAX_DOCS,
        "remaining_document_slots": max(0, app.MAX_DOCS - docs),
        "pdf_bytes": size, "pdf_budget_bytes": app.MAX_STORAGE, "remaining_pdf_bytes": max(0, app.MAX_STORAGE - size),
        "record_characters": characters, "record_budget_characters": app.MAX_TEXT,
        "remaining_record_characters": max(0, app.MAX_TEXT - characters),
        "scan_cache_bytes": scan_bytes, "scan_cache_budget_bytes": app.MAX_TEXT,
        "remaining_scan_cache_bytes": max(0, app.MAX_TEXT - scan_bytes),
        "single_upload_pdf_limit_bytes": UPLOAD_BYTES,
        "max_next_upload_original_bytes": max(0, min(UPLOAD_BYTES, app.MAX_STORAGE - size))
        if docs < app.MAX_DOCS and characters < app.MAX_TEXT else 0,
    }


def _selection(folder, mode, names):
    if mode == "demo":
        return DEMO_FILES
    if mode == "explicit":
        return tuple(dict.fromkeys(names or ()))
    if mode == "all":
        return sorted((path.name for path in folder.iterdir() if path.suffix.lower() == ".pdf"),
                      key=lambda name: (0 if name == V7 else 1 if name == V1 else 2, name.casefold(), name))
    raise ValueError("Unknown PDF selection mode")


def _read(folder, name):
    if not name or len(name) > 160 or _UNSAFE_NAME.search(name) or Path(name).suffix.lower() != ".pdf":
        raise ValueError("请选择可信目录中的单个 PDF 文件名。")
    path = folder / name
    if path.is_symlink():
        raise ValueError("预置文件必须是普通 PDF 文件。")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("预置文件必须是普通 PDF 文件。")
        if not 0 < before.st_size <= UPLOAD_BYTES:
            raise ValueError("PDF 必须非空且最多 24 MiB。")
        data = stream.read(UPLOAD_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) != before.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ValueError("读取过程中 PDF 已变化，请重试。")
    return data


def _textless(library, doc):
    textless = doc["textless_pages"]
    if not textless:
        return [], []
    with closing(sqlite3.connect(library.db)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_pages'").fetchone()
        completed = {row[0] for row in db.execute("SELECT page FROM scan_pages WHERE id=?", (doc["id"],))} if exists else set()
    return ([page for page in textless if page not in completed], [page for page in textless if page in completed])


def _one(library, folder, filename):
    started = monotonic()
    result = {"file": filename}
    try:
        data = _read(folder, filename)
        result["size_bytes"] = len(data)
        imported = library.add(data, filename)
        doc = imported["doc"]
        pending, still_empty = _textless(library, doc)
    except ValueError as error:
        result.update(state="rejected", error=str(error))
    except FileNotFoundError:
        result.update(state="rejected", error="可信目录中未找到该文件。")
    except OSError:
        result.update(state="failed", error="无法读取预置 PDF 或写入本地文库。")
    except sqlite3.Error as error:
        result.update(state="failed", error=f"本地文库操作未完成（{getattr(error, 'sqlite_errorname', 'SQLite')}）。可重跑核实。")
    else:
        state = "existing_archived" if imported.get("archived") else "existing_active" if imported["existing"] else "imported"
        result.update(state=state, id=doc["id"], library_name=doc["name"], physical_pages=doc["pages"],
                      indexed_passages=doc["indexed_blocks"], textless_pages=doc["textless_pages"],
                      pending_textless_pages=pending, recognized_textless_pages=still_empty,
                      duplicate_hints=imported["duplicates"])
    result["seconds"] = round(monotonic() - started, 4)
    return result


def preload(data_dir, pdf_dir, *, mode, names=None):
    folder = Path(pdf_dir).expanduser().resolve()
    data = Path(data_dir).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("指定的可信 PDF 目录不可用。")
    selection = _selection(folder, mode, names)
    if not selection:
        raise ValueError("没有可加载的 PDF 文件。")
    library = app.Library(data)
    started = monotonic()
    before = capacity(library)
    files = [_one(library, folder, name) for name in selection]
    after = capacity(library)
    counts = Counter(item["state"] for item in files)
    return {"selection": mode, "files_selected": len(selection), "states": dict(counts),
            "failed": counts["rejected"] + counts["failed"], "seconds": round(monotonic() - started, 4),
            "before": before, "after": after, "files": files}
