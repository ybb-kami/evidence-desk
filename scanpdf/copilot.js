'use strict';
const get = id => document.getElementById(id);
function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function link(title, href) {
  const el = element('a', title);
  el.href = href; el.target = '_blank'; el.rel = 'noopener';
  return el;
}
const labels = {
  answer_paper:'单篇证据问答', compare_papers:'逐篇比较', citation_graph:'引用与未来工作',
  scan_coverage:'扫描覆盖', publication_metadata:'JOSS 题录'
};
function appendStep(message) {get('steps').append(element('li', message));}
function render(data) {
  for (const group of data.reports) {
    const panel = element('article');
    panel.append(element('h3', group.name));
    if (group.answer) panel.append(element('p', group.answer, 'answer'));
    if (group.gap) panel.append(element('p', group.gap, 'gap'));
    for (const cell of group.cells) {
      const query = new URLSearchParams({id:cell.id,page:cell.page,region:cell.region,row:cell.row,column:cell.column});
      const a = link('第 '+cell.page+' 页 · 行 '+cell.row+' 列 '+cell.column+' · '+(cell.value || '查看单元格'),
        (cell.source === 'native' ? '/source?' : '/scan?') + query);
      a.className = 'cell'; panel.append(a);
    }
    for (const scan of group.scan || []) panel.append(link('识别物理页 '+scan.page, '/scan?'+new URLSearchParams(scan)));
    if (group.sources.length) {
      const details = element('details'); details.open = true;
      details.append(element('summary', '查看 '+group.sources.length+' 段原文'));
      for (const hit of group.sources) {
        const query = new URLSearchParams({id:hit.id,page:hit.page,block:hit.block,part:hit.part,start:hit.start,end:hit.end});
        details.append(link(hit.name+' · 物理页 '+hit.page+' · 块 '+hit.block, '/source?'+query),
          element('blockquote',hit.text,'quote'));
      }
      panel.append(details);
    }
    get('reports').append(panel);
  }
  for (const item of data.publications) {
    const panel = element('article');
    panel.append(element('h3', item.title),element('p', [item.venue,item.year,item.authors.join(', ')].filter(Boolean).join(' · ')),
      link(item.doi, item.url),element('p','出版方元数据 SHA-256 '+item.metadata_sha256,'quote'));
    get('reports').append(panel);
  }
}
async function readEvents(response, handle) {
  if (!response.ok) throw Error((await response.json()).error || '请求失败。');
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let tail = '', terminal = false;
  try {
    for (;;) {
      const {done,value} = await reader.read();
      tail += decoder.decode(value, {stream:!done});
      const lines = tail.split('\n');
      tail = lines.pop();
      for (const line of lines) {
        if (!line) continue;
        const event = JSON.parse(line);
        if (terminal) throw Error('任务返回了重复结束标记。');
        handle(event);
        if (event.event === 'final') terminal = true;
      }
      if (done) break;
    }
    if (!terminal || tail.trim()) throw Error('连接中断，本次结果未完成。');
  } finally {reader.releaseLock();}
}
function eventReceived(event) {
  switch(event.event) {
  case 'stage': appendStep(event.label+(event.detail?' · '+event.detail:'')); break;
  case 'plan': {
    const status = {not_requested:'已选择规则编排',disabled:'未配置本机模型，使用规则编排',busy:'本机模型正在使用，使用规则编排',
      invalid:'模型工具参数未通过校验，使用规则编排',unavailable:'模型工具接口不可用，使用规则编排'};
    appendStep(event.planner === 'model_tool_calls' ? '模型 '+event.model+' 返回工具调用 · '+event.proposed.map(n=>labels[n]).join('、') : status[event.planner]);
    appendStep('执行顺序 · '+event.tools.map(n=>labels[n]).join(' → '));
    break;
  }
  case 'replan': appendStep(event.reason+' 增加「'+labels[event.tool]+'」。'); break;
  case 'receipt': {
    const states = {ok:'已完成',gap:'证据有缺口',unavailable:'外部来源不可用'};
    appendStep(labels[event.tool]+' · '+states[event.status]+' · '+event.seconds+' 秒 · '+event.anchors+' 处原文定位');
    break;
  }
  case 'token': get('preview').hidden = false; get('draft').append(document.createTextNode(event.text)); break;
  case 'error': throw Error(event.message);
  case 'final':
    get('draft').replaceChildren(); get('preview').hidden = true;
    render(event); get('result').hidden = false; get('state').textContent = '本次任务已结束。'; break;
  default: throw Error('无法读取执行事件。');
  }
}
get('task').onsubmit = async event => {
  event.preventDefault();
  const ids = Array.from(get('documents').querySelectorAll(':checked')).map(n=>n.value);
  const question = get('question').value.trim();
  if (!ids.length || ids.length > 6 || !question) {get('state').textContent='请选择 1 至 6 篇论文并输入问题。'; return;}
  get('steps').replaceChildren(); get('reports').replaceChildren(); get('draft').replaceChildren();
  get('result').hidden = true; get('preview').hidden = true; get('waiting').hidden = true;
  get('run').disabled = true; get('state').textContent = '正在执行…';
  try {
    const response = await fetch('/api/copilot', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids, question, model_planner:get('planning').checked, publisher_consent:get('publisher').checked})});
    await readEvents(response, eventReceived);
  } catch(error) {
    get('state').textContent=error.message; get('draft').replaceChildren(); get('preview').hidden=true;
  } finally {get('run').disabled = false; get('publisher').checked = false;}
};
(async () => {
  try {
    const response = await fetch('/api/library');
    if (!response.ok) throw Error('文库读取失败。');
    const docs = (await response.json()).docs;
    for (const [i,doc] of docs.entries()) {
      const row = element('label',undefined,'option'), box = element('input');
      box.type='checkbox'; box.value=doc.id; box.checked=i===0;
      row.append(box,element('span',doc.name+' · '+doc.pages+' 页')); get('documents').append(row);
    }
    if (!docs.length) {get('documents').append(link('先在工作台上传 PDF','/')); get('run').disabled=true;}
  } catch(error) {get('state').textContent=error.message;}
})();
