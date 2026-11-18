const assert = require('node:assert/strict');
const {parse, analyze, MAX_BYTES, MAX_CHART} = require('./csv-stats.js');
const fixture = parse('\uFEFFname,score,comment\r\n"a,a",1,"x\r\ny"\r\nb,-3,"say ""hi"""\r\nc,,empty\r\nd,junk,invalid\r\ne,5e0,ok\r\n');
assert.deepEqual(fixture.headers,['name','score','comment']);
assert.deepEqual(fixture.rows[0],['a,a','1','x\r\ny']);
assert.equal(fixture.rows[1][2],'say "hi"');
assert.deepEqual(fixture.physicalLines,[2,4,5,6,7]);
assert.deepEqual(parse('head\n\n5\r\n6').physicalLines,[3,4]);
const stats=analyze(fixture,1,'bar');
assert.deepEqual(stats.visible,[1,-3,null,null,5]);
assert.deepEqual([stats.used,stats.missing,stats.invalid,stats.min,stats.max,stats.mean],[3,1,1,-3,5,1]);
assert.match(stats.caption,/全表共 5 行.*最小 -3，最大 5，算术平均 1/);
assert.match(analyze(fixture,0,'line').caption,/没有可用数值/);
assert.equal(analyze(parse('n\n""\n2\n'),0,'line').missing,1);
assert.throws(()=>parse('a,b\n"x,y'),/未闭合/);
assert.throws(()=>parse('a,b\n"x"x,y'),/引号格式/);
assert.throws(()=>parse('a,b\n1'),/列数/);
assert.throws(()=>parse('a,a\n1,2'),/列名/);
assert.throws(()=>parse('a, \n1,2'),/列名/);
assert.throws(()=>parse('a\n'+'x'.repeat(MAX_BYTES)),/512/);
assert.throws(()=>parse('a\n'+'1\n'.repeat(5001)),/行数/);
assert.throws(()=>parse(Array.from({length:41},(_,i)=>i).join(',')+'\n1'),/列数/);
const exotic=analyze(parse('n\n0\nNaN\nInfinity\n0x10\n1e999\n1e-999\n1,000'.replace('1,000','"1,000"')),0,'line');
assert.equal(exotic.used,1);
assert.equal(exotic.invalid,6);
assert.equal(analyze(parse('n\n1e20\n1\n-1e20'),0,'line').mean,1/3);
const many=analyze(parse('a\n'+Array.from({length:60}, (_,i)=>i).join('\n')),0,'line');
assert.equal(many.visible.length,MAX_CHART);
assert.equal(many.used,60);
assert.match(many.caption,/前 40 条.*60 行/);
console.log('CSV stats tests passed');

const {sha256, figureSVG, mappingJSON, pngAt300Dpi} = require('./csv-stats.js');
const {createHash} = require('node:crypto');
const bytes = Buffer.from('speed\r\n-2.00\r\n""\r\nNaN\r\n0\r\n1.20e2\r\n');
const exact = createHash('sha256').update(bytes).digest('hex');
const metadata = {
  sourceName: '<source&data>.csv', sourceSha256: exact, valueUnit:'m/s', observationUnit:'试次'
};
const ready = analyze(parse(bytes.toString()),0,'bar',metadata);
assert.match(ready.caption,/来源：<source&data>.csv；列：speed；数值单位：m\/s；行单位：试次/);
assert.match(ready.caption,/n = 3 条有效数据行，不代表独立样本数/);
assert.match(ready.caption,/空值 1 个，无法解析 1 个/);
const mapped = JSON.parse(mappingJSON(ready));
assert.equal(mapped.source.sha256,exact);
assert.equal(mapped.summary.totalRows,5);
assert.deepEqual(mapped.rows.map(row=>row.sourceLine),[2,3,4,5,6]);
assert.deepEqual(mapped.rows.map(row => [row.dataRow,row.cell,row.value,row.status,row.plotted]),[
  [1,'-2.00',-2,'valid',true], [2,'',null,'missing',false], [3,'NaN',null,'invalid',false],
  [4,'0',0,'valid',true], [5,'1.20e2',120,'valid',true]
]);
assert.equal(many.mapping[40].plotted,false);
assert.equal(many.mapping[40].dataRow,41);
assert.match(many.caption,/并非全表/);
assert.match(analyze(parse('n\n1'),0,'line').caption,/不假定是时间/);
const sourceSVG = figureSVG(ready,{widthMm:125});
assert.match(sourceSVG,/width="125mm"/);
assert.match(sourceSVG,/&lt;source&amp;data&gt;.csv/);
assert.match(sourceSVG,/SHA-256/);
assert.match(sourceSVG,/<rect data-row="1" data-value="-2"/);
assert.match(sourceSVG,/<path data-row="4" data-value="0"/);
assert.doesNotMatch(sourceSVG,/data-row="[23]"|<script|<image/);
const neg = figureSVG(analyze(parse('n\n-4\n-3'),0,'bar'));
assert.match(neg,/>0<\/text>/); // signed bar scale explicitly includes zero
const dot = figureSVG(analyze(parse('n\n1\n2'),0,'dot'));
assert.equal((dot.match(/<circle/g)||[]).length,2);
assert.equal((dot.match(/stroke-width="1.5"/g)||[]).length,0);
const gap = figureSVG(analyze(parse('n\n1\n2\n""\n3\n4'),0,'line'));
assert.equal((gap.match(/stroke-width="1.5"/g)||[]).length,2);
assert.throws(()=>figureSVG(ready,{widthMm:20}),/90–240 mm/);
assert.throws(()=>figureSVG(analyze(parse('n\n'+'junk\n'.repeat(40)+'2'),0,'line')),/预览范围/);
const escaped = figureSVG(analyze(parse('"a <script>\u0001"\n1'),0,'line'));
assert.doesNotMatch(escaped,/<script|\u0001/);
assert.match(escaped,/&lt;script&gt;\ufffd/);

const tinyPNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jKXcAAAAASUVORK5CYII=','base64');
const density = Buffer.from(pngAt300Dpi(tinyPNG));
assert.equal(density.toString('ascii',37,41),'pHYs');
assert.equal(density.readUInt32BE(41),11811);
assert.equal(density.readUInt32BE(45),11811);
assert.equal(density[49],1);
assert.deepEqual(pngAt300Dpi(density),new Uint8Array(density));
assert.throws(()=>pngAt300Dpi(tinyPNG.subarray(0,44)),/PNG/);
(async() => {
  assert.equal(await sha256(bytes),exact);
  assert.notEqual(await sha256(Buffer.from(bytes.toString().replace(/\r/g,''))),exact);
  console.log('CSV provenance, vector and PNG metadata tests passed');
})().catch(error=>{ console.error(error); process.exitCode=1; });
