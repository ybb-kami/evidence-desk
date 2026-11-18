"""Offline audit of one physical page, not a general question-answering model."""
import argparse,hashlib,json
from pathlib import Path
import fitz
from native_layout import recover_unruled

def inspect(raw,page,expected):
 if not isinstance(raw,bytes) or len(raw)>24*1024*1024:raise ValueError('Maximum input 24 MiB')
 src=hashlib.sha256(raw).hexdigest()
 if src!=expected:return {'status':'source_hash_mismatch','page':page,'cells':[]}
 if type(page) is not int:raise ValueError('Invalid physical page')
 with fitz.open(stream=raw,filetype='pdf') as d:
  if d.needs_pass or not 1<=page<=len(d)<=650:raise ValueError('Unsupported PDF or page')
  p=d[page-1]
  if p.rotation:return {'status':'unsupported_rotation','sha256':src,'page':page}
  words=[{'text':w[4],'bbox_pt':list(w[:4])} for w in p.get_text('words')]
  r=recover_unruled(words,page);out={'status':'no_recognized_table','sha256':src,'page':page,'cells':[]}
  if not r:return out
  cells=r.get('table',r)['cells']
  for c in cells:
   actual=' '.join(p.get_text(clip=fitz.Rect(c['bbox_pt'])).split())
   if actual!=' '.join(c['text'].split()):return {**out,'status':'source_recheck_failed'}
  return {**out,'status':'original_cells_rechecked','cells':cells,'frame':'unrotated_pdf_points','claim_support':'not_assessed'}
