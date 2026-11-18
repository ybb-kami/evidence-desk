"use strict";
const assert = require("node:assert/strict");
const {parts, translateWithTerms, architectureNames, parseGlossary} = require("./reader.js");
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

for (const token of ["<pad>","<EOS>"]) {
  assert.deepEqual(parts({text:token,protected:[]},0,token.length),[{text:token,translate:false}]);
}

const {denseNumericTable} = require("./reader.js");
assert(denseNumericTable("base\n6\n0.1\n0.1\n100K"));
assert(!denseNumericTable("This experiment uses 12 people.\nOne group has 6 participants.\n6"));

const long = "sample ".repeat(29) + "convolutional neural networks keep running.";
const split = parts({text:long,protected:[]},0,long.length,[],"en-zh");
assert.equal(split.map(p=>p.text).join(""),long);
assert(split.some(p=>p.text.includes("convolutional neural networks")));
const code = "`label smoothing`";
assert.deepEqual(parts({text:code,protected:[]},0,code.length,[],"en-zh"),[{text:code,translate:false}]);
const title = "Label Smoothing\nDuring training, we enabled label smoothing.";
assert.deepEqual(parts({text:title,protected:[]},0,title.length,[],"en-zh").map(p=>p.text),
  ["Label Smoothing\n","During training, we enabled label smoothing."]);
const names = architectureNames([
  {text:"We propose a new network architecture, the WaveGate, based on attention."},
  {text:"We propose a network architecture, the Transformer, based solely on attention."},
  {text:"我们提出一种新的模型，称为 CodeNet。"},
  {text:"我们评估名为 WaveTune 的网络架构。"},
  {text:"Our model is called LiftNet-2.5."},
  {text:"A reference about a model called OtherModel.", reference:true},
  {text:"The model called for additional input."},
  {text:"We introduce a network architecture, the Neural Network, which works."},
  {text:"A transformer supplies the network. Transformer oil insulates the winding."},
]);
assert.deepEqual(names,["WaveGate","Transformer","CodeNet","WaveTune","LiftNet-2.5"]);
assert.equal(architectureNames(Array.from({length:40}, (_,i)=>({text:`A model called Model${i}.`}))).length,24);
assert.deepEqual(parseGlossary("  BERT \nDIN\nBERT\n"),["BERT","DIN"]);
assert.throws(()=>parseGlossary("手".repeat(81)),/80/);
assert.throws(()=>parseGlossary(Array.from({length:33}, (_,i)=>String(i)).join("\n")),/32/);
assert.throws(()=>parseGlossary(" ".repeat(2049)),/2048/);

async function testTerms() {
  const forbidden = () => assert.fail("An exact term should not call the model.");
  assert.equal(await translateWithTerms("Scaled Dot-\nProduct Attention","en-zh",forbidden),"缩放点积注意力");
  assert.equal(await translateWithTerms("WaveGate","en-zh",forbidden,names),"WaveGate");
  assert.equal(await translateWithTerms("CodeNet","zh-en",forbidden,names),"CodeNet");
  assert.equal(await translateWithTerms("The attention decoder uses the Transformer.","en-zh",
      async () => "注意力解码器使用变形器。",names),"注意力解码器使用Transformer。");
  assert.equal(await translateWithTerms("Transformer oil insulates the winding.","en-zh",
      async () => "变压器油使绕组绝缘。",names),"变压器油使绕组绝缘。");
  assert.equal(await translateWithTerms("卷积神经网络对局部空间结构建模。","zh-en",
      async () => "Volatile neural networks model local spatial structures."),
      "Convolutional neural networks model local spatial structures.");
  assert.equal(await translateWithTerms("我们采用多头注意力。","zh-en",
      async () => "We use multiple attention."),"We use multi-head attention.");
  const markerInput = "Test label smoothing with multi-head attention.";
  assert.equal(await translateWithTerms(markerInput,"en-zh",async value => {
    assert.equal(value, markerInput);
    return "用多头注意测试标牌的平滑。";
  }),"用多头注意力测试标签平滑。");
  assert.equal(await translateWithTerms(markerInput,"en-zh",async value => ({
    [markerInput]:"尝试标记技术。",
    "Test [TERM1] with [TERM2].":"结合[TERM2]测试[TERM1]。",
  })[value]),"结合多头注意力测试标签平滑。");
  assert.equal(await translateWithTerms(markerInput,"en-zh",async value => ({
    [markerInput]:"尝试标记技术。",
    "Test [TERM1] with [TERM2].":"[TERM2]和[TERM2]。",
    "Test":"测试",
    "with":"配合",
  })[value]),"测试 标签平滑 配合 多头注意力.");
  for (const [value,direction] of [["The writer smooths a paper label.","en-zh"],["label smoothing","zh-en"]]) {
    assert.equal(await translateWithTerms(value,direction,async original => {
      assert.equal(original,value);
      return "MODEL";
    }),"MODEL");
  }
  console.log("Academic terms, marker validation, fallback and direction scoping verified.");
}

testTerms().catch(error => { console.error(error); process.exitCode = 1; });
