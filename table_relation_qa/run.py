"""Grade source locators, relationship outputs and safe abstention separately."""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

import fitz

from .fixtures import generate

MAX_ADAPTER_CITATIONS = 200


def _key(row):
    return tuple(row.get(k) for k in ("page","table","row","column","text"))


def verify_oracle(directory,case):
    path=directory/case["pdf"]
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=case["sha256"]:
        raise ValueError(f"PDF digest mismatch: {case['id']}")
    with fitz.open(stream=raw,filetype="pdf") as pdf:
        page=pdf[case["page"]-1]
        if case["caption"] not in " ".join(page.get_text().split()):
            raise ValueError(f"caption not in original page: {case['id']}")
        for cell in case["cells"]:
            original=" ".join(page.get_text(clip=fitz.Rect(cell["bbox"])).split())
            if original != cell["text"]:
                raise ValueError(f"cell oracle differs from original: {case['id']} {cell['row']} {cell['column']} {original!r}")
    return raw


def grade(directory,case,response):
    verify_oracle(directory,case)
    expected=case["expected"]
    declared=response.get("values")
    values=declared if isinstance(declared,dict) else {}
    citations=response.get("citations")
    valid_citations = isinstance(citations,list) and len(citations) <= MAX_ADAPTER_CITATIONS
    assert_identity=response.get("sha256")==case["sha256"]
    known={_key(c) for c in case["cells"]}
    locations=set()
    if valid_citations:
        for c in citations:
            if not isinstance(c,dict) or not all(type(c.get(k)) is int for k in ("page","table","row","column")) or not isinstance(c.get("text"),str) or _key(c) not in known:
                valid_citations=False
                continue
            locations.add((c["row"],c["column"]))
    required={tuple(pair) for pair in expected["required_cells"]}
    grounded=assert_identity and valid_citations and required.issubset(locations)
    audit_scope = {tuple(pair) for pair in expected["relation_cells"]}
    audited=grounded and audit_scope.issubset(locations)
    status=response.get("status")
    relation=False
    safe=False
    if expected["status"]=="matched":
        try:
            relation=(status=="matched" and set(values)==set(expected["values"]) and
                      values["baseline_name"]==expected["values"]["baseline_name"] and
                      all(Decimal(str(values[k]))==Decimal(expected["values"][k])
                          for k in ("model_score","baseline_score","signed_difference")) and audited)
        except (KeyError,InvalidOperation,TypeError,ValueError):
            relation=False
    else:
        # An abstention with no claimed result is safe, but only the expected
        # reason plus the required audit locators constitutes a relation pass.
        safe=status in {"unresolved","missing_row","ambiguous_baseline","missing_value"} and declared=={}
        relation=safe and status==expected["status"] and audited
    return {"case":case["id"],"expected":expected["status"],"reported":status,
            "valid_citations":assert_identity and valid_citations,"anchor_coverage":len(required & locations),
            "anchors_required":len(required),"anchor_pass":grounded,
            "relation_coverage":len(audit_scope & locations),"relation_anchors_required":len(audit_scope),
            "relation_pass":relation,"safe_abstention":safe}


def baseline_current(directory,case):
    """Inventory table fragments: intentionally does not infer a relationship."""
    from page_objects import inspect_page
    raw=verify_oracle(directory,case)
    with fitz.open(stream=raw,filetype="pdf") as source:
        count=len(source)
    output=[]
    for page in range(1,count+1):
        info=inspect_page(raw,page)
        owners={cap["number"]:[] for cap in info["captions"] if cap["kind"]=="table"}
        for r in info["table_rows"]:
            owners.setdefault(r["caption"],[]).append(r["fragments"])
        for number, rows in owners.items():
            if not rows or not number.isdigit():
                continue
            lefts=[h["bbox"][0] for h in rows[0]]
            for i,fragments in enumerate(rows):
                for cell in fragments:
                    matches=[j for j,x in enumerate(lefts,1) if abs(x-cell["bbox"][0])<=3]
                    if len(matches)!=1:
                        continue
                    output.append({"page":page,"table":int(number),"row":i,"column":matches[0],"text":cell["text"]})
    return {"id":case["id"],"sha256":case["sha256"],"status":"unresolved","values":{},"citations":output}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory",type=Path,help="Output/input directory for original synthetic PDFs")
    group=p.add_mutually_exclusive_group()
    group.add_argument("--generate-and-baseline",action="store_true")
    group.add_argument("--responses",type=Path,help="Read an external candidate JSON response without executing code")
    a=p.parse_args()
    if a.generate_and_baseline:
        manifest=generate(a.directory)
        rows={case["id"]:baseline_current(a.directory,case) for case in manifest["cases"]}
    elif a.responses:
        manifest=json.loads((a.directory/"manifest.json").read_text(encoding="utf-8"))
        payload=json.loads(a.responses.read_text(encoding="utf-8"))
        rows={row["id"]:row for row in payload["cases"]}
        if len(rows)!=len(payload["cases"]) or set(rows)!={c["id"] for c in manifest["cases"]}:
            p.error("responses must specify each fixture ID exactly once")
    else:
        p.error("choose --generate-and-baseline or --responses")
    results=[grade(a.directory,case,rows[case["id"]]) for case in manifest["cases"]]
    summary={k:sum(row[k] for row in results) for k in ("anchor_pass","relation_pass","safe_abstention")}
    print(json.dumps({"cases":results,"totals":summary},ensure_ascii=False,indent=2))
    if a.responses and summary["relation_pass"]!=len(results):
        raise SystemExit(1)


if __name__=="__main__":
    main()
