/* Local user-specified research design; no network, no invented outcome. */
(function(root){
'use strict';
function clean(s,n=160){return String(s||'').normalize('NFKC').replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g,' ').trim().slice(0,n)}
function markdown(s){return clean(s).replace(/[\\`*_\[\]<>#{}|]/g,c=>'\\'+c)}
function label(s){return [...clean(s,85)].map(ch=>/[\p{Letter}\p{Number} ,.·，。；：（）()/-]/u.test(ch)?ch:' ').join('').replace(/\s+/g,' ').trim()}
function build(fields){
 const keys=['question','material','method','baseline','measure'];const v={};
 for(const k of keys){v[k]=clean(fields[k]);if(!v[k])throw Error('请补全研究问题、数据、方法、对照和指标，未填写的内容不能推断。')}
 const values=Object.fromEntries(keys.map(k=>[k,markdown(v[k])]));
 const m=['# 研究设计草案：'+values.question,'> 下列是使用者输入的计划，尚无实验结果。不可把“拟比较”改写为“已优于”。','## Abstract · 研究计划','研究问题：'+values.question+'。拟使用 '+values.material+'，比较 '+values.method+' 与 '+values.baseline+'，观察指标为 '+values.measure+'。结果与限制待验证后补充。','## Introduction · 要证伪什么','说明 '+values.question+' 对何种对象成立；预先说明何种观测不支持研究设想。','## Related Work · 文献待核','分别对照涉及 '+values.method+' 与 '+values.baseline+' 的原文，记录任务、数据、报告指标和局限；此处未引入任何外部文献。','## Methods · 可复现对照','数据/样本：'+values.material+'。对照方案：'+values.baseline+'；待评估方案：'+values.method+'。统一数据切分与评价条件，记录 '+values.measure+' 的定义、单位、分母及缺失处理。','## Results & Limitations','尚无经核实的结果。列出每个对照与对应证据，区分观察、推论和因果。','## References','未自动生成书目。请从「论文框架和证据清单」附可核对的原件。'].join('\n\n')+'\n';
 const defs=[['Q','问题',v.question],['D','数据',v.material],['M','方法',v.method],['B','对照',v.baseline],['E','指标',v.measure]];
 const diagram=['flowchart LR',...defs.map(([id,p,s])=>'  '+id+'["'+p+'：'+label(s)+'"]'),'  Q --> D','  D --> M','  D --> B','  M --> E','  B --> E',''].join('\n');
 return {markdown:m,diagram};
}
const api={build};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.EvidenceDeskResearchPlan=api;
})(globalThis);
