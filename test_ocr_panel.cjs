// Minimal DOM/fetch exercise. This verifies UI behavior, not browser layout.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Node {
  constructor(tag) { this.tag=tag; this.children=[]; this.attrs={}; this.value=''; this._text=''; }
  setAttribute(name,value) { this.attrs[name]=String(value); this[name]=String(value); }
  set textContent(value) { this._text=value; this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
}
const panel=new Node('section'), calls=[], timers=new Map();
const all=(node,test)=>[...(test(node)?[node]:[]),...node.children.flatMap(c=>all(c,test))];
const byId=id=>all(panel,node=>node.id===id)[0];
const original='a'.repeat(64), task='b'.repeat(32);
const doc={id:original,name:'<img src=x>.pdf',pages:2,size:1024,textless_pages:[1,2]};
const response=data=>Promise.resolve({ok:true,json:async()=>data});
const document={getElementById:id=>id==='local-ocr-panel'?panel:null, createElement:tag=>new Node(tag), hidden:false,
                addEventListener:(name,action)=>{document[name]=action;}};
let job={status:'idle'}, submitted, index=0;
const fetch=(path,options={})=>{
  calls.push({path,options});
  if (path==='/api/library') return response({docs:[doc,{...doc,id:'c'.repeat(64),textless_pages:[1]}]});
  if (path==='/api/ocr' && options.method==='POST') return new Promise(resolve=>{submitted=()=>resolve(response(job));});
  if (path==='/api/ocr') return response(job);
  throw Error('Unexpected fetch: '+path);
};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
vm.runInNewContext(fs.readFileSync('ocr-panel.js','utf8'),{
  document,fetch,setTimeout:action=>{ const id=++index;timers.set(id,action);return id; },clearTimeout:id=>timers.delete(id)
});
(async()=>{
  await flush();
  const start=byId('local-ocr-start'), select=byId('local-ocr-source');
  assert.equal(select.children.length,2);
  assert.equal(start.disabled,true);
  await start.onclick();
  assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
  select.value=original; select.onchange();
  const first=start.onclick();
  await start.onclick();
  assert.equal(calls.filter(c=>c.options.method==='POST').length,1);
  assert.deepEqual(JSON.parse(calls.find(c=>c.options.method==='POST').options.body),{id:original});
  job={status:'running',job_id:task,source_name:doc.name,source_id:original};
  submitted();
  await first;
  assert.equal(start.disabled,true);
  assert.match(panel.textContent,/正在本机识别/);
  assert.equal(timers.size,1);
  document.hidden=true;
  document.visibilitychange();
  assert.equal(timers.size,0);
  document.hidden=false;
  job={...job,status:'succeeded',filename:'copy.ocr.pdf'};
  document.visibilitychange();
  await flush();
  const links=all(panel,n=>n.tag==='a');
  assert.equal(links.length,2);
  assert.equal(links[0].href,'/api/ocr/result/'+task);
  assert.equal(links[0].download,'copy.ocr.pdf');
  assert.equal(links[1].href,'/pdf/'+original);
  assert.match(panel.textContent,/尚未导入/);
  assert.equal(all(panel,n=>n.tag==='img').length,0);
  assert.ok(calls.every(c=>c.path==='/api/ocr'||c.path==='/api/library'));
  console.log('OCR UI manual submission, exclusive-job state, visibility and download passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
