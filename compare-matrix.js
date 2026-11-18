(() => {
  'use strict';
  const root = document.getElementById('compare-matrix');
  if (!root) return;
  const make = (tag,text='',cls='') => {
    const el = document.createElement(tag);
    el.textContent=text;
    if (cls) el.className=cls;
    return el;
  };
  const button = (text,type='button') => { const b=make('button',text,'compact');b.type=type;return b; };
  const heading=make('h2','多文证据矩阵');
  const guidance=make('p','选择 2–8 篇活动文献。输入要逐篇核对的术语，用分号分开，例如：编码器；层归一化；上下文窗口；参数量。仅定位原文，不判断模型之间的继承或取舍。','subtle');
  const form=make('form'), controls=make('fieldset'), picks=make('ul','','doc-list');
  picks.style.maxHeight='210px';picks.style.overflowY='auto';
  const reload=button('刷新文献');
  controls.append(make('legend','要比较的文献'), reload,picks);
  const label=make('label','比较术语');
  label.htmlFor='matrix-query';
  const query=make('input');query.id='matrix-query';query.type='search';query.maxLength=500;
  query.required=true;query.placeholder='Encoder；Decoder';
  const go=button('逐篇定位','submit');go.className='primary';
  const group=make('div','','search-row');
  group.append(query,go);
  form.append(controls,label,group);
  const status=make('p','','subtle');status.setAttribute('role','status');
  const output=make('div');
  root.append(make('div','04B / 逐文核对','eyebrow'),heading,guidance,form,status,output);
  let selected=new Set(), loadInProgress=false, reloadQueued=false, runId=0, request;
  function eligibility() {
    go.disabled=loadInProgress || !!request || !query.value.trim() || selected.size<2 || selected.size>8;
  }
  function invalidate(text='') {
    runId++;
    request?.abort();
    request=undefined;
    status.textContent=text;
    output.replaceChildren();
    eligibility();
  }
  async function call(endpoint, options) {
    const rsp=await fetch(endpoint,options);
    const body=await rsp.json();
    if (!rsp.ok) throw Error(typeof body?.error==='string' ? body.error:'本地服务未完成请求。');
    return body;
  }
  async function load() {
    if (loadInProgress) {reloadQueued=true;return;}
    loadInProgress=true;reload.disabled=true;
    invalidate('正在读取活动文献…');
    try {
      const data=await call('/api/library');
      selected=new Set([...selected].filter(id=>data.docs.some(doc=>doc.id===id)));
      const options=data.docs.map(doc=>{
        const li=make('li'), l=make('label'), b=make('input');
        b.type='checkbox';b.checked=selected.has(doc.id);
        b.addEventListener('change',()=>{
          if (b.checked) selected.add(doc.id); else selected.delete(doc.id);
          invalidate(selected.size>8?'最多选 8 篇活动文献。':'已选择 '+selected.size+' 篇。');
        });
        l.append(b,make('span',doc.name));li.append(l);return li;
      });
      picks.replaceChildren(...options);
      status.textContent=data.docs.length<2 ? '目前不足两份活动文献。请先导入 PDF。':'已选择 '+selected.size+' 篇。';
    } catch(e) { status.textContent='无法更新活动文献：'+e.message; }
    finally {
      loadInProgress=false;reload.disabled=false;eligibility();
      if (reloadQueued) {reloadQueued=false;load();}
    }
  }
  function link(id,page,label) {
    const a=make('a',label);
    a.href='/pdf/'+encodeURIComponent(id)+(page?'#page='+encodeURIComponent(page):'');
    a.target='_blank';a.rel='noopener';return a;
  }
  function render(results) {
    for (const note of results.notes) output.append(make('p',note,'subtle'));
    const frame=make('div','','matrix-overflow');
    frame.tabIndex=0;frame.setAttribute('aria-label','可横向滚动的多文证据矩阵');
    const tb=make('table','','evidence-matrix');
    const thead=make('thead'), header=make('tr');
    const matter=make('th','概念或原词');matter.scope='col';header.append(matter);
    for (const paper of results.papers) {
      const th=make('th');th.scope='col';
      th.append(link(paper.id,null,paper.name+' ↗'));
      if (paper.arxiv?.id) th.append(make('div','arXiv:'+paper.arxiv.id+
        (paper.arxiv.version?'v'+paper.arxiv.version:''),'subtle'));
      header.append(th);
    }
    thead.append(header);tb.append(thead);
    const body=make('tbody');
    for (const row of results.rows) {
      const tr=make('tr'), label=make('th',row.label);label.scope='row';
      label.append(make('div','匹配线索：'+row.terms.join(' / '),'subtle'));
      tr.append(label);
      for (const paper of results.papers) {
        const cell=row.cells.find(cell=>cell.id===paper.id), td=make('td');
        if (!cell?.hits?.length) td.append(make('p','未定位候选原文，不表示没有。','subtle'));
        else {
          const details=make('details');
          details.append(make('summary',cell.hits.length+' 条候选原文'));
          for (const h of cell.hits) {
            const block=make('div');
            block.append(link(paper.id,h.page,'PDF 第 '+h.page+' 页'+
              (h.page_label && String(h.page_label)!==String(h.page)?'（'+h.page_label+'）':'')+
              ' · 块 '+h.block+' · 片段 '+(h.part||1)+' ↗'),make('blockquote',h.text));
            details.append(block);
          }
          td.append(details);
        }
        tr.append(td);
      }
      body.append(tr);
    }
    tb.append(body);frame.append(tb);output.append(frame);
  }
  reload.onclick=load;
  root.addEventListener('library-changed',load);
  query.addEventListener('input',()=>invalidate());
  form.onsubmit=async event=>{
    event.preventDefault();
    if (go.disabled) return;
    invalidate('正在逐篇定位候选原文…');
    const curr=runId;
    request=new AbortController();
    eligibility();
    try {
      const results=await call('/api/compare-evidence',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({query:query.value.trim(),ids:[...selected]}),signal:request.signal
      });
      if (curr!==runId) return;
      status.textContent=results.rows.some(row=>row.cells.some(cell=>cell.hits.length)) ?
        '已生成逐文核对矩阵。展开候选段落，对照 PDF 原页。':'证据不足：所选文献未定位到匹配的候选段落。';
      render(results);
    } catch(e) {
      if (curr===runId) status.textContent='无法比较：'+e.message;
    } finally {
      if (curr===runId) {request=undefined;eligibility();}
    }
  };
  load();
})();
