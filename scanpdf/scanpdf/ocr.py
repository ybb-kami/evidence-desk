"""Tesseract TSV reader; coordinates refer to the supplied image."""
import csv
import io
import os
import subprocess

from PIL import Image


def recognize(image: Image.Image, dpi: int, lang: str, psm: int, *, whitelist=None, timeout=120) -> list[dict]:
    encoded = io.BytesIO()
    image.save(encoded, format="PNG", compress_level=1)
    command = ["tesseract", "stdin", "stdout", "-l", lang, "--dpi", str(dpi), "--psm", str(psm)]
    if whitelist:
        command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
    try:
        run = subprocess.run(
            [*command, "tsv"],
            input=encoded.getvalue(), capture_output=True, timeout=timeout,
            env={**os.environ, "OMP_THREAD_LIMIT": os.environ.get("OMP_THREAD_LIMIT", "1")},
        )
    except FileNotFoundError as exc:
        raise RuntimeError("tesseract executable is required") from exc
    if run.returncode:
        raise RuntimeError(run.stderr.decode("utf-8", errors="replace").strip())
    rows = csv.DictReader(io.StringIO(run.stdout.decode("utf-8")), delimiter="\t",
                          quoting=csv.QUOTE_NONE)
    if not rows.fieldnames or "text" not in rows.fieldnames:
        raise RuntimeError("Tesseract returned no TSV header")
    words = []
    for row in rows:
        if row["level"] != "5" or not row["text"].strip():
            continue
        x, y, w, h = (int(row[key]) for key in ("left", "top", "width", "height"))
        box = [max(0, x), max(0, y), min(image.width, x + w), min(image.height, y + h)]
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        words.append({
            "text": row["text"].strip(), "bbox": box, "confidence": float(row["conf"]),
            "line_id": ".".join(row[k] for k in ("block_num", "par_num", "line_num")),
        })
    return words
