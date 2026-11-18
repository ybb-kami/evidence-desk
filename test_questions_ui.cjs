// Local DOM state test; deliberately does not claim browser rendering.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Node {
  constructor(tag) { this.tag=tag; this.value='';this.children=[];this.events={};this.attrs={};this.style={};this._text=''; }
  set textContent(text) { this._text=text;this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
  setAttribute(k,v) {this.attrs[k]=String(v);}
  addEventListener(k,handler) {this.events[k]=handler;}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
}
function descendants(root,predicate) {
  return [...(predicate(root)?[root]:[]),...root.children.flatMap(c=>descendants(c,predicate))];
}
const root=new Node('section'), id='a'.repeat(64);
const document={getElementById: value=>value==='evidence-questions'?root:undefined,createElement:tag=>new Node(tag)};
const doc={id,name:'Fixture.pdf',arxiv:{id:'1234.12345',version:1}};
const payload={status:'evidence_found',concepts:[{id:'bleu',label:'BLEU',terms:['BLEU']}],
  notes:['发现不同的数值，不选定唯一分数。'],observations:[
    {id,task:'en-fr',value:'32.0',page:1,block:4},
    {id,task:'en-fr',value:'33.5',page:3,block:5}
  ],papers:[{...doc,hits:[
    {id,page:1,block:4,concept_ids:['bleu'],text:'First <img src=x>'},
    {id,page:3,block:5,concept_ids:['bleu'],text:'Second'}
  ]}]};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
let calls=[], settle;
const answer=data=>({ok:true,json:async()=>data});
const fetch=(url,args)=>{
  calls.push({url,args});
  if(url==='/api/library') return Promise.resolve(answer({docs:[doc]}));
  return new Promise(resolve=>{settle=data=>resolve(answer(data));});
};
vm.runInNewContext(fs.readFileSync('questions.js','utf8'),{document,fetch,AbortController});
const find=tag=>descendants(root,n=>n.tag===tag);
const submit=()=>find('form')[0].onsubmit({preventDefault(){}});
const requestResults=()=>calls.filter(c=>c.url==='/api/questions');
(async()=>{
  await tick();
  const box=find('input').find(n=>n.type==='checkbox');
  const query=find('input').find(n=>n.type==='search');
  query.value='英法BLEU';query.events.input();
  await submit();
  assert.equal(requestResults().length,0);
  box.checked=true;box.events.change();
  const first=submit();
  await submit();
  assert.equal(requestResults().length,1);
  assert.deepEqual(JSON.parse(requestResults()[0].args.body),{query:'英法BLEU',ids:[id]});
  query.value='updated'; query.events.input();
  assert.equal(requestResults()[0].args.signal.aborted,true);
  settle(payload);await first;
  assert.doesNotMatch(root.textContent,/32.0|33.5/);
  const second=submit(); settle(payload); await second;
  assert.match(root.textContent,/32.0.*33.5/);
  assert.match(root.textContent,/不选定唯一分数/);
  assert.match(root.textContent,/<img src=x>/);
  assert.equal(find('img').length,0);
  assert.ok(find('a').some(a=>a.href==='/pdf/'+id+'#page=3'));
  const third=submit();
  settle({...payload,status:'insufficient_evidence',observations:[],notes:[],papers:[{...doc,hits:[]}]});
  await third;
  assert.match(root.textContent,/证据不足.*这篇文献暂无匹配证据/);
  assert.equal(find('a').length,0);
  console.log('Question UI scope, duplicate submit, cancellation, multiple observations and insufficient-evidence states passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
