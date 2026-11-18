from contextlib import contextmanager
import hashlib
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
import pymupdf

from workbench import Handler
import formula_review


class LocalStore:
    def __init__(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=100, height=100)
            page.insert_text((11, 23), "x=y")
            self.data = pdf.tobytes()
        self.docid = hashlib.sha256(self.data).hexdigest()

    def page_info(self, docid, number):
        if docid != self.docid or number != 1:
            raise formula_review.app.Bad(404, "Missing fixture.")
        return {
            "doc": {"id": self.docid, "name": "Local.pdf", "pages": 1},
            "page": {"render": {"dpi": 72}, "physical_page": 1,
                     "width_px": 100, "height_px": 100, "lines": [],
                     "regions": [
                         {"id": "p1-r1", "type": "formula", "status": "detected_only",
                          "bbox": [10, 15, 45, 28], "bbox_pt": [10, 15, 45, 28]},
                         {"id": "p1-r2", "type": "table"},
                     ]},
        }

    def pdf(self, docid):
        return self.data


@contextmanager
def serving(store):
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.library = store
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


class FormulaReviewTests(unittest.TestCase):
    def setUp(self):
        self.store = LocalStore()

    def test_disabled_configuration_preserves_region_and_exact_crop(self):
        with patch.object(formula_review, "_configuration", return_value=None), serving(self.store) as url:
            scope = f"id={self.store.docid}&page=1"
            with urlopen(url + "/formula-review") as response:
                self.assertIn("未核验候选", response.read().decode())
            with urlopen(url + "/api/formula-review?" + scope) as response:
                result = json.load(response)
                self.assertFalse(result["configured"])
                self.assertIn("EVIDENCE_FORMULA_WEIGHTS", result["setup"]["configuration"])
                self.assertEqual([r["id"] for r in result["regions"]], ["p1-r1"])
            with urlopen(url + "/formula-review/image?" + scope + "&region=p1-r1") as response:
                self.assertEqual(response.headers.get_content_type(), "image/png")
                with Image.open(BytesIO(response.read())) as image:
                    self.assertEqual(image.size, (35, 13))
                    self.assertLess(image.getextrema()[0], 200)
            request = Request(url + "/api/formula-review", headers={"Content-Type": "application/json"},
                              data=json.dumps({"id": self.store.docid, "page": 1, "region": "p1-r1"}).encode())
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 503)

    def test_only_selected_formula_can_reach_worker_and_cross_origin_is_refused(self):
        config = ("/fixture/python", "/fixture/weights")
        candidate = {"candidate": {"status": "unverified_candidate"}}
        with (patch.object(formula_review, "_configuration", return_value=config),
              patch.object(formula_review, "_infer", return_value=candidate) as infer,
              serving(self.store) as url):
            for region, origin, expected in (("p1-r2", url, 404), ("p1-r1", "http://external.invalid", 403)):
                request = Request(url + "/api/formula-review",
                                  headers={"Content-Type": "application/json", "Origin": origin},
                                  data=json.dumps({"id": self.store.docid, "page": 1, "region": region}).encode())
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, expected)
            infer.assert_not_called()
            request = Request(url + "/api/formula-review",
                              headers={"Content-Type": "application/json", "Origin": url},
                              data=json.dumps({"id": self.store.docid, "page": 1, "region": "p1-r1"}).encode())
            with urlopen(request) as response:
                self.assertEqual(json.load(response), candidate)
            infer.assert_called_once()
            self.assertEqual(infer.call_args.args[3]["id"], "p1-r1")


if __name__ == "__main__":
    unittest.main()
