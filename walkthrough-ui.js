/* Intentional, one-click local walkthrough on original, openly labelled fiction. */
(()=>{'use strict';const root=document.getElementById('judge-demo');if(!root)return;
const mk=(t,s)=>{const n=document.createElement(t);n.textContent=s;return n},run=mk('button','加入两篇原创样例并运行'),out=mk('div','');
run.type='button';root.append(mk('h2','三分钟验收：同一句话，哪篇有证据？'),mk('p','点击会将两篇明确标为虚构的原创 PDF 加入本机文库，只在这两篇上查询 amber lantern。问答范围不含其他文献、不用模型、不出网；重复运行不新增重复记录。'),run,out);
async function req(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error('本机服务未完成此步（'+r.status+'）');return r.json()}
run.onclick=async()=>{run.disabled=true;out.replaceChildren(mk('p','正在装入原创 PDF、运行并核原页…'));
try{const lib=await req('/api/demo-import',{confirm:true}),ids=lib.docs.map(x=>x.id);const trace=await req('/api/workflow',{ids,query:'amber lantern'});
out.replaceChildren(mk('p','本次样例：'+lib.docs.map(d=>d.name).join('、')),mk('p',trace.summary));
for(const paper of trace.papers){
out.append(mk('h3',paper.name));const excerpts=paper.evidence||paper.verified||paper.hits||[];
for(const hit of excerpts){const a=mk('a','查看原件第 '+hit.page+' 页，第 '+hit.block+' 块');a.href='/pdf/'+paper.id+'#page='+hit.page;a.target='_blank';a.rel='noopener';out.append(mk('blockquote',hit.text),a)}
if(!excerpts.length)out.append(mk('p','未有通过原件核验的摘录。不代表文中绝无相关内容。'))}
const ol=mk('ol','');for(const s of trace.steps)ol.append(mk('li',s.label+'：'+s.status));out.append(ol);
document.dispatchEvent(new Event('evidence-desk:library'));
}catch(e){out.replaceChildren(mk('p',e.message))}finally{run.disabled=false}};
})();
