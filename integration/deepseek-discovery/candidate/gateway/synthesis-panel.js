(async function(){
'use strict';
const el=(t,v='')=>{const e=document.createElement(t);e.textContent=v;return e};
const root=el('section');root.className='panel compare';root.id='grounded-synthesis';
(document.querySelector('#evidence-workflow')||document.body.lastElementChild).after(root);
const title=el('h2','单篇问答与跨文献综述 · 逐句核对'),help=el('p','选择一篇 PDF 进行单篇问答，或选 2–6 篇文献生成综述。先在本地核对原件并预览请求，再决定是否发送。每条草稿都需要结合原页复核。');
const scopeSources=el('div'),docs=el('fieldset'),q=el('input'),preview=el('button','核对所选文献并预览');
const consent=el('input'),label=el('label'),disclosure=el('span'),send=el('button','发送并生成待审草稿');
const pre=el('pre'),status=el('p'),claims=el('div'),refresh=el('button','刷新文献'),provider=el('p'),promptLabel=el('label','研究问题');
q.id='synthesis-question';promptLabel.htmlFor=q.id;docs.setAttribute('aria-label','所选 PDF');
q.placeholder='例如：这篇论文如何描述编码器？';q.maxLength=500;consent.type='checkbox';label.append(consent,disclosure);
status.setAttribute('role','status');pre.style.whiteSpace='pre-wrap';send.disabled=true;label.hidden=true;provider.hidden=true;
const style=el('style');style.textContent=`
#grounded-synthesis{--ink:#152c36;--accent:#176457;--rule:#cddbd5;background:#fbfcf9;border:1px solid var(--rule);border-top:4px solid var(--accent);padding:clamp(16px,3vw,36px);color:var(--ink)}
#grounded-synthesis header{border-bottom:1px solid var(--rule);padding-bottom:12px;margin-bottom:18px}#grounded-synthesis h2{font-size:clamp(22px,2.3vw,34px);line-height:1.28;margin:4px 0 8px}#grounded-synthesis .eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.09em}
#grounded-synthesis .s-grid{display:grid;grid-template-columns:minmax(240px,0.85fr) minmax(0,1.35fr);gap:20px;align-items:start}#grounded-synthesis .s-card{min-width:0;background:white;border:1px solid var(--rule);border-radius:9px;padding:16px}#grounded-synthesis .s-card h3{margin:0 0 12px}#grounded-synthesis fieldset{max-height:260px;overflow:auto;border:0;padding:4px 0 10px}
#grounded-synthesis fieldset label{display:flex;align-items:baseline;gap:8px;overflow-wrap:anywhere}#grounded-synthesis fieldset br{display:none}#grounded-synthesis input:not([type]){width:100%;box-sizing:border-box}
#grounded-synthesis .s-proof{margin:10px 0}#grounded-synthesis .s-proof a{color:#075b50;font-weight:650}#grounded-synthesis blockquote{border-left:3px solid #75aa9a;margin:6px 0 14px;padding:8px 12px;background:#f3f7f3;white-space:pre-wrap;overflow-wrap:anywhere}
#grounded-synthesis details{margin:10px 0;border:1px solid var(--rule);padding:8px 10px;border-radius:5px}#grounded-synthesis summary{cursor:pointer}#grounded-synthesis pre{overflow:auto;max-height:16rem;overflow-wrap:anywhere}#grounded-synthesis button:focus-visible,#grounded-synthesis a:focus-visible,#grounded-synthesis summary:focus-visible{outline:3px solid #a26310;outline-offset:3px}
@media(max-width:760px){#grounded-synthesis .s-grid{grid-template-columns:1fr;gap:12px}#grounded-synthesis .s-card{padding:12px}}
`;
const head=el('header'),ey=el('div','研究工具 / 原文问答');ey.className='eyebrow';head.append(ey,title,help);
const grid=el('div'),left=el('div'),right=el('div');grid.className='s-grid';left.className='s-card';right.className='s-card';scopeSources.className='s-proof';
const receiptView=el('p');receiptView.id='synthesis-receipt';receiptView.hidden=true;
const raw=el('details');raw.append(el('summary','查看本次将发送的完整请求（JSON）'),pre);
left.append(el('h3','1 · 选择文献和问题'),refresh,docs,promptLabel,q,preview);
right.append(el('h3','2 · 核对原文与发送范围'),status,provider,scopeSources,raw,label,send,receiptView,claims);grid.append(left,right);root.append(style,head,grid);
const api=async(path,body)=>{const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});if(!r.ok)throw Error('请求没有完成（'+r.status+'）');return r.json()};
const names=new Map();let token='',rev=0;
const selected=()=>[...docs.querySelectorAll(':checked')].map(i=>i.value);
const syncButtons=()=>{
  const single=selected().length===1;
  preview.textContent=single?'预览单篇问答':'核对所选文献并预览';
  send.textContent=single?'发送并生成待审回答':'发送并生成待审草稿';
};
const reset=()=>{
  rev++;token='';pre.textContent='';scopeSources.replaceChildren();label.hidden=true;send.disabled=true;
  consent.checked=false;claims.replaceChildren();status.textContent='';provider.hidden=true;
  receiptView.hidden=true;receiptView.textContent='';syncButtons();
};
const markProvider=mode=>{
  const fixture=mode==='offline_fixture';
  provider.hidden=!fixture;provider.textContent=fixture?'离线模拟 · 请求只进入本地假供应商。':'';
  disclosure.textContent=fixture?'同意将本次问题和摘录交给本地假供应商进行离线测试。':
    '仅同意将下列本次问题和摘录发给 DeepSeek，可能计费。';
};
q.addEventListener('input',reset);docs.addEventListener('change',reset);
consent.onchange=()=>send.disabled=!consent.checked||!token;
refresh.onclick=async()=>{
  reset();const cur=rev;docs.replaceChildren();names.clear();syncButtons();
  try{
    const r=await api('/api/library');if(cur!==rev)return;
    for(const d of r.docs){
      names.set(d.id,d.name);const b=el('input');b.type='checkbox';b.value=d.id;
      const l=el('label',d.name);l.prepend(b);docs.append(l,el('br'));
    }
  }catch(e){if(cur===rev)status.textContent=e.message;}
};
document.getElementById('evidence-questions')?.addEventListener('library-changed',refresh.onclick);
await refresh.onclick();

