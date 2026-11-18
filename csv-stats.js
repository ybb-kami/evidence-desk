/* Standalone local CSV parsing and analysis. No network or storage. */
(function(root) {
  'use strict';
  const MAX_BYTES = 512 * 1024, MAX_ROWS = 5000, MAX_COLUMNS = 40, MAX_CHART = 40;
  const numeric = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
  const format = v => String(Number(v.toPrecision(8)));
  function parse(input) {
    const text = input.replace(/^\uFEFF/, '');
    if (new TextEncoder().encode(text).length > MAX_BYTES) throw Error('CSV 超过 512 KiB。');
    const records = [];
    let row = [], cell = '', state = 0; // 0: bare/start, 1: quoted, 2: closed
    function column() {
      row.push(cell); cell = ''; state = 0;
      if (row.length > MAX_COLUMNS) throw Error('CSV 列数超过 40。');
    }
    function record() {
      const hasRecord = row.length || cell || state === 2;
      column();
      if (hasRecord) {
        records.push(row);
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
        } else cell += c;
      } else if (c === ',') column();
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        record();
      } else if (c === '"' && !cell && state === 0) state = 1;
      else if (state === 2 || c === '"') throw Error('CSV 引号格式错误。');
      else cell += c;
    }
    if (state === 1) throw Error('CSV 有未闭合的引号。');
    if (cell || row.length || state === 2) record();
    if (records.length < 2) throw Error('需要一行列名和至少一行数据。');
    const headers = records.shift().map(header => header.trim());
    if (headers.some(header => !header) || new Set(headers).size !== headers.length) throw Error('列名不能为空或重复。');
    if (records.some(row => row.length !== headers.length)) throw Error('有数据行的列数与表头不同。');
    return {headers, rows: records};
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
  function analyze(dataset, column, mode) {
    if (!dataset.headers[column]) throw Error('请选择数值列。');
    const values = [];
    let missing = 0, invalid = 0, sum = 0, corr = 0, min = Infinity, max = -Infinity;
    for (let i = 0; i < dataset.rows.length; i++) {
      const cell = dataset.rows[i][column].trim();
      if (!cell) { missing++; values.push(null); continue; }
      const n = nvalue(cell);
      if (n === null) { invalid++; values.push(null); continue; }
      values.push(n);
      min = Math.min(min, n); max = Math.max(max, n);
      const t = sum + n;
      corr += Math.abs(sum) >= Math.abs(n) ? (sum - t) + n : (n - t) + sum;
      sum = t;
    }
    const used = values.length - missing - invalid;
    const base = {used, missing, invalid, min: used ? min : null, max: used ? max : null, mean: used ? (sum + corr) / used : null};
    const visible = values.slice(0, MAX_CHART);
    const chartLabel = mode === 'bar' ? '柱状图' : '折线图';
    const caption = used ? '“' + dataset.headers[column] + '”' + chartLabel + '按 CSV 原始行序显示前 ' + visible.length +
      ' 条数据，空值和无法解析的值留空' + (mode === 'bar' ? '。' : '，折线断开。') + '全表共 ' + values.length + ' 行，有效数值 ' + used + ' 个，空值 ' + missing +
      ' 个，无法解析 ' + invalid + ' 个。全表有效值最小 ' + format(min) + '，最大 ' + format(max) +
      '，算术平均 ' + format(base.mean) + '。仅为描述性统计。' : '所选列没有可用数值。全表空值 ' + missing + ' 个，无法解析 ' + invalid + ' 个。';
    return {...base, visible, caption};
  }
  const api = {parse, analyze, format, MAX_BYTES, MAX_ROWS, MAX_COLUMNS, MAX_CHART};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EvidenceDeskCSV = api;
})(globalThis);
