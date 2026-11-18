/* Show saved evidence without interpreting any PDF or OCR text as markup. */
(() => {
  "use strict";
  const item = (name) => document.getElementById(name);
  const status = item("status");
  const overlay = item("overlay");
  const image = item("image");
  const jump = item("jump");

  function region(box, page, selected) {
    const [left, top, right, bottom] = box;
    const element = document.createElement("div");
    element.className = selected ? "region selected" : "region";
    element.style.left = `${100 * left / page.width}%`;
    element.style.top = `${100 * top / page.height}%`;
    element.style.width = `${100 * (right - left) / page.width}%`;
    element.style.height = `${100 * (bottom - top) / page.height}%`;
    overlay.append(element);
  }

  function scrollToEvidence() {
    const first = overlay.querySelector(".selected") || overlay.querySelector(".region");
    if (!first) return;
    const viewport = first.closest(".viewport");
    viewport.scrollTop = first.offsetTop - (viewport.clientHeight - first.offsetHeight) / 2;
  }

  function showEvidence(page) {
    item("name").textContent = page.doc.name;
    const printed = page.page_label !== String(page.page) ? `（页标签 ${page.page_label}）` : "";
    const anchor = page.region
      ? `${page.table === null ? `表格区域 ${page.region}` : `表 ${page.table}`} · 行 ${page.row} · 列 ${page.column}`
      : `块 ${page.block} · 分段 ${page.part}`;
    item("anchor").textContent = `物理页 ${page.page}${printed} · ${anchor}`;
    item("pdf").href = page.pdf_url;
    const reader = new URLSearchParams({doc: page.doc.id, page: page.page});
    if (page.block) {
      reader.set("block", page.block);
      const here = new URLSearchParams({id: page.doc.id, page: page.page, block: page.block, part: page.part});
      item("ask-here").href = "/followup?" + here;
      item("ask-here").hidden = false;
    }
    item("bilingual").href = "/translation?" + reader;
    for (const neighbor of page.neighbors || []) {
      const entry = document.createElement("li"), link = document.createElement("a");
      const query = new URLSearchParams({id: page.doc.id, page: page.page, block: neighbor.block, part: neighbor.part});
      link.href = "/source?" + query;
      link.textContent = (neighbor.direction === "previous" ? "上一摘录" : "下一摘录")
        + ` · 块 ${neighbor.block} · 分段 ${neighbor.part}`;
      entry.append(link, document.createTextNode(" " + neighbor.preview));
      item("neighbors").append(entry);
    }
    item("context").hidden = !item("neighbors").childElementCount;
    item("identity").textContent = `SHA-256 ${page.doc.id}`;
    const labels = { glyph: "PDF 字符", word: "OCR 词框", line: "OCR 行框", block: "原文块", cell: "表格单元格" };
    const precision = page.selection?.boxes.length ? page.selection.precision : page.passage.precision;
    const prefix = page.source === "ocr" ? "OCR 转录。请与原页核对。" : "PDF 文本层。";
    item("precision").textContent = prefix + (labels[precision]
      ? `高亮位置依据${labels[precision]}。` : "当前可核对到物理页。");

    if (page.selection) {
      const { start, end } = page.selection;
      const chars = Array.from(page.text);
      const mark = document.createElement("mark");
      mark.textContent = chars.slice(start, end).join("");
      item("excerpt").append(chars.slice(0, start).join(""), mark, chars.slice(end).join(""));
    } else {
      item("excerpt").textContent = page.text;
    }

    page.passage.boxes.forEach((box) => region(box, page, false));
    page.selection?.boxes.forEach((box) => region(box, page, true));
    jump.hidden = !overlay.childElementCount;
    image.alt = `原始 PDF 物理页 ${page.page}`;
    image.addEventListener("load", scrollToEvidence, { once: true });
    image.addEventListener("error", () => {
      status.textContent = "原页预览无法打开。可使用原始 PDF 链接。";
      status.hidden = false;
      overlay.hidden = true;
      jump.hidden = true;
    }, { once: true });
    image.src = page.image_url;
    status.hidden = true;
    item("evidence").hidden = false;
  }

  jump.addEventListener("click", scrollToEvidence);
  const params = new URLSearchParams(location.search);
  const required = ["id", "page"];
  const fields = params.has("region")
    ? [...required, "region", "row", "column"]
    : [...required, "block", "part", ...(params.has("start") || params.has("end") ? ["start", "end"] : [])];
  const matchers = { id: /^[0-9a-f]{64}$/, region: /^p[1-9][0-9]{0,5}-r[1-9][0-9]{0,5}$/ };
  const valid = fields.every((key) => params.getAll(key).length === 1 &&
    (matchers[key] || /^[0-9]{1,6}$/).test(params.get(key)));
  if (!valid || Array.from(params).length !== fields.length) {
    status.textContent = "引文定位链接缺少必要参数或含有无效参数。";
    return;
  }
  fetch(`/api/source?${params}`)
    .then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "无法读取本地引文。");
      showEvidence(payload);
    })
    .catch((error) => { status.textContent = error.message || "无法读取本地引文。"; });
})();
