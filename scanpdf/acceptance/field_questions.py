"""Verify independently cited fields and a missing attribute using a fresh acoustic study."""
import argparse
import json
from pathlib import Path
import pymupdf
from corpus import call, workbench


def document():
    pages=[
        ("Acoustic sampling", "The sensor sampling frequency during heating was 92 Hz. "
         "The sensor sampling frequency during cooling was 67 Hz."),
        ("Validation", "The validation split contains 31 annotated spectra. "
         "The test split contains 85 annotated spectra."),
    ]
    with pymupdf.open() as pdf:
        for title, text in pages:
            page=pdf.new_page()
            page.insert_text((65,75),title,fontsize=16)
            page.insert_textbox(pymupdf.Rect(65,120,500,490),text,fontsize=12)
        return pdf.tobytes(no_new_id=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium',type=Path,default=Path('/usr/bin/chromium'))
    args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    cases=[
        ("What was the sensor sampling frequency during heating, and how many annotated spectra did the validation split contain?", "complete"),
        ("What was the sensor sampling frequency?", "ambiguous"),
        ("What was the sensor sampling frequency during cooling, and what was the instrument serial number?", "partial"),
    ]
    from hashlib import sha256
    data=document()
    with workbench() as port, sync_playwright() as pw, pw.chromium.launch(executable_path=str(args.chromium)) as browser:
        docid=call(port,"/api/import?name=acoustic-study.pdf",data,"application/pdf")["doc"]["id"]
        assert docid==sha256(data).hexdigest()
        results=[]
        for query,mode in cases:
            with browser.new_page() as page:
                page.goto(f"http://127.0.0.1:{port}/followup?id={docid}")
                page.locator("#question").fill(query)
                with page.expect_response(lambda r:r.url.endswith("/api/followup")) as response:
                    page.locator("#send").click()
                result=response.value.json()
                assert response.value.ok,result
                ui=page.locator('#history section')
                ui.wait_for()
                if mode=="ambiguous":
                    assert not result['answer'] and '条件' in result['gap'] and '条件' in ui.inner_text(),result
                else:
                    assert result["completeness"]==mode and len(result["fields"])==2,result
                    first,second=result['fields']
                    assert first['answer']==('92 Hz' if mode=='complete' else '67 Hz'),result
                    assert second['answer']==('31' if mode=='complete' else None),result
                    if mode=='partial': assert '序列号' in second['gap']
                    checked=[]
                    for field in result['fields']:
                        assert field['question'] in ui.inner_text()
                        for claim in field['claims']:
                            hit=result['hits'][claim['hit_index']]
                            assert hit['text'][claim['answer_start']:claim['answer_end']]==claim['span']
                            assert hit['id']==docid and ('92 Hz' in claim['span'] or '67 Hz' in claim['span'] or '31' in claim['span'])
                            checked.append(claim['page'])
                    assert checked==([1,2] if mode=='complete' else [1])
                results.append({'question':query,'status':mode,'result':result})
    print(json.dumps({'document_sha256':docid,'transport':'Chromium','cases':results},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
