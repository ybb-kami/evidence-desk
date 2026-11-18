"""Selective partner retrieval adapters; original matching happens in workflow.py.

Provenance: ybb-kami/evidence-desk, feature/scanpdf-parser,
abbb7140151f2e6f18813873e72e84b8da24724c (byline.py and engine.py).
Changes: retain the source byline detector separately; extract only two
intent-specific candidate types. Reference intent is adapted to our
bibliography boundaries and exact indexed windows. No engine replacement.
"""
import hashlib
import re
import unicodedata

import fitz

from engine import MAX_BYTES, _windows
from graph import _bibliography, extract_identity
from partner_byline import author_blocks, author_number

SOURCE_SHA = "abbb7140151f2e6f18813873e72e84b8da24724c"
PROVENANCE = {
    "repository":"ybb-kami/evidence-desk","revision":SOURCE_SHA,
    "features":{"partner_author_ordinal":"byline.py + engine.py ordinal intent",
                "partner_reference_entry":"engine.py citation intent, adapted to local reference entries"},
}
CITATION_INTENT = re.compile(r"参考文献|引文|引用|(?<![a-z0-9])(?:cited|cite|citations?|references?|bibliography)(?![a-z0-9])",re.I)
NUMBERED = re.compile(r"^\s*(?:\[\d{1,5}\]|\d{1,5}\.)")


def _name(text):
    return " ".join(re.sub(r"(?<=\w)[-\u00ad]\s*\n\s*(?=\w)", "",
                           unicodedata.normalize("NFKC",text)).casefold().split())


def _declared_title(query):
    match = re.search(r"《([^》]+)》|论文\s*([A-Za-z][^？?。]*?)\s*的第",query)
    return _name(next(part for part in match.groups() if part)) if match else None


def _byline(doc, ordinal, title, get_pdf):
    try:
        source=get_pdf(doc["id"])
    except Exception:
        return []
    try:
        if not isinstance(source,bytes) or not 0<len(source)<=MAX_BYTES or hashlib.sha256(source).hexdigest()!=doc["id"]:
            return []
        with fitz.open(stream=source,filetype="pdf") as pdf:
            if pdf.needs_pass or not pdf.is_pdf or not len(pdf):
                return []
            page=pdf[0]
            observed_title=extract_identity(page).get("title") or ""
            if title and title!=_name(observed_title):
                return []
            blocks=page.get_text("blocks",flags=fitz.TEXTFLAGS_BLOCKS,sort=True)
            ids=author_blocks(blocks,observed_title)
            if not 1<=ordinal<=len(ids):
                return []
            block=ids[ordinal-1]
            snippet=next(b[4].strip() for b in blocks if b[6]==0 and b[5]+1==block)
            # Keep whole verifiable windows; the visible association is not a
            # statement of the person's present employment or all affiliations.
            return [{"page":1,"block":block,"part":part,"offset":offset,"text":text}
                    for part,(text,offset) in enumerate(_windows(snippet),1)][:3]
    except (IndexError,StopIteration,ValueError,OSError,RuntimeError):
        return []


def _reference_topic(query):
    if not CITATION_INTENT.search(query):
        return None
    quoted = (r"“([^”]{3,160})”",r'"([^"]{3,160})"',r"《([^》]{3,160})》")
    unquoted = (r"关于\s*([^，。？?]{3,120}?)\s*的(?:参考文献|引用|书目)",
                r"\b(?:references?|citation|entry)\s+(?:for|on|to)\s+([A-Za-z][^?。;]{2,100})")
    for group in (quoted,unquoted):
        parsed=[]
        for pattern in group:
            for captured in re.findall(pattern,query,flags=re.I):
                key=_name(captured.strip())
                if re.search(r"[^\W\d_]",key) and key not in parsed:
                    parsed.append(key)
        if parsed:
            return parsed[0] if len(parsed)==1 else None
    return None


def _reference(doc, topic):
    _, entries=_bibliography(doc)
    exact_phrase=re.compile(r"(?<!\w)"+re.escape(topic)+r"(?!\w)") if topic.isascii() else None
    candidates=[]
    for entry in entries:
        normalized=_name(entry["text"])
        if not (exact_phrase.search(normalized) if exact_phrase else topic in normalized):
            continue
        # The display must be a complete originally extracted window containing
        # the entry, never normalized words reconstructed into a quotation.
        c=next((c for c in doc["chunks"] if (c["page"],c["block"])==(entry["page"],entry["block"])
                and c["text"].find(entry["text"])>=0),None)
        if c is not None:
            candidates.append((not bool(NUMBERED.match(entry["text"])),entry["page"],entry["block"],c))
    candidates.sort(key=lambda item:item[:3])
    hits={}
    for _, _, _, c in candidates:
        hits.setdefault((c["page"],c["block"],c["part"]),c)
        if len(hits)==3:
            break
    return list(hits.values())


def candidates(docs, query, get_pdf):
    """Emit typed per-document original candidates with no synthesized answers."""
    try:
        ordinal=author_number(query)
    except (ValueError,TypeError):
        ordinal=None
    title=_declared_title(query) if ordinal is not None else None
    topic=_reference_topic(query)
    found=[]
    for doc in docs:
        if ordinal is not None:
            for hit in _byline(doc,ordinal,title,get_pdf):
                found.append((doc["id"],"partner_author_ordinal",f"原页视觉次序：第 {ordinal} 位作者候选（不推断当前任职）",hit))
        if topic:
            for hit in _reference(doc,topic):
                found.append((doc["id"],"partner_reference_entry","参考文献区原文条目候选",hit))
    return found
