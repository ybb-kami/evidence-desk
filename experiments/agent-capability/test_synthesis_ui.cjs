const A=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{constructor(tag){this.tag=tag;this.children=[];this.events={};this.style={}} append(...c){this.children.push(...c)}prepend(...c){this.children.unshift(...c)}replaceChildren(){this.children=[]}addEventListener(k,f){this.events[k]=f}querySelectorAll(){return walk(this,n=>n.checked)}}
function walk(n,f){return [...(f(n)?[n]:[]),...n.children.flatMap(c=>walk(c,f))]}
(async()=>{let root,online=false,calls=[],refs={E1:{doc_id:'a'.repeat(64),page:2,block:12,text:'First source.'},E2:{doc_id:'b'.repeat(64),page:3,block:25,text:'Second source.'}};
let document={querySelector:()=>({after:x=>root=x}),createElement:t=>new Node(t)};
async function fetch(url,o){calls.push(url);let body;if(url.endsWith('library'))body={docs:Object.values(refs).map(x=>({id:x.doc_id,name:'sample'}))};else if(url.endsWith('preview')) body={status:'ready',wire_text:'wire',token:'token',sources:refs,online};else if(url.endsWith('confirm')){A.equal(JSON.parse(o.body).consent,true);body={status:'draft_for_review',claims:[{text:'mock',citations:Object.values(refs)}]}};return {ok:true,json:async()=>body}}
await vm.runInNewContext(fs.readFileSync(__dirname+'/gateway/synthesis-panel.js','utf8'),{document,fetch});
const nodes=t=>walk(root,n=>n.tag===t),btn=s=>nodes('button').find(n=>n.textContent.includes(s));let consent=nodes('input').find(n=>n.onchange),label=nodes('label').find(n=>n.children.includes(consent)),q=nodes('input').find(n=>n.placeholder),send=btn('发送并生成'),preview=btn('核对所选');
for(let i of nodes('fieldset')[0].querySelectorAll())i.checked=true;
walk(nodes('fieldset')[0],n=>n.tag==='input').forEach(i=>i.checked=true);
q.value='encoder';await preview.onclick();A.equal(label.hidden,true);A.equal(send.disabled,true);A.equal(nodes('a').length,2);A.equal(calls.filter(s=>s.endsWith('confirm')).length,0);
online=true;await preview.onclick();A.equal(label.hidden,false);A.equal(send.disabled,true);
consent.checked=true;consent.onchange();A.equal(send.disabled,false);q.events.input();A.equal(send.disabled,true);A.equal(nodes('pre')[0].textContent,'');await send.onclick();A.equal(calls.filter(s=>s.endsWith('confirm')).length,0);
await preview.onclick();consent.checked=true;consent.onchange();await send.onclick();await send.onclick();A.equal(calls.filter(s=>s.endsWith('confirm')).length,1);
A.equal(nodes('article').length,1);A.deepEqual(walk(nodes('article')[0],n=>n.tag==='a').map(a=>a.href),Object.values(refs).map(x=>'/pdf/'+x.doc_id+'#page='+x.page));
console.log('UI state PASS: offline preview, consent, invalidate on change, one-shot confirm, per-source PDF links (simulated DOM).')
})().catch(e=>{console.error(e);process.exitCode=1});
