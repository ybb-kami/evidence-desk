"use strict";

const byId = id => document.getElementById(id);
const before = byId("before");
const after = byId("after");
const submit = byId("submit");
const status = byId("status");
const results = byId("results");
const states = {
  shared_identifier: "两份原件有相同的作品标识。arXiv 版次如有显示，来自各自首页。",
  different_identifiers: "两份原件的同类标识不同。请分别核对原件，需要时人工检查预印本与发表版本的关系。",
  conflicting_identifiers: "原件中的作品标识有相同项也有冲突项，需要人工核对。",
  unverified: "作品关系待核实。当前原件缺少可相互确认的共同标识。",
};
const missingModel = {
  model_missing: "本机未安装 E5 模型。", model_invalid: "本机 E5 模型校验失败。",
  dependencies_missing: "本机缺少 E5 运行依赖。", busy: "E5 正在处理另一项请求，可以稍后重试。",
  index_preparing: "E5 正在为其他检索准备索引，可以稍后重试。", inference_failed: "E5 推理失败。",
};
const changes = {
  same_wording: "规范化后文字一致", changed_wording: "文字有变化",
  similar_candidate: "语义相近候选，原文有变化", unpaired: "当前样本未配对",
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求未完成。");
  return data;
}

function link(text, href) {
  const node = element("a", text);
  node.href = href;
  node.target = "_blank";
  node.rel = "noopener";
  return node;
}

function citation(anchor) {
  if (!anchor) return [element("p", "当前样本没有可配对原文。", "note")];
  const coordinates = `物理页 ${anchor.page} · 块 ${anchor.block} · 段 ${anchor.part}`;
  const links = element("div", "", "links");
  links.append(link("定位原文", anchor.source_url), link("打开原 PDF 页", anchor.pdf_url));
  return [element("p", coordinates + (anchor.source === "ocr" ? " · OCR" : ""), "note"),
          element("blockquote", anchor.text), links];
}

function pane(side, content) {
  const node = element("div", "", "pane");
  node.append(element("h4", side), ...content);
  return node;
}

function renderRow(title, row) {
  const item = element("article", "", "result-row");
  const score = row.cosine === null ? "" : ` · E5 双向平均余弦 ${row.cosine.toFixed(3)}`;
  item.append(element("h3", title), element("p", changes[row.change] + score, "score"));
  if (row.number_tokens) {
    const tokens = side => row.number_tokens[side].join("、") || "无";
    item.append(element("p", `不同的数字写法　左侧 ${tokens("left")} · 右侧 ${tokens("right")}。请核对单位、范围和条件。`, "note"));
  }
  const grid = element("div", "", "grid");
  grid.append(pane("左侧 PDF", citation(row.left)), pane("右侧 PDF", citation(row.right)));
  item.append(grid);
  return item;
}

function renderMarkers(markers) {
  const entries = Object.entries(markers);
  if (!entries.length) return [element("p", "首页未定位到 arXiv 或 DOI 作品标识。", "note")];
  return entries.flatMap(([type, data]) => {
    const version = data.version ? `v${data.version}` : "";
    const label = type === "arxiv" ? "arXiv" : "DOI";
    return [element("p", `${label} ${data.id}${version}`), ...citation(data.anchor)];
  });
}

function renderModel(model) {
  if (model.state === "ready") {
    const seconds = (model.elapsed_ms / 1000).toFixed(2);
    return `本机 ${model.model} · E5 推理耗时 ${seconds} 秒。余弦分数可用于寻找主题或措辞相近的段落。作品关系由上方标识另行核对。`;
  }
  if (model.state === "disabled") return "E5 已关闭。本次显示可核对原文与规范化后的文字匹配。";
  if (model.state === "no_text") return "当前样本没有足够的双侧文本用于语义对照。";
  return `${missingModel[model.reason] || "E5 暂不可用。"} 本次显示可核对原文与规范化后的文字匹配。`;
}

function render(data) {
  byId("identity").textContent = states[data.identity.status];
  byId("markers").replaceChildren(pane("左侧 PDF", renderMarkers(data.identity.left)),
                                  pane("右侧 PDF", renderMarkers(data.identity.right)));
  byId("model").textContent = renderModel(data.semantic);
  byId("front").replaceChildren(renderRow("首页标题", data.title), renderRow("摘要起始节选", data.abstract));
  const scope = data.scope;
  byId("scope").textContent = `只比较前 ${scope.first_pages} 个物理页、正文小节起始后的样本，每侧最多 ${scope.passages_per_side} 段、每段最多 ${scope.excerpt_chars} 字符。本次左侧 ${scope.left_sampled} 段、右侧 ${scope.right_sampled} 段。请回读原件确认实验条件、结论和全文变化。`;
  const rows = data.passages.map((row, i) => renderRow(`段落 ${i + 1}`, row));
  byId("passages").replaceChildren(...(rows.length ? rows : [element("p", "当前范围未定位到适合对照的正文小节。", "note")]));
  results.hidden = false;
}

function announce(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

for (const control of [before, after, byId("semantic")]) {
  control.addEventListener("change", () => {
    if (!results.hidden) {
      results.hidden = true;
      announce("已更改对照设置。请重新比较所选 PDF。");
    }
  });
}

document.getElementById("compare").addEventListener("submit", async event => {
  event.preventDefault();
  if (before.value === after.value) {
    results.hidden = true;
    announce("请选择两份不同的 PDF。", true);
    after.focus();
    return;
  }
  const query = {before: before.value, after: after.value};
  submit.disabled = before.disabled = after.disabled = byId("semantic").disabled = true;
  results.hidden = true;
  announce("正在对照所选 PDF…");
  try {
    const data = await request("/api/version-evidence", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({before_id: query.before, after_id: query.after, semantic: byId("semantic").checked}),
    });
    history.replaceState(null, "", "/version-evidence?" + new URLSearchParams(query));
    render(data);
    announce(`对照完成。左侧 ${data.before.name}，右侧 ${data.after.name}。`);
  } catch (error) {
    announce(error.message, true);
  } finally {
    submit.disabled = before.disabled = after.disabled = byId("semantic").disabled = false;
    submit.focus();
  }
});

async function load() {
  try {
    const [active, archived] = await Promise.all([request("/api/library"), request("/api/archive")]);
    const docs = new Map(archived.docs.map(doc => [doc.id, `${doc.name}（已归档）`]));
    for (const doc of active.docs) docs.set(doc.id, doc.name);
    for (const select of [before, after]) {
      const placeholder = new Option("请选择 PDF", "");
      placeholder.disabled = true;
      select.replaceChildren(placeholder, ...[...docs].map(([id, name]) => new Option(name, id)));
      select.value = "";
    }
    if (docs.size < 2) {
      announce("请先返回文库导入至少两份 PDF。", true);
      return;
    }
    const query = new URLSearchParams(location.search);
    for (const select of [before, after]) {
      const wanted = query.get(select.id);
      if (docs.has(wanted)) select.value = wanted;
    }
    submit.disabled = before.disabled = after.disabled = false;
    announce("选择原件后开始对照。");
  } catch (error) {
    announce(error.message, true);
  }
}

load();
