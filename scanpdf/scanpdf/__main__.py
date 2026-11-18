import argparse
import json
from pathlib import Path
import subprocess

from . import parse_pdf


def main():
    cli = argparse.ArgumentParser(description="OCR scanned PDF pages with traceable evidence.")
    cli.add_argument("input", type=Path)
    cli.add_argument("--output", type=Path, required=True, help="New UTF-8 JSON file")
    cli.add_argument("--pages", help="1-based physical pages, e.g. 1,3-5")
    cli.add_argument("--dpi", type=int, default=240)
    cli.add_argument("--lang", default="eng")
    cli.add_argument("--crops", type=Path, help="Directory for region evidence PNG files")
    args = cli.parse_args()
    if args.output.exists():
        cli.error(f"Output already exists: {args.output}")
    try:
        result = parse_pdf(args.input, pages=args.pages, dpi=args.dpi, lang=args.lang, crops=args.crops)
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        cli.exit(1, f"scanpdf: {exc}\n")


if __name__ == "__main__":
    main()
