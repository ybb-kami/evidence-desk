"""Bounded, offline comparison of normalized extracted PDF blocks."""
from difflib import SequenceMatcher
import hashlib
import re
import unicodedata

MAX_GROUPS = 24
MAX_ANCHORS = 3
MAX_EXCERPT = 800


def _blocks(doc):
    groups = {}
    for c in doc.get("chunks", ()):
        key = c["page"], c["block"]
        groups.setdefault(key, []).append(c)
    for (page, block), chunks in groups.items():
        chunks.sort(key=lambda item: item.get("offset", 0))
        base = chunks[0].get("offset", 0)
        anchor = chunks[0]["text"]
        text = anchor
        for chunk in chunks[1:]:
            offset = chunk.get("offset")
            if offset is None or offset - base > len(text):
                # Without a continuous source span, use this part only in the
                # comparison key; don't present composite text as a quotation.
                text += "\u0000" + chunk["text"]
            else:
                text += chunk["text"][max(0, len(text) - offset + base):]
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"(?<=\w)[-\u00ad]\s*\n\s*(?=\w)", "", normalized)
        labels = doc.get("page_labels", [])
        yield (hashlib.sha256(" ".join(normalized.split()).encode()).digest(), {
            "page": page, "page_label": labels[page - 1] if 1 <= page <= len(labels) else str(page),
            "block": block, "part": chunks[0].get("part", 1),
            "text": anchor[:MAX_EXCERPT],
            "clipped": len(chunks) > 1 or len(anchor) > MAX_EXCERPT,
        })


def describe_revision(before, after):
    """Compare PDF text only; anchors are literal prefixes of physical blocks."""
    left, right = list(_blocks(before)), list(_blocks(after))
    matcher = SequenceMatcher(None, [key for key, _ in left], [key for key, _ in right], autojunk=True)
    counts = dict(added=0, removed=0, replaced=0, equal=0)
    changes, truncated = [], False
    available = bool(left and right)
    if available:
        for tag, a, b, c, d in matcher.get_opcodes():
            if tag == "equal":
                counts["equal"] += b - a
                continue
            kind = {"delete": "removed", "insert": "added", "replace": "replaced"}[tag]
            common = min(b - a, d - c)
            counts["replaced"] += common
            counts["removed"] += b - a - common
            counts["added"] += d - c - common
            if len(changes) >= MAX_GROUPS:
                truncated = True
                continue
            truncated |= b - a > MAX_ANCHORS or d - c > MAX_ANCHORS
            changes.append({
                "kind": kind,
                "before": [value for _, value in left[a:min(b, a + MAX_ANCHORS)]],
                "after": [value for _, value in right[c:min(d, c + MAX_ANCHORS)]],
                "before_blocks": b - a, "after_blocks": d - c,
            })
    return {
        "counts": counts, "changes": changes, "truncated": truncated,
        "status": "ok" if available else "insufficient_text",
        "basis": "normalized_extracted_blocks",
        "text_coverage": {"before_blocks": len(left), "after_blocks": len(right),
                          "before_textless_pages": len(before.get("textless_pages", [])),
                          "after_textless_pages": len(after.get("textless_pages", []))},
    }
