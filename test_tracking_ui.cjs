// Browser-state contract using a local DOM harness; no external calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Node {
  constructor(tag) {
    this.tag = tag; this.children = []; this.events = {}; this.attrs = {};
    this.classList = { add() {} }; this._text = '';
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(k, fn) { this.events[k] = fn; }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  set textContent(s) { this._text = s; this.children = []; }
  get textContent() { return this._text + this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
  focus() { focused = this; }
}
let focused;
function query(node, test) {
  if (typeof node === 'string') return;
  if (test(node)) return node;
  for (const child of node.children) {
    const found = query(child, test);
    if (found) return found;
  }
}
const root = new Node('section');
const doc = {getElementById: id => id === 'tracking' && root, createElement: tag => new Node(tag)};
let storage = '{}', calls = [], pending;
const localStorage = {getItem: () => storage, setItem: (_key, value) => { storage = value; }};
const fakeFetch = (url, args) => {
  calls.push({url, request: JSON.parse(args.body)});
  return new Promise(resolve => { pending = resolve; });
};
vm.runInNewContext(fs.readFileSync('tracking.js', 'utf8'), {
  document: doc, localStorage, fetch: fakeFetch, AbortController, setTimeout, clearTimeout, queueMicrotask
});
const byTag = (tag, predicate = () => true) => query(root, n => n.tag === tag && predicate(n));
const btn = (text) => byTag('button', n => n.textContent === text);
const select = byTag('select'), checkbox = byTag('input');
const send = () => byTag('form').events.submit({preventDefault() {}});
const article = id => byTag('li', n => n.textContent.includes(id));
const card = (id, title, score, repository_url) => ({
  id, title, repository_url, published: '2026-11-18T12:00:00Z', updated: '2026-11-18T12:00:00Z',
  authors: [], recommendation: {base_score: score, reasons: ['fixture']},
});
(async () => {
  select.value = 'math.CO';
  assert.equal(btn('刷新并在本机排序').disabled, true);
  await send();
  assert.equal(calls.length, 0);
  checkbox.checked = true;
  checkbox.events.change();
  const active = send();
  await send();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], {url: '/api/tracking', request: {category: 'math.CO', consent: true, feedback: {}}});
  pending({ok: true, json: async () => ({items: [
    card('2611.00001', '<img src=x>', 8, 'https://github.com/valid/repo'),
    card('2611.00002', 'Another', 0, 'javascript:alert(1)'),
    card('2611.00003\n', 'Not valid', 100),
  ]})});
  await active;
  assert.equal(byTag('img'), undefined);
  assert.match(article('2611.00001').textContent, /<img src=x>/);
  assert.ok(query(article('2611.00001'), n => n.href === 'https://github.com/valid/repo'));
  assert.equal(query(article('2611.00002'), n => n.href?.startsWith('javascript:')), undefined);
  assert.equal(article('2611.00003'), undefined);
  assert.equal(checkbox.checked, false);
  assert.equal(btn('刷新并在本机排序').disabled, true);
  query(article('2611.00001'), n => n.textContent === '不相关').events.click();
  await Promise.resolve();
  assert.equal(byTag('li'), article('2611.00002'));
  assert.equal(focused.attrs['aria-pressed'], 'true');
  assert.deepEqual(JSON.parse(storage), {'2611.00001': -1});
  btn('清空本地反馈').events.click();
  assert.deepEqual(JSON.parse(storage), {});
  assert.equal(byTag('li'), article('2611.00001'));
  assert.equal(calls.length, 1);
  checkbox.checked = true;
  select.value = 'cs.CL'; select.events.change();
  assert.equal(checkbox.checked, false);
  assert.equal(byTag('li'), undefined);
  console.log('tracking consent, repeat guard, local feedback/reordering, source links, and text rendering PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
