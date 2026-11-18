"""Offline fixtures for catalog selection and fixed publisher download boundaries."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.request import Request

from app import Bad, Library
from . import fetch, joss, routes
from .test_download import Handler, Response, pdf_bytes


DOI = "10.21105/joss.08080"
BASE = "https://raw.githubusercontent.com/openjournals/joss-papers/master/joss.08080/10.21105.joss.08080"


def record(doi=DOI, title="Optical calibration tools for research microscopy", license=joss.LICENSE):
    return f"""<doi_batch xmlns="http://www.crossref.org/schema/4.4.0"
                 xmlns:ai="http://www.crossref.org/AccessIndicators.xsd">
     <body><journal><journal_metadata><full_title>Journal of Open Source Software</full_title></journal_metadata>
      <journal_article><titles><title>{title}</title></titles>
       <contributors><person_name contributor_role="author"><given_name>Pat</given_name>
        <surname>Taylor</surname></person_name></contributors>
       <publication_date><year>2026</year></publication_date>
       <ai:license_ref applies_to="vor">{license}</ai:license_ref>
       <doi_data><doi>{doi}</doi></doi_data>
      </journal_article>
     </journal></body></doi_batch>""".encode()


class JossTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "catalog"
        settings = patch.dict(os.environ, {"EVIDENCE_JOSS_CATALOG": str(self.folder)})
        settings.start()
        self.addCleanup(settings.stop)
        self.library = Library(Path(temp.name) / "library")

    def add_metadata(self, data=None):
        url = BASE + ".crossref.xml"
        with patch.object(fetch, "_open", return_value=Response(data or record(), url=url)) as network:
            item = joss.cache_record(DOI.upper(), self.folder)
            self.assertEqual(network.call_args.args[0].full_url, url)
            return item

    def test_catalog_search_is_local_and_respects_title_and_doi(self):
        entry = self.add_metadata()
        self.assertEqual(entry["authors"], ["Pat Taylor"])
        with patch.object(fetch, "_open") as network:
            handler = Handler(self.library, {"query": "OPTICAL calibration"})
            self.assertTrue(routes.handle(handler, True, "/api/download/joss/search"))
            self.assertEqual(handler.response[1]["items"], [entry])
            self.assertEqual(joss.search("Spectroscopy")["items"], [])
            network.assert_not_called()

    def test_metadata_requires_matching_doi_and_article_license(self):
        for data in (record(doi="10.21105/joss.08081"), record(license="https://example.org/terms"),
                     b"<!DOCTYPE doc [<!ENTITY a 'contents'>]>" + record()):
            with self.subTest(data=data[:80]), self.assertRaises(ValueError):
                self.add_metadata(data)
        self.assertFalse(joss.published(DOI))

    def test_download_requires_confirmation_and_valid_local_metadata(self):
        path = "/api/download/joss"
        handler = Handler(self.library, {"doi": DOI, "consent": True})
        with patch.object(fetch, "retrieve", return_value=(0, pdf_bytes())) as network:
            with self.assertRaises(Bad) as missing:
                routes.handle(handler, True, path)
            self.assertEqual(missing.exception.code, 400)
            self.add_metadata()
            handler.data = {"doi": DOI, "consent": False}
            with self.assertRaises(Bad) as consent:
                routes.handle(handler, True, path)
            self.assertEqual(consent.exception.code, 403)
            network.assert_not_called()
            handler.data = {"doi": DOI.upper(), "consent": True}
            self.assertTrue(routes.handle(handler, True, path))
            network.assert_called_once_with(DOI, "joss")
        item = handler.response[1]
        self.assertEqual(item["source"]["sha256"], item["doc"]["id"])
        self.assertEqual(item["source"]["url"], BASE + ".pdf")

    def test_download_path_is_constructed_from_an_exact_doi(self):
        self.assertEqual(fetch.pdf_url(DOI.upper(), "joss"), BASE + ".pdf")
        for bad in (BASE + ".pdf", DOI + "?next=1", DOI + "/", DOI + "\n", "10.21105/joss.00000", "10.21105/joss.1"):
            with self.subTest(value=bad), patch.object(fetch, "_open") as network:
                self.assertEqual(fetch._download(bad, "joss"), (fetch.UNAVAILABLE, b""))
                network.assert_not_called()

    def test_environment_proxy_is_opt_in_and_limited_to_joss(self):
        publisher = Request(BASE + ".pdf")
        acl = Request(fetch.ACL_ENDPOINT + "N19-1423.pdf")
        plain = "http://proxy.invalid:8080"
        cases = [(publisher, "0", plain, {}), (acl, "1", plain, {}),
                 (publisher, "1", "http://user:pass@proxy.invalid", {}), (publisher, "1", plain, {"https": plain})]
        for request, enabled, proxy, expected in cases:
            with (self.subTest(enabled=enabled, request=request),
                  patch.dict(os.environ, {"EVIDENCE_JOSS_USE_SYSTEM_PROXY": enabled}),
                  patch.object(fetch, "getproxies", return_value={"https": proxy}),
                  patch.object(fetch, "build_opener") as opener):
                fetch._open(request, timeout=3)
                self.assertEqual(opener.call_args.args[0].proxies, expected)
