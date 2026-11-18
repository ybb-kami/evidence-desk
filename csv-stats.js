/* Standalone local CSV parsing and analysis. No network or storage. */
(function(root) {
  'use strict';
  const MAX_BYTES = 512 * 1024, MAX_ROWS = 5000, MAX_COLUMNS = 40, MAX_CHART = 40;
  const numeric = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
  const format = v => String(Number(v.toPrecision(8)));
  function parse(input) {
    const text = input.replace(/^\uFEFF/, '');
    if (new TextEncoder().encode(text).length > MAX_BYTES) throw Error('CSV 超过 512 KiB。');
    const records = [], physicalLines = [];
    let row = [], cell = '', state = 0, line = 1, rowStart = 1; // 0: bare/start, 1: quoted, 2: closed
    function column() {
      row.push(cell); cell = ''; state = 0;
      if (row.length > MAX_COLUMNS) throw Error('CSV 列数超过 40。');
    }
    function record() {
      const hasRecord = row.length || cell || state === 2;
      column();
      if (hasRecord) {
        records.push(row);
        physicalLines.push(rowStart);
        if (records.length > MAX_ROWS + 1) throw Error('数据行数超过 5000。');
      }
      row = [];
    }
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (state === 1) {
        if (c === '"') {
          if (text[i + 1] === '"') { cell += '"'; i++; }
          else state = 2;
        } else {
          if (c==='\r' || (c==='\n' && text[i-1]!=='\r')) line++;
          cell += c;
        }
      } else if (c === ',') column();
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        record();
        rowStart=++line;
      } else if (c === '"' && !cell && state === 0) state = 1;
      else if (state === 2 || c === '"') throw Error('CSV 引号格式错误。');
      else cell += c;
    }
    if (state === 1) throw Error('CSV 有未闭合的引号。');
    if (cell || row.length || state === 2) record();
    if (records.length < 2) throw Error('需要一行列名和至少一行数据。');
    const headers = records.shift().map(header => header.trim());
    physicalLines.shift();
    if (headers.some(header => !header) || new Set(headers).size !== headers.length) throw Error('列名不能为空或重复。');
    if (records.some(row => row.length !== headers.length)) throw Error('有数据行的列数与表头不同。');
    return {headers, rows: records, physicalLines};
  }
  function nvalue(value) {
    const text = value.trim();
    if (!numeric.test(text)) return null;
    const n = Number(text);
    // Bounds keep finite arithmetic and chart coordinates, including scientific notation.
    if (!Number.isFinite(n) || Math.abs(n) > 1e100 || (n !== 0 && Math.abs(n) < 1e-100)) return null;
    if (n === 0 && /[1-9]/.test(text.split(/[eE]/)[0])) return null;
    return n;
  }
  const clean = (value, max) => typeof value === 'string' ?
    value.trim().replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, ' ').slice(0, max) : '';
  function analyze(dataset, column, mode, metadata = {}) {
    if (!dataset.headers[column]) throw Error('请选择数值列。');
    if (!['bar', 'line', 'dot'].includes(mode)) throw Error('请选择受支持的图形。');
    const values = [], mapping = [];
    const source = {
      name: clean(metadata.sourceName, 160),
      sha256: /^[a-f0-9]{64}$/.test(metadata.sourceSha256 || '') ? metadata.sourceSha256 : null,
      column: dataset.headers[column],
      columnIndex: column + 1,
      valueUnit: clean(metadata.valueUnit, 40),
      observationUnit: clean(metadata.observationUnit, 40)
    };
    let missing = 0, invalid = 0, sum = 0, corr = 0, min = Infinity, max = -Infinity;
    for (let i = 0; i < dataset.rows.length; i++) {
      const original = dataset.rows[i][column], cell = original.trim();
      const n = nvalue(cell);
      const status = !cell ? 'missing' : n === null ? 'invalid' : 'valid';
      values.push(n);
      mapping.push({
        dataRow: i + 1, sourceLine: dataset.physicalLines?.[i] ?? null, cell: original, value: n,
        displayedValue: n === null ? null : format(n),
        status, plotted: n !== null && i < MAX_CHART
      });
      if (status === 'missing') { missing++; continue; }
      if (status === 'invalid') { invalid++; continue; }
      min = Math.min(min, n); max = Math.max(max, n);
      const t = sum + n;
      corr += Math.abs(sum) >= Math.abs(n) ? (sum - t) + n : (n - t) + sum;
      sum = t;
    }
    const used = values.length - missing - invalid;
    const base = {used, missing, invalid, min: used ? min : null, max: used ? max : null, mean: used ? (sum + corr) / used : null};
    const visible = values.slice(0, MAX_CHART);
    const chartLabel = {bar:'柱状图', line:'折线图', dot:'点图'}[mode];
    const caption = used ? '“' + dataset.headers[column] + '”' + chartLabel + '按 CSV 原始行序显示前 ' + visible.length +
      ' 条数据，空值和无法解析的值留空' + (mode === 'line' ? '，折线断开。' : '。') + '全表共 ' + values.length + ' 行，有效数值 ' + used + ' 个，空值 ' + missing +
      ' 个，无法解析 ' + invalid + ' 个。全表有效值最小 ' + format(min) + '，最大 ' + format(max) +
      '，算术平均 ' + format(base.mean) + '。仅为描述性统计。' : '所选列没有可用数值。全表空值 ' + missing + ' 个，无法解析 ' + invalid + ' 个。';
    const provenance = '来源：' + (source.name || '本机 CSV（名称未记录）') +
      '；列：' + source.column + '；数值单位：' + (source.valueUnit || '未注明') +
      '；行单位：' + (source.observationUnit || '未注明') + '。';
    const scope = 'n = ' + used + ' 条有效数据行，不代表独立样本数。' +
      (visible.length < values.length ? '图仅展示原表前 ' + visible.length + ' 行，并非全表；描述性汇总使用全表有效值。' : '') +
      (mode === 'line' ? '横轴是数据行序号，不假定是时间。' : '') +
      '完全空白的行不作 CSV 记录；显式空字段保留。';
    return {...base, source, mode, totalRows: values.length, mapping, visible, caption: provenance + caption + scope};
  }

  async function sha256(bytes) {
    const crypto = root.crypto || (typeof module !== 'undefined' ? require('node:crypto').webcrypto : undefined);
    if (!crypto?.subtle) throw Error('当前环境不支持本地 SHA-256。');
    if (bytes.byteLength > MAX_BYTES) throw Error('CSV 超过 512 KiB。');
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2, '0')).join('');
  }

  function mappingJSON(stats) {
    return JSON.stringify({
      schema: 'evidence-desk-csv-figure-v1', source: stats.source, mode: stats.mode,
      summary: {totalRows:stats.totalRows, previewRows:stats.visible.length, used:stats.used,
                missing:stats.missing, invalid:stats.invalid, min:stats.min, max:stats.max, mean:stats.mean},
      caption: stats.caption, rows: stats.mapping
    }, null, 2);
  }

  const xml = value => String(value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]|\p{Surrogate}/gu, '\ufffd')
    .replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&apos;'}[char]));

  function ticks(min,max) {
    const ideal=(max-min)/4, magnitude=10**Math.floor(Math.log10(ideal));
    const step=([1,2,2.5,5,10].find(multiplier=>multiplier*magnitude>=ideal) || 10)*magnitude;
    const first=Math.ceil(min/step), last=Math.floor(max/step), result=[];
    for (let i=0;i<=Math.min(8,last-first);i++) {
      const value=(first+i)*step;
      if (value>=min && value<=max && value!==result.at(-1)) result.push(value);
    }
    if (result.length<2) return min<0 && max>0 ? [min,0,max] : [min,max];
    return result;
  }

  function figureSVG(stats, {widthMm = 183} = {}) {
    if (!stats.used || !stats.visible.some(value => value !== null)) throw Error('预览范围没有可绘数值。');
    if (!Number.isFinite(widthMm) || widthMm < 90 || widthMm > 240) throw Error('图宽应在 90–240 mm。');
    const visible = stats.visible, values = visible.filter(value => value !== null);
    const W = widthMm * 96 / 25.4, H = Math.round(W * .5 + 70);
    const L=96, R=W-18, T=38, B=H-47;
    let min = Math.min(...values), max = Math.max(...values);
    if (stats.mode === 'bar') { min = Math.min(0, min); max = Math.max(0, max); }
    if (min === max) {
      const pad = Math.abs(min) * .05 || 1;
      min -= pad;
      max += pad;
    }
    const x = i => L + (R-L) * (i+.5) / visible.length;
    const y = v => B - (B-T) * ((v-min)/(max-min));
    const n = v => Number(v.toFixed(4));
    const marks = [];
    const ink='#153244', paint='#14756d', grid='#ced8db';
    const text = (x0,y0,t,align='start',size=11) =>
      `<text x="${n(x0)}" y="${n(y0)}" text-anchor="${align}" font-size="${size}">${xml(t)}</text>`;
    const rule = (x0,y0,x1,y1,stroke,width=1) => `<line x1="${n(x0)}" y1="${n(y0)}" x2="${n(x1)}" y2="${n(y1)}" stroke="${stroke}" stroke-width="${width}"/>`;
    const tickValues=ticks(min,max);
    const precise=new Set(tickValues.map(format)).size<tickValues.length;
    for (const value of tickValues) {
      const yy=y(value), label=precise?String(value):format(value);
      marks.push(rule(L,yy,R,yy,grid), text(L-7,yy+4,label,'end',label.length>14?9:11));
    }
    marks.push(rule(L,T,L,B,ink),rule(L,B,R,B,ink));
    if (min<=0 && max>=0) marks.push(rule(L,y(0),R,y(0),ink,1.15));
    const stride = Math.max(1, Math.ceil(visible.length/Math.floor((R-L)/36)));
    const tickEnd = visible.length-1;
    for (let i=0;i<visible.length;i++) {
      if (i === tickEnd || (i%stride === 0 && (tickEnd-i >= stride || i===0))) {
        marks.push(rule(x(i),B,x(i),B+4,ink),text(x(i),B+17,i+1,'middle'));
      }
    }
    marks.push(text((L+R)/2,H-9,'数据行（CSV 原始顺序）','middle'));
    const short = (value, length) => [...value].length <= length ? value : [...value].slice(0,length-1).join('')+'…';
    const label = stats.source.column + (stats.source.valueUnit ? '（' + stats.source.valueUnit + '）' : '（单位未注明）');
    marks.push(text(L,23,short(label,Math.floor((R-L)/13)),'start',13));
    let previous = null;
    visible.forEach((value,i) => {
      if (value === null) { previous=null; return; }
      const tip = `<title>${xml('数据行 '+(i+1)+'：'+stats.mapping[i].cell)}</title>`;
      const attributes = `data-row="${i+1}" data-value="${value}"`;
      if (stats.mode === 'bar') {
        const w=(R-L)/visible.length*.68, yy=y(value), zero=y(0);
        marks.push(value===0 ?
          `<path ${attributes} d="M${n(x(i)-w/2)} ${n(zero)}h${n(w)}" stroke="${paint}" stroke-width="2">${tip}</path>` :
          `<rect ${attributes} x="${n(x(i)-w/2)}" y="${n(Math.min(yy,zero))}" width="${n(w)}" height="${n(Math.abs(yy-zero))}" fill="${paint}">${tip}</rect>`);
      } else {
        if (stats.mode==='line' && previous !== null) marks.push(rule(x(i-1),y(previous),x(i),y(value),paint,1.5));
        marks.push(`<circle ${attributes} cx="${n(x(i))}" cy="${n(y(value))}" r="2.5" fill="${paint}">${tip}</circle>`);
      }
      previous=value;
    });
    const heightMm = Number((H*25.4/96).toFixed(4));
    const title = 'CSV ' + ({bar:'柱状图',line:'折线图',dot:'点图'}[stats.mode]) + '：' + label;
    return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="csv-svg-title csv-svg-desc" width="${widthMm}mm" height="${heightMm}mm" viewBox="0 0 ${W} ${H}"><title id="csv-svg-title">${xml(title)}</title><desc id="csv-svg-desc">${xml(stats.caption+(stats.source.sha256?' 原始文件 SHA-256：'+stats.source.sha256+'。':''))}</desc><rect width="100%" height="100%" fill="#fff"/><g fill="${ink}" font-family="system-ui, sans-serif">${marks.join('')}</g></svg>`;
  }

  function pngAt300Dpi(buffer) {
    // Browser canvas exports typically record 96 dpi. Replace its pHYs
    // chunk with the density actually used for the canvas dimensions.
    const data = new Uint8Array(buffer);
    const signature = [137,80,78,71,13,10,26,10];
    if (data.length < 33 || !signature.every((byte,i) => data[i] === byte)) throw Error('PNG 预览无效。');
    const chunk = new Uint8Array(21);
    const view = new DataView(chunk.buffer);
    const density = Math.round(300 / .0254);
    view.setUint32(0,9);
    chunk.set([112,72,89,115],4); // pHYs
    view.setUint32(8,density);
    view.setUint32(12,density);
    chunk[16]=1;
    let crc=0xffffffff;
    for (const byte of chunk.slice(4,17)) {
      crc ^= byte;
      for (let i=0;i<8;i++) crc=(crc>>>1)^((crc&1)?0xedb88320:0);
    }
    view.setUint32(17,crc^0xffffffff);
    const fields=new DataView(data.buffer,data.byteOffset,data.byteLength), parts=[data.slice(0,8)];
    let offset=8, ended=false;
    while (offset+12<=data.length) {
      const len=fields.getUint32(offset), kind=String.fromCharCode(...data.subarray(offset+4,offset+8));
      const end=offset+12+len;
      if (end>data.length || (offset===8 && (kind!=='IHDR' || len!==13))) throw Error('PNG 预览无效。');
      if (kind!=='pHYs') parts.push(data.subarray(offset,end));
      if (kind==='IHDR') parts.push(chunk);
      if (kind==='IEND') { ended=true; break; }
      offset=end;
    }
    if (!ended) throw Error('PNG 预览无效。');
    const result=new Uint8Array(parts.reduce((total,part)=>total+part.length,0));
    let cursor=0;
    for (const part of parts) { result.set(part,cursor); cursor+=part.length; }
    return result;
  }

  async function figurePNG(stats, {widthMm=183}={}) {
    const source = figureSVG(stats, {widthMm});
    const preview = new Image();
    await new Promise((resolve, reject) => {
      preview.onload=resolve;
      preview.onerror=()=>reject(Error('本机无法栅格化 SVG 预览。'));
      preview.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(source);
    });
    const pxWidth=widthMm*96/25.4, pxHeight=Math.round(pxWidth*.5+70);
    const canvas=document.createElement('canvas');
    canvas.width=Math.round(pxWidth*300/96);
    canvas.height=Math.round(pxHeight*300/96);
    const context=canvas.getContext('2d');
    if (!context) throw Error('本机无法创建 PNG 画布。');
    context.drawImage(preview,0,0,canvas.width,canvas.height);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    if (!blob) throw Error('本机无法编码 PNG 预览。');
    return new Blob([pngAt300Dpi(await blob.arrayBuffer())],{type:'image/png'});
  }
  const api = {parse, analyze, format, sha256, figureSVG, figurePNG, pngAt300Dpi, mappingJSON,
               MAX_BYTES, MAX_ROWS, MAX_COLUMNS, MAX_CHART};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EvidenceDeskCSV = api;
})(globalThis);
