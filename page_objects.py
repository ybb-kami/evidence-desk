"""On-demand, coordinate-retaining PDF page inventory. No reconstructed LaTeX."""
import re
import fitz

CAPTION=re.compile(r'^(table|figure|fig\.|表|图)\s*([A-Za-z]?\d+(?:\.\d+)*)(?:[.:：]|$)',re.I)
MATH=re.compile(r'[∑∏√∫∥⊙≡≈∈]|[α-ωϵ][₀-₉\d]?\s*=')
def box(rect):return [round(float(x),2) for x in rect]
def inspect_page(data, number):
    with fitz.open(stream=data,filetype='pdf') as pdf:
        if not 1<=number<=len(pdf):raise ValueError('页码超出 PDF 范围。')
        page=pdf[number-1];record={'page':number,'size':[round(page.rect.width,2),round(page.rect.height,2)],'captions':[],'math_candidates':[],'table_rows':[],'images':[]}
        blocks=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES,sort=True)['blocks']
        lines=[]
        for b in blocks:
            if b.get('type')!=0:continue
            txt=' '.join(''.join(s['text'] for s in l['spans']).strip() for l in b['lines']).strip()
            cap=CAPTION.match(txt)
            if cap and len(record['captions'])<50:record['captions'].append({'kind':'table' if cap[1].lower() in ('表','table') else 'figure','number':cap[2],'text':txt[:1200],'bbox':box(b['bbox'])})
            for line in b['lines']:
                spans=line['spans'];text=''.join(s['text'] for s in spans).strip()
                if not text:continue
                if MATH.search(text) and len(record['math_candidates'])<24:record['math_candidates'].append({'text':text[:600],'bbox':box(line['bbox'])})
                lines.append((line['bbox'],text))
        # Expose visually aligned fragments below each table caption. Never infer a missing cell.
        for cap in (x for x in record['captions'] if x['kind']=='table'):
            x0,_,x1,cy=cap['bbox']; bands=[]
            for geom,text in sorted(lines,key=lambda x:(x[0][1],x[0][0])):
                x,y,r,bottom=geom
                if not (cy-1<=y<=min(cy+page.rect.height*.4,page.rect.height) and x0-5<=x<=x1+5):continue
                if (r-x) > .88*(x1-x0) and len(text)>65:break
                if not bands or abs(y-bands[-1][0])>2.6:bands.append((y,[]))
                bands[-1][1].append({'text':text[:300],'bbox':box(geom)})
            for _,cells in bands:
                if len(cells)>=2 and len(record['table_rows'])<100:
                    record['table_rows'].append({'caption':cap['number'],'fragments':sorted(cells,key=lambda c:c['bbox'][0])})
        record['images']=[{'bbox':box(im['bbox']),'pixels':[im['width'],im['height']]} for im in page.get_image_info()[:80]]
        return record
