"""Exercise a running loopback workbench using an isolated verification library."""

import argparse
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
from urllib.parse import quote, urlencode


SAMPLE = Path(__file__).resolve().parents[2] / "sample.pdf"


def request(port, path, data=None, *, content_type="application/json", expected=200, headers=None):
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
    fields = (headers or {}) | ({"Content-Type": content_type} if data is not None else {})
    connection = HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        connection.request("GET" if data is None else "POST", path, data, fields)
        reply = connection.getresponse()
        content = reply.read()
        if reply.status != expected:
            raise ValueError(f"{path}: expected HTTP {expected}, received {reply.status}")
        mime = reply.headers.get_content_type()
        return json.loads(content) if mime == "application/json" else content
    finally:
        connection.close()


def import_pdf(port, file):
    content = file.read_bytes()
    identity = hashlib.sha256(content).hexdigest()
    result = request(port, "/api/import?name=" + quote(file.name), content, content_type="application/pdf")
    assert result["doc"]["id"] == identity
    assert request(port, f"/pdf/{identity}") == content
    return result["doc"], result["existing"]


def source(port, hit):
    anchor = {key: hit[key] for key in ("id", "page", "block", "part")}
    observed = request(port, "/api/source?" + urlencode(anchor))
    assert observed["doc"]["id"] == hit["id"] and observed["page"] == hit["page"]
    assert hit["text"] == observed["text"] and observed["passage"]["boxes"]
    assert request(port, observed["image_url"]).startswith(b"\x89PNG\r\n\x1a\n")
    return observed["passage"]["precision"]


def smoke(port, sample, *, attention=None, scan=None):
    for path in ("/", "/followup", "/architecture", "/synthesis", "/translation", "/scan", "/download",
                 "/source.js", "/formula-review", "/csv-trends", "/csv-trends.js"):
        assert request(port, path)
    request(port, "/api/library", headers={"Host": "outside.invalid"}, expected=403)
    request(port, "/api/search", {"ids": [], "query": "sample"}, expected=403,
            headers={"Origin": "https://outside.invalid"})
    demo, existing = import_pdf(port, sample)
    assert any(row["id"] == demo["id"] for row in request(port, "/api/library")["docs"])
    hits = request(port, "/api/search", {"ids": [demo["id"]], "query": "verify page numbering"})["hits"]
    target = next(hit for hit in hits if hit["page"] == 2 and "verify page numbering" in hit["text"])
    report = {"sample_sha256": demo["id"], "sample_already_in_library": existing,
              "sample_page": target["page"], "native_locator": source(port, target),
              "foreign_host_status": 403, "foreign_origin_status": 403}
    if attention:
        paper, _ = import_pdf(port, attention)
        answers = request(port, "/api/followup", {"id": paper["id"], "anchor": None,
                          "question": "为什么用不同频率的正弦和余弦做位置编码？"})
        assert answers["retrieval"]["dense"] == "ready"
        assert "multilingual-e5-small" in answers["retrieval"]["methods"]
        hit = next(hit for hit in answers["hits"] if hit["page"] == 6 and "sine and cosine" in hit["text"])
        report["dense"] = {"id": paper["id"], "page": hit["page"], "block": hit["block"],
                           "locator": source(port, hit)}
    if scan:
        paper, _ = import_pdf(port, scan)
        page = request(port, "/api/scan", {"id": paper["id"], "page": 7})["page"]
        assert page["physical_page"] == 7 and page["lines"]
        hits = request(port, "/api/search", {"ids": [paper["id"]], "query": "NVIDIA P100 GPUs"})["hits"]
        hit = next(hit for hit in hits if hit["page"] == 7 and hit.get("source") == "ocr")
        report["ocr"] = {"id": paper["id"], "page": hit["page"], "block": hit["block"],
                         "locator": source(port, hit)}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sample", type=Path, default=SAMPLE)
    parser.add_argument("--attention", type=Path, help="Optional Attention v7 PDF for a real model inference check")
    parser.add_argument("--scan", type=Path, help="Optional scanned Attention v1 PDF for a real OCR check")
    options = parser.parse_args()
    print(json.dumps(smoke(options.port, options.sample, attention=options.attention, scan=options.scan), indent=2))
