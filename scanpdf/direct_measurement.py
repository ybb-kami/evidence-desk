"""Extract a uniquely supported scalar when the requested condition is explicit."""
import re

from claim_support import check, _scalars
from evidence_context import sentences, terms
from passage_answer import ASK_NUMBER, COMPARE, FOCUS

MEASURE = re.compile(r"\b(?:value|frequency|power|duration|count|speed|rate|accuracy|precision|recall|distance|length|height|width|temperature|pressure|mass|weight|size|score|voltage|capacity|latency|cost|amount|number)\b|多少|数量|频率|速度|距离|温度|准确率|得分|大小|时长",re.I)


def extract(question, english, hits):
    query=english or question
    scope=terms(query)
    if re.search(r"^\s*(?:who|when|where|why|(?:which|what)\s+(?:instrument|method|device|procedure)|how\s+(?:did|does|was|were|is|are))\b",query,re.I):
        return None,None
    if len(scope)<2 or COMPARE.search(query) or not (ASK_NUMBER.search(query) or MEASURE.search(query)):
        return None,None
    counted=FOCUS.search(query)
    entity=terms(counted[1]) if counted else set()
    options=[]
    for index,hit in enumerate(hits):
        for a,b in sentences(hit["text"]):
            sentence=hit["text"][a:b]
            if not 12<=len(sentence)<=400:
                continue
            words=terms(sentence)
            shared=scope & words
            if (len(shared)<2 or len(shared)/len(scope)<.8 or entity and not entity & words):
                continue
            readings=_scalars(sentence)
            if len(readings)!=1:
                continue
            reading,unit=readings[0]
            # A scalar attached only to a figure/table/section number cannot answer a measurement.
            if re.search(r"(?:figure|fig\.|table|Eq\.|section|图|表|第)\s*"+re.escape(reading)+r"(?!\d)",sentence,re.I):
                continue
            claim=dict(text=reading,quote=sentence,span=sentence,id=hit["id"],page=hit["page"],
                       block=hit["block"],part=hit.get("part",1),hit_index=index,answer_start=a,answer_end=b)
            options.append((len(shared),claim))
    best=max((score for score,_ in options),default=0)
    verified=[]
    for score, claim in options:
        if score != best:
            continue
        rejection=check(question,english,hits,claim)
        if rejection=="ambiguous_measurement":
            return None,rejection
        if not rejection:
            verified.append(claim)
    if len({v["text"].casefold() for v in verified})>1:
        return None,"ambiguous_measurement"
    return (verified[0],None) if verified else (None,None)
