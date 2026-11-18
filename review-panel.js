(() => {
  'use strict';
  const root = document.getElementById('local-review');
  if (!root) return;

  function node(tag, text) {
    const element = document.createElement(tag);
    element.textContent = text;
    return element;
  }

  function excerpt(parent, row) {
    const link = node('a', row.source_document_name + ' · PDF 第 ' + row.page + ' 页，第 ' + row.block + ' 块');
    link.href = '/pdf/' + encodeURIComponent(row.source_document) + '#page=' + row.page;
    link.target = '_blank';
    link.rel = 'noopener';
    parent.append(node('blockquote', row.text), link);
  }

  function section(parent, label, rows) {
    parent.append(node('h3', label));
    if (!rows.length) parent.append(node('p', '没有可核对的条目。'));
    for (const row of rows) {
      if (row.target) parent.append(node('p', row.source + ' → ' + row.target));
      excerpt(parent, row);
    }
  }

  function laterCandidates(parent, candidates) {
    parent.append(
      node('h3', '日期更晚的本地版本 · 待人工核查'),
      node('p', '本检查使用 PDF 首页 arXiv 版本日期、较晚版本指向原作品的参考文献，以及正文与原方向摘录共享的英文主题词。每份 PDF 检查首条未来工作摘录。版本日期只描述手头版本。请结合原文判断这些摘录是否回应了该方向。')
    );
    if (!candidates.length) {
      parent.append(node('p', '当前文库未找到同时满足上述条件的候选。'));
    }
    for (const candidate of candidates) {
      const detail = node('details', '');
      detail.append(
        node('summary', candidate.later_excerpt.source_document_name + ' · ' + candidate.later_stamp.date),
        node('p', '匹配词：' + candidate.matched_terms.join('、')),
        node('h4', '原方向摘录')
      );
      excerpt(detail, candidate.direction);
      detail.append(node('h4', '较晚版本的正文摘录'));
      excerpt(detail, candidate.later_excerpt);
      detail.append(node('h4', '较晚版本的参考文献条目'));
      excerpt(detail, candidate.citation);
      detail.append(node('h4', '首页版本日期依据'));
      excerpt(detail, candidate.earlier_stamp);
      excerpt(detail, candidate.later_stamp);
      parent.append(detail);
    }
  }

  const button = node('button', '生成本库综述提要');
  const output = node('div', '');
  output.setAttribute('aria-live', 'polite');
  root.append(node('h2', '从引用到综述提要'), node('p', '仅使用当前活动文库；附原页，不联网推测前沿。'), button, output);
  button.onclick = async () => {
    button.disabled = true;
    output.replaceChildren(node('p', '正在分析本地文库…'));
    try {
      const response = await fetch('/api/local-review', {cache: 'no-store'});
      if (!response.ok) throw Error('读取本地文库失败');
      const data = await response.json();
      output.replaceChildren(node('p', data.summary));
      section(output, '可核对的引用', data.edges);
      section(output, '含未来工作措辞的候选摘录', data.future_mentions);
      laterCandidates(output, data.later_candidates || []);
      output.append(node('p', data.limits));
    } catch (error) {
      output.replaceChildren(node('p', error.message));
    } finally {
      button.disabled = false;
    }
  };
})();
