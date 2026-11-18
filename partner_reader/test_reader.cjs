"use strict";
const assert = require("node:assert/strict");
const {parts} = require("./reader.js");
const text = "😀The model uses x = 2 + 3 and `fit(x)`.\nconst y = 5;\n$z_1$ and 计算。";
const slices = parts({text,protected:[]},0,text.length);
assert.equal(slices.map(p=>p.text).join(""),text);
for (const literal of ["x = 2 + 3","`fit(x)`","const y = 5;","$z_1$"])
  assert(slices.some(p => !p.translate && p.text.includes(literal)),literal);
const chinese = "😀甲编码乙";
const pick = parts({text:chinese,protected:[[2,4]]},2,5);
assert.deepEqual(pick,[{text:"甲",translate:true},{text:"编码",translate:false}]);
assert.equal(parts({text:"a".repeat(500),protected:[]},0,500).length,3);
console.log("Protected ranges, source offsets, and inference length checks passed.");
const {TranslationBook,sourceSignature,bilingualHtml} = require("./export.js");
const book = new TranslationBook();
const source = {block:2,bbox:[0,0,2,2],text:"plain <script>model</script>",protected:[]};
const entry = {doc:"abc",direction:"en-zh",page:2,block:2,order:0,original:source.text,
                translation:"<img onerror=alert(1)>",start:0,end:source.text.length,
                startChar:0,endChar:source.text.length,selected:false,signature:sourceSignature(source)};
book.save(entry);book.save(entry);
book.save({...entry,selected:true,start:1,startChar:1,original:"lain"});
book.save({...entry,doc:"another"});
book.save({...entry,direction:"zh-en"});
assert.equal(book.list("abc","en-zh").length,2);
const output=bilingualHtml("<paper>","abc","en-zh",book.list("abc","en-zh"));
assert(!output.includes("<script>"));
assert(!output.includes("<img onerror"));
assert(output.includes("&lt;paper&gt;") && output.includes("选区") && output.includes("整段"));
assert.equal(book.reconcile("abc",{page:2,segments:[source]}),0);
assert.equal(book.reconcile("abc",{page:2,segments:[{...source,text:"changed source"}]}),3);
assert.equal(book.list("another","en-zh").length,1);
const prose = "We study A+B and A+B.";
const protectedTerms = parts({text:prose,protected:[]},0,prose.length,["A+B"]);
assert.equal(protectedTerms.filter(p=>!p.translate && p.text==="A+B").length,2);
console.log("HTML escaping, separate ranges, source invalidation and literal terminology verified.");
for(const formula of ['softmax(QKᵀ/√d)V','∑ᵢ yᵢ log pᵢ','f(x) = x² + β']){
 const line='A plain font expression: '+formula+'.', protectedParts=parts({text:line,protected:[]},0,line.length);
 assert.equal(protectedParts.filter(x=>x.translate).length,0);
 assert.equal(protectedParts.map(x=>x.text).join(''),line);
}
const sourceId='a'.repeat(64);
assert.ok(bilingualHtml('Title',sourceId,'en-zh',[entry],'http://127.0.0.1:8765').includes('href="http://127.0.0.1:8765/pdf/'+sourceId+'#page=2"'));
assert.ok(!bilingualHtml('Title',sourceId,'en-zh',[entry],'https://external.invalid').includes('external.invalid'));
console.log('Plain-font math line protection and safe local source links passed.');
