"use strict";
// Offsets from PyMuPDF count Unicode code points. DOM ranges count UTF-16 units.
function preservedRanges(text, pdfRanges) {
  const offsets = [0];
  for (const char of text) offsets.push(offsets.at(-1) + char.length);
  const ranges = pdfRanges.map(([a,b]) => [offsets[a], offsets[b]]);
  const syntax = /```[^]*?(?:```|$)|`[^`\n]+`|\$\$[^]*?\$\$|\$[^$\n]+\$|\\\([^]*?\\\)|\\\[[^]*?\\\]|^\s*(?:def |class |import |from \S+ import |function |const |let |return |#include|for\s*\(|if\s*\().*$/gm;
  const functionAssignment = /^\s*[A-Za-z][\w]*\([^\n()]*\)\s*=\s*[^\n]+$/gm;
  const equations = /(?:[\p{L}\d_]+(?:\([^()\n]*\))?\s*[=≈≤≥≠]\s*[\p{L}\d_().^{}]+(?:\s*[-+*/×÷]\s*[\p{L}\d_().^{}]+)*)/gu;
  for (const pattern of [syntax,functionAssignment,equations]) {
    for (const match of text.matchAll(pattern)) ranges.push([match.index, match.index + match[0].length]);
  }
  ranges.sort((a,b) => a[0] - b[0]);
  const merged = [];
  for (const [start,end] of ranges) {
    if (merged.length && start <= merged.at(-1)[1]) merged.at(-1)[1] = Math.max(end,merged.at(-1)[1]);
    else merged.push([start,end]);
  }
  return merged;
}

function parts(segment, start, end) {
  let pos = start;
  const out = [];
  function prose(stop) {
    if (pos < stop) {
      const proseText = segment.text.slice(pos,stop);
      const forBoundaries = proseText.replace(/[\r\n]/g," ");
      for (const sentence of new Intl.Segmenter(undefined,{granularity:"sentence"}).segment(forBoundaries)) {
        let remaining = proseText.slice(sentence.index,sentence.index + sentence.segment.length);
        while (remaining) {
          let length = remaining.length;
          if (length > 220) {
            const prefix = Array.from(remaining).slice(0,210).join("");
            const boundary = Math.max(prefix.lastIndexOf(" "),prefix.lastIndexOf("\n"),prefix.lastIndexOf("，"));
            length = boundary >= 100 ? boundary + 1 : prefix.length;
          }
          const value = remaining.slice(0,length);
          out.push({text:value, translate:/[\p{L}]/u.test(value)});
          remaining = remaining.slice(length);
        }
      }
    }
  }
  for (const [left,right] of preservedRanges(segment.text,segment.protected)) {
    if (right <= start || left >= end) continue;
    const begin = Math.max(start,left);
    prose(begin);
    pos = Math.min(end,right);
    out.push({text:segment.text.slice(begin,pos),translate:false});
  }
  prose(end);
  return out;
}

if (typeof module !== "undefined") module.exports = {parts, preservedRanges};
if (typeof document !== "undefined") startReader();

async function startReader() {
  const $ = id => document.getElementById(id);
  const docs = $("doc"), direction = $("direction"), rows = $("rows"), status = $("status");
  const saved = new Map(), engines = new Map(), keep = new Set();
  let localDirections = [], current, epoch = 0, generation = 0, running = false, selected = null;
  const key = (page, block) => [docs.value,direction.value,page,block].join("/");
  async function json(url, options) {
    const response = await fetch(url,options);
    const result = await response.json();
    if (!response.ok) throw Error(result.error || "本地服务请求失败。");
    return result;
  }
  async function getEngine() {
    const pair = direction.value;
    if (localDirections.includes(pair)) return async text => (await json("/api/bilingual/translate", {
      method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({direction:pair,text})
    })).text;
    if (!engines.has(pair)) {
      if (!globalThis.Translator) throw Error("请安装本机翻译模型，或使用支持 Translator API 及对应语言包的 Chrome 浏览器。");
      const [sourceLanguage,targetLanguage] = pair.split("-");
      const options = {sourceLanguage,targetLanguage};
      if (await Translator.availability(options) === "unavailable") throw Error("浏览器不支持此语言对，请安装本机翻译模型。");
      status.textContent = "正在准备浏览器本地语言包…";
      engines.set(pair,await Translator.create(options));
    }
    return text => engines.get(pair).translate(text);
  }
  function setRunning(value) {
    running = value;
    for (const id of ["doc","direction","page","go","pageRun","all"]) $(id).disabled = value;
    for (const button of rows.querySelectorAll("button")) button.disabled = value;
    $("cancel").disabled = !value;
  }
  async function load(page) {
    const token = ++epoch;
    current = null; selected = null; rows.replaceChildren(); $("original").removeAttribute("href"); $("count").textContent = "";
    if (!docs.value) return;
    const data = await json("/api/bilingual/page/" + docs.value + "?page=" + encodeURIComponent(page));
    if (token !== epoch) return;
    current = data;
    $("original").href = "/pdf/" + docs.value + "#page=" + data.page;
    $("page").value = data.page; $("page").max = data.pages;
    $("count").textContent = "共 " + data.pages + " 页"; status.textContent = data.segments.length ? "" : "此页没有可划选文字。可先在扫描工作台识别本页，再返回阅读。";
    for (const segment of data.segments) {
      const card = document.createElement("article"), head = document.createElement("header");
      const title = document.createElement("span"), button = document.createElement("button");
      title.textContent = (data.source === "ocr" ? "OCR · " : "") + "第 " + data.page + " 页 · 块 " + segment.block;
      button.textContent = "翻译此段或选区"; button.disabled = running;
      const source = document.createElement("p"), target = document.createElement("p");
      source.className = "source"; target.className = "translated"; source.textContent = segment.text;
      target.textContent = saved.get(key(data.page,segment.block)) || "待翻译";
      function remember() {
        const selection = window.getSelection();
        if (!selection.rangeCount || selection.isCollapsed) { selected = null; return; }
        const range = selection.getRangeAt(0);
        if (range.startContainer === source.firstChild && range.endContainer === source.firstChild)
          selected = {segment,start:range.startOffset,end:range.endOffset};
        else selected = null;
      }
      source.onmouseup = remember; source.onkeyup = remember;
      button.onmousedown = e => e.preventDefault();
      button.onclick = () => run("one",segment,selected?.segment === segment ? selected : null);
      const keepLabel = document.createElement("label"), checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      const keepKey = [docs.value,data.page,segment.block].join("/");
      checkbox.checked = keep.has(keepKey);
      checkbox.onchange = () => checkbox.checked ? keep.add(keepKey) : keep.delete(keepKey);
      keepLabel.append(checkbox," 公式或代码整段保留");
      const proof = document.createElement("details"), label = document.createElement("summary");
      label.textContent = "核对原 PDF 段落截图";
      proof.append(label);
      proof.ontoggle = () => {
        if (!proof.open || proof.childElementCount > 1) return;
        const img = document.createElement("img");
        img.alt = title.textContent + "的 PDF 原图";
        img.src = "/api/bilingual/crop/" + docs.value + "?page=" + data.page + "&block=" + segment.block;
        proof.append(img);
      };
      head.append(title,button,keepLabel); card.append(head,source,target,proof); rows.append(card);
    }
  }
  async function run(mode, chosen, selection) {
    if (running || !current) return;
    setRunning(true);
    const token = ++generation; let completed = 0;
    try {
      const translate = await getEngine();
      while (token === generation) {
        const page = current.page;
        for (const [index,segment] of current.segments.entries()) {
          if (chosen && chosen !== segment) continue;
          const start = selection?.start ?? 0, end = selection?.end ?? segment.text.length;
          status.textContent = "正在翻译第 " + page + " 页 · 块 " + segment.block;
          let result = "";
          const keepKey = [docs.value,page,segment.block].join("/");
          const fragments = keep.has(keepKey) ? [{text:segment.text.slice(start,end),translate:false}] : parts(segment,start,end);
          for (const part of fragments) {
            if (token !== generation) return;
            if (part.translate) {
              const lead = part.text.match(/^\s*/)[0], tail = part.text.match(/\s*$/)[0];
              result += lead + await translate(part.text.trim().replace(/\s*\n\s*/g, " ").replace(/[\uFB00-\uFB06]/g, c => c.normalize("NFKC"))) + tail;
            } else result += part.text;
          }
          if (token !== generation) return;
          const content = selection ? "选区「" + segment.text.slice(start,end) + "」\n" + result : result;
          saved.set(key(page,segment.block),content);
          rows.children[index].querySelector(".translated").textContent = content;
          completed++;
        }
        if (mode !== "all" || page === current.pages) break;
        await load(page + 1);
        if (!running || !current) return;

      }
      if (token === generation) status.textContent = "本次已翻译 " + completed + " 段。";
    } catch (e) { if (token === generation) status.textContent = e.message; }
    finally { setRunning(false); }
  }
  function show(page) {load(page).catch(e => {status.textContent=e.message;});}
  docs.onchange = () => show(1); direction.onchange = () => show(current?.page || 1);
  $("go").onclick = () => show($("page").value); $("pageRun").onclick = () => run("page");
  $("all").onclick = () => run("all"); $("cancel").onclick = () => {generation++; status.textContent = "已停止。";};
  try {
    const [library,models] = await Promise.all([json("/api/library"),json("/api/bilingual/models")]);
    localDirections = models.directions;
    for (const doc of library.docs) docs.add(new Option(doc.name,doc.id));
    const requested = new URLSearchParams(location.search);
    if (library.docs.some(doc => doc.id === requested.get("doc"))) docs.value=requested.get("doc");
    if (docs.value) await load(requested.get("page") || 1);
    else status.textContent = "请先在文库导入 PDF。";
  } catch (e) {status.textContent=e.message;}
}
