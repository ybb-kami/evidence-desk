"""Loopback Evidence Desk entry point with on-demand local scan recognition."""
import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import zlib
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from scanpdf import parse_pdf as recognize_pdf
import app
import engine
from graph import _blocks


def index_page(doc, page):
    number = page["physical_page"]
    from source_locator import ocr_paragraphs
    texts = ["\n".join(line["text"] for line in lines) for lines in ocr_paragraphs(page)]
    for region in page["regions"]:
        texts.extend(cell["text"] for cell in region.get("cells", [])
                     if cell.get("text_method") == "cell_crop_tesseract")
    doc.setdefault("native_textless", list(doc["textless_pages"]))
    for block, text in enumerate(texts, 1):
        if not any(c.isalnum() for c in text):
            continue
        doc["text_chars"] += len(text)
        for part, (excerpt, offset) in enumerate(engine._windows(text), 1):
            doc["chunks"].append(dict(page=number, block=block, part=part,
                                      offset=offset, text=excerpt, source="ocr"))
    if doc["text_chars"] > engine.MAX_CHARS or len(doc["chunks"]) > engine.MAX_CHUNKS:
        raise ValueError("识别结果超过文献索引容量。")
    doc["chunks"].sort(key=lambda c: (c["page"], c["block"], c["part"]))
    if any(c["page"] == number for c in doc["chunks"]):
        doc["textless_pages"].remove(number)
        digest = hashlib.sha256()
        for _, _, _, _, text in _blocks(doc):
            digest.update(" ".join(engine._normalized(text).split()).encode() + b" ")
        doc["text_hash"] = digest.hexdigest()


