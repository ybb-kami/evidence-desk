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
