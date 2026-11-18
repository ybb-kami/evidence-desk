"""Keep layout-sensitive mathematical table reads available as raw evidence."""
import re
import unicodedata


ASYMPTOTIC = re.compile(r"^[OoΘΩω]\s*[\(\[{]\s*\S")
SIMPLE = re.compile(r"^[OoΘΩω]\s*\(\s*(?:[a-zA-Z]|[0-9]+)\s*\)$")
ROOT = re.compile(r"^(?:[√∛∜]\s*\S|sqrt\s*[(\{])", re.I)
MIN_SIMPLE_CONFIDENCE = 70


def review_math_cells(regions):
    """Separate unsupported mathematical notation from recognized cell values."""
    for region in regions:
        if region.get("type") != "table":
            continue
        for cell in region.get("cells", ()):
            if cell.get("status") != "ocr_text":
                continue
            text = " ".join(unicodedata.normalize("NFKC", cell["text"]).split())
            if not (ASYMPTOTIC.match(text) or ROOT.match(text)):
                continue
            reason = "layout_sensitive_notation"
            if SIMPLE.fullmatch(text):
                if (cell.get("ocr_confidence") or 0) >= MIN_SIMPLE_CONFIDENCE:
                    continue
                reason = "low_confidence_notation"
            cell.setdefault("ocr_raw", {"text": cell["text"], "confidence": cell.get("ocr_confidence")})
            cell.update(text="", status="math_unverified", text_method="math_original_review",
                        math_review={"status": "needs_original", "reason": reason})
