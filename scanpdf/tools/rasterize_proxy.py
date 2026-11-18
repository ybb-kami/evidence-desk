"""Create an image-only PDF from a locally available source PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf


def positive_dpi(value: str) -> int:
    dpi = int(value)
    if dpi < 1:
        raise argparse.ArgumentTypeError("DPI must be positive")
    return dpi


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dpi", type=positive_dpi, default=150)
    args = parser.parse_args()

    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output must be different files")
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")

    with pymupdf.open(args.input) as source, pymupdf.open() as proxy:
        if not source.is_pdf or source.is_encrypted or not len(source):
            parser.error("Input must be a readable PDF with at least one page")
        for source_page in source:
            image = source_page.get_pixmap(dpi=args.dpi)
            page = proxy.new_page(
                width=source_page.rect.width, height=source_page.rect.height
            )
            page.insert_image(page.rect, pixmap=image, keep_proportion=False)
        proxy.set_metadata(
            {
                "title": "Image-only proxy",
                "subject": "Rasterized from a locally supplied PDF",
            }
        )
        with args.output.open("xb") as output:
            proxy.save(output, deflate=True, no_new_id=True)
        pages = len(proxy)

    print(
        json.dumps(
            {
                "input": str(args.input),
                "input_sha256": sha256(args.input),
                "output": str(args.output),
                "output_sha256": sha256(args.output),
                "pages": pages,
                "dpi": args.dpi,
                "pymupdf_version": pymupdf.VersionBind,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
