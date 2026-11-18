/* Source file selection and audit table for the local radar module. */
(function(){
  'use strict';
  const panel=document.getElementById('radar-panel'), csv=globalThis.EvidenceDeskCSV,
        radar=globalThis.EvidenceDeskRadar;
  if(!panel||!csv||!radar)return;
  const n=(tag,text,attrs={})=>{
    const el=document.createElement(tag);
    if(text!==undefined)el.textContent=text;
    for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);
    return el;
  };
  panel.setAttribute('aria-labelledby','radar-title');
  const title=n('h2','多指标二维雷达图',{id:'radar-title'});
  const note=n('p','选择 3–8 个指标和 1–4 行，按原始单位明确每轴的正上界和向外方向。同一轴对所有行使用相同刻度，不补缺失、不推断独立样本或综合排名。遇到负数、空值或超界，请使用带零线的原值点图核对。CSV 不上传。',{class:'subtle'});
  const file=n('input',undefined,{type:'file',accept:'.csv,text/csv',id:'radar-file'});
  const fileLabel=n('label','本机 CSV：',{for:'radar-file'});fileLabel.append(file);
  const status=n('p','请选择 CSV。',{role:'status',class:'subtle'});
  const editor=n('div'), output=n('div');
  panel.append(title,note,fileLabel,status,editor,output);
  let dataset, source, version=0, current, renderVersion=0;
  function download(data,type,name){
    const blob=data instanceof Blob?data:new Blob([data],{type});
    const href=URL.createObjectURL(blob);
    const link=n('a',undefined,{href,download:name});
    link.click();setTimeout(()=>URL.revokeObjectURL(href),3000);
  }
  function clear(){
    current=null;renderVersion++;output.replaceChildren();
  }
  function table(header,rows){
    const wrapper=n('div',undefined,{class:'csv-scroll',tabindex:'0','aria-label':'原值和计算对照表'});
    const out=n('table'), head=n('thead'), label=n('tr'), body=n('tbody');
    header.forEach(value=>label.append(n('th',value,{scope:'col'})));
    head.append(label);
    rows.forEach(row=>{
      const line=n('tr');
      row.forEach((value,i)=>line.append(n(i?'td':'th',String(value),i?{}:{scope:'row'})));
      body.append(line);
    });
    out.append(head,body);wrapper.append(out);return wrapper;
  }
  function show(model){
    const target=++renderVersion;
    current=model;output.replaceChildren();
    const serialized=radar.figureSVG(model);
    const svg=new DOMParser().parseFromString(serialized,'image/svg+xml').documentElement;
    const sample=n('div',undefined,{class:'csv-scroll',tabindex:'0','aria-label':'二维雷达图'});
    sample.append(document.importNode(svg,true));
    const svgSave=n('button','下载 SVG 矢量图',{type:'button'}),
          pngSave=n('button','下载 300 dpi PNG',{type:'button'}),
          jsonSave=n('button','下载数值映射 JSON',{type:'button'});
    const buttons=n('div',undefined,{class:'actions'});
    buttons.append(svgSave,pngSave,jsonSave);
    svgSave.onclick=()=>{if(current===model)download(serialized,'image/svg+xml','radar.svg');};
    jsonSave.onclick=()=>{if(current===model)download(JSON.stringify(model,null,2),'application/json','radar-mapping.json');};
    pngSave.onclick=async()=>{
      if(current!==model||pngSave.disabled)return;
      pngSave.disabled=true;
      try{
        const blob=await radar.figurePNG(model);
        if(target===renderVersion&&current===model)download(blob,'image/png','radar-300dpi.png');
      }catch(error){if(target===renderVersion)status.textContent='PNG 无法导出：'+error.message;}
      finally{pngSave.disabled=false;}
    };
    output.append(sample,n('p',model.caption),n('p','原 CSV SHA-256：'+model.source.sha256,{class:'subtle'}),buttons);
    output.append(n('h3','指标和归一化规则'),
      table(['轴','源列','单位','原值基线','正上界','向外方向和公式'],
            model.axes.map(a=>[a.id,a.column+' · '+a.name,a.unit,a.baseline,a.upper,(a.direction==='higher'?'值越大；':'值越小；')+a.transform])));
    output.append(n('h3','数值核对表'),
      table(['系列','数据行 / CSV 行','轴','原始字段','原值','归一值'],
            model.series.flatMap(s=>s.points.map(p=>[s.id+' '+s.label,s.dataRow+' / '+(s.sourceLine??'未知'),p.axis,p.cell,p.raw,csv.format(p.score)]))));
  }
  function build(){
    const form=n('form'), settings=n('details'), head=n('summary','设置指标、量纲与方向（必填）');
    settings.open=true;settings.append(head);
    const fields=dataset.headers.map((name,i)=>{
      const checkbox=n('input',undefined,{type:'checkbox','aria-label':'使用指标 '+name});
      const unit=n('input',undefined,{type:'text',placeholder:'单位或无量纲',maxlength:'30','aria-label':name+' 的单位'});
      const upper=n('input',undefined,{type:'number',step:'any',placeholder:'正上界','aria-label':name+' 的上界'});
      const direction=n('select',undefined,{'aria-label':name+' 的向外方向'});
      direction.append(n('option','选方向',{value:''}),n('option','值越大越向外',{value:'higher'}),n('option','值越小越向外',{value:'lower'}));
      const row=n('div',undefined,{class:'actions'});row.append(checkbox,n('strong',(i+1)+' · '+name),unit,upper,direction);
      settings.append(row);
      return {checkbox,unit,upper,direction};
    });
    const rows=n('input',undefined,{type:'text',id:'radar-rows',maxlength:'28',placeholder:'例如 1, 2, 5'});
    const rowLabel=n('label','数据行号（不含表头，逗号分隔）：',{for:'radar-rows'});rowLabel.append(rows);
    const labels=n('select',undefined,{id:'radar-labels'});
    labels.append(n('option','使用数据行号',{value:''}),...dataset.headers.map((name,i)=>n('option',name,{value:String(i)})));
    const label=n('label','可选：行标签列',{for:'radar-labels'});label.append(labels);
    const run=n('button','绘制与核对',{type:'submit'});
    form.append(settings,rowLabel,label,run);
    form.oninput=()=>{if(current){clear();status.textContent='设置已更改，请重新绘制。';}};
    form.onsubmit=event=>{
      event.preventDefault();clear();
      try{
        const raw=rows.value.trim().split(/\s*[,，]\s*/);
        if(raw.some(v=>!/^\d{1,4}$/.test(v))) throw Error('请填写有效的逗号分隔数据行号。');
        const axes=fields.flatMap((f,i)=>f.checkbox.checked?
          [{column:i,unit:f.unit.value,upper:Number(f.upper.value),direction:f.direction.value}]:[]);
        const model=radar.analyze(dataset,{axes,rowNumbers:raw.map(Number),
                                  labelColumn:labels.value===''?null:Number(labels.value),
                                  sourceName:source.name,sourceSha256:source.hash});
        show(model);
        status.textContent='已绘制。请检查单位、上界与数值核对表。';
      }catch(error){status.textContent='未绘图：'+error.message;}
    };
    editor.append(form);
  }
  file.onchange=async()=>{
    const selection=file.files?.[0], seen=++version;
    dataset=source=undefined;editor.replaceChildren();clear();
    if(!selection){status.textContent='请选择 CSV。';return;}
    status.textContent='正在本机读取原 CSV…';
    try{
      if(selection.size>csv.MAX_BYTES)throw Error('CSV 超过 512 KiB。');
      const bytes=await selection.arrayBuffer();
      const model=csv.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
      const hash=await csv.sha256(bytes);
      if(seen!==version)return;
      dataset=model;source={name:selection.name,hash};build();
      status.textContent='已读取 '+model.rows.length+' 行 × '+model.headers.length+' 列。未上传。';
    }catch(error){if(seen===version)status.textContent='未能读取 CSV：'+error.message;}
    finally{if(seen===version)file.value='';}
  };
})();
