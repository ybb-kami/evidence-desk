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
    let protocol = '定义一个可观察的结果、一个对照条件，以及何种结果会使研究假设不成立。';
    if (/OCR|扫描|表格识别|公式识别|文本识别/i.test(title)) {
      protocol = '按版式与扫描质量抽样，预先固定物理页和人工标注。文字记录字符错误率，表格核对行列表头与数值，公式分开核对定位与转写。';
    } else if (/检索|问答|引用溯源|retrieval|\bRAG\b/i.test(title)) {
      protocol = '建立问题、作答所需原文和物理页的对应表，用相同文库比较基线与候选方法。分别记录检索召回、答案是否有据以及缺少证据时的表现。';
    } else if (/比较|对比|benchmark|versus|消融/i.test(title)) {
      protocol = '明确比较对象，共用相同数据与指标。预先约定划分、重复次数和消融条件，记录差值与不确定性。';
    }
    const related = list.length
      ? '逐项对照 ' + list.map((_, i) => 'E' + (i + 1)).join('、') +
        ' 在研究对象、方法及评价条件上的异同。为每项实际引用保留对应 R 编号，移除与研究问题无关的摘录。'
      : '检索与该问题直接相关的原始论文，再记录各工作的条件、方法与局限。';
    return {
      count:evidence.length,
      markdown:[
        '# ' + title,
        '> 本地填写模板；未使用模型撰写论文。下列证据为检索结果，不自动支持任何主张。发布前请自行核对原件及完整书目信息。' +
        ' 本地链接需配合同一个证据台文库与端口使用，不能供其他电脑访问。' +
        (localOrigin ? '' : ' 若在外部 Markdown 阅读器查看，可在文件路径前补写当前本机服务地址。'),
        '## 摘要（Abstract）\n\n围绕“' + title + '”用一句话界定适用对象和拟检验的假设。完成实验后补写主要结果及适用范围。',
        '## 引言（Introduction）\n\n说明“' + title + '”针对的实际困难。列出已有工作已解决的问题，以及本研究可以验证的具体差距。',
        '## 相关工作（Related Work）\n\n' + related,
        '## 方法、结果与限制\n\n' + protocol + ' 结果部分附原始数据来源、指标单位与个别失败样本。',
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
  // Each line is an author-supplied chain. Repeated labels represent shared nodes.
  function methodDiagram(input) {
    if (typeof input !== 'string' || !input.trim() || input.length > 4000)
      throw new Error('请输入方法步骤，每行用 → 或 -> 连接。');
    const labels = new Map(), edges = new Set();
    for (const line of input.split(/\r?\n/).filter(s => s.trim())) {
      const chain = line.split(/→|->/).map(s => s.trim());
      if (chain.length < 2 || chain.some(s => !s || s.length > 100 || /[\u0000-\u001f\u007f]/.test(s)))
        throw new Error('每行至少需要两个步骤，每个步骤为 1 至 100 个字符。');
      for (const text of chain) if (!labels.has(text)) labels.set(text, 'n' + labels.size);
      for (let i = 1; i < chain.length; i++) edges.add(labels.get(chain[i-1]) + ' -> ' + labels.get(chain[i]));
    }
    if (labels.size > 60 || edges.size > 100) throw new Error('单图最多 60 个步骤、100 条连接。');
    const lines = ['digraph Method {', '  rankdir=LR;', '  node [shape=box];'];
    for (const [label,id] of labels) lines.push('  ' + id + ' [label=' + JSON.stringify(label) + '];');
    for (const edge of edges) lines.push('  ' + edge + ';');
    return [...lines, '}', ''].join('\n');
  }

  function attachDiagram(panel) {
    const doc = panel.ownerDocument;
    const label = doc.createElement('label');
    label.htmlFor = 'method-steps';
    label.textContent = '论文方法图 · 用箭头连接步骤，相同名称会合并为同一节点';
    const steps = doc.createElement('textarea');
    steps.id = label.htmlFor;
    steps.rows = 3;
    steps.maxLength = 4000;
    steps.placeholder = '输入文献 → 编码 → 排序\n排序 → 原文核对';
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'compact';
    button.textContent = '下载方法图 DOT';
    const message = doc.createElement('p');
    message.setAttribute('role','status');
    button.onclick = () => {
      let source;
      try { source = methodDiagram(steps.value); }
      catch (err) { message.textContent = err.message; return; }
      const link = doc.createElement('a');
      link.href = URL.createObjectURL(new Blob([source],{type:'text/vnd.graphviz;charset=utf-8'}));
      link.download = 'paper-method.dot';
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      message.textContent = '已生成 Graphviz DOT，可编辑后渲染。';
    };
    panel.append(label,steps,button,message);
  }

  const api = {paper,mermaid,methodDiagram,MAX_TOPIC,MAX_EVIDENCE};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    root.EvidenceDeskWriting = api;
    const panel = root.document && root.document.getElementById('writing-workbench');
    if (panel) attachDiagram(panel);
  }
})(globalThis);
