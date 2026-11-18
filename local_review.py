"""Extractive, limited-scope synthesis for the active PDF collection; no network."""
import re
from graph import build_graph
from partner_frontier import later_candidates
FUTURE = re.compile(r'\b(?:future (?:work|research)|further research|remains? (?:an )?open (?:question|problem))\b|未来(?:的)?(?:工作|研究)|有待进一步研究',re.I)
def collection_review(docs):
    graph=build_graph(docs)
    ns,es=graph['nodes'],graph['edges']
    lookup={n['id']:n.get('title') or n['name'] for n in ns}
    anchors=[]
    for e in es[:12]:
        anchors.append({'source':lookup[e['source']],'target':lookup[e['target']],
            **{k:e[k] for k in ('source_document','source_document_name','page','block','text')}})
    directions=[]
    for d in docs:
        if len(directions)>=12: break
        for c in d.get('chunks',[]):
            sentences=re.split(r'(?<=[.!?。！？])\s+|(?<=[。！？])',c['text'])
            quote=next((s.strip() for s in sentences if FUTURE.search(s) and 25<=len(s.strip())<=650),None)
            if quote:
                directions.append({'source_document':d['id'],'source_document_name':d['name'],'page':c['page'],'block':c['block'],'text':quote})
                break
    return {'summary':f'当前活动库有 {len(docs)} 份 PDF，按识别出的作品合并为 {len(ns)} 个节点，其中 {len(es)} 条有向引用可定位至本地原页。'
            + ('以下列出可回查的引用关系。引用不等于认同，图中缺边也不是没有引用的证明。' if es else '未找到可核对的本地引用边，不能据此推断观点共识。'),
            'edges':anchors,'future_mentions':directions,
            'later_candidates':later_candidates(docs,graph,directions),
            'limits':'仅为本库引文描述和关键词命中的原文摘录，不是系统性领域综述。未来工作语句可能转述他人观点，未查证相关问题今天是否仍然开放。后续阅读线索不等于问题已解决，不应据此舍弃原想法。'}
