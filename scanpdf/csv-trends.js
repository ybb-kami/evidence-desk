/* Data and vector export for CSV plots with a user-selected horizontal axis. */
(function(root) {
  'use strict';
  const csv = typeof module !== 'undefined' ? require('../csv-stats.js') : root.EvidenceDeskCSV;
  const palette = ['#135e70', '#a23b15', '#685291', '#3c6940', '#303b49', '#8d5900', '#914969', '#28678e'];
  const MAX_GROUPS = palette.length;
  const MAX_MERMAID_ROWS = 200;
  const clean = (value, limit) => String(value ?? '').replace(/[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/g, ' ').slice(0, limit);
  const short = (value, limit) => [...value].length <= limit ? value : [...value].slice(0, limit - 1).join('') + '…';
  const escape = value => String(value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]|\p{Surrogate}/gu, '\ufffd')
    .replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&apos;'}[char]));
  const display = value => typeof value === 'number' ? String(value) : short(clean(value, 160), 40);
  const axisName = axis => axis.unit === axis.column || axis.unit.startsWith(axis.column + ' (') ? axis.unit :
    axis.column + (axis.unit ? ` (${axis.unit})` : '');
  const ref = row => ({dataRow: row.dataRow, sourceLine: row.sourceLine, x: row.x, y: row.y});
  const categorical = value => value.trim() ? value : null;
  const groupValues = (data, column) => [...new Set(data.rows.map(row => categorical(row[column])).filter(value => value !== null))];

  function observationMaximum(rows) {
    if (!rows.length) return null;
    const value = Math.max(...rows.map(row => row.y));
    return {value, observations: rows.filter(row => row.y === value).map(ref)};
  }

  function summarize(group, rows, xKind) {
    const points = rows.filter(row => row.plotted);
    const summary = {group, totalRows: rows.length, plotted: points.length, peak: observationMaximum(points)};
    const buckets = new Map();
    for (const row of rows) {
      if (row.x === null) continue;
      if (!buckets.has(row.x)) buckets.set(row.x, []);
      buckets.get(row.x).push(row);
    }
    if (xKind === 'category') {
      return {...summary, categories: [...buckets].map(([category, values]) => ({category, rows: values.length,
        peak: observationMaximum(values.filter(row => row.plotted))})), segments: []};
    }

    const sorted = [...buckets].sort(([a], [b]) => a - b);
    const singleValid = bucket => bucket?.[1].length === 1 && bucket[1][0].plotted ? bucket[1][0] : null;
    const knownRows = rows.filter(row => row.x !== null);
    const unknownX = rows.filter(row => row.x === null).map(ref);
    const gaps = sorted.filter(([, bucket]) => bucket.some(row => row.y === null))
      .map(([x, bucket]) => ({x, missingOrInvalid: bucket.filter(row => row.y === null).map(ref)}));
    const duplicates = sorted.filter(([, bucket]) => bucket.length > 1)
      .map(([x, bucket]) => ({x, rows: bucket.map(ref)}));
    const segments = [];
    const changes = {increase: 0, decrease: 0, equal: 0};
    if (!unknownX.length) {
      for (let i = 1; i < sorted.length; i++) {
        const from = singleValid(sorted[i - 1]), to = singleValid(sorted[i]);
        if (!from || !to) continue;
        const delta = to.y - from.y;
        segments.push({from: ref(from), to: ref(to), deltaY: delta});
        changes[delta > 0 ? 'increase' : delta < 0 ? 'decrease' : 'equal']++;
      }
    }
    let endpointStatus = sorted.length < 2 ? 'too_few_x' : 'observed';
    const first = singleValid(sorted[0]), last = singleValid(sorted.at(-1));
    if (unknownX.length) endpointStatus = 'unknown_x';
    else if (endpointStatus === 'observed' && (!first || !last)) endpointStatus = 'uncertain_endpoint';
    const endpoints = endpointStatus === 'observed' ? {first: ref(first), last: ref(last), deltaY: last.y - first.y} : null;
    let observedDirection = null;
    if (segments.length && segments.length === sorted.length - 1) {
      observedDirection = changes.increase && changes.decrease ? 'mixed' : changes.increase ? 'nondecreasing' :
        changes.decrease ? 'nonincreasing' : 'constant';
    }
    return {...summary, unknownX, gaps, duplicates, segments, changes, endpointStatus, endpoints, observedDirection,
      orderedFromUnsortedInput: knownRows.some((row, i) => i > 0 && row.x < knownRows[i - 1].x)};
  }

  function caption(source, xKind, rows, groups, scope) {
    const count = status => rows.filter(row => !row.plotted && row.exclusion === status).length;
    const label = axis => display(axisName(axis));
    const units = source.observationUnit ? `每行单位为${source.observationUnit}。` : '';
    const subset = scope === null ? '' : `仅绘制分组“${display(scope)}”。`;
    const stratification = source.group ? `按${display(source.group.column)}分组。` : '';
    const order = xKind === 'numeric' ? '数值横轴按 X 从小到大排列。' : '类别按首次出现显示。';
    const header = `数据来自 ${source.name || '本机 CSV'}。横轴为${label(source.x)}，纵轴为${label(source.y)}。` +
      `${order}${stratification}${subset}共 ${rows.length} 条数据记录，绘制 ${rows.filter(row => row.plotted).length} 条。` +
      `${count('outside_group') ? `其他分组 ${count('outside_group')} 条。` : ''}` +
      `${count('missing_group') ? `分组缺失 ${count('missing_group')} 条。` : ''}` +
      `${count('invalid_coordinate') ? `坐标缺失或无效 ${count('invalid_coordinate')} 条。` : ''}${units}`;
    const lines = groups.map(summary => {
      const name = summary.group === null ? '' : `“${display(summary.group)}”组有 ${summary.plotted} 条有效观测。`;
      const rowsAtPeak = summary.peak?.observations || [];
      const peak = rowsAtPeak.length ? `观测峰值 ${display(summary.peak.value)}${source.y.unit ? ' ' + source.y.unit : ''}，` +
        `对应${rowsAtPeak.slice(0, 6).map(row => ` X = ${display(row.x)}（CSV 第 ${row.sourceLine ?? '?'} 行）`).join('、')}` +
        `${rowsAtPeak.length > 6 ? `等 ${rowsAtPeak.length} 条并列观测，完整行号见映射` : ''}。` : '无可绘观测。';
      if (xKind === 'category') return name + peak + `共 ${summary.categories.length} 个类别。各类别的观测与原行见映射。`;
      const changed = summary.changes;
      const steps = summary.segments.length ? `可比相邻点中，上升 ${changed.increase} 段、下降 ${changed.decrease} 段、持平 ${changed.equal} 段。` : '';
      const direction = {nondecreasing:'本组沿 X 的观测值不下降。', nonincreasing:'本组沿 X 的观测值不上升。',
        mixed:'本组沿 X 的观测值既有上升也有下降。', constant:'本组沿 X 的观测值保持相同。'}[summary.observedDirection] || '';
      const endpoints = summary.endpoints;
      const delta = endpoints ? `${endpoints.deltaY > 0 ? '+' : ''}${csv.format(endpoints.deltaY)}` : '';
      const endpoint = endpoints ?
        `最小 X ${display(endpoints.first.x)}（CSV 第 ${endpoints.first.sourceLine ?? '?'} 行）的 Y 为 ${display(endpoints.first.y)}，` +
        `最大 X ${display(endpoints.last.x)}（第 ${endpoints.last.sourceLine ?? '?'} 行）的 Y 为 ${display(endpoints.last.y)}。` +
        `首末差（末值减首值）为 ${delta}${source.y.unit ? ' ' + source.y.unit : ''}。` :
        {too_few_x: '可比较的横轴位置不足，首末差留空。', unknown_x: '有未确定的横轴位置，首末差留空。',
          uncertain_endpoint: '端点值有缺失或重复，首末差留空。'}[summary.endpointStatus];
      const groups = [summary.gaps.length && `有 ${summary.gaps.length} 个 X 位置的 Y 缺失或无效。`,
        summary.duplicates.length && `有 ${summary.duplicates.length} 个重复 X，重合点可按数据映射核对。`,
        summary.unknownX.length && `有 ${summary.unknownX.length} 条横轴位置未知，该组仅画点。`,
        summary.orderedFromUnsortedInput && '原表该组的 X 并非升序。'].filter(Boolean).join('');
      return name + peak + endpoint + steps + direction + groups;
    });
    return [header, ...lines, 'n 按有效观测行计。变化只描述观测范围。'].join('\n');
  }

  function analyze(dataset, options) {
    const {xColumn, yColumn, xKind, groupColumn = null, focus = null, connect = false} = options;
    const validIndex = column => Number.isInteger(column) && column >= 0 && column < dataset.headers.length;
    if (!validIndex(xColumn) || !validIndex(yColumn) || xColumn === yColumn || !['numeric', 'category'].includes(xKind)) {
      throw Error('请选择不同的横轴列和目标数值列，并指定横轴类型。');
    }
    if (groupColumn !== null && (!validIndex(groupColumn) || groupColumn === xColumn || groupColumn === yColumn)) {
      throw Error('分组列应与横轴和目标列不同。');
    }
    const allGroups = groupColumn === null ? [null] : groupValues(dataset, groupColumn);
    if (focus !== null && !allGroups.includes(focus)) throw Error('所选分组已变化。');
    if (focus === null && allGroups.length > MAX_GROUPS) throw Error(`有 ${allGroups.length} 个分组。请选择一个分组绘图。`);
    const selectedGroups = focus === null ? allGroups : [focus];
    const xNumbers = xKind === 'numeric' ? csv.analyze(dataset, xColumn, 'dot').mapping : null;
    const yNumbers = csv.analyze(dataset, yColumn, 'dot').mapping;
    const axis = (column, unit) => ({column: dataset.headers[column], columnIndex: column + 1, unit: clean(unit, 40).trim()});
    const source = {name: clean(options.sourceName, 160),
      sha256: /^[a-f0-9]{64}$/.test(options.sourceSha256 || '') ? options.sourceSha256 : null,
      x: axis(xColumn, options.xUnit), y: axis(yColumn, options.yUnit),
      group: groupColumn === null ? null : axis(groupColumn, ''), observationUnit: clean(options.observationUnit, 40).trim()};
    const rows = dataset.rows.map((cells, i) => {
      const xCell = cells[xColumn], yCell = cells[yColumn], groupCell = groupColumn === null ? null : cells[groupColumn];
      const x = xNumbers ? xNumbers[i].value : categorical(xCell);
      const group = groupCell === null ? null : categorical(groupCell);
      const y = yNumbers[i].value;
      const exclusion = groupColumn !== null && group === null ? 'missing_group' :
        focus !== null && group !== focus ? 'outside_group' : x === null || y === null ? 'invalid_coordinate' : null;
      return {dataRow: i + 1, sourceLine: dataset.physicalLines?.[i] ?? null, xCell, yCell, groupCell, x, y, group,
        xStatus: xNumbers ? xNumbers[i].status : x === null ? 'missing' : 'valid', yStatus: yNumbers[i].status,
        plotted: exclusion === null, exclusion, markId: exclusion === null ? `observation-${i + 1}` : null};
    });
    const groups = selectedGroups.map(group => summarize(group, rows.filter(row => row.group === group), xKind));
    return {schema: 'evidence-desk-csv-variable-figure-v1', source, xKind, focus, numericRepresentation: 'IEEE-754 binary64',
      connect: xKind === 'numeric' && connect === true, orientation: xKind === 'numeric' ? 'ascending_numeric_x' : 'first_appearance',
      caption: caption(source, xKind, rows, groups, focus), rows, groups};
  }

  function numericDomain(values) {
    const min = Math.min(...values), max = Math.max(...values);
    if (min !== max) return [min, max];
    const pad = Math.abs(min) * .06 || 1;
    return [min - pad, max + pad];
  }

  function figure(stats, {widthMm = 183} = {}) {
    if (!Number.isFinite(widthMm) || widthMm < 90 || widthMm > 240) throw Error('图宽应在 90–240 mm。');
    const points = stats.rows.filter(row => row.plotted);
    if (!points.length) throw Error('所选范围没有可绘观测。');
    const categories = stats.xKind === 'category' ? [...new Set(stats.rows.filter(row => row.x !== null &&
      row.exclusion !== 'outside_group' && row.exclusion !== 'missing_group').map(row => row.x))] : null;
    const width = widthMm * 96 / 25.4, legendColumns = widthMm < 120 ? 2 : 4;
    const hasLegend = stats.source.group !== null;
    const legendHeight = hasLegend ? 16 * Math.ceil(stats.groups.length / legendColumns) : 0;
    const height = Math.round(width * .52 + 86 + legendHeight);
    const left = 80, right = width - 38, top = 31 + legendHeight, bottom = height - 52;
    const ink = '#183447', guide = '#d5dde2', scene = [];
    const axis = column => display(axisName(column));
    const coordinate = value => Number(value.toFixed(4));
    const text = (x, y, value, alignment = 'start', size = 11) => {
      scene.push({kind:'text', x:coordinate(x),y:coordinate(y),value,alignment,size,color:ink});
      return `<text x="${coordinate(x)}" y="${coordinate(y)}" text-anchor="${alignment}" font-size="${size}">${escape(value)}</text>`;
    };
    const line = (x1, y1, x2, y2, color, segment = null) => {
      const from = [coordinate(x1),coordinate(y1)], to = [coordinate(x2),coordinate(y2)];
      const weight = segment ? 1.45 : 1;
      scene.push({kind:'line', from, to, color, weight, segment});
      const details = segment ? ` data-from-row="${segment.from.dataRow}" data-to-row="${segment.to.dataRow}"` : '';
      return `<line x1="${from[0]}" y1="${from[1]}" x2="${to[0]}" y2="${to[1]}" stroke="${color}" stroke-width="${weight}"${details}/>`;
    };
    const content = [text(left, 16, short(axis(stats.source.y), Math.floor((right - left) / 8)), 'start', 13)];
    const yDomain = numericDomain(points.map(row => row.y));
    const xDomain = categories ? null : numericDomain(stats.rows.filter(row => row.x !== null &&
      row.exclusion !== 'outside_group' && row.exclusion !== 'missing_group').map(row => row.x));
    const categoryIndex = categories ? new Map(categories.map((value, i) => [value, i])) : null;
    const x = value => categories ? left + (right - left) * (categoryIndex.get(value) + .5) / categories.length :
      left + (right - left) * ((value - xDomain[0]) / (xDomain[1] - xDomain[0]));
    const y = value => bottom - (bottom - top) * ((value - yDomain[0]) / (yDomain[1] - yDomain[0]));
    const ticks = (range, count) => [...new Set(Array.from({length: count}, (_, i) =>
      range[0] + (range[1] - range[0]) * i / (count - 1)))];
    const formatted = values => new Set(values.map(csv.format)).size === values.length ? csv.format : display;
    const yTicks = ticks(yDomain, 5), yLabel = formatted(yTicks);
    for (const value of yTicks) {
      const yy = y(value), label = yLabel(value);
      content.push(line(left, yy, right, yy, guide), text(left - 7, yy + 4, label, 'end', label.length > 13 ? 9 : 11));
    }
    if (yDomain[0] < 0 && yDomain[1] > 0) content.push(line(left, y(0), right, y(0), ink));
    content.push(line(left, top, left, bottom, ink), line(left, bottom, right, bottom, ink));
    const categoryLabel = value => short(display(value), 11);
    const labelWidth = categories ? Math.max(28, Math.max(...categories.map(value => [...categoryLabel(value)].length)) * 9 + 13) : 63;
    const maxTicks = Math.max(2, Math.floor((right - left) / labelWidth));
    const stride = categories ? Math.max(1, Math.ceil(categories.length / maxTicks)) : 1;
    const xTicks = categories ? categories.filter((_, i) => i === categories.length - 1 ||
      (i % stride === 0 && (i === 0 || categories.length - 1 - i >= stride))) : ticks(xDomain, Math.min(5, maxTicks));
    const xLabel = categories ? categoryLabel : formatted(xTicks);
    for (const value of xTicks) {
      const xx = x(value);
      content.push(line(xx, bottom, xx, bottom + 4, ink), text(xx, bottom + 17, xLabel(value), 'middle'));
    }
    content.push(text((left + right) / 2, height - 10, short(axis(stats.source.x), Math.floor((right - left) / 8)), 'middle', 13));
    const symbols = ['circle', 'square', 'diamond', 'triangle'];
    const symbol = (xx, yy, i, attributes = '', title = '', row = null) => {
      const cx = coordinate(xx), cy = coordinate(yy), color = palette[i];
      const outlined = i >= symbols.length;
      const fill = outlined ? '#fff' : color;
      const shape = symbols[i % symbols.length];
      scene.push({kind:'symbol', x:cx, y:cy, color, fill, shape, row});
      const style = `fill="${fill}" stroke="${color}" stroke-width="1.15" ${attributes}`;
      const tip = title ? `<title>${escape(title)}</title>` : '';
      switch (shape) {
        case 'square': return `<rect x="${cx - 3}" y="${cy - 3}" width="6" height="6" ${style}>${tip}</rect>`;
        case 'diamond': return `<path d="M${cx} ${cy - 4}l4 4l-4 4l-4 -4Z" ${style}>${tip}</path>`;
        case 'triangle': return `<path d="M${cx} ${cy - 4}l4 7h-8Z" ${style}>${tip}</path>`;
        default: return `<circle cx="${cx}" cy="${cy}" r="3.2" ${style}>${tip}</circle>`;
      }
    };
    const marks = new Map(stats.groups.map((group, i) => [group.group, i]));
    stats.groups.forEach((group, i) => {
      if (hasLegend) {
        const xx = left + (right - left) * (i % legendColumns) / legendColumns;
        const yy = 30 + 16 * Math.floor(i / legendColumns);
        content.push(symbol(xx, yy - 3, i), text(xx + 10, yy, short(display(group.group), widthMm < 120 ? 8 : 14)));
      }
      if (stats.connect) {
        for (const segment of group.segments) {
          const {from, to} = segment;
          content.push(line(x(from.x), y(from.y), x(to.x), y(to.y), palette[i], segment));
        }
      }
    });
    for (const row of points) {
      const attributes = `id="${row.markId}" data-row="${row.dataRow}" data-source-line="${row.sourceLine ?? ''}" ` +
        `data-x="${escape(row.x)}" data-y="${escape(row.y)}"`;
      const tip = `CSV ${row.sourceLine ?? '?'} · X = ${row.x} · Y = ${row.y}${row.group === null ? '' : ` · ${row.group}`}`;
      content.push(symbol(x(row.x), y(row.y), marks.get(row.group), attributes, tip, row));
    }
    const heightMm = Number((height * 25.4 / 96).toFixed(4));
    const desc = stats.caption + (stats.source.sha256 ? `\n原文件 SHA-256 ${stats.source.sha256}` : '');
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="trend-title trend-desc" width="${widthMm}mm" height="${heightMm}mm" viewBox="0 0 ${width} ${height}">` +
      `<title id="trend-title">${escape(axis(stats.source.y) + ' / ' + axis(stats.source.x))}</title><desc id="trend-desc">${escape(desc)}</desc>` +
      `<rect width="100%" height="100%" fill="#fff"/><g fill="${ink}" font-family="system-ui, sans-serif">${content.join('')}</g></svg>`;
    return {svg, width, height, widthMm, heightMm, scene, sourceSha256:stats.source.sha256};
  }

  const texEscapes = {'\\':'\\textbackslash{}','{':'\\{','}':'\\}','$':'\\$','&':'\\&','#':'\\#',
    '%':'\\%','_':'\\_','^':'\\textasciicircum{}','~':'\\textasciitilde{}'};
  const latex = value => clean(value, 500).replace(/[\\{}$&#%_^~]/g, char => texEscapes[char]);
  const texPoint = (x,y) => `(${x},${y})`;
  const texColor = color => color === '#fff' ? 'white' : `ed${color.slice(1)}`;

  function tikz(rendered) {
    const lines = ['% Compile with XeLaTeX (standalone, TikZ, fontspec).',
      ...(rendered.sourceSha256 ? [`% Source SHA-256 ${rendered.sourceSha256}`] : []),
      '\\documentclass{standalone}', '\\usepackage{tikz}', '\\usepackage{fontspec}',
      '\\IfFontExistsTF{Noto Sans CJK SC}{\\setmainfont{Noto Sans CJK SC}}{}', '\\begin{document}',
      '\\begin{tikzpicture}[x=0.75bp,y=-0.75bp]'];
    const colors = [...new Set(rendered.scene.map(mark => mark.color))];
    for (const color of colors) lines.push(`\\definecolor{${texColor(color)}}{HTML}{${color.slice(1).toUpperCase()}}`);
    lines.push(`\\path[use as bounding box,fill=white] (0,0) rectangle (${rendered.width},${rendered.height});`);
    for (const mark of rendered.scene) {
      const color = texColor(mark.color);
      if (mark.kind === 'line') {
        if (mark.segment) lines.push(`% Segment records ${mark.segment.from.dataRow} ${mark.segment.to.dataRow}`);
        lines.push(`\\draw[draw=${color},line width=${mark.weight * .75}bp] ${texPoint(...mark.from)} -- ${texPoint(...mark.to)};`);
      } else if (mark.kind === 'text') {
        const anchor = {start:'base west',middle:'base',end:'base east'}[mark.alignment];
        lines.push(`\\node[anchor=${anchor},inner sep=0pt,text=${color},font=\\fontsize{${mark.size * .75}bp}` +
          `{${mark.size * .9}bp}\\selectfont] at ${texPoint(mark.x,mark.y)} {${latex(mark.value)}};`);
      } else {
        const {x,y,shape,row,fill} = mark;
        if (row) lines.push(`% Observation ${row.dataRow}, CSV line ${row.sourceLine ?? '?'}, X=${JSON.stringify(clean(row.x,500))}, Y=${row.y}`);
        const start = texPoint(x,y);
        let path;
        if (shape === 'circle') path = `${start} circle[radius=2.4bp]`;
        else if (shape === 'square') path = `${texPoint(x-3,y-3)} rectangle ${texPoint(x+3,y+3)}`;
        else {
          const corners = shape === 'diamond' ? [[x,y-4],[x+4,y],[x,y+4],[x-4,y]] : [[x,y-4],[x+4,y+3],[x-4,y+3]];
          path = corners.map(([a,b]) => texPoint(a,b)).join(' -- ') + ' -- cycle';
        }
        lines.push(`\\filldraw[draw=${color},fill=${texColor(fill)},line width=0.8625bp] ${path};`);
      }
    }
    return [...lines,'\\end{tikzpicture}','\\end{document}',''].join('\n');
  }

  const mermaidLabel = value => clean(value, value.length).replace(/["#<>&\\\[\]`]/g, char => `#${char.codePointAt(0)};`);

  function mermaid(stats) {
    const points = stats.rows.filter(row => row.plotted);
    if (!points.length) throw Error('所选范围没有可绘观测。');
    if (points.length > MAX_MERMAID_ROWS) throw Error(`Mermaid 观测图最多 ${MAX_MERMAID_ROWS} 个点。可按组导出。`);
    const lines = ['flowchart LR',
      '  %% Observation graph only. Edge lengths and geometry do not encode a numeric axis.',
      ...(stats.source.sha256 ? [`  %% Source SHA-256 ${stats.source.sha256}`] : []),
      `  observations["${mermaidLabel(`可绘观测 ${points.length} 条，未绘 ${stats.rows.length - points.length} 条。原行及缺失原因见数据映射。`)}"]`];
    const label = (axis, variable) => `${axisName(axis)} = ${variable}`;
    const ids = new Map(stats.groups.map((group, i) => [group.group, `group${i}`]));
    if (stats.source.group) {
      for (const [group,id] of ids) {
        lines.push(`  ${id}["${mermaidLabel(`${stats.source.group.column} = ${group}`)}"]`, `  observations --- ${id}`);
      }
    }
    for (const row of points) {
      lines.push(`  row${row.dataRow}["${mermaidLabel(`CSV ${row.sourceLine ?? '?'} · ${label(stats.source.x,row.xCell)} · ${label(stats.source.y,row.yCell)}`)}"]`);
      lines.push(`  ${stats.source.group ? ids.get(row.group) : 'observations'} --- row${row.dataRow}`);
    }
    if (stats.connect) for (const group of stats.groups) {
      for (const segment of group.segments) lines.push(`  row${segment.from.dataRow} --- row${segment.to.dataRow}`);
    }
    const source = [...lines,''].join('\n');
    if (source.length > 48000) throw Error('Mermaid 源码超过 48,000 字符。可按组导出。');
    return source;
  }

  function mermaidXY(stats) {
    const unavailable = reason => ({code:'', reason});
    if (stats.xKind !== 'numeric' || !stats.connect) return unavailable('数值趋势图需要数值横轴，并由用户选择连线。');
    if (stats.groups.length !== 1) return unavailable('数值趋势图需要一条系列。可选择一个分组。');
    const group = stats.groups[0];
    const rows = stats.focus === null ? stats.rows : stats.rows.filter(row => row.group === stats.focus);
    if (rows.length < 2) return unavailable('数值趋势图需要至少两个横轴位置。');
    if (rows.some(row => !row.plotted) || group.duplicates.length) {
      return unavailable('所选范围含缺失、无效或重复的观测位置。可用观测关系图保留各原行。');
    }
    const points = [...rows].sort((a,b) => a.x - b.x);
    const min = points[0].x, max = points.at(-1).x, step = (max - min) / (points.length - 1);
    // Mermaid 11.12 reconstructs X by repeated increments across the declared range.
    // Match every rendered coordinate and the position past the last point.
    let position = min;
    for (const point of points) {
      if (point.x !== position) return unavailable('横轴间距不能由 Mermaid 的等距数值轴逐点复现。可用 SVG 或 TikZ。');
      position += step;
    }
    if (!(position > max)) return unavailable('横轴间距不能由 Mermaid 的等距数值轴逐点复现。可用 SVG 或 TikZ。');
    const values = points.map(point => point.y);
    const yBounds = numericDomain(values);
    const decimal = value => /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(String(value));
    if (![min,max,...yBounds,...values].every(decimal)) {
      return unavailable('数值趋势图需要普通十进制数字表示。请选择 SVG 或 TikZ 核对这组数值。');
    }
    const x = axisName(stats.source.x), y = axisName(stats.source.y);
    const chartScope = `${x} ${min}–${max} / ${y}`;
    const title = group.group === null ? chartScope : `${stats.source.group.column} = ${group.group} · ${chartScope}`;
    const lines = ['---', 'config:', '  themeVariables:', '    xyChart:',
      `      plotColorPalette: '${palette[0]}'`, '---', 'xychart',
      ...(stats.source.sha256 ? [`  %% Source SHA-256 ${stats.source.sha256}`] : []),
      `  %% CSV source lines ${points.map(point => point.sourceLine ?? '?').join(', ')}`,
      `  title "${mermaidLabel(title)}"`, `  x-axis "${mermaidLabel(x)}" ${min} --> ${max}`,
      `  y-axis "${mermaidLabel(y)}" ${yBounds[0]} --> ${yBounds[1]}`,
      `  line [${values.join(', ')}]`, ''];
    const code = lines.join('\n');
    return code.length <= 48000 ? {code, reason:null} : unavailable('Mermaid 源码超过 48,000 字符。可按组导出。');
  }

  async function png(rendered) {
    const image = new Image();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(Error('无法生成栅格预览。'));
      image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(rendered.svg);
    });
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(rendered.width * 300 / 96);
    canvas.height = Math.round(rendered.height * 300 / 96);
    canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw Error('无法导出 PNG。');
    return new Blob([csv.pngAt300Dpi(await blob.arrayBuffer())], {type: 'image/png'});
  }

  const api = {analyze, figure, png, tikz, mermaid, mermaidXY, groupValues, MAX_MERMAID_ROWS};
  if (typeof module !== 'undefined') module.exports = api;
  else root.EvidenceDeskCSVTrends = api;
})(globalThis);
