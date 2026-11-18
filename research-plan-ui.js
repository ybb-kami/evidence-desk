(()=>{'use strict';const root=document.getElementById('research-plan');if(!root)return;
const mk=(t,s)=>{const n=document.createElement(t);n.textContent=s;return n};
const fields=[['question','研究问题'],['material','数据或样本'],['method','拟检验的方法'],['baseline','实际对照'],['measure','指标与单位']];
root.append(mk('h2','从研究想法生成对照方案'),mk('p','输入实际计划后，生成对应章节草案和 Mermaid 数据流图；不编造样本量、结果或书目。'));
const form=mk('form',''),out=mk('textarea',''),status=mk('p','');out.rows=10;out.readOnly=true;out.setAttribute('aria-label','研究计划预览');const entries={};let result=null;
for(const [key,name] of fields){const lab=mk('label',name),input=mk('input','');input.type='text';input.required=true;input.maxLength=160;lab.append(input);form.append(lab);entries[key]=input}
const submit=mk('button','生成计划和研究图');submit.type='submit';form.append(submit);const dm=mk('button','下载研究计划 Markdown'),dg=mk('button','下载此研究的 Mermaid 图');dm.disabled=dg.disabled=true;
function reset(){result=null;dm.disabled=dg.disabled=true;out.value='';status.textContent=''}
form.addEventListener('input',reset);form.onsubmit=e=>{e.preventDefault();reset();try{result=globalThis.EvidenceDeskResearchPlan.build(Object.fromEntries(Object.entries(entries).map(([k,v])=>[k,v.value])));out.value=result.markdown;dm.disabled=dg.disabled=false;status.textContent='生成的是研究计划，不是完成的实验。'}catch(e){status.textContent=e.message}};
function download(type){if(!result)return;const href=URL.createObjectURL(new Blob([result[type]],{type:'text/plain;charset=utf-8'})),a=mk('a','');a.href=href;a.download=type==='markdown'?'研究对照计划.md':'研究对照流程.mmd';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1000)}
dm.onclick=()=>download('markdown');dg.onclick=()=>download('diagram');root.append(form,status,out,dm,dg);
})();
