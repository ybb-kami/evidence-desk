/* Explicit, two-step arXiv PDF preview and local import. */
(function(){
  'use strict';
  const panel=document.getElementById('arxiv-import-panel');
  if(!panel)return;
  const n=(tag,text,attrs={})=>{
    const node=document.createElement(tag);
    if(text!==undefined)node.textContent=text;
    for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);
    return node;
  };
  const id=/^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]{0,30}\/\d{7})$/;
  const token=/^[a-f0-9]{32}$/;
  const title=n('h2','公开 PDF：先预览，再入库',{id:'arxiv-import-title'});
  panel.setAttribute('aria-labelledby','arxiv-import-title');
  const guidance=n('p','只接受最近一次手动 arXiv 刷新的官方候选。第一步只从 arXiv 下载你选中的公开 PDF，第二步才写入本机活动文库。未确认不会下载。不支持其他网址或付费文件。',{class:'subtle'});
  const refresh=n('button','读取最近刷新候选',{type:'button'});
  const select=n('select',undefined,{id:'arxiv-import-choice'});
  const choices=n('label','公开题录：',{for:'arxiv-import-choice'});choices.append(select);
  const stage=n('button','确认下载并准备预览',{type:'button'});
  const actions=n('div',undefined,{class:'actions'});actions.append(refresh,choices,stage);
  const status=n('p','正在读取本机状态…',{role:'status',class:'subtle'});
  const preview=n('div');
  panel.append(title,guidance,actions,status,preview);
  let state,working=false;
  async function api(path,payload){
    const r=await fetch(path,payload===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
    });
    const body=await r.json();
    if(!r.ok)throw Error(typeof body.error==='string'?body.error:'本机请求失败。');
    return body;
  }
  function controls(){
    refresh.disabled=working;
    select.disabled=working||!state?.candidates.length;
    stage.disabled=working||!!state?.busy||!id.test(select.value);
  }
  function draw(){
    const previous=select.value;
    const candidates=(state?.candidates||[]).filter(row=>id.test(row.id));
    select.replaceChildren(n('option',candidates.length?'请选择':'尚无近期候选',{value:''}),
                           ...candidates.map(row=>n('option',row.id+' · '+row.title,{value:row.id})));
    select.value=candidates.some(row=>row.id===previous)?previous:'';
    controls();
    preview.replaceChildren();
    const data=state?.preview;
    if(!data||!id.test(data.id)||!token.test(data.token))return;
    const heading=n('h3','暂存预览：'+data.title);
    const source=n('a','arXiv 题录 '+data.id+' ↗',{href:'https://arxiv.org/abs/'+data.id,target:'_blank',rel:'noopener noreferrer'});
    const pdf=n('a','打开本机暂存 PDF ↗',{href:'/api/arxiv-import/preview/'+data.token,target:'_blank',rel:'noopener'});
    const summary=n('p',data.pages+' 个物理页 · '+data.bytes+' 字节 · 尚未进入文库。');
    const hash=n('p','SHA-256：'+data.sha256,{class:'subtle'});
    const approve=n('button','确认将此预览入本机库',{type:'button'});
    approve.disabled=working||!!state.busy;
    approve.onclick=async()=>{
      if(approve.disabled||working)return;
      working=true;approve.disabled=true;controls();
      status.textContent='正在导入本机文库…';
      try{
        const result=await api('/api/arxiv-import/commit',{token:data.token,confirm:true});
        state={...state,preview:null};draw();
        status.textContent=result.existing?'相同字节的 PDF 已在本机库中，未新增副本。':'已导入「'+result.doc.name+'」。';
        window.dispatchEvent(new Event('evidence-desk:library-changed'));
      }catch(error){status.textContent='未能导入：'+error.message;}
      finally{working=false;draw();}
    };
    preview.append(heading,source,n('p','官方下载来源：https://arxiv.org/pdf/'+data.id,{class:'subtle'}),
                   summary,hash,pdf,n('p','核对预览后再入库。临时预览 10 分钟后失效。',{class:'subtle'}),approve);
  }
  async function load(){
    if(working)return;
    working=true;controls();
    try{
      state=await api('/api/arxiv-import');
      status.textContent=state.source_mode==='simulation'?'模拟模式，不接通真实 arXiv。':
        state.candidates.length?'请选择最近刷新结果，不会自动下载。':'没有近期候选。请先在 arXiv 追踪里手动刷新。';
    }catch(error){status.textContent='无法读取本机预览：'+error.message;}
    finally{working=false;draw();}
  }
  stage.onclick=async()=>{
    const ident=select.value;
    if(stage.disabled||!state?.candidates.some(row=>row.id===ident))return;
    working=true;draw();
    status.textContent='正从官方 arXiv 下载公开 PDF…';
    try{
      const data=await api('/api/arxiv-import/stage',{id:ident,consent:true});
      state={...state,preview:data};
      status.textContent='PDF 已通过下载格式与大小检查，仅暂存。';
    }catch(error){status.textContent='未能准备预览：'+error.message;}
    finally{working=false;draw();}
  };
  refresh.onclick=load;
  select.onchange=controls;
  load();
})();
