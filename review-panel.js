(()=>{'use strict';
const root=document.getElementById('local-review');if(!root)return;
function node(t,s){const n=document.createElement(t);n.textContent=s;return n}
const btn=node('button','生成本库综述提要'),out=node('div','');out.setAttribute('aria-live','polite');
root.append(node('h2','从引用到综述提要'),node('p','仅使用当前活动文库；附原页，不联网推测前沿。'),btn,out);
function anchor(row){
  const a=node('a',row.source_document_name+' · PDF 第 '+row.page+' 页，第 '+row.block+' 块');
  a.href='/pdf/'+encodeURIComponent(row.source_document)+'#page='+row.page;
  a.target='_blank';a.rel='noopener';
  return a;
}
btn.onclick=async()=>{btn.disabled=true;out.replaceChildren(node('p','正在分析本地文库…'));
try{const r=await fetch('/api/local-review',{cache:'no-store'});if(!r.ok)throw Error('读取本地文库失败');const data=await r.json();
out.replaceChildren(node('p',data.summary));
for(const [label,rows] of [['可核对的引用',data.edges],['含未来工作措辞的候选摘录',data.future_mentions]]){
out.append(node('h3',label));if(!rows.length)out.append(node('p','没有可核对的条目。'));
for(const row of rows){if(row.target)out.append(node('p',row.source+' → '+row.target));out.append(node('blockquote',row.text),anchor(row))}}
out.append(node('h3','可能相关的后续阅读线索'),
  node('p','仅在本地确有引文方向、两个首页 arXiv 版本日期可核对，且正文词项有明显重合时列出。引文连到作品，不一定引用此版本。线索不表示旧问题已经解决，也不是舍弃原想法的依据。'));
const leads=Array.isArray(data.later_candidates)?data.later_candidates:[];
if(!leads.length)out.append(node('p','未找到满足上述条件的条目，不代表没有后续研究。'));
for(const lead of leads){
  const group=node('section','');group.className='evidence';
  group.append(node('p','正文共享词项：'+lead.matched_terms.join('、')));
  for(const [label,row] of [['早期摘录',lead.direction],['后续候选正文',lead.later_excerpt],['引文方向原条目',lead.citation],
                            ['早期首页版本标记',lead.earlier_stamp],['后续首页版本标记',lead.later_stamp]]){
    group.append(node('h4',label+(row.date?' · '+row.date:'')),node('blockquote',row.text),anchor(row));
  }
  out.append(group);
}
out.append(node('p',data.limits));}catch(e){out.replaceChildren(node('p',e.message))}finally{btn.disabled=false}}})();
