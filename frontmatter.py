"""Conservative, inspectable first-page fields. No author database or email export."""
import re
from graph import _blocks
INSTITUTION=re.compile(r'\b(?:university|department|institute|laborator(?:y|ies)|college|research|google|microsoft|openai|academy|school)\b|大学|研究所|学院',re.I)
END=re.compile(r'^(?:[0-9]+\.?\s+|introduction\b|keywords?\b|index terms\b|摘要关键词|关键词|引言|绪论|[∗*†‡]|copyright|proceedings|[0-9]+(?:st|th|nd) conference)',re.I)
def first_page(doc):
    rows=[(b,txt) for p,b,cs,off,txt in _blocks(doc) if p==1 and not cs[0].get('arxiv_stamp')]
    title=(doc.get('bibliographic_identity') or {}).get('title')
    loc=lambda block:{'page':1,'block':block}
    authors=[];unassigned=[];abstract=[]; active=False; abstract_start=False
    for block,txt in rows:
        lines=[s.strip() for s in txt.splitlines() if s.strip()]
        if not lines:continue
        first=lines[0]
        if re.fullmatch(r'abstract|摘要',first,re.I):
            active=True;abstract_start=True;lines=lines[1:]
            if lines:abstract.append({**loc(block),'text':'\n'.join(lines)[:3500]})
            continue
        if active:
            if END.search(' '.join(lines[:2])) or '@' in txt or len(abstract)>=5:
                active=False
            elif len(txt.strip())>=50:
                abstract.append({**loc(block),'text':txt[:3500]})
            continue
        if abstract_start:continue
        if not any('@' in line for line in lines):continue
        before=lines[:next(i for i,line in enumerate(lines) if '@' in line)]
        affiliation=next((i for i,line in enumerate(before) if INSTITUTION.search(line)),None)
        names=before if affiliation is None else before[:affiliation]
        def name_ok(value):
            return 2<=len(value.split())<=5 and len(value)<=65 and not re.search(r'\d|[,:;/@=]',value) and sum(c.isalpha() for c in value)>4
        if not names or len(names)>10 or not all(name_ok(n) for n in names):continue
        for name in names:
            normalized=re.sub(r'[∗*†‡]+$','',name).strip()
            if affiliation is None:unassigned.append({'name':normalized,**loc(block)})
            else:
                authors.append({'name':normalized,'institution':before[affiliation],**loc(block),'relation':'same_source_block'})
    return {'id':doc['id'],'title':title,'authors':authors,'authors_without_local_affiliation':unassigned,'abstract_candidates':abstract,
            'note':'作者和单位仅按首页可核对的相同版面块并列，不跨块猜归属；共享单位块未分配贡献。摘要为显式标题后的候选块，可截断或漏识。请回原页核对。'}
