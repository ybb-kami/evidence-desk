/* Workbench OCR controls. All PDF bytes stay in the local app. */
(function () {
  'use strict';
  const panel=document.getElementById('local-ocr-panel');
  if (!panel) return;
  const node=(tag,text,attrs={})=>{
    const element=document.createElement(tag);
    if (text !== undefined) element.textContent=text;
    for (const [key,value] of Object.entries(attrs)) element.setAttribute(key,value);
    return element;
  };
  const heading=node('h2','本机英文扫描件 OCR',{id:'local-ocr-title'});
  panel.setAttribute('aria-labelledby','local-ocr-title');
  const note=node('p','仅 Linux / macOS，须已安装 Tesseract 英文语言包。仅处理 1–20 页、最多 15 MiB 的纯图片 PDF，不上传、不改写文库原件。',{class:'subtle'});
  const select=node('select',undefined,{id:'local-ocr-source'});
  const label=node('label','活动扫描件：',{for:select.id});
  label.append(select);
  const start=node('button','运行本机 OCR',{type:'button',id:'local-ocr-start'});
  const refresh=node('button','刷新文库和任务状态',{type:'button',class:'compact'});
  const controls=node('div',undefined,{class:'actions'});
  controls.append(label,start,refresh);
  const status=node('p','正在读取本机文库…',{role:'status',id:'local-ocr-status',class:'subtle'});
  const result=node('div',undefined,{class:'actions'});
  const reminder=node('p','下载后可另行导入带文字层的 .ocr.pdf。引用前应打开原件同一物理页，核对识别结果，尤其是公式、数字和图中文字。新任务或关闭程序会清除上一任务的临时下载。',{class:'subtle'});
  panel.append(heading,note,controls,status,result,reminder);

  let docs=[], state={status:'idle'}, checking=false, submitting=false, timer, pollErrors=0, generation=0;
  const jobId=/^[a-f0-9]{32}$/;
  const local=async (path, options)=>{
    const response=await fetch(path,{cache:'no-store',...options});
    const value=await response.json();
    if (!response.ok) throw Error(typeof value.error==='string' ? value.error : '本机请求失败。');
    return value;
  };
  const eligible=doc=>Number.isInteger(doc.pages)&&doc.pages>0&&doc.pages<=20&&doc.size<=15*1024**2&&
                          Array.isArray(doc.textless_pages)&&doc.textless_pages.length===doc.pages;
  const disabled=()=>{
    start.disabled=checking||submitting||state.status==='running'||!select.value;
    select.disabled=submitting||state.status==='running'||!docs.length;
  };
  const loadDocs=async()=>{
    const previous=select.value;
    const data=await local('/api/library');
    docs=(Array.isArray(data.docs)?data.docs:[]).filter(eligible);
    const placeholder=node('option',docs.length?'请选择要处理的原件':'没有符合条件的活动扫描件',{value:''});
    select.replaceChildren(placeholder,...docs.map(doc=>node('option',doc.name,{value:doc.id})));
    select.value=docs.some(doc=>doc.id===previous)?previous:'';
    disabled();
  };
  const showState=()=>{
    disabled();
    result.replaceChildren();
    const source=state.source_name||'所选扫描件';
    if (state.status==='running') {
      status.textContent='正在本机识别「'+source+'」。本任务最长约 2 分钟，可继续使用证据台。';
    } else if (state.status==='failed') {
      status.textContent='OCR 未完成：'+(state.message||'未生成可下载副本。')+' 原件未改动。';
    } else if (state.status==='succeeded' && jobId.test(state.job_id||'')) {
      status.textContent='已生成「'+source+'」的独立 OCR 副本；尚未导入文库。';
      const link=node('a','下载 OCR 副本',{
        href:'/api/ocr/result/'+state.job_id,
        download:state.filename||'document.ocr.pdf'
      });
      result.append(link);
      if (/^[a-f0-9]{64}$/.test(state.source_id||'')) {
        result.append(node('a','打开原件 ↗',{href:'/pdf/'+state.source_id,target:'_blank',rel:'noopener'}));
      }
    } else {
      status.textContent=docs.length?'请选择一份原件，再明确点击运行。':'未找到纯图片扫描件。导入后点「刷新文库和任务状态」。';
    }
  };
  const scheduled=()=>{
    clearTimeout(timer);
    if (state.status==='running' && pollErrors<5 && !document.hidden) {
      timer=setTimeout(syncJob,pollErrors?3000:1000);
    }
  };
  async function syncJob() {
    const current=generation;
    try {
      const next=await local('/api/ocr');
      if (current!==generation) return;
      state=next;
      pollErrors=0;
      showState();
    } catch (error) {
      if (current!==generation) return;
      pollErrors++;
      status.textContent='暂时无法读取 OCR 状态：'+error.message;
    } finally {
      if (current===generation) scheduled();
    }
  }
  async function loadAll() {
    if (checking||submitting) return;
    checking=true; disabled();
    try {
      await loadDocs();
      await syncJob();
    } catch (error) {
      status.textContent='无法刷新本机文库：'+error.message;
    } finally {
      checking=false; disabled();
    }
  }
  start.onclick=async()=>{
    const id=select.value;
    if (start.disabled||!id||!docs.some(doc=>doc.id===id)) return;
    submitting=true;
    const run=++generation;
    disabled();
    clearTimeout(timer);
    status.textContent='正在提交本机 OCR…';
    try {
      const next=await local('/api/ocr',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
      if (run!==generation) return;
      state=next; pollErrors=0;
      showState();
    } catch (error) {
      if (run!==generation) return;
      status.textContent='无法启动 OCR：'+error.message;
    } finally {
      if (run===generation) {
        submitting=false; disabled(); scheduled();
      }
    }
  };
  select.onchange=disabled;
  refresh.onclick=loadAll;
  document.addEventListener('visibilitychange',()=>{
    if (document.hidden) clearTimeout(timer);
    else if (!submitting) loadAll();
  });
  loadAll();
})();
