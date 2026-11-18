/* Local extraction-layer reader. Never inject PDF text as HTML. */
(() => {
  "use strict";
  const root = document.getElementById("reader");
  if (!root) return;
  function element(tag, text, attrs = {}) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, value);
    return n;
  }
  function button(text, action, attrs = {}) {
    const n = element("button", text, { type: "button", ...attrs });
    n.addEventListener("click", action);
    return n;
  }
  async function get(path, signal) {
    const response = await fetch(path, { signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "本地请求失败。");
    return data;
  }
  const docs = element("select", undefined, { id: "reader-docs", "aria-label": "阅读本地文献" });
  const status = element("p", "导入 PDF 后，可在这里查看原文。", { role: "status", class: "subtle" });
  const output = element("div", undefined, { class: "reader-output" });
  const load = button("打开阅读", open, { class: "primary" });
  load.disabled = true;
  root.classList.add("panel", "compare", "reader");
  root.replaceChildren(
    element("div", "文献阅读 / 引文定位", { class: "eyebrow" }),
    element("h2", "目录与正文引文", { id: "reader-title" }),
    element("p", "这里显示提取文本；单栏、双栏、表格与公式请核对 PDF 原页。目录及方括号数字引文为本地识别结果，未识别或冲突的引文不跳转。本模块不翻译。", { class: "subtle" }),
    element("label", "选择活动文献", { for: docs.id }),
    element("div", undefined, { class: "reader-controls" }),
    status, output
  );
  root.querySelector(".reader-controls").append(docs, load, button("刷新文库", refresh));
  let libraryRequest, documentRequest;
  function reset() {
    documentRequest?.abort();
    output.replaceChildren();
  }
  docs.addEventListener("change", () => {
    reset();
    status.textContent = "点击“打开阅读”查看所选文献。";
  });
  async function refresh() {
    libraryRequest?.abort();
    const controller = new AbortController();
    libraryRequest = controller;
    const original = docs.value;
    try {
      const library = await get("/api/library", controller.signal);
      if (controller.signal.aborted) return;
      const selected = docs.value || original;
      docs.replaceChildren(...library.docs.map(d => element("option", d.name, { value: d.id })));
      if (library.docs.some(d => d.id === selected)) docs.value = selected;
      if (!library.docs.length) docs.append(element("option", "文库暂无活动文献", { value: "" }));
      load.disabled = !docs.value;
      docs.disabled = !docs.value;
      if (docs.value !== selected) reset();
      if (!output.children.length) status.textContent = docs.value ? "点击“打开阅读”查看所选文献。" : "请先导入 PDF，再刷新文库。";
    } catch (error) {
      if (!controller.signal.aborted) status.textContent = "暂时无法刷新本地文库。";
    }
  }
  async function open() {
    const id = docs.value;
    if (!/^[a-f0-9]{64}$/.test(id)) return;
    reset();
    const controller = new AbortController();
    documentRequest = controller;
    status.textContent = "正在读取本地提取文本…";
    try {
      const data = await get(`/api/reader/${id}?view=outline`, controller.signal);
      if (controller.signal.aborted) return;
      render(data, id, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted) status.textContent = "无法打开所选文献。请刷新文库后重试。";
    }
  }
  function render(data, id, signal) {
    const sectionList = element("ul", undefined, { class: "reader-directory" });
    const pages = element("select", undefined, { "aria-label": "选择物理页" });
    const rawPage = element("a", "打开 PDF 原页 ↗", { target: "_blank", rel: "noopener noreferrer" });
    const pageHeader = element("h3", undefined, { tabindex: "-1" });
    const pageBody = element("div", undefined, { class: "reader-page" });
    const refSelect = element("select", undefined, { "aria-label": "选择参考文献编号" });
    const refBody = element("div", undefined, { class: "reader-reference", tabindex: "-1" });
    const references = new Map(data.references.map(r => [r.number, r]));
    const nav = element("nav", undefined, { "aria-label": "识别出的章节" });
    const reading = element("div", undefined, { class: "reader-main" });
    const refPane = element("aside", undefined, { "aria-label": "参考文献及正文回链" });
    function jump(target) {
      const found = document.getElementById(target);
      if (!found) return;
      found.focus({ preventScroll: true });
      found.scrollIntoView({ block: "nearest" });
    }
    for (const p of data.pages) {
      const label = String(p.page) === p.label ? `物理页 ${p.page}` : `物理页 ${p.page} · 标签 ${p.label}`;
      pages.append(element("option", label, { value: p.page }));
    }
    for (const entry of data.outline) {
      const row = element("li", undefined, { class: `reader-level-${entry.level}` });
      row.append(button(`${entry.title} · ${entry.page}页`, () => toPage(entry.page, entry.id)));
      sectionList.append(row);
    }
    for (const ref of data.references) refSelect.append(element("option", `[${ref.number}] · ${ref.page}页`, { value: ref.number }));
    if (!references.size) {
      refSelect.append(element("option", "没有识别出可定位的数字参考条目"));
      refSelect.disabled = true;
    }
    const prev = button("上一页", () => toPage(Number(pages.value) - 1));
    const next = button("下一页", () => toPage(Number(pages.value) + 1));
    const bar = element("div", undefined, { class: "reader-controls" });
    bar.append(prev, pages, next, rawPage);
    nav.append(element("h3", "识别目录"));
    nav.append(data.outline.length ? sectionList : element("p", "没有识别出章节。可按页阅读。", { class: "subtle" }));
    reading.append(bar, pageHeader, pageBody);
    refPane.append(element("h3", "参考文献"), refSelect, refBody);
    if (data.ambiguous_numbers.length) refPane.append(element("p", `重复编号未链接：${data.ambiguous_numbers.join("、")}`, { class: "subtle" }));
    const layout = element("div", undefined, { class: "reader-layout" });
    layout.append(nav, reading, refPane);
    output.replaceChildren(layout);
    pages.addEventListener("change", () => toPage(Number(pages.value)));
    refSelect.addEventListener("change", () => showReference(Number(refSelect.value)));
    let activeCitation = null;
    function showReference(number, occurrence = null) {
      const ref = references.get(number);
      if (!ref) return;
      refSelect.value = String(number);
      activeCitation = occurrence;
      refBody.replaceChildren(
        element("p", ref.text, { class: "reader-text" }),
        button(`定位参考条目 · 第 ${ref.page} 页`, () => toPage(ref.page, ref.id)),
        element("h4", `正文引文（${ref.occurrences.length} 处）`)
      );
      if (activeCitation) {
        refBody.append(button("返回刚才的正文引用", () => toPage(activeCitation.page, activeCitation.id), { class: "reader-jump" }));
      }
      if (ref.occurrences.length) {
        const choices = element("select", undefined, { "aria-label": "选择正文引用位置" });
        ref.occurrences.forEach((location, i) => {
          choices.append(element("option", `第 ${location.page} 页 · 引用 ${i + 1}`, { value: i }));
          if (location.id === activeCitation?.id) choices.selectedIndex = i;
        });
        const controls = element("div", undefined, { class: "reader-controls" });
        controls.append(choices, button("定位正文", () => {
          const location = ref.occurrences[Number(choices.value)];
          toPage(location.page, location.id);
        }));
        refBody.append(controls);
      }
      if (occurrence) {
        refBody.focus({ preventScroll: true });
        refBody.scrollIntoView({ block: "nearest" });
      }
    }
    let pageRequest = 0;
    const localPages = new Map();
    async function toPage(page, target) {
      let p = data.pages[page - 1];
      if (!p) return;
      pages.value = String(page);
      pageHeader.textContent = pages.selectedOptions[0].textContent;
      rawPage.href = `/pdf/${id}#page=${page}`;
      prev.disabled = page === 1;
      next.disabled = page === data.pages.length;
      const serial=++pageRequest;
      if (data.paged) {
        pageBody.replaceChildren(element('p','读取这一页…'));
        try {
          p=localPages.get(page) || await get(`/api/reader/${id}?view=page&page=${page}`,signal);
          if(signal.aborted || serial!==pageRequest)return;
          localPages.set(page,p);
          if(localPages.size>8)localPages.delete(localPages.keys().next().value);
        } catch (_) {if(!signal.aborted && serial===pageRequest)pageBody.replaceChildren(element('p','本页未能加载，请重选页码。'));return;}
      }
      const frag = document.createDocumentFragment();
      for (const block of p.blocks) {
        const node = element("p", undefined, { class: "reader-text", id: `reader-${block.id}`, tabindex: "-1" });
        if (block.heading) node.setAttribute("data-heading", "");
        const codePoints = Array.from(block.text);
        let position = 0;
        for (const citation of block.citations || block.entries || []) {
          node.append(codePoints.slice(position, citation.start).join(""));
          const text = codePoints.slice(citation.start, citation.end).join("");
          const location = { id: citation.id, page };
          if (!citation.numbers) {
            node.append(element("span", text, { id: `reader-${citation.id}`, tabindex: "-1", class: "reader-reference-anchor" }));
          } else if (citation.numbers.length === 1) {
            node.append(button(text, () => showReference(citation.numbers[0], location),
              { class: "reader-citation", id: `reader-${citation.id}`, title: "查看参考条目" }));
          } else {
            const group = element("span", undefined, { id: `reader-${citation.id}`, tabindex: "-1", class: "reader-citation-group" });
            group.append(text);
            for (const number of citation.numbers) group.append(button(`[${number}]`, () => showReference(number, location),
              { class: "reader-citation", title: "查看参考条目" }));
            node.append(group);
          }
          position = citation.end;
        }
        node.append(codePoints.slice(position).join(""));
        frag.append(node);
      }
      if (!p.blocks.length) frag.append(element("p", "本页没有可提取文本，请核对 PDF 原页。", { class: "subtle" }));
      pageBody.replaceChildren(frag);
      if (target) jump(`reader-${target}`);
    }
    status.textContent = `${data.doc.name} · ${data.pages.length} 页 · 识别 ${data.outline.length} 个目录位置、${data.occurrences} 处可跳转引文${data.at_occurrence_limit ? "（已达上限）" : ""}。`;
    toPage(1);
    if (data.references.length) showReference(data.references[0].number);
  }
  document.addEventListener("evidence-desk:library", refresh);
  refresh();
})();
