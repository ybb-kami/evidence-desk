"use strict";
// Offsets from PyMuPDF count Unicode code points. DOM ranges count UTF-16 units.
function preservedRanges(text, pdfRanges, terms = []) {
  const offsets = [0];
  for (const char of text) offsets.push(offsets.at(-1) + char.length);
  const ranges = pdfRanges.map(([a,b]) => [offsets[a], offsets[b]]);
  const syntax = /```[^]*?(?:```|$)|`[^`\n]+`|\$\$[^]*?\$\$|\$[^$\n]+\$|\\\([^]*?\\\)|\\\[[^]*?\\\]|^\s*(?:def |class |import |from \S+ import |function |const |let |return |#include|for\s*\(|if\s*\().*$/gm;
  const specialTokens = /<(?:pad|unk|bos|eos|s|\/s)>/gi;
  const functionAssignment = /^\s*[A-Za-z][\w]*\([^\n()]*\)\s*=\s*[^\n]+$/gm;
  const equations = /(?:[\p{L}\d_]+(?:\([^()\n]*\))?\s*[=≈≤≥≠]\s*[\p{L}\d_().^{}]+(?:\s*[-+*/×÷]\s*[\p{L}\d_().^{}]+)*)/gu;
  for (const pattern of [syntax,functionAssignment,equations,specialTokens]) {
    for (const match of text.matchAll(pattern)) ranges.push([match.index, match.index + match[0].length]);
  }
  for (const term of terms) {
    if (!term) continue;
    for (let i = text.indexOf(term); i >= 0; i = text.indexOf(term,i+term.length))
      ranges.push([i,i+term.length]);
  }
  ranges.sort((a,b) => a[0] - b[0]);
  const merged = [];
  for (const [start,end] of ranges) {
    if (merged.length && start <= merged.at(-1)[1]) merged.at(-1)[1] = Math.max(end,merged.at(-1)[1]);
    else merged.push([start,end]);
  }
  return merged;
}

function denseNumericTable(text) {
  const lines = text.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  const numeric = lines.filter(line => /^[+−-]?(?=[\d.])[\d.,+/%×·−–—eE^\s-]+[KMGT]?$/u.test(line));
  return numeric.length >= 3 && numeric.length*2 >= lines.length;
}

const englishTerms = [
  [/\bconvolutional\s+neural\s+networks?\b/giu, "卷积神经网络", /卷积神经网络|转录神经网络/gu],
  [/\bscaled\s+dot(?:\s*[-‐‑–]\s*|\s+)product\s+attention\b/giu, "缩放点积注意力", /缩放点积注意力|规模化点-生产注意/gu],
  [/\bmulti(?:[-‐‑–]|\s+)?head\s+attention\b/giu, "多头注意力", /多头注意力?/gu],
  [/\blabel\s+smoothing\b/giu, "标签平滑", /标签平滑|标牌\s*(?:的\s*)?平滑/gu],
];
const chineseTerms = [
  [/卷积神经网络/gu, "convolutional neural network", /\b(?:convolutional|volatile) neural networks?\b/giu],
  [/缩放点积注意力/gu, "scaled dot-product attention", /\bscaled dot[- ]product attention\b/giu],
  [/多头注意力/gu, "multi-head attention", /\b(?:multi[- ]?head(?:ed)?|multiple) attention\b/giu],
  [/标签平滑/gu, "label smoothing", /\blabel smoothing\b/giu],
];

const MAX_NAMES = 24;
const name = String.raw`[A-Za-z][A-Za-z0-9-]{0,31}(?:\.[0-9]{1,4})?(?:[ \t]+[A-Za-z][A-Za-z0-9-]{0,23}){0,2}`;
const endOfName = String.raw`(?=[ \t]*[,.;:!?，。；：、！？”’"）)]|\s*$|\s*\()`;
const namedIntroductions = [
  new RegExp(String.raw`\b(?:architecture|framework|network|model|encoder|decoder)\b(?:\s*,\s*the\s+|\s*,?\s*(?:(?:is|was)\s+)?(?:called|named|dubbed)\s+(?:the\s+)?)[“"]?(${name})${endOfName}`, "giu"),
  new RegExp(String.raw`(?:架构|框架|网络|模型|编码器|解码器)[，,、\s]*(?:称为|命名为|简称为|名为)[“"]?\s*(${name})${endOfName}`, "gu"),
  new RegExp(String.raw`(?:称为|命名为|简称为|名为)[“"]?\s*(${name})[”"]?\s*的\s*(?:网络架构|架构|框架|网络|模型|编码器|解码器)`, "gu"),
];
const neuralContext = /\b(?:attention|encoder|decoder|neural|pre[- ]?train\w*|language model\w*)\b|注意力|编码器|解码器|神经网络|预训练/u;
const electricalContext = /\b(?:electric(?:al)?|voltage|winding|insulation|insulates?|transformer oil|power grid)\b|电压|线圈|绕组|绝缘/iu;

function architectureNames(segments) {
  const names = new Set();
  for (const {text,reference} of segments) {
    if (reference || denseNumericTable(text)) continue;
    const line = text.replace(/\s+/g," ");
    if (/\bTransformers?\b/u.test(line) && neuralContext.test(line.toLowerCase()) && !electricalContext.test(line))
      names.add("Transformer");
    for (const pattern of namedIntroductions) {
      for (const [,candidate] of line.matchAll(pattern)) {
        if (/^[A-Z][A-Za-z0-9-]*(?:\.[0-9]+)?(?: [A-Z][A-Za-z0-9-]*){0,2}$/u.test(candidate) &&
            !/^(?:Architecture|Framework|Model|Network|Neural Network|Encoder|Decoder|The|Our)$/u.test(candidate))
          names.add(candidate);
        if (names.size >= MAX_NAMES) return [...names];
      }
    }
    if (names.size >= MAX_NAMES) break;
  }
  return [...names];
}

function namePattern(name) {
  return "\\b" + name.replace(/[.*+?^${}()|[\]\\]/g,"\\$&").replace(/ /g,"\\s+") + "\\b";
}

function academicMatches(text, direction, names = []) {
  const terms = direction === "en-zh" ? [...englishTerms] : direction === "zh-en" ? [...chineseTerms] : [];
  for (const name of names) {
    if (name === "Transformer") {
      if (!electricalContext.test(text)) {
        terms.push([/\bTransformer\b/gu, name, /\bTransformer\b|变形器|变压器|转换器|转化器/gu]);
        terms.push([/\bTransformers\b/gu, "Transformers", /\bTransformers\b|变形器|变压器|转换器|转化器/gu]);
      }
    } else {
      const pattern = new RegExp(namePattern(name),"gu");
      terms.push([pattern,name,pattern]);
    }
  }
  const hits = terms.flatMap(([pattern, target, variants]) =>
    Array.from(text.matchAll(pattern), match => ({
      start:match.index, end:match.index + match[0].length, target, variants,
    }))).sort((a,b) => a.start - b.start || b.end - a.end);
  const result = [];
  for (const hit of hits) if (!result.length || hit.start >= result.at(-1).end) result.push(hit);
  return result;
}

async function translateWithTerms(text, direction, translate, names) {
  const matches = academicMatches(text, direction, names);
  if (!matches.length) return translate(text);
  if (matches[0].start === 0 && matches[0].end === text.length) return matches[0].target;
  const raw = await translate(text);
  const counts = new Map();
  for (const {target,variants} of matches) {
    const count = counts.get(target)?.count || 0;
    counts.set(target, {variants, count:count + 1});
  }
  // Keep the model's existing phrasing when all the recognized terms can be corrected in place.
  const seen = new Set();
  if ([...counts.values()].every(({variants,count}) => {
    const found = Array.from(raw.matchAll(variants));
    if (found.length !== count || found.some(match => seen.has(match.index))) return false;
    for (const match of found) seen.add(match.index);
    return true;
  })) {
    let result = raw;
    for (const [target,{variants}] of counts) result = result.replace(variants,variant => {
      let word = target;
      if (target === "convolutional neural network" && /networks$/i.test(variant)) word += "s";
      return /^[a-z]/.test(word) && /^[A-Z]/.test(variant) ? word[0].toUpperCase() + word.slice(1) : word;
    });
    return result;
  }
  const markers = /\[TERM[1-9]\d*\]/g;
  if (!text.match(markers)) {
    const replacements = new Map(matches.map(({target},i) => ["[TERM" + (i + 1) + "]",target]));
    let cursor = 0, mask = "";
    const keys = [...replacements.keys()];
    matches.forEach(({start,end},i) => {
      mask += text.slice(cursor,start) + keys[i];
      cursor = end;
    });
    const drafted = await translate(mask + text.slice(cursor));
    const found = drafted.match(markers) || [];
    if (found.length === replacements.size && new Set(found).size === found.length &&
        found.every(marker => replacements.has(marker))) {
      return drafted.replace(markers, marker => replacements.get(marker));
    }
  }
  // A model may change or omit a marker. Translate the surrounding prose separately on failure.
  async function surrounding(value) {
    if (!/\p{L}/u.test(value)) return value;
    const lead = value.match(/^\s*/)[0], tail = value.match(/\s*$/)[0];
    return lead + await translate(value.trim()) + tail;
  }
  let result = "", position = 0;
  for (const {start,end,target} of matches) {
    result += await surrounding(text.slice(position,start)) + target;
    position = end;
  }
  return result + await surrounding(text.slice(position));
}

function parts(segment, start, end, terms = [], direction, names) {
  let pos = start;
  const out = [];
  function prose(stop) {
    if (pos < stop) {
      const proseText = segment.text.slice(pos,stop);
      let forBoundaries = proseText.replace(/[\r\n]/g," ");
      const firstBreak = proseText.indexOf("\n");
      if (pos === 0 && firstBreak > 0 && /^\n\s*[A-Z]/.test(proseText.slice(firstBreak))) {
        const title = proseText.slice(0,firstBreak).trim();
        const [match] = academicMatches(title,direction,names);
        if (match?.start === 0 && match.end === title.length) {
          forBoundaries = forBoundaries.slice(0,firstBreak) + "\n" + forBoundaries.slice(firstBreak + 1);
        }
      }
      for (const sentence of new Intl.Segmenter(undefined,{granularity:"sentence"}).segment(forBoundaries)) {
        const value = proseText.slice(sentence.index,sentence.index + sentence.segment.length);
        const matches = value.length > 220 ? academicMatches(value,direction,names) : [];
        let remaining = value, position = 0;
        while (remaining) {
          let length = remaining.length;
          if (length > 220) {
            const prefix = Array.from(remaining).slice(0,210).join("");
            const boundary = Math.max(prefix.lastIndexOf(" "),prefix.lastIndexOf("\n"),prefix.lastIndexOf("，"));
            length = boundary >= 100 ? boundary + 1 : prefix.length;
            const cut = position + length;
            const crossing = matches.find(({start,end}) => start < cut && cut < end);
            if (crossing && crossing.start > position) length = crossing.start - position;
          }
          const chunk = remaining.slice(0,length);
          out.push({text:chunk, translate:/[\p{L}]/u.test(chunk)});
          remaining = remaining.slice(length);
          position += length;
        }
      }
    }
  }
  for (const [left,right] of preservedRanges(segment.text,segment.protected,terms)) {
    if (right <= start || left >= end) continue;
    const begin = Math.max(start,left);
    prose(begin);
    pos = Math.min(end,right);
    out.push({text:segment.text.slice(begin,pos),translate:false});
  }
  prose(end);
  return out;
}

function parseGlossary(text) {
  const terms = [...new Set(text.split(/\r?\n/).map(t=>t.trim()).filter(Boolean))].sort();
  if (text.length > 2048 || terms.length > 32 || terms.some(term=>Array.from(term).length > 80))
    throw Error("每篇每个方向最多保留 32 个术语，每项最多 80 字符，总长度不超过 2048 字符。");
  return terms;
}

if (typeof module !== "undefined") module.exports = {parts, preservedRanges, denseNumericTable, translateWithTerms, architectureNames, parseGlossary};
if (typeof document !== "undefined") startReader();

async function startReader() {
  const $ = id => document.getElementById(id);
  const docs = $("doc"), direction = $("direction"), rows = $("rows"), status = $("status");
  const saved = new TranslationBook(), engines = new Map(), keep = new Set(), glossaries = new Map(), openingNames = new Map();
  const alignment = alignedReader(rows);
  let localDirections = [], current, targets = [], epoch = 0, generation = 0, running = false;
  const pairKey = () => docs.value + "/" + direction.value;
  const records = () => saved.list(docs.value,direction.value);
  function refreshExport() {
    $("export").disabled = running || !current || !records().length;
  }
  function rendered(record) {
    if (!record) return "待翻译";
    return record.selected ? "选区「" + record.original + "」\n" + record.translation : record.translation;
  }
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
    for (const id of ["doc","direction","page","go","pageRun","all","terms","applyTerms"]) $(id).disabled = value;
    for (const button of rows.querySelectorAll(".run-segment,input")) button.disabled = value;
    $("cancel").disabled = !value; refreshExport();
  }
  async function load(page) {
    const token = ++epoch;
    current = null; targets = []; alignment.render([]); $("original").removeAttribute("href"); $("count").textContent = "";
    refreshExport();
    if (!docs.value) return;
    const data = await json("/api/bilingual/page/" + docs.value + "?page=" + encodeURIComponent(page));
    if (token !== epoch) return;
    current = data;
    saved.reconcile(docs.value,data);
    const latest = new Map(records().filter(r => r.page === data.page).map(r => [r.block,r]));
    $("terms").value = (glossaries.get(pairKey()) || []).join("\n");
    refreshExport();
    $("original").href = "/pdf/" + docs.value + "#page=" + data.page;
    $("page").value = data.page; $("page").max = data.pages;
    $("count").textContent = "共 " + data.pages + " 页"; status.textContent = data.segments.length ? "" : "此页没有可划选文字。可先在扫描工作台识别本页，再返回阅读。";
    const [from,to] = direction.value.split("-");
    const language = {en:"英文",zh:"中文"};
    $("source-heading").textContent = "原文 · " + language[from];
    $("translation-heading").textContent = "译文 · " + language[to];
    const cards = data.segments.map((segment,index) => {
      const title = (data.source === "ocr" ? "OCR · " : "") + "第 " + data.page + " 页 · 块 " + segment.block + (segment.reference ? " · 编号参考文献" : "");
      const anchor = "p" + data.page + "-b" + segment.block;
      function makeCard(side, content, label, other) {
        const card = document.createElement("article"), header = document.createElement("header");
        const heading = document.createElement("span"), locate = document.createElement("button");
        heading.textContent = label + " · " + title;
        heading.id = side + "-title-" + anchor;
        card.setAttribute("aria-labelledby",heading.id);
        card.tabIndex = -1;
        card.className = "passage " + side + "-card";
        card.dataset.anchor = anchor;
        locate.textContent = "定位" + other;
        locate.className = "locate";
        locate.onclick = () => alignment.locate(index,side === "source" ? 1 : 0);
        header.append(heading,locate);
        card.append(header,content);
        return [card,header];
      }
      const source = document.createElement("p"), target = document.createElement("p");
      source.className = "source"; target.className = "translated"; source.textContent = segment.text;
      source.lang = from; target.lang = to;
      targets.push(target);
      target.textContent = rendered(latest.get(segment.block));
      const [sourceCard,head] = makeCard("source",source,"原文","译文");
      const [targetCard] = makeCard("translation",target,"译文","原文");
      const button = document.createElement("button");
      button.textContent = "翻译此段或选区";
      button.className = "run-segment";
      button.disabled = running;
      function currentSelection() {
        const selection = window.getSelection();
        if (!selection.rangeCount || selection.isCollapsed) return null;
        const range = selection.getRangeAt(0);
        if (!source.contains(range.startContainer) || !source.contains(range.endContainer)) return null;
        const offsetAt = (node, offset) => node === source ? (offset ? segment.text.length : 0) : offset;
        return {
          start:offsetAt(range.startContainer,range.startOffset),
          end:offsetAt(range.endContainer,range.endOffset),
        };
      }
      let selectedOnPress = null;
      button.onpointerdown = () => { selectedOnPress = currentSelection(); };
      button.onpointercancel = () => { selectedOnPress = null; };
      button.onmousedown = e => e.preventDefault();
      button.onclick = event => {
        run("one",segment,currentSelection() || (event.detail ? selectedOnPress : null));
        selectedOnPress = null;
      };
      const keepLabel = document.createElement("label"), checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      const keepKey = [docs.value,data.page,segment.block].join("/");
      checkbox.checked = keep.has(keepKey);
      checkbox.disabled = running;
      checkbox.onchange = () => {
        if (checkbox.checked) keep.add(keepKey); else keep.delete(keepKey);
        saved.remove(r => r.doc===docs.value && r.page===data.page && r.block===segment.block);
        target.textContent = "待翻译";
        refreshExport();
      };
      keepLabel.append(checkbox," 公式或代码整段保留");
      const proof = document.createElement("details"), label = document.createElement("summary");
      label.textContent = "核对原 PDF 段落截图";
      proof.append(label);
      proof.ontoggle = () => {
        if (!proof.open || proof.childElementCount > 1) return;
        const img = document.createElement("img");
        img.alt = title + "的 PDF 原图";
        img.src = "/api/bilingual/crop/" + docs.value + "?page=" + data.page + "&block=" + segment.block;
        proof.append(img);
      };
      head.append(button,keepLabel);
      sourceCard.append(proof);
      return [sourceCard,targetCard];
    });
    alignment.render(cards);
  }
  async function run(mode, chosen, selection) {
    if (running || !current) return;
    setRunning(true);
    const token = ++generation; let completed = 0;
    try {
      const engine = await getEngine();
      const memo = new Map();
      async function translate(text) {
        if (token !== generation) throw Error("已停止。");
        if (memo.has(text)) return memo.get(text);
        const result = await engine(text);
        if (!result.trim()) throw Error("模型返回空译文，请核对当前段后重试。");
        if (memo.size < 3000) memo.set(text,result);
        return result;
      }
      while (token === generation) {
        const page = current.page;
        const doc = docs.value;
        const localNames = architectureNames(current.segments);
        if (page === 1) openingNames.set(doc,localNames);
        else if (!openingNames.has(doc)) {
          const opening = await json("/api/bilingual/page/" + doc + "?page=1").catch(() => null);
          if (opening) openingNames.set(doc,architectureNames(opening.segments));
        }
        const names = [...new Set([...localNames,...(openingNames.get(doc) || [])])].slice(0,MAX_NAMES);
        for (const [index,segment] of current.segments.entries()) {
          if (chosen && chosen !== segment) continue;
          const start = selection?.start ?? 0, end = selection?.end ?? segment.text.length;
          status.textContent = "正在翻译第 " + page + " 页 · 块 " + segment.block;
          let result = "";
          const keepKey = [docs.value,page,segment.block].join("/");
          const reference = mode !== "one" && segment.reference;
          const table = !selection && denseNumericTable(segment.text);
          const fragments = reference || table || keep.has(keepKey) ? [{text:segment.text.slice(start,end),translate:false}] : parts(segment,start,end,glossaries.get(pairKey()),direction.value,names);
          for (const part of fragments) {
            if (token !== generation) return;
            if (part.translate) {
              const lead = part.text.match(/^\s*/)[0], tail = part.text.match(/\s*$/)[0];
              const text = part.text.trim().replace(/\s*\n\s*/g, " ").replace(/[\uFB00-\uFB06]/g, c => c.normalize("NFKC"));
              result += lead + await translateWithTerms(text,direction.value,translate,names) + tail;
            } else result += part.text;
          }
          if (token !== generation) return;
          const record = {doc:docs.value,direction:direction.value,page,block:segment.block,order:index,
            start,end,startChar:Array.from(segment.text.slice(0,start)).length,
            endChar:Array.from(segment.text.slice(0,end)).length,selected:Boolean(selection),
            original:segment.text.slice(start,end),translation:result,
            treatment:reference ? "reference" : table ? "table" : fragments.some(p=>p.translate) ? "machine" : "preserved",
            signature:sourceSignature(segment,current.source),ocr:current.source==="ocr"};
          saved.save(record);
          targets[index].textContent = rendered(record);
          completed++;
        }
        if (mode !== "all" || page === current.pages) break;
        await load(page + 1);
        if (!running || !current) return;

      }
      if (token === generation) status.textContent = "本次已处理 " + completed + " 段。";
    } catch (e) { if (token === generation) status.textContent = e.message; }
    finally { setRunning(false); }
  }
  $("applyTerms").onclick = () => {
    if (!current || running) return;
    const key = pairKey();
    let terms;
    try { terms = parseGlossary($("terms").value); }
    catch (error) { status.textContent = error.message; return; }
    if (JSON.stringify(terms) === JSON.stringify(glossaries.get(key) || [])) return;
    glossaries.set(key,terms);
    const count = saved.remove(r=>r.doc===docs.value && r.direction===direction.value);
    for (const cell of rows.querySelectorAll(".translated")) cell.textContent = "待翻译";
    status.textContent = "已应用 " + terms.length + " 个术语，需重新翻译 " + count + " 条已有译文。";
    refreshExport();
  };
  $("export").onclick = async () => {
    if (running || !current || !records().length) return;
    setRunning(true);
    $("cancel").disabled = true;
    try {
      status.textContent = "正在核对导出译段的来源…";
      const pages = [...new Set(records().map(r=>r.page))];
      let stale = 0;
      for (const page of pages) {
        const result = await json("/api/bilingual/page/"+docs.value+"?page="+page);
        stale += saved.reconcile(docs.value,result);
      }
      const items = records();
      if (stale) await load(current.page);
      if (!items.length) throw Error("原文已变化，请重新翻译后导出。");
      const name = docs.selectedOptions[0].text;
      const html = bilingualHtml(name,docs.value,direction.value,items);
      const url = URL.createObjectURL(new Blob([html],{type:"text/html;charset=utf-8"}));
      const link = document.createElement("a");
      link.href = url;
      link.download = name.replace(/[^\p{L}\p{N}._-]/gu,"_").slice(0,90) + "-" + direction.value + ".html";
      link.click();
      setTimeout(()=>URL.revokeObjectURL(url),60000);
      status.textContent = "已导出 " + items.length + " 条译文。" + (stale ? "原文变动的 " + stale + " 条已移除。" : "");
    } catch (error) { status.textContent = error.message; }
    finally { setRunning(false); }
  };
  function show(page) {load(page).catch(e => {status.textContent=e.message;});}
  function pageNumber(value, maximum = Infinity) {
    if (!/^[0-9]+$/.test(value)) return null;
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= 1 && number <= maximum ? number : null;
  }
  function pageWarning() {
    if (!current) return;
    $("page").value = current.page;
    status.textContent = `请输入 1 至 ${current.pages} 的物理页码，当前显示第 ${current.page} 页。`;
  }
  function jump() {
    const input = $("page");
    const page = pageNumber(input.value, current?.pages);
    if (page !== null) {show(page); return;}
    if (current) pageWarning();
    else status.textContent = "请输入有效的物理页码。";
    input.focus();
  }
  docs.onchange = () => show(1); direction.onchange = () => show(current?.page || 1);
  $("go").onclick = jump;
  $("page").addEventListener("keydown", event => {
    if (event.key !== "Enter" || $("go").disabled) return;
    event.preventDefault(); jump();
  });
  $("pageRun").onclick = () => run("page");
  $("all").onclick = () => run("all"); $("cancel").onclick = () => {generation++; status.textContent = "已停止。";};
  try {
    const [library,models] = await Promise.all([json("/api/library"),json("/api/bilingual/models")]);
    localDirections = models.directions;
    for (const doc of library.docs) docs.add(new Option(doc.name,doc.id));
    const requested = new URLSearchParams(location.search);
    if (requested.has("doc")) {
      docs.value = requested.get("doc");
      if (!docs.value) {
        status.textContent = "指定的文献版本不在活动文库。请返回文库恢复原件，或手动选择另一份文献。";
        return;
      }
    }
    const selected = library.docs.find(doc => doc.id === docs.value);
    if (selected) {
      const page = pageNumber(requested.get("page") || "1", selected.pages);
      await load(page ?? 1);
      if (page === null) pageWarning();
      else if (requested.has("block") && current?.page === page && selected.id === docs.value) {
        const index = current.segments.findIndex(segment => String(segment.block) === requested.get("block"));
        if (index >= 0) alignment.locate(index, 0);
        else status.textContent = "此摘录没有可对应的双语段落，请核对本页原图。";
      }
    } else status.textContent = "请先在文库导入 PDF。";
  } catch (e) {status.textContent=e.message;}
}
