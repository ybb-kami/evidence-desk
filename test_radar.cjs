const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const csv=require('./csv-stats.js');
const {analyze,figureSVG}=require('./radar.js');

const content='型号,耗时,准确率,成绩,成本\r\n"甲,组",20,0.8,40,0\r\n乙组,60,1,0,5\r\n丙组,-5,0.7,15,3\r\n丁组,,0.6,2,20\r\n戊组,101,0.6,3,1\r\n己组,NaN,0.8,4,4\r\n 乙组 ,50,0.95,4,9\r\n';
const hash=crypto.createHash('sha256').update(content).digest('hex');
const dataset=csv.parse(content);
const opts={axes:[{column:1,unit:'秒',upper:100,direction:'lower'},
                  {column:2,unit:'无量纲',upper:1,direction:'higher'},
                  {column:3,unit:'分',upper:50,direction:'higher'},
                  {column:4,unit:'元',upper:20,direction:'lower'}],
            rowNumbers:[2,1],labelColumn:0,sourceName:'<test&sample>.csv',sourceSha256:hash};
const model=analyze(dataset,opts);
assert.deepEqual(model.source,{name:'<test&sample>.csv',sha256:hash,dataRows:7,labelColumn:{column:1,name:'型号'}});
assert.deepEqual(model.axes.map(a=>[a.name,a.unit,a.baseline,a.upper,a.transform]),[
  ['耗时','秒',0,100,'1 - raw / upper'],['准确率','无量纲',0,1,'raw / upper'],
  ['成绩','分',0,50,'raw / upper'],['成本','元',0,20,'1 - raw / upper']
]);
assert.deepEqual(model.series.map(s=>[s.label,s.dataRow,s.sourceLine]),[['乙组',2,3],['甲,组',1,2]]);
assert.deepEqual(model.series[0].points.map(p=>[p.cell,p.raw,p.score]),[
  ['60',60,.4],['1',1,1],['0',0,0],['5',5,.75]
]);
assert.deepEqual(model.series[1].points.map(p=>p.score),[.8,.8,.8,1]);
assert.match(model.caption,/不代表独立样本数.*未聚合或补值.*不表示综合评分/);
for(const row of [3,4,5,6]){
  assert.throws(()=>analyze(dataset,{...opts,rowNumbers:[row]}),/未绘图|不会截断或平移/);
}
assert.throws(()=>analyze(dataset,{...opts,rowNumbers:[2,7]}),/标签.*重复/);
assert.throws(()=>analyze(dataset,{...opts,rowNumbers:[1,1]}),/不同/);
assert.throws(()=>analyze(dataset,{...opts,axes:opts.axes.slice(0,2)}),/3–8/);
assert.throws(()=>analyze(dataset,{...opts,axes:[opts.axes[0],opts.axes[0],opts.axes[1]]}),/不可重复/);
for(const [field,value,message] of [['direction','',/方向/],['unit','',/单位/],['upper',0,/正上界/],['upper',Infinity,/正上界/]]){
  const axes=opts.axes.map(a=>({...a}));
  axes[0][field]=value;
  assert.throws(()=>analyze(dataset,{...opts,axes}),message);
}
assert.throws(()=>analyze(dataset,{...opts,sourceSha256:'no'}),/SHA-256/);
const svg=figureSVG(model,{widthMm:183});
assert.match(svg,/width="183mm"/);
assert.match(svg,/&lt;test&amp;sample&gt;\.csv/);
assert.match(svg,/stroke-dasharray="8 4"/);
assert.match(svg,/<circle data-axis="A1" data-series="R1"/);
assert.match(svg,/<rect data-axis="A1" data-series="R2"/);
assert.match(svg,/A1 耗时（秒）/);
assert.doesNotMatch(svg,/<script|<image|data:image/);
assert.throws(()=>figureSVG(model,{widthMm:80}),/120–240/);
console.log('Radar provenance, row/axis mapping, direction, bounds, SVG and no imputation passed');
