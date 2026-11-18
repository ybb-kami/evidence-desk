'use strict';
const byId = id => document.getElementById(id);
const paper = byId('paper'), reference = byId('reference'), consent = byId('consent');
const button = byId('audit-button'), result = byId('result');
const snapshotResult = byId('snapshot-result');
const fileLabels = {license:'许可证文件', readme:'README', environment:'依赖与环境文件',
  training:'训练入口候选', evaluation:'评测入口候选'};
let refs = [], documentInfo = null, version = 0, busy = false;

function element(tag, text) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  return node;
}

function link(label, href) {
  const node = element('a', label);
  node.href = href;
  node.target = '_blank';
  node.rel = 'noopener';
  return node;
}

async function json(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw Error(body.error || '请求失败。');
  return body;
}

function resetAudit() {
  version++;
  busy = false;
  consent.checked = false;
  consent.disabled = false;
  button.disabled = true;
  result.hidden = true;
  result.replaceChildren();
  snapshotResult.hidden = true;
  snapshotResult.replaceChildren();
  byId('audit-status').textContent = '';
}

function showReference() {
  resetAudit();
  const item = refs.find(entry => entry.id === reference.value);
  byId('selection').hidden = !item;
  if (!item) return;
  const {page, block, part, start, end, context} = item.citation;
  byId('quote').textContent = context;
  const source = byId('source');
  source.href = '/source?' + new URLSearchParams({id: documentInfo.sha256, page, block, part, start, end});
  source.textContent = '核对物理页 ' + page + ' · 文本块 ' + block + ' · 摘录 ' + part;
  byId('consent-text').textContent = '确认本次向 GitHub 公共 API 查询 ' + item.label +
    (item.cited_commit ? ' 的所列提交与文件目录。' : ' 当前默认分支的提交与文件目录。');
  if (item.snapshot) showSnapshot(item);
}

async function loadReferences() {
  resetAudit();
  refs = [];
  documentInfo = null;
  byId('selection').hidden = true;
  const active = version;
  byId('notice').textContent = '正在检索选中文献的原文…';
  try {
    const report = await json('/api/reproducibility/references?' + new URLSearchParams({id:paper.value}));
    if (active !== version) return;
    refs = report.references;
    documentInfo = report.document;
    reference.replaceChildren(...refs.map(item => new Option(item.label +
      (item.cited_commit ? ' · 引用固定提交' : '') + (item.snapshot ? ' · 获取时快照' : ''), item.id)));
    const pending = report.pending_pages.length ? ' 还有 ' + report.pending_pages.length + ' 页待识别。' : '';
    byId('notice').textContent = refs.length ?
      '原文中找到 ' + refs.length + ' 条仓库引用。' + (report.truncated ? ' 当前显示前 24 条。' : '') + pending :
      '当前可检索原文中未提取到完整的公开 GitHub 仓库地址。' + pending;
    showReference();
  } catch (error) {
    if (active === version) byId('notice').textContent = error.message;
  }
}

function showResult(response) {
  const {reference: item, audit, cached} = response;
  result.hidden = false;
  result.append(element('h2', item.label));
  if (audit.status !== 'checked') {
    result.append(element('p', audit.message), element('p', audit.checked_at + (cached ? ' · 本地缓存' : '')));
    return;
  }
  const fields = [
    ['论文文件 SHA-256', element('code', item.citation.doc_sha256)],
    ['固定提交', link(audit.commit, audit.commit_url)],
    ['提交选取方式', element('span', audit.basis === 'cited_commit' ? '论文链接含提交 SHA' : '核验时的默认分支 ' + audit.default_branch)],
    ['核验时间', element('span', audit.checked_at + (cached ? ' · 本地缓存' : ''))],
    ['目录状态', element('span', audit.tree_complete ? 'GitHub 返回完整目录' : '当前只取得部分目录')],
  ];
  if (audit.api_repository_spdx) fields.push(['仓库 API 许可标记', element('span', audit.api_repository_spdx)]);
  result.append(descriptions(fields), renderFiles(audit.groups),
    element('p', '入口候选来自文件名。证据范围为公开提交与已跟踪文件。实验运行记录为空。'));
}

