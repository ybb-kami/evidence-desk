// Offline, minimal DOM harness for the reader's state transitions; not a browser layout test.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
let focused;
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.attrs = {}; this.classList = {add() {}}; this._text = ""; }
  setAttribute(k,v) { this.attrs[k] = String(v); if (k === "id") this.id = v; }
  addEventListener(name,fn) { this.events[name] = fn; }
  append(...children) { for (const child of children) this.children.push(...(child?.tag === "#fragment" ? child.children : [child])); }
  replaceChildren(...children) { this.children = []; this.append(...children); if (this.tag === "select") this._value = undefined; }
  set textContent(x) { this._text = x; this.children = []; }
  get textContent() { return this._text + this.children.map(x => typeof x === "string" ? x : x.textContent).join(""); }
  get value() { return this._value ?? this.children[0]?.attrs.value ?? ""; }
  set value(v) { this._value = v; }
  get selectedOptions() { return this.children.filter(x=>x.attrs.value===this.value); }
  focus() { focused = this; }
  scrollIntoView() {}
  querySelector(selector) { return find(this, n => n.attrs.class === selector.slice(1)); }
}
function find(node, pred) {
  if (typeof node === "string") return;
  if (pred(node)) return node;
  for (const c of node.children) { const result = find(c, pred); if (result) return result; }
}
const root = new Node("section");
root.id = "reader";
const id = "a".repeat(64);
const document = {
  createElement: tag => new Node(tag),
  createDocumentFragment: () => new Node("#fragment"),
  getElementById: id => find(root, n => n.id === id),
  addEventListener() {},
};
const data = {
  doc: {name:"Fixture"},
  pages:[
    {page:1,label:"1",blocks:[{id:"body",text:"😀 [11] and [30, 99]",heading:"Intro",
      citations:[{id:"cite",start:2,end:6,numbers:[11]}]}]},
    {page:2,label:"ii",blocks:[{id:"bib",text:"[11] <img src=bad>",
      entries:[{id:"ref",start:0,end:4}]}]}
  ],
  outline:[{page:1,id:"body",title:"Intro",level:2}],
  references:[{number:11,page:2,id:"ref",text:"[11] <img src=bad>",occurrences:[{id:"cite",page:1}]}],
  ambiguous_numbers:[],
  occurrences:1,
};
const fetch = async path => ({ok:true,json:async()=>path==="/api/library" ? {docs:[{id,name:"Fixture"}]} : data});
vm.runInNewContext(fs.readFileSync("reader.js","utf8"), {document,fetch,AbortController});
const click = text => find(root,n=>n.tag==="button" && n.textContent.includes(text)).events.click();
(async () => {
  await new Promise(resolve=>setImmediate(resolve));
  await click("打开阅读");
  click("Intro");
  assert.equal(focused.id,"reader-body");
  assert.equal(document.getElementById("reader-cite").textContent,"[11]");
  assert.match(focused.textContent,/\[30, 99\]/);
  click("[11]");
  assert.match(focused.textContent,/<img src=bad>/);
  assert.equal(find(root,n=>n.tag==="img"),undefined);
  click("定位参考条目");
  assert.equal(focused.id,"reader-ref");
  assert.equal(focused.textContent,"[11]");
  assert.match(find(root,n=>n.tag==="a").href,/#page=2$/);
  click("返回刚才的正文引用");
  assert.equal(focused.id,"reader-cite");
  click("定位正文");
  assert.equal(focused.id,"reader-cite");
  assert.match(find(root,n=>n.tag==="a").href,/#page=1$/);
  await click("打开阅读");
  click("[11]");
  click("定位参考条目");
  assert.equal(focused.id,"reader-ref");
  console.log("reader DOM round trip, repeated open, Unicode and text rendering PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
