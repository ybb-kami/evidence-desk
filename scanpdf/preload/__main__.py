"""Import trusted local PDFs through the workbench's library implementation."""

import argparse
import json
from pathlib import Path
import sqlite3

from .load import preload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path, help="The workbench's existing or new library data directory")
    parser.add_argument("--pdf-dir", type=Path, default=Path("pdf"), help="Trusted directory containing the original PDFs")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="Select every top-level PDF")
    selection.add_argument("--demo", action="store_true", help="Select the competition demonstration documents")
    selection.add_argument("--file", action="append", metavar="BASENAME", help="Select a specific PDF, repeat as needed")
    args = parser.parse_args()
    try:
        report = preload(args.data, args.pdf_dir, mode="all" if args.all else "demo" if args.demo else "explicit",
                         names=args.file)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
