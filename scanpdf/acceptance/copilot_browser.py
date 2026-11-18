"""Exercise research planning/reporting in Chromium on newly generated PDFs."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time

import pymupdf
from corpus import call, workbench


def inputs():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((55, 67), "Tidal sensor calibration")
        page.insert_textbox(pymupdf.Rect(55, 108, 470, 350),
                            "A quartz transducer measured pressure in the test chamber during calibration. "
                            "The validation split contains 37 annotated abstracts.", fontsize=12)
        calibration = pdf.tobytes(no_new_id=True)
    with pymupdf.open() as pdf:
        pdf.new_page()
        blank = pdf.tobytes(no_new_id=True)
    return calibration, blank


def task(browser, port, ids, query, *, planner=False, publisher=False):
    with browser.new_page() as page:
        base = f"http://127.0.0.1:{port}"
        page.goto(base)
        page.get_by_role("link", name="科研 Copilot").click()
        page.locator("#documents input").first.wait_for()
        for box in page.locator("#documents input").all():
            box.set_checked(box.input_value() in ids)
        page.locator("#question").fill(query)
        page.locator("#planning").set_checked(planner)
        page.locator("#publisher").set_checked(publisher)
        page.evaluate("""() => {
            const originalFetch=window.fetch.bind(window);
            window.fetch=async (...args) => {
              const response=await originalFetch(...args);
              if (args[0]==='/api/copilot') window.recordedResponse=response.clone().text();
              return response;
            };
            window.draftHistory = []; new MutationObserver(() => {
            if (!document.querySelector('#preview').hidden) window.draftHistory.push({
                ms:performance.now(), text:document.querySelector('#draft').textContent})
        }).observe(document.querySelector('#draft'),{childList:true,subtree:true});}""")
        start = time.monotonic()
        with page.expect_response(lambda r: r.url.endswith("/api/copilot"), timeout=140000) as response:
            page.locator("#run").click()
        page.locator("#run:enabled").wait_for(timeout=240000)
        assert response.value.ok
        events = [json.loads(line) for line in page.evaluate("window.recordedResponse").splitlines()]
        assert events[-1]["event"] == "final", events
        assert page.locator("#preview").is_hidden() and not page.locator("#draft").inner_text()
        assert page.locator("#result").is_visible()
        assert page.locator("#state").inner_text() == "本次任务已结束。"
        links = []
        for a in page.locator('#reports a[href^="/source?"]').all():
            href = a.get_attribute("href")
            with page.expect_popup() as opened:
                a.click()
            with opened.value as source:
                source.locator("#evidence:visible").wait_for()
                assert "物理页" in source.locator("#anchor").inner_text()
                assert source.locator("#excerpt mark").inner_text().strip()
            links.append(href)
        return {"seconds":round(time.monotonic()-start,2),"events":events,"source_links_opened":links,
                "provisional_deltas":page.evaluate("window.draftHistory"),
                "steps_displayed":page.locator("#steps").inner_text()}


@contextmanager
def mode_env(mode):
    names = ("EVIDENCE_LOCAL_QA_URL","EVIDENCE_LOCAL_QA_MODEL","EVIDENCE_QA_READER")
    saved = {key:os.environ.get(key) for key in names}
    if mode == "rules":
        for key in names:
            os.environ.pop(key,None)
    elif not all(saved[key] for key in names[:2]):
        raise ValueError("Real mode needs the configured loopback model URL and alias")
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None: os.environ[key]=value
            else: os.environ.pop(key,None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", type=Path, default=Path("/usr/bin/chromium"))
    parser.add_argument("--mode", choices=("rules","real"), default="rules")
    parser.add_argument("--publisher",action="store_true",help="Send the public seaborn JOSS DOI to its publisher")
    args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    data,blank=inputs()
    with mode_env(args.mode), workbench() as port, sync_playwright() as pw, pw.chromium.launch(executable_path=str(args.chromium)) as browser:
        ids=[]
        for name, file in (("calibration.pdf",data),("unread.pdf",blank)):
            actual=call(port,"/api/import?name="+name,file,"application/pdf")["doc"]["id"]
            assert actual == hashlib.sha256(file).hexdigest()
            ids.append(actual)
        cases={}
        missing=task(browser,port,ids[1:],"What instrument measured chamber pressure?")
        cases["unindexed"]=missing
        assert [(event["tool"],event["status"]) for event in missing["events"] if event["event"]=="receipt"] == [
                ("answer_paper","gap"),("scan_coverage","ok")]
        assert any(event["event"]=="replan" for event in missing["events"])
        assert missing["events"][-1]["reports"][1]["scan"]==[{"id":ids[1],"page":1}]
        graph=task(browser,port,ids,"Show the citation graph of the selected papers.", planner=True)
        cases["graph"]=graph
        plan=next(event for event in graph["events"] if event["event"]=="plan")
        if args.mode=="real":
            assert plan["planner"]=="model_tool_calls" and "citation_graph" in plan["proposed"],plan
            reply=task(browser,port,ids[:1],"Which instrument measured chamber pressure during calibration?")
            cases["streamed_answer"]=reply
            assert any(e["event"]=="token" for e in reply["events"])
            assert len(reply["provisional_deltas"])>=2
            group=reply["events"][-1]["reports"][0]
            assert group["generation"]=="cited_draft" and "quartz transducer" in group["answer"].lower()
            assert group["sources"][0]["page"]==1 and group["sources"][0]["id"]==ids[0] and reply["source_links_opened"]
        else:
            assert plan["planner"]=="disabled"
        if args.publisher:
            publication=task(browser,port,ids[:1],"请查询 JOSS 出版题录 10.21105/joss.03021。",planner=args.mode=="real",publisher=True)
            cases["publisher"]=publication
            if args.mode=="real":
                plan=next(event for event in publication["events"] if event["event"]=="plan")
                assert plan["planner"]=="model_tool_calls" and "publication_metadata" in plan["proposed"],plan
            p=publication["events"][-1]["publications"]
            assert len(p)==1 and "seaborn" in p[0]["title"].lower() and p[0]["doi"]=="10.21105/joss.03021"
        print(json.dumps({"mode":args.mode,"pdfs":ids,"cases":cases},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
