(() => {
  'use strict';
  const root = document.getElementById('evidence-questions');
  if (!root) return;
  const make = (tag,text,cls) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  const btn = (text,type='button') => { const e=make('button',text,'compact');e.type=type;return e; };
  const heading = make('h2','自然语言找证据');
  const intro = make('p','用中文或英文询问训练硬件、优化器、β 参数或 BLEU。工具只定位可核对段落，不生成答案。一次最多选择 8 篇活动文献。','subtle');
  const form = make('form'), refresh = btn('刷新活动文献');
  const picker = make('fieldset');
  picker.append(make('legend','检索范围（请明确勾选）'),refresh);
  const choices = make('ul','','doc-list');
  choices.style.maxHeight = '220px';
  choices.style.overflowY = 'auto';
  picker.append(choices);
  const label = make('label','你的问题');
  label.htmlFor = 'question-input';
  const row = make('div','','search-row');
  const input = make('input');
  input.id = 'question-input'; input.maxLength = 500; input.type = 'search'; input.required = true;
  input.placeholder = '例如：英译法的 BLEU 分数有哪些？';
  const run = btn('定位证据','submit');
  run.className = 'primary';
  row.append(input,run);
  form.append(picker,label,row);
  const status = make('p','','subtle');
  status.setAttribute('role','status');
  const results = make('div');
  root.append(make('div','02B / 问题与出处','eyebrow'),heading,intro,form,status,results);
  let docs=[], picked=new Set(), controller, version=0, loading=false;
  const eligibility = () => {
    run.disabled = loading || !!controller || !input.value.trim() || picked.size < 1 || picked.size > 8;
  };
  function invalidate(message = '') {
    version++;
    controller?.abort();
    controller = undefined;
    results.replaceChildren();
    status.textContent = message;
    eligibility();
  }
  async function request(path, opts) {
    const response = await fetch(path,opts), data = await response.json();
    if (!response.ok) throw Error(typeof data?.error === 'string' ? data.error : '本机操作未完成。');
    return data;
  }
  async function load() {
    if (loading) return;
    loading = true;
    refresh.disabled = true;
    invalidate('正在载入活动文献…');
    try {
      const data = await request('/api/library');
      docs = data.docs;
      picked = new Set(docs.filter(doc=>picked.has(doc.id)).map(doc=>doc.id));
      const opts = docs.map(doc=>{
        const li=make('li'), box=make('input'), lbl=make('label');
        box.type='checkbox'; box.checked=picked.has(doc.id);
        box.addEventListener('change',()=>{
          if (box.checked) picked.add(doc.id); else picked.delete(doc.id);
          invalidate(picked.size > 8 ? '最多选择 8 篇活动文献。' : '已选 ' + picked.size + ' 篇。');
        });
        lbl.append(box,make('span',doc.name));
        li.append(lbl); return li;
      });
      choices.replaceChildren(...opts);
      if (docs.length) status.textContent = '已选 ' + picked.size + ' 篇。';
      else status.textContent = '暂无活动文献，请导入 PDF。';
    } catch (e) { status.textContent='无法载入文献：'+e.message; }
    finally { loading=false;refresh.disabled=false;eligibility(); }
  }
  refresh.onclick = load;
  root.addEventListener('library-changed',load);
  input.addEventListener('input',()=>invalidate());
  function pdf(id,page,text) {
    const a=make('a',text);
    a.href='/pdf/'+encodeURIComponent(id)+(page ? '#page='+encodeURIComponent(page) : '');
    a.rel='noopener';a.target='_blank';
    return a;
  }
  function render(data) {
    if (data.concepts.length) {
      const terms = data.concepts.map(c=>c.label+'：'+c.terms.join('、')).join('；');
      results.append(make('p','已用检索词映射：'+terms,'subtle'));
    }
    for (const note of data.notes) results.append(make('p',note,'subtle'));
    if (data.observations.length) {
      const ol=make('ul','','doc-list'), summary=make('details');
      summary.append(make('summary','展开同句 BLEU 数值线索'),ol);
      for (const o of data.observations) {
        const paper=data.papers.find(p=>p.id===o.id);
        const li=make('li');
        li.append(make('span',(o.task==='en-fr'?'英译法':'英译德')+' · '+o.value+' · '),pdf(o.id,o.page,(paper?.name||'本地文献')+' 第 '+o.page+' 页 · 文本块 '+o.block+' ↗'));
        ol.append(li);
      }
      results.append(summary);
    }
    for (const paper of data.papers) {
      const section=make('section','','panel compare'), cards=make('ul','','card-list');
      section.append(make('h3',paper.name));
      if (paper.arxiv?.id) section.append(make('p','原件可识别标记：arXiv:'+paper.arxiv.id+
        (paper.arxiv.version ? 'v'+paper.arxiv.version : ''),'subtle'));
      if (!paper.hits.length) section.append(make('p','这篇文献暂无匹配证据。','empty'));
      else {
        const covered = new Set(paper.hits.flatMap(hit=>hit.concept_ids || []));
        const missing = data.concepts.filter(c=>!covered.has(c.id)).map(c=>c.label);
        if (missing.length) section.append(make('p','这篇文献未定位到：'+missing.join('、')+'。','subtle'));
      }
      for (const hit of paper.hits) {
        const li=make('li','','evidence');
        const label=hit.page_label && String(hit.page_label)!==String(hit.page) ? ' · 页面标签 '+hit.page_label : '';
        const concepts=hit.concept_ids?.map(id=>data.concepts.find(c=>c.id===id)?.label).filter(Boolean).join(' / ');
        li.append(make('div','PDF 第 '+hit.page+' 页'+label+' · 文本块 '+hit.block+(concepts?' · '+concepts:''),'metadata'),
          make('blockquote',hit.text),pdf(paper.id,hit.page,'打开原文页 ↗'));
        cards.append(li);
      }
      if (paper.hits.length) section.append(cards);
      results.append(section);
    }
  }
  form.onsubmit = async event => {
    event.preventDefault();
    if (run.disabled) return;
    invalidate('正在定位候选原文…');
    const tick=version, current = new AbortController();
    controller=current;
    eligibility();
    try {
      const data=await request('/api/questions',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({query:input.value.trim(),ids:[...picked]}),signal:current.signal});
      if (tick!==version) return;
      status.textContent=data.status==='insufficient_evidence'?'证据不足：未定位到匹配的原文。':'已定位候选出处。请以 PDF 和原始条件为准。';
      render(data);
    } catch(e) {
      if (tick===version) status.textContent='检索未完成：'+e.message;
    } finally {
      if (tick===version) {controller=undefined;eligibility();}
    }
  };
  load();
})();
