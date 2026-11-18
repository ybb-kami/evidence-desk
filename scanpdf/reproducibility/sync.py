"""Acquire an explicit, publicly referenced source snapshot for this local library."""

import argparse
import json
from pathlib import Path
import sqlite3

from app import ID, Library

from .github import CATEGORIES, CheckError
from .references import REFERENCE_ID, find_references
from . import snapshot


def select_reference(store, docid, *, repo=None, refid=None):
    docs = store.records([docid]) if isinstance(docid, str) and ID.fullmatch(docid) else []
    if len(docs) != 1:
        raise CheckError("active_document_required")
    references, _ = find_references(docs[0])
    if repo is None and refid is None:
        return references
    if refid is not None and not REFERENCE_ID.fullmatch(refid):
        raise CheckError("invalid_reference")
    matches = [item for item in references if (item["id"] == refid if refid is not None
               else item["label"].casefold() == repo.casefold())]
    if len(matches) != 1:
        raise CheckError("select_unique_pdf_reference")
    return matches[0]


def acquire(store, docid, *, repo=None, refid=None, candidates, consent=False):
    if consent is not True:
        raise CheckError("consent_required")
    if (repo is None) == (refid is None):
        raise CheckError("select_unique_pdf_reference")
    reference = select_reference(store, docid, repo=repo, refid=refid)
    evidence = snapshot.inspect_reference(reference, candidates)
    digest = snapshot.save(store, docid, reference["id"], evidence)
    return {"doc_sha256":docid, "reference_id":reference["id"], "snapshot":evidence | {"record_sha256":digest}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--document", required=True, metavar="PDF_SHA256")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--list", action="store_true", help="List indexed references without network access")
    choice.add_argument("--repository", metavar="OWNER/REPO", help="Select a unique reference from the PDF")
    choice.add_argument("--reference", metavar="LOCAL_ID", help="Select the exact local reference when there are multiple versions")
    parser.add_argument("--file", nargs=2, action="append", metavar=("CATEGORY", "RELATIVE_PATH"),
                        help="Read a specific UTF-8 candidate; categories: " + ", ".join(CATEGORIES))
    parser.add_argument("--confirm", action="store_true",
                        help="Approve sending the public repository and requested paths to the two fixed GitHub hosts")
    args = parser.parse_args()
    if not args.list and (not args.confirm or not args.file):
        parser.error("选定快照请使用 --file 指定文件，并用 --confirm 确认这次公开读取。")
    if not (args.data / "library.sqlite").is_file():
        parser.error("指定已有工作台文库目录。")
    try:
        store = Library(args.data)
        report = {"references":select_reference(store, args.document)} if args.list else acquire(
            store, args.document, repo=args.repository, refid=args.reference, candidates=args.file, consent=args.confirm)
    except (OSError, sqlite3.Error, CheckError) as error:
        print(json.dumps({"status":"unavailable", "reason": getattr(error, "code", "local_storage_error")}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
