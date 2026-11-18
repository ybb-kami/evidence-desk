"""Local, read-only version review endpoints."""

from pathlib import Path

import app
from .compare import compare


HERE = Path(__file__).resolve().parent
ASSETS = {
    "/version-evidence": ("review.html", "text/html; charset=utf-8"),
    "/version-evidence.js": ("review.js", "application/javascript; charset=utf-8"),
}
API = "/api/version-evidence"
ROUTES = set(ASSETS) | {API}


def handle(handler, post, url):
    if not post and url.path in ASSETS:
        asset, mime = ASSETS[url.path]
        return handler.payload(200, (HERE / asset).read_bytes(), mime)
    if not post or url.path != API:
        raise app.Bad(405, "版本证据入口的方法无效。")
    request = handler.json_request()
    semantic = request.pop("semantic", True)
    if not isinstance(semantic, bool):
        raise app.Bad(400, "语义对照开关应为布尔值。")
    before, after = handler.document_ids(request, "before_id", "after_id")
    store = handler.server.library
    docs = {doc["id"]: doc for doc in store.records([before, after], archived=None)}
    if len(docs) != 2:
        raise app.Bad(404, "所选版本不在本地文库中。")
    return handler.payload(200, compare(docs[before], docs[after], semantic=semantic, dense=getattr(store, "dense", None)))
