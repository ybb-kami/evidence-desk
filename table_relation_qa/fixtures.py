"""Deterministic synthetic PDFs, not scientific results or contest originals."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import fitz

HEADERS = ("Task", "Year", "System", "Role", "BLEU")
X = (50, 188, 285, 456, 585)
W, H = 730, 850
TASK = "English-to-German"
YEAR = "2037"
MODEL = "Paper Swan"
BASE = "Candle Net"
QUESTION = ("For the 2037 English-to-German task, what is the BLEU score of Paper Swan, "
            "what is the single earlier baseline marked prior, and what is the signed difference?")
PAPER = "Synthetic Tables for Evidence Lookup - Not a Scientific Publication"


def row(task,year,system,role,score):
    return dict(zip(HEADERS,(task,year,system,role,score)))


DECOYS = (
    row("English-to-French",YEAR,MODEL,"tested","28.4"),
    row("English-to-French",YEAR,BASE,"prior","26.36"),
    row(TASK,"2036",MODEL,"tested","57"),
    row(TASK,YEAR,"Paper Swan Extra","alternate","76"),
)


@dataclass(frozen=True)
class Layout:
    id: str
    page: int
    number: int
    columns: tuple
    rows: tuple
    signal: str


LAYOUTS = (
    Layout("positive",2,5,HEADERS,(
        DECOYS[0],row(TASK,YEAR,BASE,"prior","28.50"),DECOYS[2],
        row(TASK,YEAR,MODEL,"tested","31.75"),DECOYS[1],DECOYS[3]),"matched"),
    Layout("reordered_negative",4,9,("BLEU","Role","Year","System","Task"),(
        DECOYS[3],row(TASK,YEAR,MODEL,"tested","27.25"),DECOYS[2],
        row(TASK,YEAR,BASE,"prior","29.50"),DECOYS[0],DECOYS[1]),"matched"),
    Layout("missing_baseline_cell",3,3,("System","BLEU","Task","Year","Role"),(
        DECOYS[1],row(TASK,YEAR,BASE,"prior",""),DECOYS[3],
        row(TASK,YEAR,MODEL,"tested","31.75"),DECOYS[0]),"missing_value"),
    Layout("missing_target_row",4,2,HEADERS,(
        DECOYS[0],DECOYS[1],DECOYS[2],DECOYS[3],row(TASK,YEAR,BASE,"prior","28.50")),"missing_row"),
    Layout("ambiguous_prior",2,7,("Year","System","Task","BLEU","Role"),(
        DECOYS[1],row(TASK,YEAR,BASE,"prior","28.50"),row(TASK,YEAR,"Lantern Max","prior","30.0"),
        row(TASK,YEAR,MODEL,"tested","31.75"),DECOYS[2]),"ambiguous_baseline"),
    Layout("no_target_scope",5,4,("Role","System","Task","Year","BLEU"),
           DECOYS,"missing_row"),
)


def _box(x,y,index):
    xmax=X[index+1]-5 if index+1<len(X) else W-38
    return [x-1,y-12,xmax,y+4]


def _table(page,spec):
    caption=f"Table {spec.number}: Invented translation bench, separated by task, year and evaluation role. Synthetic QA only."
    page.insert_text((50,100),caption,fontsize=13)
    cells=[]
    fields=[dict(zip(spec.columns,spec.columns)),*spec.rows]
    for i,values in enumerate(fields):
        y=158+31*i
        for j,col in enumerate(spec.columns):
            value=values[col]
            if value:
                page.insert_text((X[j],y),value,fontsize=10)
            cells.append({"page":spec.page,"table":spec.number,"row":i,"column":j+1,
                          "field":col,"text":value,"bbox":_box(X[j],y,j)})
    for yy in (143,167,169+31*len(spec.rows)):
        page.draw_line((47,yy),(690,yy),width=1)
    return caption,cells


def _text(pdf,spec):
    with fitz.open() as out:
        out.set_metadata({"title":PAPER,"author":"Evidence Desk QA synthetic fixture","subject":spec.id})
        for i in range(1,spec.page+1):
            page=out.new_page(width=W,height=H)
            if i==spec.page:
                caption,cells=_table(page,spec)
            elif i==1:
                page.insert_text((50,65),PAPER,fontsize=17)
                page.insert_text((50,99),"The values below are unrelated repetitions, not evaluation results.",fontsize=11)
                page.insert_text((50,130),"Example noise: Paper Swan 28.4; Candle Net 26.36; arithmetic illustration 2.04.",fontsize=11)
                page.insert_text((50,158),"A separate 2036 English-to-French exposition is not the requested task.",fontsize=11)
            else:
                page.insert_text((50,55),f"Synthetic filler page {i}, no evaluated data.",fontsize=11)
        doc=out.tobytes(garbage=4,deflate=True,no_new_id=True)
    pdf.write_bytes(doc)
    return caption,cells,hashlib.sha256(doc).hexdigest()


def _oracle(spec,cells):
    scopes=[(i,r) for i,r in enumerate(spec.rows,1) if r["Task"]==TASK and r["Year"]==YEAR]
    model=[(i,r) for i,r in scopes if r["System"]==MODEL and r["Role"]=="tested"]
    prior=[(i,r) for i,r in scopes if r["Role"]=="prior"]
    if not model or not prior:
        state="missing_row"
    elif len(prior)!=1:
        state="ambiguous_baseline"
    elif not model[0][1]["BLEU"] or not prior[0][1]["BLEU"]:
        state="missing_value"
    else:
        state="matched"
    assert state==spec.signal
    row_ids=sorted(i for i,_ in model+prior)
    needed_positions={(c["row"],c["column"]) for c in cells if c["row"] in (0,*row_ids)
                      and (c["row"]==0 or c["text"])}
    expected = {}
    if state=="matched":
        av,bv=(Decimal(item[0][1]["BLEU"]) for item in (model,prior))
        expected={"model_score":str(av),"baseline_name":prior[0][1]["System"],"baseline_score":str(bv),
                  "signed_difference":str(av-bv)}
    relation_positions = needed_positions.copy()
    if state=="missing_value":
        relation_positions.update((c["row"],c["column"]) for c in cells
                                  if c["row"] in row_ids and c["field"]=="BLEU")
    elif state=="missing_row":
        # An absent competitor is a scope claim. Require the inspected
        # table rows underlying that claim, not just a few visible counts.
        relation_positions.update((c["row"],c["column"]) for c in cells if c["text"])
    return {"status":state,"required_cells":[list(c) for c in sorted(needed_positions)],
            "relation_cells":[list(c) for c in sorted(relation_positions)],"values":expected}


def generate(directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    cases=[]
    for spec in LAYOUTS:
        pdf=directory/(spec.id+".pdf")
        caption,cells,digest=_text(pdf,spec)
        cases.append({"id":spec.id,"pdf":pdf.name,"sha256":digest,"query":QUESTION,
                      "page":spec.page,"table_number":spec.number,"caption":caption,"cells":cells,
                      "expected":_oracle(spec,cells)})
    manifest={"schema":"evidence-desk-synthetic-table-fixtures-v1","cases":cases}
    requests={"schema":"evidence-desk-synthetic-table-requests-v1",
              "cases":[{key:c[key] for key in ("id","pdf","sha256","query")} for c in cases]}
    (directory/"requests.json").write_text(json.dumps(requests,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return manifest


if __name__=="__main__":
    import argparse
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("output",type=Path)
    print(f"{len(generate(cli.parse_args().output)['cases'])} fixtures")
