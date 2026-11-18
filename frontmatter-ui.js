(()=>{'use strict';const root=document.getElementById('frontmatter');if(!root)return;
const mk=(t,s)=>{let n=document.createElement(t);n.textContent=s;return n},pick=mk('select',''),load=mk('button','刷新文库'),go=mk('button','读取所选首页'),out=mk('div','');pick.setAttribute('aria-label','选择查看首页的文献');root.append(mk('h2','作者、单位与摘要原文'),load,pick,go,out);let serial=0;
load.onclick=async()=>{const n=++serial;out.replaceChildren();try{const r=await fetch('/api/library');if(!r.ok)throw Error();const data=await r.json();if(n!==serial)return;pick.replaceChildren(...data.docs.map(d=>{const o=mk('option',d.name);o.value=d.id;return o}))}catch{out.textContent='无法读取本地文库。'}};
pick.onchange=()=>{serial++;out.replaceChildren()};go.onclick=async()=>{const id=pick.value,n=++serial;if(!id){out.textContent='请先刷新并选择文献。';return}try{const res=await fetch('/api/frontmatter/'+id);if(!res.ok)throw Error();const d=await res.json();if(n!==serial)return;out.replaceChildren(mk('h3',d.title||'题名待核'),mk('p',d.note));
for(const row of d.authors)out.append(mk('p',row.name+' · '+row.institution+'（第1页，第'+row.block+'块）'));for(const row of d.authors_without_local_affiliation)out.append(mk('p',row.name+' · 单位未能从同块确认'));if(!d.authors.length&&!d.authors_without_local_affiliation.length)out.append(mk('p','未识别可可靠配对的作者单位块。'));
for(const row of d.abstract_candidates)out.append(mk('blockquote',row.text+'（第1页，第'+row.block+'块）'));if(!d.abstract_candidates.length)out.append(mk('p','未识别显式摘要段落。'));
const a=mk('a','打开首页核对');a.href='/pdf/'+id+'#page=1';a.target='_blank';a.rel='noopener';out.append(a)
}catch{out.textContent='无法读取首页字段。'}};
})();
