"use strict";

function sourceSignature(segment, origin) {
  return JSON.stringify([origin || "pdf",segment.text,segment.protected,segment.bbox,Boolean(segment.reference)]);
}

// Keep distinct translated selections, replacing only repeated requests for the same range.
class TranslationBook {
  constructor() { this.rows = new Map(); }

  save(record) {
    const key = JSON.stringify([record.doc,record.direction,record.page,record.block,
                                record.selected,record.start,record.end]);
    this.rows.delete(key);
    this.rows.set(key,record);
  }

  list(doc, direction) {
    return Array.from(this.rows.values()).filter(row => row.doc === doc && row.direction === direction);
  }

  remove(predicate) {
    let count = 0;
    for (const [key,row] of this.rows) {
      if (predicate(row)) { this.rows.delete(key); count++; }
    }
    return count;
  }

  reconcile(doc, data) {
    const signatures = new Map(data.segments.map(segment =>
      [segment.block,sourceSignature(segment,data.source)]));
    return this.remove(row => row.doc === doc && row.page === data.page &&
      row.signature !== signatures.get(row.block));
  }
}

function htmlText(value) {
  return String(value).replace(/[&<>"']/g,ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
}

function bilingualHtml(name, doc, pair, items) {
  const [from,to] = pair.split("-");
  const language = {en:"英文",zh:"中文"};
  const ordered = [...items].sort((a,b) => a.page-b.page || a.order-b.order || a.start-b.start || b.end-a.end);
  const pages = [...new Set(ordered.map(r=>r.page))];
  const navigation = pages.map(page=>`<a href="#page-${page}">第 ${page} 页</a>`).join(" ");
  let previous = 0;
  const body = ordered.map(row => {
    const anchor = row.page === previous ? "" : `id="page-${row.page}"`;
    previous = row.page;
    const scope = row.selected ? `选区（段内字符 ${row.startChar + 1}–${row.endChar}）` : "整段";
    return `<article ${anchor} data-treatment="${["reference","table","preserved"].includes(row.treatment) ? row.treatment : "machine"}"><h2>物理第 ${row.page} 页 · 块 ${row.block} · ${scope}${row.ocr?" · OCR":""}</h2>
      <div class="pair"><section><h3>原文 · ${language[from]}</h3><p lang="${from}">${htmlText(row.original)}</p></section>
      <section><h3>${row.treatment === "reference" ? "参考文献 · 原样保留" : row.treatment === "table" ? "数据表 · 原样保留" : row.treatment === "preserved" ? "原样保留" : "译文 · "+language[to]}</h3><p lang="${to}">${htmlText(row.translation)}</p></section></div></article>`;
  }).join("\n");
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>${htmlText(name)} · 双语对照</title><style>
*{box-sizing:border-box}body{margin:0;color:#192b2a;background:#f5f7f5;font:16px/1.65 system-ui,sans-serif}main{max-width:72rem;margin:auto;padding:1.5rem}
h1{font-size:2rem;overflow-wrap:anywhere}h2{font-size:1.05rem}h3{font-size:.85rem;color:#375751}nav{display:flex;flex-wrap:wrap;gap:.7rem}a{color:#075e53}
article{background:white;padding:.8rem 1.2rem;margin:1rem 0;border:1px solid #bccdc6}.pair{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
section{min-width:0}section p{margin-top:0;white-space:pre-wrap;overflow-wrap:anywhere}.digest{overflow-wrap:anywhere;font-size:.8rem}
@media(max-width:620px){.pair{grid-template-columns:1fr;gap:0}main{padding:.7rem}}@media print{nav{display:none}body{background:white}article{break-inside:avoid}}
</style></head><body><main><h1>${htmlText(name)}</h1><p>${language[from]} → ${language[to]} · 已处理 ${items.length} 条记录，涉及 ${pages.length} 个物理页。专业术语请与原文核对。</p>
<p class="digest">源 PDF SHA-256：${htmlText(doc)}</p><nav aria-label="按物理页定位">${navigation}</nav>${body}</main></body></html>`;
}

if (typeof module !== "undefined") module.exports = {TranslationBook,sourceSignature,bilingualHtml};
