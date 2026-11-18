/* Local scanpdf v1 sidecar viewer. Raw JSON never leaves the browser. */
(function(root){
  'use strict';
  const MAX_BYTES=8*1024*1024, MAX_PAGES=650, MAX_REGIONS=512, MAX_CELLS=4096;
  const SHA=/^[a-f0-9]{64}$/;
  const FRAME='rotated_visible_page_top_left';
  const invalid=()=>{throw Error('此 JSON 不是受支持的 scanpdf v1 结构，或范围不合理。');};
  const obj=x=>x!==null && typeof x==='object' && !Array.isArray(x);
  const integer=(x,min,max)=>Number.isSafeInteger(x)&&x>=min&&x<=max;
  const text=(x,max=600)=>{
    if (typeof x!=='string'||x.length>max) invalid();
    return x.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g,' ').trim();
  };
  function box(value,width,height,scope=[0,0,width,height]){
    if (!Array.isArray(value)||value.length!==4 ||
        value.some((xy,i)=>!integer(xy,scope[i%2],scope[i%2+2])) ||
        value[0]>=value[2]||value[1]>=value[3]) invalid();
    return [...value];
  }
  function parse(jsonText){
    if (typeof jsonText!=='string'||jsonText.length>MAX_BYTES||
        new TextEncoder().encode(jsonText).length>MAX_BYTES) throw Error('JSON 超过 8 MiB。');
    let raw;
    try {raw=JSON.parse(jsonText.replace(/^\uFEFF/,''));} catch {throw Error('文件不是有效的 JSON。');}
    if (!obj(raw)||raw.schema_version!==1||!obj(raw.source)||!obj(raw.render)||
        typeof raw.source.sha256!=='string'||!SHA.test(raw.source.sha256)||
        !integer(raw.source.page_count,1,MAX_PAGES)||!integer(raw.render.dpi,72,600)||
        raw.render.frame!==FRAME||!Array.isArray(raw.pages)||
        !integer(raw.pages.length,1,raw.source.page_count)) invalid();
    const pagesSeen=new Set();
    let regionTotal=0, cellTotal=0;
    const pages=raw.pages.map(candidate=>{
      if (!obj(candidate)||!integer(candidate.physical_page,1,raw.source.page_count)||
          pagesSeen.has(candidate.physical_page)||!integer(candidate.width_px,1,50000)||
          !integer(candidate.height_px,1,50000)||!Array.isArray(candidate.regions)||
          candidate.regions.length>64) invalid();
      if (!['words_found','no_words'].includes(candidate.ocr_status)) invalid();
      pagesSeen.add(candidate.physical_page);
      const width=candidate.width_px, height=candidate.height_px;
      const regionCount=(regionTotal+=candidate.regions.length);
      if (regionCount>MAX_REGIONS) invalid();
      const regions=candidate.regions.map(region=>{
        if (!obj(region)||!['table','formula'].includes(region.type)) invalid();
        const bbox=box(region.bbox,width,height);
        if (region.status==='detected_only') return {type:region.type,status:region.status,bbox};
        if (region.type==='formula'&&region.status==='transcribed') {
          const transcription=text(region.text,1000);
          if (!transcription) invalid();
          return {type:region.type,status:region.status,bbox,text:transcription};
        }
        if (region.type!=='table'||region.status!=='structure_recognized'||
            !Array.isArray(region.cells)||!integer(region.cells.length,1,400)) invalid();
        const seenCells=new Set();
        const cells=region.cells.map(cell=>{
          if (!obj(cell)||!integer(cell.row,1,100)||!integer(cell.column,1,32)||
              !['ocr_text','empty_or_unread'].includes(cell.status)) invalid();
          if (seenCells.has(cell.row+':'+cell.column)) invalid();
          seenCells.add(cell.row+':'+cell.column);
          const content=cell.status==='ocr_text'?text(cell.text):'';
          if (cell.status==='ocr_text'&&!content) invalid();
          return {row:cell.row,column:cell.column,status:cell.status,text:content,
                  bbox:box(cell.bbox,width,height,bbox)};
        });
        const slots=Math.max(...cells.map(cell=>cell.row))*Math.max(...cells.map(cell=>cell.column));
        if (slots>400||(cellTotal+=slots)>MAX_CELLS) invalid();
        return {type:region.type,status:region.status,bbox,cells};
      });
      return {page:candidate.physical_page,width,height,ocrStatus:candidate.ocr_status,regions};
    }).sort((a,b)=>a.page-b.page);
    return {sourceSha256:raw.source.sha256,pageCount:raw.source.page_count,dpi:raw.render.dpi,pages};
  }
  function matchActive(sidecar,docs){
    const found=Array.isArray(docs)&&docs.find(doc=>obj(doc)&&doc.id===sidecar.sourceSha256);
    if (!found) throw Error('当前活动文库没有字节 SHA-256 匹配的原件。请导入原件，或恢复已归档的同一文件。');
    if (found.pages!==sidecar.pageCount) throw Error('原件的物理页数与 JSON 不符，未显示机器结果。');
    return found;
  }
  function sourceLink(source,page){
    if (typeof source!=='string'||!SHA.test(source)||!integer(page,1,MAX_PAGES)) invalid();
    return '/pdf/'+source+'#page='+page;
  }
  function mount(panel){
    const n=(tag,value,attrs={})=>{
      const el=document.createElement(tag);
      if(value!==undefined) el.textContent=value;
      for(const [key,v] of Object.entries(attrs)) el.setAttribute(key,v);
      return el;
    };
    panel.setAttribute('aria-labelledby','scan-sidecar-title');
    const title=n('h2','扫描结构核对',{id:'scan-sidecar-title'});
    const guidance=n('p','先用本机 scanpdf 命令行生成 JSON，并将扫描 PDF 原件导入活动文库。这里仅在浏览器读取 JSON，不上传、不保存；SHA-256 和页数匹配不证明机器转写正确。',{class:'subtle'});
    const file=n('input',undefined,{type:'file',accept:'.json,application/json',id:'scan-sidecar-file'});
    const fileLabel=n('label','选择本机 scanpdf JSON：',{for:'scan-sidecar-file'});
    fileLabel.append(file);
    const state=n('p','尚未选择 JSON。',{role:'status',class:'subtle'});
    const view=n('div');
    panel.append(title,guidance,fileLabel,state,view);
    let sequence=0, refreshLink=0;
    const valid=async (model)=>{
      const reply=await fetch('/api/library',{cache:'no-store'});
      if(!reply.ok) throw Error('无法读取当前活动文库。');
      const lib=await reply.json();
      return matchActive(model,lib.docs);
    };
    function visual(page,bbox){
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
      const attrs={viewBox:'0 0 '+page.width+' '+page.height,width:'60',height:'78',role:'img','aria-label':'区域在该页的相对位置示意'};
      Object.entries(attrs).forEach(([k,v])=>svg.setAttribute(k,v));
      for(const [x,y,w,h,fill] of [[0,0,page.width,page.height,'#e2e8f0'],[bbox[0],bbox[1],bbox[2]-bbox[0],bbox[3]-bbox[1],'#14756d']]){
        const r=document.createElementNS(svg.namespaceURI,'rect');
        for(const [k,v] of Object.entries({x,y,width:w,height:h,fill})) r.setAttribute(k,String(v));
        svg.append(r);
      }
      return svg;
    }
    function table(region){
      const wrapper=n('div',undefined,{class:'csv-scroll',tabindex:'0','aria-label':'机器识别表格，可横向滚动'});
      const out=n('table');
      out.append(n('caption','识别网格，内容必须对照原页；— 表示未映射单元。'));
      const rows=Math.max(...region.cells.map(cell=>cell.row)), cols=Math.max(...region.cells.map(cell=>cell.column));
      const slots=new Map(region.cells.map(cell=>[cell.row+':'+cell.column,cell]));
      const head=n('thead'), header=n('tr');
      header.append(n('th','行 / 列',{scope:'col'}));
      for(let c=1;c<=cols;c++) header.append(n('th',String(c),{scope:'col'}));
      head.append(header);
      const body=n('tbody');
      for(let r=1;r<=rows;r++){
        const line=n('tr');line.append(n('th',String(r),{scope:'row'}));
        for(let c=1;c<=cols;c++){
          const cell=slots.get(r+':'+c);
          line.append(n('td',!cell?'—':cell.status==='ocr_text'?cell.text:'空白或未读'));
        }
        body.append(line);
      }
      out.append(head,body);wrapper.append(out);return wrapper;
    }
    function loaded(model,doc,run){
      view.replaceChildren();
      const selector=n('select',undefined,{id:'scan-sidecar-page'});
      const label=n('label','物理页：',{for:'scan-sidecar-page'});
      for(const page of model.pages) selector.append(n('option','第 '+page.page+' 页 · '+page.regions.length+' 个区',{value:String(page.page)}));
      selector.value=String((model.pages.find(p=>p.regions.length)||model.pages[0]).page);
      label.append(selector);
      const details=n('div');
      view.append(label,details);
      function show(){
        const page=model.pages.find(p=>String(p.page)===selector.value);
        if(!page) return;
        refreshLink++;
        const href=sourceLink(model.sourceSha256,page.page);
        details.replaceChildren();
        const link=n('a','打开原件第 '+page.page+' 页 ↗',{href,target:'_blank',rel:'noopener'});
        details.append(n('p','物理第 '+page.page+' 页 · '+page.width+'×'+page.height+' 像素（'+model.dpi+' dpi）；坐标以可见旋转页的左上角为原点。',{class:'subtle'}),link);
        if(!page.regions.length) details.append(n('p','该页没有机器结构检测结果，不代表没有表格或公式。',{class:'subtle'}));
        for(const [i,region] of page.regions.entries()){
          const block=n('section',undefined,{class:'evidence'});
          const label=(region.type==='table'?'表格':'公式')+'候选 '+(i+1);
          block.append(n('h3',label),visual(page,region.bbox),n('p','候选区域像素框（左、上、右、下）：'+region.bbox.join('、'),{class:'subtle'}));
          if(region.status==='detected_only') block.append(n('p','仅定位区框，未可靠转写；请查看原页。'));
          else if(region.type==='table') block.append(table(region));
          else block.append(n('p','机器转写（未经人工核对）：'+region.text));
          details.append(block);
        }
      }
      selector.onchange=async()=>{
        const token=++refreshLink;
        try {await valid(model);}
        catch(error) {if(run===sequence&&token===refreshLink){view.replaceChildren();state.textContent=error.message;}return;}
        if(run===sequence&&token===refreshLink) show();
      };
      state.textContent='已按 SHA-256 和 '+model.pageCount+' 个物理页匹配活动原件「'+doc.name+'」，加载 '+model.pages.length+' 页机器结果。表格或公式未经核验。';
      show();
    }
    file.onchange=async()=>{
      const chosen=file.files?.[0];
      const run=++sequence;
      view.replaceChildren();
      state.textContent=chosen?'正在本机解析并核对原件…':'尚未选择 JSON。';
      if(!chosen) return;
      try{
        if(chosen.size>MAX_BYTES) throw Error('JSON 超过 8 MiB。');
        const bytes=await chosen.arrayBuffer();
        if(run!==sequence) return;
        const model=parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
        const doc=await valid(model);
        if(run===sequence) loaded(model,doc,run);
      }catch(error){
        if(run===sequence) state.textContent='未加载：'+error.message;
      }finally{
        if(run===sequence) file.value='';
      }
    };
  }
  const api={parse,matchActive,sourceLink,MAX_BYTES};
  if(typeof module!=='undefined'&&module.exports) module.exports=api;
  const panel=typeof document!=='undefined'&&document.getElementById('scan-sidecar-panel');
  if(panel) mount(panel);
})(globalThis);
