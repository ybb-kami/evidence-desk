/* Evidence-backed drafting scaffold. This module never writes scientific claims. */
(function(root) {
  'use strict';
  const MAX_TOPIC = 180, MAX_EVIDENCE = 2;
  const ID = /^[a-f0-9]{64}$/;
  function inline(value, limit) {
    return value.slice(0,limit).replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g,' ')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/[\\`*_[\]{}#!|]/g,'\\$&')
      .trim();
  }
  function excerptBlock(text) {
    const longest = (text.match(/`+/g)||[]).reduce((n,s)=>Math.max(n,s.length),2);
    const fence = '`'.repeat(longest + 1);
    return fence + 'text\n' + text + (text.endsWith('\n') ? '' : '\n') + fence;
  }
  function validatedEvidence(values) {
    const evidence = [], seen = new Set();
    for (const row of values) {
      if (!row || typeof row.id !== 'string' || !ID.test(row.id) ||
          !Number.isSafeInteger(row.page) || row.page < 1 || row.page > 650 ||
          !Number.isSafeInteger(row.block) || row.block < 0 ||
          typeof row.name !== 'string' || !row.name.trim() || typeof row.text !== 'string' || !row.text.trim()) continue;
      const part = Number.isSafeInteger(row.part) && row.part > 0 ? row.part : 1;
      const key = [row.id,row.page,row.block,part].join(':');
      if (seen.has(key)) continue;
      evidence.push({id:row.id,page:row.page,block:row.block,part,name:row.name,text:row.text,
        pageLabel:typeof row.page_label === 'string' ? row.page_label : ''});
      seen.add(key);
      if (evidence.length === MAX_EVIDENCE) break;
    }
    return evidence;
  }
  function paper(topic, input = [], origin = '') {
    const localOrigin = /^http:\/\/(?:127\.0\.0\.1|localhost):[1-9]\d{0,4}$/.test(origin) ? origin : '';
    const title = inline(typeof topic === 'string' ? topic.trim() : '',MAX_TOPIC) || '〔待填：论文题目〕';
    const evidence = validatedEvidence(input);
    const refs = new Map();
    const list = evidence.map((row,i) => {
      if (!refs.has(row.id)) refs.set(row.id,{n:refs.size + 1,name:row.name});
      const n = refs.get(row.id).n;
      const label = row.pageLabel && row.pageLabel !== String(row.page) ? '，页面标签 ' + inline(row.pageLabel,160) : '';
      return '### E' + (i+1) + ' · 对应候选来源 R' + n + '\n\n' +
        '[本地 PDF 第 ' + row.page + ' 页](' + localOrigin + '/pdf/' + row.id + '#page=' + row.page + ')' + label +
        ' · 文本块 ' + row.block + ' · 片段 ' + row.part + '\n\n' + excerptBlock(row.text);
    });
    const formatted = [...refs].map(([id,ref]) => '- R' + ref.n +
      ' · 文件名：' + inline(ref.name,160) + ' · [本地原件](' + localOrigin + '/pdf/' + id + ')');
    return {
      count:evidence.length,
      markdown:[
        '# ' + title,
        '> 本地填写模板；未使用模型撰写论文。下列证据为检索结果，不自动支持任何主张。发布前请自行核对原件及完整书目信息。' +
        ' 本地链接需配合同一个证据台文库与端口使用，不能供其他电脑访问。' +
        (localOrigin ? '' : ' 若在外部 Markdown 阅读器查看，可在文件路径前补写当前本机服务地址。'),
        '## 摘要（Abstract）\n\n〔待填：研究对象与问题；数据与方法；已验证的主要结果及其范围。〕',
        '## 引言（Introduction）\n\n〔待填：研究问题、事实背景与可检验的贡献。为每条外部事实查找可核验来源；保留重要边界。〕',
        '## 相关工作（Related Work）\n\n〔待填：按问题或方法分组，核对哪些条件可比、证据支持什么。不要依据相似关键词推断论文间关系。〕',
        '## 方法、结果与限制\n\n〔待填：数据来源、指标/单位、对照、观察结果、解释及适用条件；明确区分观察与因果。〕',
        '## 证据核对清单\n\n' + (list.length ? list.join('\n\n') : '未选中可核对的本地证据。没有插入任何引文。'),
        '## 参考文献待核（References）\n\n' + (formatted.length ?
          '仅列已选证据指向的本地 PDF 文件。不猜测作者、题名、年份、出处或 DOI。\n\n' + formatted.join('\n') :
          '没有可列出的已选来源。')
      ].join('\n\n') + '\n'
    };
  }
  // A schematic of the shipped workbench, not arbitrary auto-generated system design.
  const mermaid = [
    'flowchart LR',
    '  User["用户浏览器"]',
    '  App["本机 Python HTTP 服务"]',
    '  DB["本地 SQLite：PDF 与文本索引"]',
    '  PDF["原件页链接"]',
    '  Access["本地词法检索、版本对照、引文图"]',
    '  CSV["浏览器内 CSV 描述统计"]',
    '  Draft["浏览器内论文框架与证据清单"]',
    '  Crossref["Crossref 公开题录"]',
    '  DeepSeek["可选 DeepSeek 摘录核选"]',
    '  User --> App',
    '  App --> DB',
    '  DB --> PDF',
    '  DB --> Access',
    '  User --> CSV',
    '  Access --> Draft',
    '  App -.->|逐次同意，仅公开关键词| Crossref',
    '  App -.->|配置后逐次同意，查询与摘录| DeepSeek',
    ''
  ].join('\n');
  const api = {paper,mermaid,MAX_TOPIC,MAX_EVIDENCE};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EvidenceDeskWriting = api;
})(globalThis);
