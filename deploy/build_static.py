"""Generate a fixed, upload-free public demo. No server, network or personal library."""
import argparse, hashlib, html, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine import parse_pdf, find_evidence
from graph import build_graph

def build(out):
    out.mkdir(parents=True,exist_ok=False)
    docs=[]
    for name in ("A-lantern.pdf","B-cloud.pdf"):
        raw=(ROOT/"demo"/name).read_bytes()
        docs.append(parse_pdf(raw,name)); (out/name).write_bytes(raw)
    graph=build_graph(docs)
    if len(graph["edges"])!=1: raise ValueError("Unexpected fixture graph")
    edge=graph["edges"][0]
    esc=lambda v:html.escape(str(v),quote=True)
    parts=[]
    for doc in docs:
        hits=find_evidence([doc],"amber lantern")
        part=next((x for x in hits if "amber lantern" in x["text"].lower()),None)
        parts.append('<article><h3>'+esc(doc["name"])+'</h3>'+('<blockquote>'+esc(part["text"])+'</blockquote><a href="'+esc(doc["name"])+'#page='+str(part["page"])+'">核对 PDF 第 '+str(part["page"])+' 页</a>' if part else '<p>未找到该词的证据。不能把另一篇的结论归给本篇。</p>')+'</article>')
    page='''<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>证据台｜只读公开演示</title><style>body{background:#f7f6ee;color:#182c27;font:17px/1.65 system-ui;margin:3em auto;padding:0 1em;max-width:52em}article{background:white;padding:1em 1.4em;margin:1em 0;border-left:.35em solid #267061}small{color:#34564e}a{color:#145e68}blockquote{margin:0 0 .5em}</style><main><small>Evidence Desk / 只读预计算演示</small><h1>一句话，哪篇有证据？</h1><p>下列输出由真实本地检索与图谱代码生成。两篇 PDF 是原创虚构样例，不是真实研究或模型实时作答。此页不接受上传、查询或模型请求。</p><h2>跨文对照：amber lantern</h2>'''+''.join(parts)+'<h2>引用链：B → A</h2><blockquote>'+esc(edge["text"])+'</blockquote><a href="'+esc(edge["source_document_name"])+'#page='+str(edge["page"])+'">查看 B 的参考文献原页</a></main></html>'
    (out/'index.html').write_text(page,encoding='utf8')
if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("new_directory",type=Path)
    build(parser.parse_args().new_directory)
