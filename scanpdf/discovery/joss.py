"""Prepare and search local metadata cached from the official JOSS repository."""

import argparse
from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
import unicodedata
from urllib.request import Request
from xml.etree import ElementTree

from . import fetch


MAX_XML = 512 * 1024
LICENSE = "https://creativecommons.org/licenses/by/4.0/"
VENUE = "Journal of Open Source Software"
_ACCESS = "{http://www.crossref.org/AccessIndicators.xsd}"


def catalog_root():
    return Path(os.environ.get("EVIDENCE_JOSS_CATALOG",
                               Path.home() / ".local/share/evidence-desk/joss-catalog"))


def _slug(doi):
    return doi.rsplit("/", 1)[1]


def _text(element):
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _parse(data, doi):
    if len(data) > MAX_XML or b"\x00" in data or re.search(rb"<!DOCTYPE|<!ENTITY", data):
        raise ValueError("Unsupported JOSS metadata")
    try:
        root = ElementTree.fromstring(data.decode("utf-8"))
    except (ElementTree.ParseError, UnicodeError) as error:
        raise ValueError("Invalid JOSS metadata") from error
    journal = root.find("./{*}body/{*}journal")
    if journal is None or journal.findtext("./{*}journal_metadata/{*}full_title") != VENUE:
        raise ValueError("Expected JOSS journal metadata")
    articles = [article for article in journal.findall("./{*}journal_article")
                if fetch.joss_doi(article.findtext("./{*}doi_data/{*}doi")) == doi]
    if len(articles) != 1:
        raise ValueError("JOSS metadata DOI does not match the requested record")
    item = articles[0]
    title = _text(item.find("./{*}titles/{*}title"))
    licenses = {_text(entry).replace("http://", "https://", 1).rstrip("/")
                for entry in item.iter(_ACCESS + "license_ref") if entry.get("applies_to") == "vor"}
    if not title or len(title) > 500 or LICENSE.rstrip("/") not in licenses:
        raise ValueError("JOSS metadata must publish a title and a CC BY 4.0 article license")
    authors = []
    for person in item.findall("./{*}contributors/{*}person_name")[:40]:
        if person.get("contributor_role") == "author":
            name = " ".join(filter(None, (_text(person.find("{*}given_name")), _text(person.find("{*}surname")))))
            if name:
                authors.append(name[:120])
    year = item.findtext("./{*}publication_date/{*}year")
    return {"title": title, "authors": authors, "year": int(year) if year and re.fullmatch(r"\d{4}", year) else None,
            "venue": VENUE, "doi": doi, "url": "https://doi.org/" + doi, "license": LICENSE,
            "metadata_url": fetch.joss_metadata_url(doi), "metadata_sha256": sha256(data).hexdigest()}


@lru_cache(maxsize=10000)
def _cached_entry(file, revision):
    doi = fetch.joss_doi("10.21105/" + file.stem)
    if doi is None:
        return None
    try:
        with file.open("rb") as metadata:
            return _parse(metadata.read(MAX_XML + 1), doi)
    except (OSError, ValueError):
        return None


def _read_entry(file):
    try:
        details = file.stat()
    except OSError:
        return None
    if not stat.S_ISREG(details.st_mode):
        return None
    return _cached_entry(file, (details.st_ino, details.st_size, details.st_mtime_ns))


def cached_publication(doi):
    canonical = fetch.joss_doi(doi)
    return _read_entry(catalog_root() / (_slug(canonical) + ".xml")) if canonical else None


def published(doi):
    return cached_publication(doi) is not None


def _terms(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def search(query):
    terms = _terms(query)
    required = set(terms)
    phrase = " " + " ".join(terms) + " "
    results = []
    count = 0
    for file in catalog_root().glob("joss.*.xml"):
        item = _read_entry(file)
        if item is None:
            continue
        count += 1
        if not terms:
            continue
        title = _terms(item["title"])
        if required <= set(title):
            has_phrase = phrase in " " + " ".join(title) + " "
            results.append((terms == title, has_phrase, -len(title), item["doi"], item))
    return {"provider": VENUE, "cached": True, "catalog_count": count,
            "items": [record for *_, record in sorted(results, reverse=True)[:5]]}


def read_publication(doi):
    doi = fetch.joss_doi(doi)
    if doi is None:
        raise ValueError("Expected a JOSS DOI such as 10.21105/joss.03021")
    url = fetch.joss_metadata_url(doi)
    request = Request(url, headers={"Accept": "application/xml, text/plain", "User-Agent": "EvidenceDesk/0.4"})
    with fetch._open(request, timeout=fetch.NETWORK_TIMEOUT) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("JOSS returned an unexpected metadata endpoint")
        data = response.read(MAX_XML + 1)
    return _parse(data, doi), data


def cache_record(doi, folder):
    item, data = read_publication(doi)
    doi = item["doi"]
    folder.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=folder, delete=False) as temporary:
        temporary.write(data)
        file = Path(temporary.name)
    try:
        file.replace(folder / (_slug(doi) + ".xml"))
    finally:
        file.unlink(missing_ok=True)
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=catalog_root())
    parser.add_argument("doi", nargs="+", help="Public JOSS DOIs to retrieve from the official repository")
    args = parser.parse_args()
    failed = False
    for doi in args.doi:
        try:
            item = cache_record(doi, args.catalog)
        except (OSError, ValueError) as error:
            print(f"{doi}: {error}")
            failed = True
        else:
            print(f"{item['doi']}  {item['title']}  {item['metadata_sha256']}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