class ScanLibrary(app.Library):
    # Bump when older table or formula regions need an explicit refresh.
    REGION_REVISION = 2

    def __init__(self, root, lang="eng"):
        super().__init__(root)
        self.lang = lang
        from semantic import DenseIndex
        from passage_answer import PassageReader
        from local_generation import LocalGenerator
        self.dense = DenseIndex(cache_dir=Path(root) / "semantic-vectors")
        self.passage_reader = PassageReader()
        self.local_generator = LocalGenerator()
        self.worker = threading.Lock()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS scan_pages "
                       "(id TEXT, page INTEGER, result BLOB, PRIMARY KEY(id,page))")

    def lookup(self, docid, number):
        if not isinstance(docid, str) or not app.ID.fullmatch(docid) or type(number) is not int:
            raise app.Bad(400, "文献或页码无效。")
        docs = self.records([docid])
        if not docs:
            raise app.Bad(404, "找不到文献。")
        if not 1 <= number <= docs[0]["pages"]:
            raise app.Bad(400, "物理页码超出文献范围。")
        return docs[0]

    def page_info(self, docid, number):
        doc = self.lookup(docid, number)
        with closing(sqlite3.connect(self.db)) as db:
            row = db.execute("SELECT result FROM scan_pages WHERE id=? AND page=?",
                             (docid, number)).fetchone()
            completed = {r[0] for r in db.execute("SELECT page FROM scan_pages WHERE id=?", (docid,))}
        page = json.loads(zlib.decompress(row[0])) if row else None
        revision = page.get("region_revision", 0) if page is not None else None
        native_textless = doc.get("native_textless", doc["textless_pages"])
        return {"doc": app.summary(doc), "native_textless": native_textless,
                "pending_pages": [n for n in native_textless if n not in completed],
                "region_revision": revision,
                "region_update_available": revision is not None and revision < self.REGION_REVISION,
                "page": page}

    def recognize(self, docid, number, *, region_revision=None):
        if not self.worker.acquire(blocking=False):
            raise app.Bad(409, "正在处理其他页面，请稍后重试。")
        try:
            info = self.page_info(docid, number)
            cached = info["page"]
            if region_revision is not None:
                if type(region_revision) is not int or region_revision < 0:
                    raise app.Bad(400, "区域修订号无效。")
                if cached is None:
                    raise app.Bad(400, "请先识别本页。")
                if not info["region_update_available"]:
                    return info
                if region_revision != info["region_revision"]:
                    raise app.Bad(409, "区域修订号已变化，请重新读取本页。")
            elif cached is not None:
                return info
            elif number not in info["native_textless"]:
                raise app.Bad(400, "本页已有原生文字。")
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "source.pdf"
                source.write_bytes(self.pdf(docid))
                if cached is None:
                    result = recognize_pdf(source, pages=str(number), lang=self.lang)
                else:
                    render = cached["render"]
                    result = recognize_pdf(source, pages=str(number), lang=render["lang"], dpi=render["dpi"])
            if result["source"]["sha256"] != docid:
                raise ValueError("原件校验失败。")
            page = result["pages"][0]
            if cached is None:
                page["render"] = result["render"]
            else:
                geometry = ("physical_page", "width_px", "height_px", "width_pt", "height_pt", "rotation", "cropbox")
                if (result["render"] != cached["render"]
                        or any(page[key] != cached[key] for key in geometry)):
                    raise ValueError("更新后的区域坐标与原页不一致。")
                # Keep line, word, and indexed block anchors from the original OCR.
                page = cached | {"regions": page["regions"]}
                if "indexed_crop_cells" not in cached:
                    from source_locator import ocr_index_cells
                    page["indexed_crop_cells"] = ocr_index_cells(cached)
            page["region_revision"] = self.REGION_REVISION
            packed = zlib.compress(json.dumps(page, ensure_ascii=False).encode())
            with self.lock, closing(sqlite3.connect(self.db)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT result FROM scan_pages WHERE id=? AND page=?", (docid, number)).fetchone()
                if cached is None and row is None:
                    doc = self.lookup(docid, number)
                    index_page(doc, page)
                    record = json.dumps(doc, ensure_ascii=False)
                    used, = db.execute("SELECT coalesce(sum(length(record)),0) FROM docs WHERE id<>?", (docid,)).fetchone()
                    scans, = db.execute("SELECT coalesce(sum(length(result)),0) FROM scan_pages").fetchone()
                    if used + len(record) > app.MAX_TEXT or scans + len(packed) > app.MAX_TEXT:
                        raise ValueError("扫描结果超过本地文库容量。")
                    db.execute("INSERT INTO scan_pages VALUES(?,?,?)", (docid, number, packed))
                    db.execute("UPDATE docs SET record=? WHERE id=?", (record, docid))
                elif cached is not None:
                    self.lookup(docid, number)
                    if row is None:
                        raise app.Bad(409, "本页识别记录已变化，请重新读取本页。")
                    latest = json.loads(zlib.decompress(row[0]))
                    if latest.get("region_revision", 0) < self.REGION_REVISION:
                        if latest != cached:
                            raise app.Bad(409, "本页识别记录已变化，请重新读取本页。")
                        scans, = db.execute("SELECT coalesce(sum(length(result)),0) FROM scan_pages").fetchone()
                        if scans - len(row[0]) + len(packed) > app.MAX_TEXT:
                            raise ValueError("扫描结果超过本地文库容量。")
                        db.execute("UPDATE scan_pages SET result=? WHERE id=? AND page=?", (packed, docid, number))
            return self.page_info(docid, number)
        finally:
            self.worker.release()


class Handler(app.Handler):
    def serve(self, post):
        url = urlsplit(self.path)
        path = url.path
        host = self.headers.get("Host", "").lower()
        if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            raise app.Bad(403, "仅接受本机地址。")
        if post and (self.headers.get("Origin") not in (None, "http://" + host)
                     or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            raise app.Bad(403, "拒绝跨站请求。")
        from source_locator import ROUTES as source_routes, handle as handle_source
        if path in source_routes:
            return handle_source(self, post, url)
        if path in {"/formula-review", "/formula-review/image", "/api/formula-review"}:
            from formula_review import handle
            return handle(self, post, url)
        if not post and path in {"/csv-trends", "/csv-trends.js"}:
            filename, mime = (("csv-trends.html", "text/html; charset=utf-8") if path == "/csv-trends" else
                              ("csv-trends.js", "application/javascript; charset=utf-8"))
            return self.payload(200, (HERE / filename).read_bytes(), mime)
        if not post and path == "/synthesis":
            return self.payload(200, (HERE / "synthesis.html").read_bytes(), "text/html; charset=utf-8")
        if post and path == "/api/synthesis":
            from synthesis import handle
            return handle(self)
        if path == "/translation" or path.startswith(("/api/bilingual/", "/bilingual/")):
            from bilingual.routes import handle
            if handle(self, post, path):
                return
        if path == "/download" or path.startswith("/api/download/"):
            from discovery.routes import handle
            if handle(self, post, path):
                return
        if not post and path in ("/", "/index.html"):
            home = (app.HERE / "index.html").read_text()
            home = home.replace('<span class="tag">本地工作区</span>',
                                '<nav><a href="/scan">扫描页识别</a> · <a href="/translation">双语精读</a> · <a href="/download">下载论文</a> · <a href="/followup">证据追问</a> · <a href="/architecture">跨篇问答</a></nav>')
            home = home.replace('<p class="subtle" id="csv-help">', '<p><a href="/csv-trends">按变量绘图与核对趋势</a></p><p class="subtle" id="csv-help">', 1)
            home = home.replace('id="local-review"></section>', 'id="local-review"><a href="/synthesis">选择文献生成综述</a></section>', 1)
            home = home.replace("扫描件若没有文本层，可以查看原件，但不能检索图片中的文字。",
                                "扫描件可按页识别，完成后可在这里检索 OCR 文字。")
            home = home.replace("detail.append(archive);", """
                const ask = el('a', '', '问这篇论文');
                ask.href = '/followup?id=' + doc.id;
                detail.append(ask);
                if (doc.textless_pages?.length) {
                  const scan = el('a', '', '识别扫描页');
                  scan.href = '/scan?id=' + doc.id + '&page=' + doc.textless_pages[0];
                  detail.append(scan);
                }
                detail.append(archive);""")
            home = home.replace("actions.append(source(hit.id,", """
                if (hit.source === 'ocr') {
                  const scan = el('a', '', '核对 OCR 定位');
                  scan.href = '/scan?id=' + hit.id + '&page=' + hit.page;
                  actions.append(scan);
                }
                actions.append(source(hit.id,""")
            return self.payload(200, home.encode(), "text/html; charset=utf-8")
        if not post and path == "/architecture":
            return self.payload(200, (HERE / "architecture.html").read_bytes(), "text/html; charset=utf-8")
        if post and path == "/api/architecture":
            from architecture import route
            return route(self)
        if not post and path == "/followup":
            return self.payload(200, (HERE / "followup.html").read_bytes(), "text/html; charset=utf-8")
        if post and path == "/api/followup/model":
            from grounded_draft import route
            return route(self)
        if post and path == "/api/followup":
            from followup import answer_question
            result = answer_question(self.server.library, self.json_request())
            result["model_available"] = bool(self.server.enable_model and app.provider.configured())
            return self.payload(200, result)
        if not post and path == "/scan":
            return self.payload(200, (HERE / "scan.html").read_bytes(), "text/html; charset=utf-8")
        store = self.server.library
        if post and path == "/api/scan":
            req = self.json_request()
            if "region_revision" in req:
                if type(req["region_revision"]) is not int:
                    raise app.Bad(400, "区域修订号无效。")
                return self.payload(200, store.recognize(req.get("id"), req.get("page"),
                                                         region_revision=req["region_revision"]))
            return self.payload(200, store.recognize(req.get("id"), req.get("page")))
        if not post and path.startswith(("/api/scan/", "/scan-image/")):
            docid = path.rsplit("/", 1)[1]
            number = int(parse_qs(url.query).get("page", ["1"])[0])
            if path.startswith("/api/scan/"):
                return self.payload(200, store.page_info(docid, number))
            store.lookup(docid, number)
            with pymupdf.open(stream=store.pdf(docid), filetype="pdf") as pdf:
                page = pdf[number - 1]
                from source_locator import render_image
                return self.payload(200, render_image(page), "image/png")
        return super().serve(post)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=app.HERE / "data")
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--enable-deepseek", action="store_true")
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.library = ScanLibrary(args.data, args.lang)
        server.enable_model = args.enable_deepseek
        from discovery.schedule import TrackingSchedule
        server.tracking_schedule = TrackingSchedule(server.library)
        server.tracking_schedule.start()
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.tracking_schedule.close()


if __name__ == "__main__":
    main()
