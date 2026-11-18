"""HTTP acceptance tests: python -m unittest -v test_acceptance."""
import contextlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
SAMPLE = (ROOT / "sample.pdf").read_bytes()


class EvidenceDeskAcceptance(unittest.TestCase):
    def setUp(self):
        self.resources = contextlib.ExitStack()
        self.addCleanup(self.resources.close)
        self.data = Path(self.resources.enter_context(tempfile.TemporaryDirectory()))
        self.opener = build_opener(ProxyHandler({}))
        self.start_server()

    def start_server(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        with contextlib.ExitStack() as launch:
            log = launch.enter_context(tempfile.TemporaryFile(mode="w+b"))
            self.server = subprocess.Popen(
                [sys.executable, str(ROOT / "app.py"), "--port", str(port),
                 "--data", str(self.data)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            )
            launch.callback(self.stop_server)
            for _ in range(100):
                if self.server.poll() is not None:
                    break
                try:
                    self.read_json("/api/library")
                except (URLError, OSError):
                    time.sleep(.05)
                else:
                    self.resources.enter_context(launch.pop_all())
                    return
            log.seek(0)
            self.fail("Application did not start:\n" + log.read().decode(errors="replace"))

    def stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=3)

    def request(self, path, data=None, content_type=None):
        request = Request(self.base + path, data=data)
        if content_type:
            request.add_header("Content-Type", content_type)
        with self.opener.open(request, timeout=3) as response:
            return response.read(), response.headers

    def read_json(self, path, payload=None):
        if payload is None:
            return json.loads(self.request(path)[0])
        return json.loads(self.request(path, json.dumps(payload).encode(), "application/json")[0])

    def import_pdf(self, name, content=SAMPLE):
        path = "/api/import?name=" + quote(name, safe="")
        return json.loads(self.request(path, content, "application/pdf")[0])

    def test_import_search_source_and_restart(self):
        self.assertEqual(self.read_json("/api/library")["docs"], [])
        self.assertEqual(self.request("/sample.pdf")[0], SAMPLE)
        imported = self.import_pdf("Evidence Walkthrough.pdf")
        self.assertIs(imported["existing"], False)
        self.assertEqual(imported["duplicates"], [])
        doc = imported["doc"]
        self.assertEqual(doc["name"], "Evidence Walkthrough.pdf")
        self.assertEqual(doc["pages"], 2)

        query = {"query": "verify page numbering", "ids": [doc["id"]]}
        hits = self.read_json("/api/search", query)["hits"]
        evidence = [hit for hit in hits if hit["page"] == 2 and
                    "physical position" in hit["text"].casefold() and
                    "printed label" in hit["text"].casefold()]
        self.assertTrue(evidence, hits)
        self.assertEqual(evidence[0]["id"], doc["id"])
        self.assertEqual(evidence[0]["name"], doc["name"])
        self.assertIsNotNone(evidence[0]["block"])
        self.assertEqual(self.request("/pdf/" + quote(str(doc["id"]), safe=""))[0], SAMPLE)
        self.assertEqual(self.read_json("/api/search", {"query": "zzzxqz", "ids": [doc["id"]]})["hits"], [])

        self.stop_server()
        self.start_server()
        self.assertEqual([d["id"] for d in self.read_json("/api/library")["docs"]], [doc["id"]])
        self.assertEqual(self.read_json("/api/search", query)["hits"], hits)

    def test_renamed_duplicate_reuses_id_and_keeps_one_entry(self):
        first = self.import_pdf("First.pdf")
        second = self.import_pdf("Renamed file.pdf")
        self.assertIs(second["existing"], True)
        self.assertEqual(first["doc"]["id"], second["doc"]["id"])
        self.assertEqual(len(self.read_json("/api/library")["docs"]), 1)

    def test_malformed_import_does_not_change_library(self):
        for bad_data in (b"not a pdf", b"%PDF-1.7\nThis file is incomplete."):
            with self.subTest(bad_data=bad_data):
                with self.assertRaises(HTTPError) as rejected:
                    self.import_pdf("broken.pdf", bad_data)
                with rejected.exception as response:
                    self.assertGreaterEqual(response.code, 400)
                    self.assertLess(response.code, 500)
        self.assertEqual(self.read_json("/api/library")["docs"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
