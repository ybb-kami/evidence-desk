// Local DOM contract, no external network and no claim of browser paint verification.
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
let downloads=[], blobs=[];
class Node {
  constructor(tag) {this.tag=tag;this.children=[];this.events={};this.attrs={};this.style={};this._text='';this.value='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  set textContent(value){this._text=value;this.children=[];}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,fn){this.events[k]=fn;}
  click(){downloads.push(this.download);}
  remove(){}
}
const root=new Node('section'), id='a'.repeat(64);
const doc={id,name:'Local Fixture.pdf'}, document={
  getElementById:k=>k==='evidence-workflow'?root:null,createElement:t=>new Node(t),body:new Node('body')
};
let calls=[], pending;
const response=data=>({ok:true,json:async()=>data});
const fetch=(url,opts)=>{
  calls.push([url,opts]);
  if(url==='/api/library')return Promise.resolve(response({docs:[doc]}));
  return new Promise(resolve=>{pending=data=>resolve(response(data));});
};
const URL={createObjectURL:b=>{blobs.push(b);return 'blob:mock';},revokeObjectURL(){}};
vm.runInNewContext(fs.readFileSync('workflow-panel.js','utf-8'),{document,fetch,AbortController,Blob,URL,setTimeout:()=>{}});
const nodes=(tag,rootNode=root)=>[...(rootNode.tag===tag?[rootNode]:[]),...rootNode.children.flatMap(child=>nodes(tag,child))];
const data={
  schema:'evidence-desk-workflow-v1',status:'verified_evidence',summary:'只含已核证据。',notes:[],
  concepts:{encoder:'Encoder'},steps:[{id:'plan',label:'锁定范围',status:'completed',paper_count:1}],
  papers:[{...doc,status:'verified_evidence',failures:[],evidence:[
    {text:'Verified <img src=bad> Encoder',page:1,block:2,part:1,offset:0,concept_ids:['encoder']}
  ]}]
};
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const query=nodes('input').find(n=>n.id==='wf-query'),box=nodes('input').find(n=>n.type==='checkbox');
  const submit=()=>nodes('form')[0].onsubmit({preventDefault(){}});
  const download=nodes('button').find(n=>n.textContent.includes('机器可读'));
  assert.match(root.textContent,/执行计划.*SHA-256/);
  query.value='Encoder';query.events.input();
  await submit();assert.equal(calls.length,1);
  box.checked=true;box.events.change();
  const stale=submit();
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body),{query:'Encoder',ids:[id]});
  query.value='newer Encoder';query.events.input();
  assert.equal(calls.at(-1)[1].signal.aborted,true);
  pending(data);await stale;
  assert.equal(nodes('blockquote').length,0);
  const active=submit();pending(data);await active;
  assert.equal(download.disabled,false);
  assert.equal(nodes('img').length,0);
  assert.match(root.textContent,/Verified <img src=bad> Encoder/);
  assert.ok(nodes('a').some(a=>a.href==='/pdf/'+id+'#page=1'));
  download.onclick();
  assert.deepEqual(downloads,['evidence-desk-workflow.json']);
  assert.deepEqual(JSON.parse(await blobs[0].text()),data);
  const refusal=submit();
  pending({...data,status:'insufficient_evidence',summary:'没有通过原件核验的摘录，不提供提要。',papers:[
    {...doc,status:'verification_failed',evidence:[],failures:[{reason:'source_hash_mismatch',page:1,block:2}]}
  ]});
  await refusal;
  assert.match(root.textContent,/不提供提要/);
  assert.match(root.textContent,/原件 SHA-256 不符/);
  assert.equal(nodes('blockquote').length,0);
  assert.equal(download.disabled,false);
  console.log('Workflow UI preflight, stale result, verified anchor, blocked evidence and JSON export passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
