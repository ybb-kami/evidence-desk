"""Verify an acquired public snapshot and the separate REST status in Chromium."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from app import Library
from ..preload.verify import ROOT, _service
from . import snapshot


REPOSITORY = "google-research/bert"
FILES = (("license","LICENSE"), ("readme","README.md"), ("environment","requirements.txt"),
         ("training","run_pretraining.py"), ("evaluation","run_squad.py"))


def capture(data, identity):
    command = [sys.executable, "-m", "scanpdf.reproducibility.sync", "--data", str(data), "--document", identity,
               "--repository", REPOSITORY, "--confirm"]
    for category, path in FILES:
        command.extend(("--file", category, path))
    response = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=40)
    evidence = json.loads(response.stdout)
    assert response.returncode == 0, (evidence,response.stderr)
    return evidence


def verify_files(evidence):
    data = evidence["snapshot"]
    fixed_paths = {file["path"]:file for group in data["groups"].values() for file in group["files"]}
    assert set(fixed_paths) == {path for _, path in FILES}
    deadline = monotonic() + snapshot.TOTAL_SECONDS
    for path, file in fixed_paths.items():
        assert file["raw_url"] == f'https://raw.githubusercontent.com/{REPOSITORY}/{data["commit"]}/{path}'
        content, _ = snapshot._read(file["raw_url"], snapshot.RAW_TYPES, deadline)
        assert len(content) == file["bytes"] and sha256(content).hexdigest() == file["sha256"]
    canonical = json.dumps({key:value for key,value in data.items() if key != "record_sha256"},
                           ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
    assert sha256(canonical).hexdigest() == data["record_sha256"]
    return len(fixed_paths)


def click_audit(page):
    assert not page.locator("#consent").is_checked() and page.locator("#audit-button").is_disabled()
    page.check("#consent")
    with page.expect_response(lambda reply: reply.url.endswith("/api/reproducibility/audit"), timeout=35000) as received:
        page.locator("#audit-button").click()
    reply = received.value
    assert reply.ok, reply.text()
    page.locator("#result").wait_for(state="visible")
    assert not page.locator("#consent").is_checked()
    return reply.json()


def browser_check(port, identity, evidence, output, chromium, expect_forbidden):
    base = f"http://127.0.0.1:{port}"
    payload = evidence["snapshot"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium,args=["--no-sandbox","--disable-background-networking"])
        page = browser.new_page(viewport={"width":1080,"height":950})
        audits, external, errors = [], [], []

        def local_only(route):
            url = route.request.url
            if url.startswith(("http://", "https://")) and not url.startswith(base + "/"):
                external.append(url)
                route.abort()
            else:
                if url.endswith("/api/reproducibility/audit"):
                    audits.append(route.request.post_data_json)
                route.continue_()

        page.route("**/*",local_only)
        page.on("pageerror",lambda error: errors.append(str(error)))
        page.goto(base + "/")
        page.get_by_role("link",name="核对论文代码").click()
        page.select_option("#paper", identity)
        page.select_option("#reference", evidence["reference_id"])
        panel = page.locator("#snapshot-result")
        panel.get_by_text("获取时快照 · " + REPOSITORY,exact=True).wait_for()
        assert payload["commit"] in panel.text_content()
        assert payload["checked_at"] in panel.text_content()
        links = panel.locator(".file-group a").evaluate_all("links => links.map(link => link.href)")
        assert len(links) == 10 and all(payload["commit"] in link for link in links)
        for group in payload["groups"].values():
            file, = group["files"]
            assert file["sha256"] in panel.text_content()
        page.screenshot(path=output / "acquired-snapshot.png",full_page=True)

        source_link = page.locator("#source").get_attribute("href")
        source_query = urlsplit(source_link).query
        resolved = page.request.get(base + "/api/source?" + source_query)
        assert resolved.ok, resolved.text()
        source = resolved.json()
        anchors = parse_qs(source_query)
        snippet = source["text"][int(anchors["start"][0]):int(anchors["end"][0])]
        assert REPOSITORY in snippet and source["page"] == 2 and source["selection"]["boxes"], source
        assert not audits

        attempt = click_audit(page)
        if expect_forbidden:
            assert attempt["audit"]["reason"] == "forbidden", attempt
            assert "403" in page.locator("#result").text_content()
        assert panel.is_visible() and payload["commit"] in panel.text_content()
        page.screenshot(path=output / "rest-status-and-snapshot.png",full_page=True)
        repeated = click_audit(page)
        assert repeated["cached"] and repeated["audit"] == attempt["audit"]
        assert len(audits) == 2 and all(request == {"id":identity, "reference":evidence["reference_id"], "consent":True}
                                        for request in audits)
        assert not external and not errors, (external,errors)
        browser.close()
    return {"proof_page":source["page"], "proof_block":source["block"], "exact_reference":snippet,
            "physical_page_url":source["pdf_url"], "selection_boxes":source["selection"]["boxes"],
            "browser_external_requests":len(external), "live":attempt["audit"], "second_live_read_cached":True}


def run(pdf, output, chromium, expect_forbidden):
    output.mkdir(parents=True,exist_ok=False)
    with TemporaryDirectory(prefix="evidence-repository-snapshot-") as folder:
        data = Path(folder)
        identity = Library(data).add(pdf.read_bytes(),pdf.name)["doc"]["id"]
        evidence = capture(data,identity)
        verified = verify_files(evidence)
        # Serving from a distinct process verifies shared SQLite persistence.
        with _service(data) as (port, _):
            browser = browser_check(port,identity,evidence,output,chromium,expect_forbidden)
    result = evidence | {"raw_files_readback_verified":verified, "browser":browser}
    (output / "snapshot-verified.json").write_text(json.dumps(result,ensure_ascii=False,indent=2) + "\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--chromium",default="/usr/bin/chromium")
    parser.add_argument("--confirm-public",action="store_true",help="Allow the BERT public Git, raw file and REST reads")
    parser.add_argument("--expect-live-forbidden",action="store_true",help="Check the CONNECT 403 observed in this environment")
    args = parser.parse_args()
    if not args.confirm_public:
        parser.error("This acceptance replays public GitHub requests. Pass --confirm-public to confirm.")
    observed = run(args.pdf_dir / "N19-1423.pdf",args.output,args.chromium,args.expect_live_forbidden)
    print(json.dumps({"repository":REPOSITORY,"commit":observed["snapshot"]["commit"],
                      "raw_files_readback_verified":observed["raw_files_readback_verified"],"browser":observed["browser"]},
                     ensure_ascii=False,indent=2))
