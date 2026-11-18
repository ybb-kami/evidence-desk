"""Offline, opt-in English OCR for small image-only PDFs (Linux/macOS)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

DPI = 150
MAX_PAGES = 20
MAX_BYTES = 15 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024
MAX_PIXELS = 2_500_000
MAX_SECONDS = 120
PAGE_SECONDS = 12
MAX_CPU_SECONDS = 60


def _ocr_worker(input_path: Path, staged: Path) -> None:
    import resource
    import fitz

    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    tool = shutil.which("tesseract")
    if not tool:
        raise ValueError("Install Tesseract and its English ('eng') data through your system package manager.")
    if not input_path.is_file():
        raise ValueError("Provide a regular local PDF file.")
    with input_path.open("rb") as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
        raise ValueError("Provide a PDF no larger than 15 MiB.")
    raster_file = staged.parent / "ocr-page.pdf"
    env = {"PATH": os.defpath, "LANG": "C", "OMP_THREAD_LIMIT": "1"}
    # Keep explicit tessdata lookup for installations that need it (e.g. Homebrew).
    if os.environ.get("TESSDATA_PREFIX"):
        env["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    with fitz.open(stream=data, filetype="pdf") as original, fitz.open() as out:
        if original.needs_pass or not 1 <= len(original) <= MAX_PAGES:
            raise ValueError(f"Encrypted PDFs are unsupported; provide 1 to {MAX_PAGES} pages.")
        for index, page in enumerate(original):
            if page.get_text().strip():
                raise ValueError(f"Page {index + 1} already has text; this helper handles image-only scans.")
            rect = page.rect
            factor = DPI / 72
            width, height = int(rect.width * factor) + 2, int(rect.height * factor) + 2
            if width < 3 or height < 3 or width * height > MAX_PIXELS:
                raise ValueError(f"Page {index + 1} exceeds the OCR rendering limits.")
            rendered = page.get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False)
            png = rendered.tobytes("png")
            raster_file.unlink(missing_ok=True)
            try:
                run = subprocess.run([tool, "stdin", str(raster_file.with_suffix("")), "-l", "eng",
                                      "--dpi", str(DPI), "pdf"], input=png,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     env=env, timeout=PAGE_SECONDS, check=False)
            except subprocess.TimeoutExpired:
                raise ValueError(f"OCR timed out on page {index + 1}.") from None
            if run.returncode != 0 or not raster_file.is_file() or raster_file.stat().st_size > MAX_OUTPUT:
                raise ValueError(f"OCR failed on page {index + 1}; check the English data installation.")
            with fitz.open(raster_file) as recognized:
                if len(recognized) != 1 or not recognized[0].get_text().strip():
                    raise ValueError(f"OCR found no text on page {index + 1}; no derivative was written.")
                # Match the source's visible bounds, including rotations, rather
                # than keep the OCR PDF's pixel-rounded physical dimensions.
                dest = out.new_page(width=rect.width, height=rect.height)
                dest.show_pdf_page(dest.rect, recognized, 0, keep_proportion=False)
        out.set_metadata({"title": "Local English OCR text-layer derivative",
                          "subject": "Machine transcription; verify against the original scan"})
        expected_pages = len(original)
        out.save(staged, garbage=3, deflate=True)
    with fitz.open(staged) as verified:
        if len(verified) != expected_pages or not all(p.get_text().strip() for p in verified):
            raise ValueError("The saved OCR derivative did not pass text verification.")




def create_derivative(source: Path, output: Path, *, cancel_event=None) -> None:
    if os.name != "posix":
        raise ValueError("This OCR helper supports Linux and macOS only.")
    if not output.name.endswith(".ocr.pdf"):
        raise ValueError("Name the new output file with the suffix .ocr.pdf.")
    if os.path.lexists(output):
        raise ValueError("Output already exists; no file was changed.")
    if cancel_event is not None and cancel_event.is_set():
        raise ValueError("OCR was stopped; no derivative was written.")
    with tempfile.TemporaryDirectory(prefix=".local-ocr-", dir=output.parent) as folder:
        staged = Path(folder) / "verified.pdf"
        cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", str(source), str(staged)]
        proc = subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        try:
            with proc:
                try:
                    deadline = time.monotonic() + MAX_SECONDS
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ValueError("OCR was stopped; no derivative was written.")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(cmd, MAX_SECONDS)
                        try:
                            _, stderr = proc.communicate(timeout=remaining if cancel_event is None else min(remaining, 0.25))
                            break
                        except subprocess.TimeoutExpired:
                            if cancel_event is None:
                                raise
                except BaseException:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    raise
        except subprocess.TimeoutExpired:
            raise ValueError(f"OCR exceeded the {MAX_SECONDS}-second limit; no derivative was written.") from None
        if proc.returncode or not staged.is_file():
            raise ValueError(stderr.decode("utf-8", "replace").strip()[:400] or
                             "OCR failed; no derivative was written.")
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("OCR was stopped; no derivative was written.")
        try:
            os.link(staged, output)
        except FileExistsError:
            raise ValueError("Output already exists; no file was changed.") from None


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        try:
            # Only create a new staging PDF inside the parent's private temp dir.
            target = Path(sys.argv[3])
            if not target.parent.name.startswith(".local-ocr-") or target.name != "verified.pdf" or target.exists():
                raise ValueError("Invalid OCR staging path.")
            _ocr_worker(Path(sys.argv[2]), target)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        except Exception:
            print("OCR could not process this PDF; no derivative was written.", file=sys.stderr)
            return 1
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, help="new file ending in .ocr.pdf")
    args = parser.parse_args()
    try:
        create_derivative(args.input, args.output)
    except (OSError, ValueError) as e:
        parser.exit(1, f"OCR: {e}\n")
    print(f"Created local OCR derivative: {args.output}")
    print("Verify recognized text against the original scanned page before citing it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
