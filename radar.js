/* Local, explicitly scaled two-dimensional radar charts; no network. */
(function(root){
  'use strict';
  const csv=typeof module!=='undefined'&&module.exports?require('./csv-stats.js'):root.EvidenceDeskCSV;
  const SHA=/^[a-f0-9]{64}$/;
  const MIN_AXES=3,MAX_AXES=8,MAX_SERIES=4;
  const tidy=(s,cap)=>typeof s==='string'?s.trim().replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g,' ').slice(0,cap):'';
  const integer=(val,min,max)=>Number.isInteger(val)&&val>=min&&val<=max;
  const xml=value=>String(value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]|\p{Surrogate}/gu,'\ufffd')
    .replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'})[c]);

  function analyze(dataset,{axes,rowNumbers,labelColumn=null,sourceName,sourceSha256}={}){
    if(!csv||!Array.isArray(dataset?.headers)||!Array.isArray(dataset?.rows))throw Error('请选择本机 CSV。');
    if(typeof sourceSha256!=='string'||!SHA.test(sourceSha256))throw Error('需要原始 CSV 字节的 SHA-256。');
    if(!Array.isArray(axes)||!integer(axes.length,MIN_AXES,MAX_AXES))throw Error('请选择 3–8 个不同指标。');
    if(!Array.isArray(rowNumbers)||!integer(rowNumbers.length,1,MAX_SERIES)||
       rowNumbers.some(r=>!integer(r,1,dataset.rows.length))||new Set(rowNumbers).size!==rowNumbers.length)
      throw Error('请选择 1–4 个不同的有效数据行号。');
    if(labelColumn!==null&&!integer(labelColumn,0,dataset.headers.length-1))throw Error('行标签列无效。');
    const unique=new Set();
    const definitions=axes.map((axis,i)=>{
      if(!axis||!integer(axis.column,0,dataset.headers.length-1)||unique.has(axis.column))
        throw Error('指标列不可重复或越界。');
      unique.add(axis.column);
      const unit=tidy(axis.unit,30), name=dataset.headers[axis.column];
      if(!unit)throw Error('指标「'+name+'」必须明确单位（可写无量纲）。');
      if(typeof axis.upper!=='number'||!Number.isFinite(axis.upper)||axis.upper<1e-100||axis.upper>1e100)
        throw Error('指标「'+name+'」必须明确正上界。');
      if(!['higher','lower'].includes(axis.direction))throw Error('指标「'+name+'」必须明确较大或较小所对应的径向方向。');
      return {id:'A'+(i+1),column:axis.column+1,name,unit,baseline:0,upper:axis.upper,direction:axis.direction,
              transform:axis.direction==='higher'?'raw / upper':'1 - raw / upper'};
    });
    const values=definitions.map(axis=>csv.analyze(dataset,axis.column-1,'dot').mapping);
    const labels=new Set();
    const series=rowNumbers.map((row,i)=>{
      const sourceRow=dataset.rows[row-1], label=labelColumn===null?'数据行 '+row:tidy(sourceRow[labelColumn],200);
      if(!label||labels.has(label))throw Error('所选行的标签为空或重复，请核对标签列。');
      labels.add(label);
      return {id:'R'+(i+1),label,dataRow:row,sourceLine:dataset.physicalLines?.[row-1]??null,
              points:definitions.map((axis,j)=>{
        const observation=values[j][row-1], raw=observation.value;
        if(observation.status!=='valid')throw Error('数据行 '+row+' 的「'+axis.name+'」缺失或不是有限数值；未绘图。');
        if(raw<0||raw>axis.upper)throw Error('数据行 '+row+' 的「'+axis.name+'」不在 [0, '+axis.upper+']；不会截断或平移。');
        const fraction=raw/axis.upper;
        return {axis:axis.id,cell:observation.cell,raw,score:axis.direction==='higher'?fraction:1-fraction};
      })};
    });
    return {
      schema:'evidence-desk-radar-v1',source:{name:tidy(sourceName,160),sha256:sourceSha256,
        dataRows:dataset.rows.length,labelColumn:labelColumn===null?null:{column:labelColumn+1,name:dataset.headers[labelColumn]}},
      axes:definitions,series,
      caption:'来源：'+(tidy(sourceName,160)||'本机 CSV')+'；本图选择 '+series.length+' 条数据行，不代表独立样本数。每轴以原值 0 至显式上界映射到统一径向尺度 [0,1]；方向由各指标表列出。未聚合或补值；多边形面积和轴顺序不表示综合评分、面积效应或排名。'
    };
  }

  const palette=[['#126b72',''],['#9b491d','8 4'],['#4a559c','2 4'],['#333b44','9 3 2 3']];
  function radarCanvas(widthMm){
    if(!Number.isFinite(widthMm)||widthMm<120||widthMm>240)throw Error('图宽应在 120–240 mm。');
    const width=widthMm*96/25.4,plotHeight=Math.round(width*.77+62),height=plotHeight+67;
    return {width,height,plotHeight,heightMm:Number((height*25.4/96).toFixed(4))};
  }
  function figureSVG(model,{widthMm=183}={}){
    const {width:W,height:H,plotHeight:P,heightMm}=radarCanvas(widthMm);
    const N=model.axes.length,CX=W/2,CY=P*.44,R=Math.min(W*.31,P*.30), parts=[];
    const point=(index,r)=>{
      const a=index*2*Math.PI/N-Math.PI/2;
      return [CX+Math.cos(a)*R*r,CY+Math.sin(a)*R*r];
    };
    const f=n=>Number(n.toFixed(4));
    const coords=(pts)=>pts.map(p=>p.map(f).join(',')).join(' ');
    const t=(p,value,anchor='middle',size=11)=>`<text x="${f(p[0])}" y="${f(p[1])}" font-size="${size}" text-anchor="${anchor}">${xml(value)}</text>`;
    const vertices=r=>model.axes.map((_,i)=>point(i,r));
    for(const scale of [.25,.5,.75,1]) parts.push(`<polygon points="${coords(vertices(scale))}" fill="none" stroke="#b9c4cc"/>`);
    model.axes.forEach((axis,i)=>{
      const p=point(i,1), label=point(i,1.16);
      parts.push(`<line x1="${f(CX)}" y1="${f(CY)}" x2="${f(p[0])}" y2="${f(p[1])}" stroke="#b9c4cc"/>`);
      parts.push(t([label[0],label[1]+4],axis.id));
    });
    for(const scale of [0,.25,.5,.75,1]){
      const q=point(0,scale);
      parts.push(t([q[0]+7,q[1]+3],String(scale),'start',9));
    }
    model.series.forEach((row,i)=>{
      const [color,dash]=palette[i], points=row.points.map((pt,j)=>point(j,pt.score));
      const stroke=`stroke="${color}" stroke-width="2" ${dash?'stroke-dasharray="'+dash+'"':''}`;
      parts.push(`<polygon data-series="${row.id}" points="${coords(points)}" fill="none" ${stroke}/>`);
      points.forEach((p,j)=>{
        const x=f(p[0]),y=f(p[1]),tip=`<title>${xml(row.label+' · '+row.points[j].axis+' = '+row.points[j].score)}</title>`;
        const at=`data-axis="${row.points[j].axis}" data-series="${row.id}"`;
        if(i===0)parts.push(`<circle ${at} cx="${x}" cy="${y}" r="3" fill="${color}">${tip}</circle>`);
        else if(i===1)parts.push(`<rect ${at} x="${f(x-3)}" y="${f(y-3)}" width="6" height="6" fill="${color}">${tip}</rect>`);
        else {
          const shape=i===2?[[x,y-4],[x+4,y+3],[x-4,y+3]]:[[x,y-4],[x+4,y],[x,y+4],[x-4,y]];
          parts.push(`<polygon ${at} points="${coords(shape)}" fill="${color}">${tip}</polygon>`);
        }
      });
      const lx=14+(i%2)*W/2,ly=P-45+Math.floor(i/2)*19, limit=Math.floor((W/2-64)/12);
      const name=[...row.label];
      parts.push(`<line x1="${lx}" x2="${lx+23}" y1="${ly-4}" y2="${ly-4}" ${stroke}/>`);
      parts.push(t([lx+29,ly],row.id+' '+(name.length<=limit?row.label:name.slice(0,limit-1).join('')+'…'),'start'));
    });
    model.axes.forEach((axis,i)=>{
      const lx=14+(i%2)*W/2,ly=P-7+Math.floor(i/2)*17;
      const limit=Math.floor((W/2-24)/10), label=[...axis.id+' '+axis.name+'（'+axis.unit+'）'];
      parts.push(t([lx,ly],label.length<=limit?label.join(''):label.slice(0,limit-1).join('')+'…','start',10));
    });
    parts.push(t([W/2,23],'二维雷达图 · 归一尺度 [0,1]','middle',14));
    const description=model.caption+' '+model.axes.map(a=>a.id+' '+a.name+'（'+a.unit+'，0–'+a.upper+'，'+a.transform+'）').join('；')+
      '。CSV SHA-256：'+model.source.sha256;
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${widthMm}mm" height="${heightMm}mm" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="radar-title-svg radar-desc-svg"><title id="radar-title-svg">二维雷达图</title><desc id="radar-desc-svg">${xml(description)}</desc><rect width="100%" height="100%" fill="#fff"/><g fill="#193544" font-family="system-ui, sans-serif">${parts.join('')}</g></svg>`;
  }
  async function figurePNG(model,{widthMm=183}={}){
    const {width,height}=radarCanvas(widthMm), preview=new Image();
    await new Promise((resolve,reject)=>{
      preview.onload=resolve;preview.onerror=()=>reject(Error('无法栅格化雷达图。'));
      preview.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(figureSVG(model,{widthMm}));
    });
    const canvas=document.createElement('canvas');
    canvas.width=Math.round(width*300/96);canvas.height=Math.round(height*300/96);
    const context=canvas.getContext('2d');
    if(!context)throw Error('本机无法创建 PNG。');
    context.drawImage(preview,0,0,canvas.width,canvas.height);
    const out=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    if(!out)throw Error('本机无法编码 PNG。');
    return new Blob([csv.pngAt300Dpi(await out.arrayBuffer())],{type:'image/png'});
  }
  const api={analyze,figureSVG,figurePNG,MAX_AXES,MAX_SERIES};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.EvidenceDeskRadar=api;
})(globalThis);
