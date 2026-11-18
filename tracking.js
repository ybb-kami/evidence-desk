(() => {
  'use strict';
  const section = document.getElementById('tracking');
  if (!section) return;
  section.classList.add('panel', 'compare');
  section.tabIndex = -1;
  section.removeAttribute('aria-label');
  section.setAttribute('aria-labelledby', 'tracking-title');
  const storageKey = 'evidence-desk.arxiv.votes.v1';
  const categories = [
    ['cs.AI', '人工智能'], ['cs.CL', '计算语言学'], ['cs.CV', '计算机视觉'],
    ['cs.LG', '机器学习'], ['stat.ML', '统计机器学习'], ['math.CO', '组合数学'], ['math.OC', '优化与控制']
  ];
  const idPattern = /^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]{0,30}\/\d{7})$/;
  const votes = new Map();
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || '{}');
    for (const [id, vote] of Object.entries(stored).slice(-100)) {
      if (idPattern.test(id) && [1, -1].includes(vote)) votes.set(id, vote);
    }
  } catch { /* Storage unavailable or invalid: keep votes in this page only. */ }

  function node(tag, cls = '', text = '') {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    element.textContent = text;
    return element;
  }
  function link(text, href) {
    const a = node('a', '', text);
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    return a;
  }
  function button(text, cls = 'compact') {
    const btn = node('button', cls, text);
    btn.type = 'button';
    return btn;
  }
  function repoLink(value) {
    return typeof value === 'string' &&
      /^https:\/\/github\.com\/[a-zA-Z0-9][a-zA-Z0-9-]{0,38}\/[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,99}$/.test(value)
      ? value : null;
  }

  const header = node('h2', '', 'arXiv 预印本追踪');
  header.id = 'tracking-title';
  const disclosure = node('p', 'subtle', '仅在本次同意后，向 arXiv 发送下面选中的公开分类。文库、文件名、摘录和反馈不出本机。结果是预印本题录，不代表已同行评审或核实结论。');
  disclosure.id = 'tracking-disclosure';
  const form = node('form');
  const label = node('label', '', '公开分类');
  const category = node('select');
  category.id = 'tracking-category';
  label.htmlFor = category.id;
  for (const [code, name] of categories) {
    const option = node('option', '', code + ' · ' + name);
    option.value = code;
    category.append(option);
  }
  const consent = node('input');
  consent.type = 'checkbox';
  consent.setAttribute('aria-describedby', disclosure.id);
  const consentLabel = node('label');
  consentLabel.append(consent, ' 同意本次公开分类请求');
  const consentRow = node('p');
  consentRow.append(consentLabel);
  const submit = button('刷新并在本机排序', 'primary');
  submit.type = 'submit';
  const clear = button('清空本地反馈');
  form.append(label, category, disclosure, consentRow, submit, ' ', clear);
  const explanation = node('p', 'subtle', '排序结合词项重合、GitHub 仓库提及和你在此浏览器的反馈。仓库只显示题录所含链接，不主动打开或验证。不后台运行，不区分用户账号。');
  const status = node('p', 'subtle', '尚未刷新。');
  status.setAttribute('role', 'status');
  const source = node('p', 'subtle');
  const feedbackStatus = node('p', 'subtle');
  feedbackStatus.setAttribute('role', 'status');
  const results = node('ul', 'card-list');
  results.setAttribute('aria-label', 'arXiv 预印本推荐');
  section.append(header, form, explanation, status, source, feedbackStatus, results);

  let papers = [];
  let busy = false;
  function controls() {
    submit.disabled = busy || !consent.checked;
    category.disabled = busy;
    consent.disabled = busy;
    clear.disabled = !votes.size;
  }
  function persist(message) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(votes)));
      feedbackStatus.textContent = message;
    } catch {
      feedbackStatus.textContent = message + ' 浏览器存储不可用，本次修改可能仅在当前页面有效。';
    }
  }
  function render(focus) {
    results.replaceChildren();
    controls();
    const score = row => row.recommendation.base_score + 18 * (votes.get(row.id) || 0);
    const sorted = [...papers].sort((a, b) => score(b) - score(a)
      || String(b.published).localeCompare(String(a.published)) || b.id.localeCompare(a.id));
    for (const row of sorted) {
      const card = node('li', 'evidence');
      const meta = node('div', 'metadata', row.id + ' · 首次提交 ' + String(row.published).slice(0, 10)
        + ' · 最近更新 ' + String(row.updated).slice(0, 10));
      const title = node('h3');
      title.append(link(row.title, 'https://arxiv.org/abs/' + row.id));
      const authors = node('p', 'subtle', Array.isArray(row.authors) ? row.authors.join('、') : '');
      const notes = Array.isArray(row.recommendation.reasons) ? row.recommendation.reasons.slice() : [];
      if (votes.get(row.id)) notes.push(votes.get(row.id) === 1 ? '你标记了感兴趣' : '你标记了不相关');
      const reasons = node('p', 'subtle', notes.join('；') + '。');
      const repo = repoLink(row.repository_url);
      card.append(meta, title, authors, reasons);
      if (repo) {
        const repoRow = node('p', 'subtle');
        repoRow.append(link('查看 GitHub 线索（未核实）', repo));
        card.append(repoRow);
      }
      const voteRow = node('div');
      for (const [value, name] of [[1, '感兴趣'], [-1, '不相关']]) {
        const voteButton = button(name);
        const pressed = votes.get(row.id) === value;
        voteButton.setAttribute('aria-pressed', String(pressed));
        voteButton.setAttribute('aria-label', name + '：' + row.title);
        if (pressed) voteButton.classList.add('primary');
        voteButton.addEventListener('click', () => {
          const same = votes.get(row.id) === value;
          votes.delete(row.id);
          if (!same) votes.set(row.id, value);
          if (votes.size > 100) votes.delete(votes.keys().next().value);
          persist(same ? '已撤销此条反馈。' : '已记录反馈，并在本机重排。');
          render([row.id, value]);
        });
        if (focus?.[0] === row.id && focus[1] === value)
          queueMicrotask(() => voteButton.focus({preventScroll: true}));
        voteRow.append(voteButton, ' ');
      }
      card.append(voteRow);
      results.append(card);
    }
  }
  clear.addEventListener('click', () => {
    votes.clear();
    persist('已清空此浏览器的 arXiv 反馈。');
    render();
    category.focus();
  });
  category.addEventListener('change', () => {
    consent.checked = false;
    papers = [];
    status.textContent = '尚未刷新所选分类。';
    source.textContent = '';
    feedbackStatus.textContent = '';
    render();
  });
  consent.addEventListener('change', controls);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !consent.checked) return;
    busy = true;
    consent.checked = false;
    papers = [];
    render();
    feedbackStatus.textContent = '';
    status.textContent = '正在向 arXiv 获取公开题录…';
    source.textContent = '';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 11000);
    try {
      const response = await fetch('/api/tracking', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        signal: controller.signal,
        body: JSON.stringify({category: category.value, consent: true, feedback: Object.fromEntries(votes)})
      });
      const body = await response.json();
      if (!response.ok || body.error) {
        status.textContent = typeof body.error === 'string' ? body.error
          : body.error?.message || '刷新失败，可继续使用本地文库。';
        return;
      }
      papers = Array.isArray(body.items) ? body.items.filter(item => item && typeof item.id === 'string'
        && !item.id.includes('\n') && idPattern.test(item.id) && typeof item.title === 'string'
        && Number.isFinite(item.recommendation?.base_score)) : [];
      status.textContent = papers.length ? '获取 ' + papers.length + ' 条题录，已在本机排序。' : '此分类本次没有返回可用题录。';
      const fetched = new Date(body.retrieved_at);
      if (body.retrieved_at && !Number.isNaN(fetched.valueOf()))
        source.textContent = '来源：arXiv · ' + category.value + ' · 获取于 ' + fetched.toLocaleString();
    } catch (error) {
      status.textContent = error.name === 'AbortError' ? '等待本地服务超时，未自动重试。' : '刷新失败，可继续使用本地文库。';
    } finally {
      clearTimeout(timer);
      busy = false;
      render();
    }
  });
  controls();
})();
