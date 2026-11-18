"""Opt-in check of public Attention v7 source positions; consumes a local PDF."""
import argparse, hashlib, json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import find_evidence,parse_pdf

def norm(x):return ' '.join(x.casefold().split())
def check(path):
    data=path.read_bytes();doc=parse_pdf(data,'arxiv-1706.03762v7.pdf')
    assert doc['arxiv']['id']=='1706.03762' and doc['arxiv']['version']==7
    cases=[('GPU','NVIDIA P100 GPUs',7,['8 NVIDIA P100 GPUs']),
           ('Adam parameters','Adam optimizer',7,['β1 = 0.9','β2 = 0.98']),
           ('affiliation','Google Brain',1,['Ashish Vaswani','Google Brain']),
           ('German BLEU','28.4 BLEU',8,['English-to-German','28.4']),
           ('French BLEU in abstract','28.4 BLEU',1,['English-to-French','41.8']),
           ('French BLEU in section 6.1','41.0',8,['English-to-French','41.0'])]
    out=[]
    for name,q,p,terms in cases:
        candidates=find_evidence([doc],q)
        hit=next(x for x in candidates if x['page']==p and all(norm(t) in norm(x['text']) for t in terms))
        raw=subprocess.check_output(['pdftotext','-f',str(p),'-l',str(p),'-raw',str(path),'-'],text=True)
        clean=norm(raw).replace('β1','β1').replace('β 1','β1').replace('β 2','β2')
        assert all(norm(t) in clean for t in terms),(name,terms)
        out.append({'check':name,'query':q,'page':p,'block':hit['block'],'rank':1+candidates.index(hit)})
    return {'sha256':hashlib.sha256(data).hexdigest(),'anchors':out}
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('pdf',type=Path)
    print(json.dumps(check(ap.parse_args().pdf),indent=2))
