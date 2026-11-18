"""Opt-in offline acceptance using a raster proxy, not an organizer scan.

Run: python test_ocr_public_proxy.py /path/to/1706.03762v1.pdf
Requires existing public arXiv PDF, PyMuPDF, Tesseract eng and Poppler.
Original and derivative are not redistributed; the proxy is temporary.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import unittest

import fitz

from ocr_local import create_derivative
from test_acceptance import LocalHTTPFixture

SHA256_V1 = "0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476"
PROXY_DPI = 150
EXPECTED_PAGES = 15
EXPECTED_PAGE = 7
QUERIES = {
    "NVIDIA P100 GPUs": "8 NVIDIA P100 GPUs",
    "Adam optimizer": "Adam optimizer [18]",
}


def _proxy(native, proxy):
    """Flatten every original page into one raster; never copy text or annotations."""
    with fitz.open(native) as original, fitz.open() as scanned:
        assert len(original) == EXPECTED_PAGES
        dimensions = [tuple(page.rect) for page in original]
        for page in original:
            scan = scanned.new_page(width=page.rect.width, height=page.rect.height)
            render = page.get_pixmap(dpi=PROXY_DPI, alpha=False, annots=False)
            scan.insert_image(scan.rect, stream=render.tobytes("jpeg", jpg_quality=90))
        scanned.set_metadata({"title": "Public arXiv v1 raster OCR proxy, not official scan"})
        scanned.save(proxy, garbage=4, deflate=True)
    with fitz.open(proxy) as check:
        assert [tuple(p.rect) for p in check] == dimensions
        assert all(not p.get_text().strip() and len(p.get_image_info()) == 1 for p in check)
    return dimensions


class _HTTP(LocalHTTPFixture, unittest.TestCase):
    pass


def accept(native):
    native = Path(native)
    original_hash = hashlib.sha256(native.read_bytes()).hexdigest()
    assert original_hash == SHA256_V1, "expected public arXiv 1706.03762v1 matching stated bytes"
    fixture = _HTTP()
    try:
        fixture.setUp()
        proxy = fixture.data / "public-v1.scan-proxy.pdf"
        ocr = fixture.data / "public-v1.scan-proxy.ocr.pdf"
        dims = _proxy(native, proxy)
        initial = fixture.import_pdf(proxy.name, proxy.read_bytes())["doc"]
        assert initial["indexed_blocks"] == 0
        assert initial["textless_pages"] == list(range(1, EXPECTED_PAGES + 1))
        started = time.monotonic()
        create_derivative(proxy, ocr)
        duration = time.monotonic() - started
        with fitz.open(ocr) as derivative:
            assert [tuple(p.rect) for p in derivative] == dims
            assert all(p.get_text().strip() for p in derivative)
        raw = ocr.read_bytes()
        indexed = fixture.import_pdf(ocr.name, raw)["doc"]
        assert not indexed["textless_pages"] and indexed["pages"] == EXPECTED_PAGES
        digital = subprocess.check_output(
            ["pdftotext", "-f", str(EXPECTED_PAGE), "-l", str(EXPECTED_PAGE), "-layout", str(native), "-"],
            text=True)
        observations = []
        for query, exact in QUERIES.items():
            assert exact in re.sub(r"\s+", " ", digital)
            assert fixture.read_json("/api/search", {"query": query, "ids": [initial["id"]]})["hits"] == []
            data = fixture.read_json("/api/search", {"query": query, "ids": [indexed["id"]]})
            found = next((hit for hit in data["hits"] if hit["page"] == EXPECTED_PAGE and exact in hit["text"]), None)
            assert found, data
            observations.append({"query": query, "source_phrase": exact, "physical_page": found["page"],
                                 "block": found["block"], "ocr_excerpt": found["text"]})
        for path, info in ((proxy, initial), (ocr, indexed)):
            assert fixture.request("/pdf/" + info["id"])[0] == path.read_bytes()
        assert hashlib.sha256(native.read_bytes()).hexdigest() == original_hash
        return {"source": "public arXiv 1706.03762v1; rasterized locally, not organizer v1_img",
                "source_sha256": original_hash, "proxy_dpi": PROXY_DPI, "pages": EXPECTED_PAGES,
                "proxy_textless_pages": len(initial["textless_pages"]),
                "ocr_seconds": round(duration, 3), "proxy_bytes": proxy.stat().st_size,
                "ocr_bytes": len(raw), "indexed_blocks": indexed["indexed_blocks"],
                "evidence": observations}
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_arxiv_v1")
    args = parser.parse_args()
    print(json.dumps(accept(args.public_arxiv_v1), ensure_ascii=False, indent=2))