function descriptions(fields) {
  const rows = element('dl');
  for (const [label, value] of fields) {
    const description = element('dd');
    description.append(value);
    rows.append(element('dt', label), description);
  }
  return rows;
}

function renderFiles(groups, acquisition = false) {
  const files = element('div');
  files.className = 'files';
  for (const [key, label] of Object.entries(fileLabels)) {
    const group = groups[key];
    const card = element('div');
    card.className = 'file-group';
    card.append(element('h3', label));
    if (!group.files.length) {
      const details = {tree_incomplete:'目录部分可读，此项待核。',not_requested:'本次未选取此类文件。',unavailable:'本次未取得此类文件。'};
      card.append(element('p', details[group.status] || '在所检目录中未找到匹配文件。'));
    } else {
      const list = element('ul');
      for (const file of group.files) {
        const row = element('li');
        row.append(link(file.path, file.url));
        if (acquisition) row.append(document.createTextNode(' · '),link('固定原始字节',file.raw_url),
          element('div', `${file.bytes} 字节 · SHA-256`), element('code',file.sha256));
        list.append(row);
      }
      card.append(list);
      if (group.files.length < group.count) card.append(element('p', '显示 ' + group.files.length + ' 项，共 ' + group.count + ' 项。'));
    }
    files.append(card);
  }
  return files;
}

function showSnapshot(item) {
  const acquisition = item.snapshot;
  snapshotResult.hidden = false;
  snapshotResult.append(element('h2', '获取时快照 · ' + item.label), descriptions([
    ['论文文件 SHA-256', element('code',item.citation.doc_sha256)],
    ['固定提交', link(acquisition.commit,acquisition.commit_url)],
    ['提交选取方式', element('span',acquisition.basis === 'cited_advertised_commit' ? '原文 SHA 与公开 Git 引用公告一致' :
      '获取时默认分支 ' + acquisition.default_branch)],
    ['获取时间', element('span',acquisition.checked_at)],
    ['Git 引用来源', link('发布方 HTTPS Git 引用公告',acquisition.git_advertisement.url)],
    ['引用公告 SHA-256', element('code',acquisition.git_advertisement.sha256)],
    ['本地记录 SHA-256', element('code',acquisition.record_sha256)],
  ]), renderFiles(acquisition.groups,true), element('p','范围为本次指定并取得的文件。入口是指定候选。实验运行记录为空。'));
  if (acquisition.unavailable.length) {
    const list = element('ul');
    for (const row of acquisition.unavailable) list.append(element('li',`${row.path} · ${row.reason}`));
    snapshotResult.append(element('p','本次未取得的文件'), list);
  }
}

consent.onchange = () => {button.disabled = busy || !consent.checked;};
reference.onchange = showReference;
paper.onchange = loadReferences;
byId('audit-form').onsubmit = async event => {
  event.preventDefault();
  if (!consent.checked || busy || !reference.value) return;
  const active = ++version, id = paper.value, selected = reference.value;
  busy = true;
  consent.checked = false;
  consent.disabled = true;
  button.disabled = true;
  result.replaceChildren();
  result.hidden = true;
  const status = byId('audit-status');
  status.textContent = '正在核对所选仓库…';
  try {
    const details = await json('/api/reproducibility/audit', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id, reference:selected, consent:true}),
    });
    if (active === version) {
      showResult(details);
      status.textContent = '';
    }
  } catch (error) {
    if (active === version) status.textContent = error.message;
  } finally {
    if (active === version) {
      busy = false;
      consent.disabled = false;
    }
  }
};

(async () => {
  try {
    const {docs} = await json('/api/library');
    paper.replaceChildren(...docs.map(doc => new Option(doc.name, doc.id)));
    const requested = new URLSearchParams(location.search).get('id');
    if (docs.some(doc => doc.id === requested)) paper.value = requested;
    paper.disabled = !docs.length;
    if (docs.length) await loadReferences();
    else byId('notice').textContent = '请先在工作台导入论文 PDF。';
  } catch (error) {
    byId('notice').textContent = error.message;
  }
})();
