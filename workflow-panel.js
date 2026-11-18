(() => {
  'use strict';
  const root=document.getElementById('evidence-workflow');
  if (!root) return;
  const E=(t,v='',cls='')=>{const n=document.createElement(t);n.textContent=v;if(cls)n.className=cls;return n;};
  const B=(text,type='button')=>{const n=E('button',text,'compact');n.type=type;return n;};
  const form=E('form'), picker=E('fieldset'), docs=E('ul','','doc-list');
  docs.style.maxHeight='180px';docs.style.overflowY='auto';
  const refresh=B('刷新活动文献');
  picker.append(E('legend','选 1–8 篇要核对的文献'),refresh,docs);
  const query=E('input');query.id='wf-query';query.type='search';query.maxLength=500;
  query.required=true;query.placeholder='例如：对照两篇论文关于 Encoder 的描述';
  const label=E('label','本次证据问题');label.htmlFor='wf-query';
  const input=E('div','','search-row'), run=B('按此计划运行','submit');run.className='primary';
  input.append(query,run);
  const plan=E('ol');
  for (const label of ['锁定本次问题和明确选中的活动文献。',
    '在本地逐篇查找候选段落。','校验原文件 SHA-256 和页块；重新提取并逐字核对。',
    '整理通过核验的摘录，保留未命中和失败记录。没有已核摘录时不生成提要。']) plan.append(E('li',label));
  form.append(picker,label,E('h3','执行计划'),plan,input);
  const status=E('p','','subtle');status.setAttribute('role','status');
  const result=E('div'), exportBtn=B('下载机器可读日志（JSON）');exportBtn.disabled=true;
  root.append(E('div','04C / 查证工作流','eyebrow'),E('h2','本地证据查证工作流'),
    E('p','只读取你选择的 PDF，不调用模型或外网，不执行文献内容。核对的是可提取文本，不判定证据是否支持主张。','subtle'),
    form,status,exportBtn,E('p','下载仅在本机进行。JSON 含本次问题及通过核验的摘录，转发前请检查内容和文献授权。','subtle'),result);
  let selected=new Set(), controller, version=0, loading=false, queued=false, state;
  function eligibility(){run.disabled=loading||!!controller||selected.size<1||selected.size>8||!query.value.trim();}
  function clear(text='') {
    version++;
    controller?.abort();
    controller=undefined;
    result.replaceChildren();
    state=undefined;
    exportBtn.disabled=true;
    status.textContent=text;
    eligibility();
  }
  async function api(path,opts) {
    const response=await fetch(path,opts), data=await response.json();
    if(!response.ok) throw Error(typeof data?.error==='string'?data.error:'本机服务未完成请求。');
    return data;
  }
  async function load(){
    if(loading){queued=true;return;}
    loading=true; refresh.disabled=true;clear('正在载入活动文献…');
    try{
      const data=await api('/api/library');
      selected=new Set([...selected].filter(id=>data.docs.some(doc=>doc.id===id)));
      docs.replaceChildren(...data.docs.map(doc=>{
        const row=E('li'), l=E('label'), check=E('input');
        check.type='checkbox';check.checked=selected.has(doc.id);
        check.addEventListener('change',()=>{
          if(check.checked)selected.add(doc.id);else selected.delete(doc.id);
          clear(selected.size>8?'最多可选 8 篇。':'已选 '+selected.size+' 篇。');
        });
        l.append(check,E('span',doc.name));row.append(l);return row;
      }));
      status.textContent=data.docs.length?'已选 '+selected.size+' 篇。':'暂无活动文献，请先导入 PDF。';
    }catch(error){status.textContent='无法载入活动文献：'+error.message;}
    finally{loading=false;refresh.disabled=false;eligibility();if(queued){queued=false;load();}}
  }
  function original(id,page,text){
    const a=E('a',text); a.href='/pdf/'+encodeURIComponent(id)+(page?'#page='+encodeURIComponent(page):'');
    a.target='_blank';a.rel='noopener';return a;
  }
  const stepStatus={completed:'已完成',skipped_no_candidates:'无候选，已跳过',refused_no_verified_evidence:'无已核摘录，拒绝给提要'};
  const failures={source_unavailable:'原件不可读取',source_hash_mismatch:'原件 SHA-256 不符',
    invalid_index_anchor:'索引锚点无效',page_not_found:'原件没有对应页',page_unreadable:'无法提取该页文本',
    block_or_fragment_not_found:'原件缺少对应文本块或片段',text_or_offset_mismatch:'文字或偏移未通过核验',
    source_unreadable:'原件格式或提取失败'};
  function render(data){
    for(const note of data.notes)result.append(E('p',note,'subtle'));
    const steps=E('ol');
    for(const s of data.steps){
      const stats=[];
      if(s.paper_count!==undefined)stats.push(s.paper_count+' 篇');
      if(s.candidate_count!==undefined)stats.push(s.candidate_count+' 条候选');
      if(s.verified_count!==undefined)stats.push(s.verified_count+' 条通过、'+s.rejected_count+' 条未通过');
      steps.append(E('li',s.label+' · '+(stepStatus[s.status]||s.status)+(stats.length?'（'+stats.join('；')+'）':'')));
    }
    const stepDetails=E('details');
    stepDetails.append(E('summary','查看本次实际步骤记录'),steps);result.append(stepDetails);
    for(const paper of data.papers){
      const panel=E('section','','panel compare');
      const title=E('h3');title.append(original(paper.id,null,paper.name+' ↗'));
      panel.append(title);
      if(paper.status==='no_match')panel.append(E('p','未定位候选摘录，未重读此原件；不表示文献没有该内容。','subtle'));
      if(paper.status==='verification_failed')panel.append(E('p','候选均未获原件逐字核验，未收录摘录。','subtle'));
      if(paper.table_calculation){
        const calc=paper.table_calculation;
        panel.append(E('p','原表同列计算：'+calc.text));
        panel.append(original(paper.id,calc.page,'查看第 '+calc.page+' 页原表 ↗'));
      }
      if(paper.evidence.length){
        panel.append(E('p','原件哈希匹配；'+paper.evidence.length+' 条摘录已核验。','subtle'));
        const list=E('ul','','card-list');
        for(const e of paper.evidence){
          const card=E('li','','evidence'), tags=e.concept_ids.map(id=>data.concepts[id]).filter(Boolean).join(' / ');
          card.append(original(paper.id,e.page,'PDF 第 '+e.page+' 页'+
            (e.page_label && e.page_label!==String(e.page)?'（'+e.page_label+'）':'')+
            ' · 文本块 '+e.block+' · 片段 '+e.part+' ↗'));
          if(tags)card.append(E('div','匹配线索：'+tags,'subtle'));
          card.append(E('blockquote',e.text));list.append(card);
        }
        panel.append(list);
      }
      if(paper.failures.length){
        const detail=E('details'), list=E('ol');
        detail.append(E('summary',paper.failures.length+' 条未通过记录（不显示未核原文）'),list);
        for(const e of paper.failures)list.append(E('li',(e.page?'第 '+e.page+' 页 · ':'')+
          (e.block?'块 '+e.block+' · ':'')+(failures[e.reason]||'核验未通过')));
        panel.append(detail);
      }
      result.append(panel);
    }
  }
  refresh.onclick=load;root.addEventListener('library-changed',load);
  query.addEventListener('input',()=>clear());
  form.onsubmit=async event=>{
    event.preventDefault();
    if(run.disabled)return;
    clear('正在执行本地检索和原件逐字核验…');
    const now=version;
    controller=new AbortController();
    eligibility();
    try{
      const data=await api('/api/workflow',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({query:query.value.trim(),ids:[...selected]}),signal:controller.signal});
      if(now!==version)return;
      render(data);
      status.textContent=data.summary;
      state=data;exportBtn.disabled=false;
    }catch(error){if(now===version)status.textContent='查证未完成：'+error.message;}
    finally{if(now===version){controller=undefined;eligibility();}}
  };
  exportBtn.onclick=()=>{
    if(!state)return;
    const href=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)+'\n'],{type:'application/json;charset=utf-8'}));
    const a=E('a');a.href=href;a.download='evidence-desk-workflow.json';
    document.body.append(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(href),1000);
  };
  load();
})();
