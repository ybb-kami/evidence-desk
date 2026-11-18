"""Bind a model quotation to an unambiguous original PDF character interval."""
import re
import unicodedata

_PUNCT = str.maketrans({"’":"'","‘":"'","“":'"',"”":'"'})
_HYPHEN_WRAP = re.compile(r"(?<=[A-Za-z])[-\u2010][ \t]*\r?\n[ \t]*(?=[a-z])")


def _normalized(text):
    skip = {i for m in _HYPHEN_WRAP.finditer(text) for i in range(*m.span())}
    chars, positions = [], []
    for i, original in enumerate(text):
        if i in skip or original in {"\u00ad","\u200b"}:
            continue
        for char in unicodedata.normalize("NFKC",original).translate(_PUNCT):
            if char.isspace():
                if chars and chars[-1]==" ":
                    positions[-1]=(positions[-1][0],i+1)
                    continue
                char=" "
            chars.append(char)
            positions.append((i,i+1))
    return "".join(chars),positions


def bind(quote, raw):
    if not isinstance(quote,str) or not 1<=len(quote.strip())<=620:
        return None
    quote=quote.strip()
    index=raw.find(quote)
    if index>=0 and raw.find(quote,index+1)<0:
        return index,index+len(quote)
    needle,_=_normalized(quote)
    text,positions=_normalized(raw)
    needle=needle.strip()
    if not needle:
        return None
    index=text.find(needle)
    if index<0 or text.find(needle,index+1)>=0:
        return None
    return positions[index][0],positions[index+len(needle)-1][1]