function evidenceLink(ref,text){
  const a=el('a',text);a.target='_blank';a.rel='noopener';
  a.href='/pdf/'+encodeURIComponent(ref.doc_id)+'#page='+ref.page;return a;
}
preview.onclick=async()=>{
  reset();const cur=rev,ids=selected();
  if(ids.length<1||ids.length>6||!q.value.trim()){
    status.textContent='请选择 1 至 6 篇 PDF 并输入问题。';return;
  }
  try{
    const r=await api('/api/synthesis/preview',{ids,query:q.value});if(cur!==rev)return;
    markProvider(r.provider_mode);pre.textContent=r.wire_text||'';
    for(const [id,ref] of Object.entries(r.sources||{})){
      const label=id+' · '+(names.get(ref.doc_id)||'原件')+' · 第 '+ref.page+' 页 · 块 '+ref.block;
      scopeSources.append(evidenceLink(ref,label),el('blockquote',ref.text));
    }
    token=r.token||'';label.hidden=!token||!r.online;
    status.textContent=r.status!=='ready'?'没有可核实的原文摘录，本次不会发送请求。':
      (r.online?'请核对本次发送内容。':'付费生成未启用，本次仅预览本地证据。');
  }catch(e){if(cur===rev)status.textContent=e.message;}
};
send.onclick=async()=>{
  if(!token||!consent.checked)return;
  const pending=token,cur=rev;token='';send.disabled=true;consent.checked=false;
  try{
    const r=await api('/api/synthesis/confirm',{token:pending,consent:true});if(cur!==rev)return;
    markProvider(r.provider_mode);
    status.textContent=r.mode==='single'?
      (r.status==='abstained'?'本次未返回可核对的回答。':'草稿已返回，请逐句核对所引原文。'):
      '草稿状态：'+r.status+'。请逐句审签。';
    if(r.provider_mode==='approved_unix'){
      const receipt=r.generation_receipt;
      const parts=[];
      if(receipt?.model)parts.push('响应模型 '+receipt.model);
      if(receipt?.usage){
        const u=receipt.usage;
        parts.push('输入 '+u.prompt_tokens+'、输出 '+u.completion_tokens+'，共 '+u.total_tokens+' tokens');
      }else{
        parts.push('本次响应未附可核对的 token 用量');
      }
      if(receipt?.provider_request_id_sha256)parts.push('请求编号摘要 '+receipt.provider_request_id_sha256.slice(0,12));
      receiptView.textContent=parts.join(' · ');receiptView.hidden=false;
    }
    for(const c of r.claims||[]){
      const item=el('article');item.append(el('p',c.text));
      for(const ref of c.evidence||c.citations||[]){
        item.append(evidenceLink(ref,'第 '+ref.page+' 页 · 块 '+ref.block),el('blockquote',ref.text));
      }
      claims.append(item);
    }
  }catch(e){if(cur===rev)status.textContent=e.message;}
};
})();
