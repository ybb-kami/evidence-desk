// Pure parsing, source binding and DOM smoke test, with optional real scan fixture.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');
const {parse,matchActive,sourceLink,MAX_BYTES}=require('./scan-sidecar.js');
const hash='a'.repeat(64), unsafe='<img src=https://example.invalid>';
const test={
  schema_version:1,source:{sha256:hash,page_count:8,filename:'ignore.pdf'},
  render:{dpi:240,frame:'rotated_visible_page_top_left'},
  pages:[{physical_page:6,width_px:200,height_px:260,ocr_status:'words_found',
    lines:[{text:'this whole page must not be projected'}],
    regions:[{type:'table',status:'structure_recognized',bbox:[5,6,190,200],crop:'../../private.png',
      cells:[{row:1,column:1,bbox:[5,6,80,50],status:'ocr_text',text:unsafe},
             {row:1,column:2,bbox:[80,6,190,50],status:'empty_or_unread',text:'unstated value'}]},
      {type:'formula',status:'detected_only',bbox:[18,222,37,242],text:'unreliable text',crop:'https://example.invalid/x'}]}]};
const identity={id:hash,pages:8,name:'active.pdf'};
const load=obj=>parse(JSON.stringify(obj));
const sidecar=load(test), table=sidecar.pages[0].regions[0];
assert.equal(matchActive(sidecar,[identity]).name,identity.name);
assert.equal(table.cells[0].text,unsafe);
assert.equal(table.cells[1].text,'');
assert.deepEqual(sidecar.pages[0].regions[1],{type:'formula',status:'detected_only',bbox:[18,222,37,242]});
assert.equal(sourceLink(hash,6),'/pdf/'+hash+'#page=6');
assert.doesNotMatch(JSON.stringify(sidecar),/private.png|this whole page|unreliable text|unstated value/);
assert.throws(()=>matchActive(sidecar,[]),/没有字节 SHA/);
assert.throws(()=>matchActive(sidecar,[{...identity,pages:7}]),/页数/);
assert.throws(()=>sourceLink('../storage',6),/结构/);
assert.throws(()=>parse('x'.repeat(MAX_BYTES+1)),/8 MiB/);
for(const mutation of [
  j=>j.schema_version=2,
  j=>j.render.frame='wrong',
  j=>j.source.sha256='https://example.invalid',
  j=>j.pages[0].physical_page=9,
  j=>j.pages.push(j.pages[0]),
  j=>j.pages[0].regions[0].bbox[0]=-1,
  j=>j.pages[0].regions[0].bbox[2]=500,
  j=>j.pages[0].regions[0].cells[0].bbox[0]=0,
  j=>j.pages[0].regions[0].cells[0].row=101,
  j=>j.pages[0].regions[0].cells[1].column=1,
  j=>j.pages[0].regions[1].status='invalid',
]){
  const j=structuredClone(test);
  mutation(j);
  assert.throws(()=>load(j),/结构/);
}

class Node {
  constructor(tag,uri) {this.tag=tag;this.namespaceURI=uri;this.children=[];this.attrs={};this._text='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(k,v){this.attrs[k]=String(v);this[k]=String(v);}
  set textContent(text){this._text=text;this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
}
const panel=new Node('section'), calls=[];
const document={getElementById:id=>id==='scan-sidecar-panel'?panel:null,createElement:tag=>new Node(tag),
  createElementNS:(uri,tag)=>new Node(tag,uri)};
let docs=[identity];
const fetch=async (url,options)=>{
  calls.push({url,options});
  return {ok:true,json:async()=>({docs})};
};
vm.runInNewContext(fs.readFileSync('scan-sidecar.js','utf-8'),{document,fetch,TextEncoder,TextDecoder});
const find=(node,tag)=>[...(node.tag===tag?[node]:[]),...node.children.flatMap(c=>find(c,tag))];
(async()=>{
  const file=find(panel,'input')[0];
  file.files=[{size:1000,arrayBuffer:async()=>new TextEncoder().encode(JSON.stringify(test)).buffer}];
  await file.onchange();
  assert.match(panel.textContent,/SHA-256.*活动原件/);
  assert.match(panel.textContent,/仅定位区框，未可靠转写/);
  assert.match(panel.textContent,/空白或未读/);
  assert.ok(panel.textContent.includes(unsafe));
  assert.equal(find(panel,'img').length,0);
  assert.equal(find(panel,'a')[0].href,'/pdf/'+hash+'#page=6');
  assert.equal(find(panel,'td').length,2);
  assert.ok(calls.every(c=>c.url==='/api/library'&&!('body' in c.options)));
  docs=[];
  await file.onchange();
  assert.equal(find(panel,'a').length,0);
  assert.match(panel.textContent,/没有字节 SHA/);

  if(process.argv.length===4){
    const real=parse(fs.readFileSync(process.argv[2],'utf-8'));
    const exact=crypto.createHash('sha256').update(fs.readFileSync(process.argv[3])).digest('hex');
    assert.equal(exact,real.sourceSha256);
    assert.equal(real.pageCount,15);
    const active=matchActive(real,[{id:exact,pages:15,name:'mirror.pdf'}]);
    const region=(page,index=0)=>real.pages.find(x=>x.page===page).regions[index];
    assert.equal(region(6).cells.length,20);
    assert.equal(region(6).cells[0].text,'Layer Type');
    assert.equal(region(8).status,'detected_only');
    assert.equal(region(9,1).cells.length,36);
    assert.equal(region(7).status,'detected_only');
    assert.equal(sourceLink(active.id,6),'/pdf/'+exact+'#page=6');
    console.log('Actual mirror: SHA matches, page 6 5×4, page 8 frame only, page 9 12×3, page 7 formula frame only');
  }
  console.log('Sidecar validation, local-only read, active source binding, table and frame rendering passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
