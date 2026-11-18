(()=>{'use strict';
const root=document.getElementById('local-review');if(!root)return;
function node(t,s){const n=document.createElement(t);n.textContent=s;return n}
const btn=node('button','生成本库综述提要'),out=node('div','');out.setAttribute('aria-live','polite');
root.append(node('h2','从引用到综述提要'),node('p','仅使用当前活动文库；附原页，不联网推测前沿。'),btn,out);
btn.onclick=async()=>{btn.disabled=true;out.replaceChildren(node('p','正在分析本地文库…'));
try{const r=await fetch('/api/local-review',{cache:'no-store'});if(!r.ok)throw Error('读取本地文库失败');const data=await r.json();
out.replaceChildren(node('p',data.summary));
for(const [label,rows] of [['可核对的引用',data.edges],['含未来工作措辞的候选摘录',data.future_mentions]]){
out.append(node('h3',label));if(!rows.length)out.append(node('p','没有可核对的条目。'));
for(const row of rows){const a=node('a',row.source_document_name+' · PDF 第 '+row.page+' 页，第 '+row.block+' 块');a.href='/pdf/'+encodeURIComponent(row.source_document)+'#page='+row.page;a.target='_blank';a.rel='noopener';if(row.target)out.append(node('p',row.source+' → '+row.target));out.append(node('blockquote',row.text),a)}}
out.append(node('p',data.limits));}catch(e){out.replaceChildren(node('p',e.message))}finally{btn.disabled=false}}})();
