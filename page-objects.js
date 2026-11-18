(()=>{'use strict';
const r=document.getElementById('page-objects');if(!r)return;
const el=(t,s)=>{const n=document.createElement(t);n.textContent=s;return n},pick=el('select',''),page=el('input',''),refresh=el('button','刷新文库'),go=el('button','分析本页'),out=el('div','');
pick.setAttribute('aria-label','选择本地文献');page.type='number';page.min=1;page.max=650;page.value=1;page.setAttribute('aria-label','物理页码');
r.append(el('h2','逐页图表与公式线索'),el('p','按物理页查看题注、图像位置、表格同行片段与含数学符号的原文；不会反推 LaTeX。'),refresh,pick,page,go,out);
refresh.onclick=async()=>{try{const res=await fetch('/api/library');if(!res.ok)throw Error();const lib=await res.json();pick.replaceChildren(...lib.docs.map(d=>{const o=el('option',d.name);o.value=d.id;return o}));if(!lib.docs.length)out.replaceChildren(el('p','请先导入文献。'))}catch(_){out.replaceChildren(el('p','未能读取文库。'))}};
go.onclick=async()=>{const id=pick.value,n=Number(page.value);if(!id||!Number.isInteger(n)||n<1||n>650){out.replaceChildren(el('p','请刷新文库，选择文献并输入有效页码。'));return}
go.disabled=true;try{const res=await fetch('/api/page-objects/'+encodeURIComponent(id)+'/'+n);if(!res.ok)throw Error();const d=await res.json(),a=el('a','打开原 PDF 第 '+n+' 页');a.href='/pdf/'+id+'#page='+n;a.target='_blank';a.rel='noopener';out.replaceChildren(a);
for(const [k,rows] of [['题注',d.captions.map(x=>x.text)],['表格同行片段（未恢复单元格语义）',d.table_rows.map(x=>x.fragments.map(s=>s.text).join('  |  '))],['嵌入图片位置',d.images.map(x=>x.pixels.join('×')+' 像素；边界 '+x.bbox.join(', '))],['疑似数学表达',d.math_candidates.map(x=>x.text)]]){out.append(el('h3',k));for(const s of (rows.length?rows:['未识别到。']))out.append(el('p',s))}
}catch(_){out.replaceChildren(el('p','无法处理该物理页；请核对页数。'))}finally{go.disabled=false}};
})();
