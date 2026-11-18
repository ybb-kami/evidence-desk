"""Find passages comparing two named subjects in the requested units."""
from collections import Counter
import re
import unicodedata

from evidence_context import sentences
from hybrid_retrieval import _stem

_NAME = r"[A-Z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*"
_PAIR = re.compile(r"(?<![A-Za-z0-9])(?=(" + _NAME + r")\s*(?:和|与|及|、|&|(?i:\band\b|\bversus\b|\bvs\b\.?|\bwith\b))\s*("
                   + _NAME + r")(?![A-Za-z0-9]))")
_COUNT_REQUEST = re.compile(r"多少|几[个层步张块次种片头]|\b(?:how many|number of|counts?)\b", re.I)
_NUMBER = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
_MEASURE = re.compile(r"(?<!\w)" + _NUMBER + r"[-\s]+([a-z]{2,})\b", re.I)
# Numbered units expressed in Chinese questions about English-language sources.
_UNITS = {"层": "layers", "参数": "parameters", "样本": "samples", "轮": "epochs", "帧": "frames",
          "通道": "channels", "注意力头": "heads", "步": "steps", "小时": "hours", "批次": "batches"}


def paired_measurements(doc, query):
    """Return source spans only; the quantity-to-subject mapping is checked downstream."""
    if not isinstance(query, str) or not _COUNT_REQUEST.search(query):
        return []
    pairs = _PAIR.findall(query)
    if len(pairs) != 1 or pairs[0][0].casefold() == pairs[0][1].casefold():
        return []
    names = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I)
             for name in pairs[0]]
    units = {_stem(word.lower()) for word in re.findall(r"[A-Za-z]+", query)}
    units.update(_stem(word) for term, word in _UNITS.items() if term in query)
    # Both names and two reports of the same requested unit must occur in one source sentence.
    matches = []
    for chunk in doc.get("chunks", ()):
        text = chunk["text"]
        if chunk.get("arxiv_stamp") or not all(pattern.search(text) for pattern in names):
            continue
        for start, end in sentences(text):
            raw = text[start:end]
            if not all(pattern.search(raw) for pattern in names):
                continue
            normalized = unicodedata.normalize("NFKC", raw)
            counts = Counter(_stem(unit.lower()) for unit in _MEASURE.findall(normalized))
            if any(counts[term] >= 2 for term in units):
                matches.append(dict(chunk, id=doc["id"], name=doc["name"], start=start, end=end,
                                    retrieval_candidate=True))
                break
    return sorted(matches, key=lambda hit: hit["end"] - hit["start"])[:2]
