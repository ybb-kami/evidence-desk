"""Extractive review of the active PDF collection."""

from collections import defaultdict
from datetime import date
import re
import unicodedata

from engine import _ARXIV, _STOPWORDS
from graph import _HEADING, build_graph

FUTURE = re.compile(
    r'\b(?:future (?:work|research)|further research|remains? (?:an )?open (?:question|problem))\b'
    r'|未来(?:的)?(?:工作|研究)|有待进一步研究', re.I,
)
SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?。！？])\s+|(?<=[。！？])')
STAMP_DATE = re.compile(r'\s(\d{1,2}) ([A-Za-z]{3}) (\d{4})$')
MONTHS = {
    month: number for number, month in enumerate(
        'jan feb mar apr may jun jul aug sep oct nov dec'.split(), 1
    )
}
TOPIC_WORD = re.compile(r'\b[a-z]{3,}(?:-[a-z]{3,})*\b')
GENERIC_WORDS = _STOPWORDS | frozenset(
    'address also analysis appendix approach approaches author authors based data develop direction directions discussion '
    'evaluate evaluated evaluation experiments explore explored find findings fine-tune fine-tuned '
    'figure fine-tuning framework further future improve improvement include investigated investigate '
    'learning method methods model models more need novel open our paper papers performance potential '
    'pre-train pre-trained pre-training pretrain pretrained pretraining previous problem problems '
    'progress promising propose proposed question questions remains research result results section sections see show study '
    'studies suggest system systems table task tasks techniques test them training use used using way work works'.split()
)
MAX_EXCERPTS = 12


def _anchor(document, chunk, text):
    return {
        'source_document': document['id'],
        'source_document_name': document['name'],
        'page': chunk['page'],
        'block': chunk['block'],
        'text': text,
    }


def _citation_anchor(edge):
    return {key: edge[key] for key in ('source_document', 'source_document_name', 'page', 'block', 'text')}


def _version_stamp(document):
    """Find one unambiguous full date on a recognized arXiv version stamp."""
    identity = document.get('arxiv') or {}
    stamps = {}
    for chunk in document.get('chunks', ()):
        if chunk['page'] != 1 or chunk.get('arxiv_stamp') is not True:
            continue
        text = ' '.join(chunk['text'].split())
        marker = _ARXIV.fullmatch(text)
        if (
            not marker or not marker[2] or marker[1].lower() != identity.get('id')
            or int(marker[2]) != identity.get('version')
        ):
            return None
        parts = STAMP_DATE.search(text)
        if not parts:
            return None
        month = MONTHS.get(parts[2].lower())
        if not month:
            return None
        try:
            stamped = date(int(parts[3]), month, int(parts[1]))
        except ValueError:
            return None
        stamps.setdefault(stamped, {
            **_anchor(document, chunk, chunk['text']), 'date': stamped.isoformat(),
        })
    if len(stamps) == 1:
        return next(iter(stamps.values()))
    return None


def _topic_terms(text):
    normalized = unicodedata.normalize('NFKC', text).casefold()
    # Rejoin words hyphenated at a PDF line wrap.
    normalized = re.sub(r'(?<=[a-z])[-\u00ad] *\n\s*(?=[a-z])', '', normalized)
    return [word for word in TOPIC_WORD.findall(normalized) if word not in GENERIC_WORDS]


def _body_chunks(document):
    """Conservatively scan the pre-bibliography text of the citing PDF."""
    chunks = sorted(document.get('chunks', ()), key=lambda chunk: (
        chunk['page'], chunk['block'], chunk.get('offset', 0)
    ))
    for chunk in chunks:
        first_line = chunk['text'].split('\n', 1)[0]
        if _HEADING.fullmatch(' '.join(first_line.split())):
            break
        if not chunk.get('arxiv_stamp'):
            yield chunk


def _body_passages(document):
    for chunk in _body_chunks(document):
        for sentence in SENTENCE_BOUNDARY.split(chunk['text']):
            excerpt = sentence.strip()
            if 25 <= len(excerpt) <= 650:
                terms = _topic_terms(excerpt)
                if len(set(terms)) >= 3:
                    yield chunk, excerpt, set(terms), set(zip(terms, terms[1:]))


def _find_passage(passages, direction):
    terms = _topic_terms(FUTURE.sub('', direction))
    term_set = set(terms)
    pairs = set(zip(terms, terms[1:]))
    best = None
    best_score = (0, 0)
    for chunk, excerpt, passage_terms, passage_pairs in passages:
        overlap = passage_terms & term_set
        # A shared adjacent pair of retained terms and a third distinct term
        # are stronger evidence than a single generic subject.
        phrase_hits = {pair for pair in passage_pairs & pairs if pair[0] != pair[1]}
        if len(overlap) < 3 or not phrase_hits:
            continue
        score = len(overlap), len(phrase_hits)
        if score > best_score:
            best = chunk, excerpt, sorted(overlap)
            best_score = score
    return best


def _later_candidates(documents, graph, directions):
    if not directions or not graph['edges']:
        return []
    documents = {doc['id']: doc for doc in documents}
    work_by_copy = {
        copy['id']: node['id'] for node in graph['nodes'] for copy in node['documents']
    }
    incoming = defaultdict(list)
    for edge in graph['edges']:
        incoming[edge['target']].append(edge)

    stamps = {id_: _version_stamp(doc) for id_, doc in documents.items()}
    passages = {}
    matches = []
    for direction in directions:
        earlier_id = direction['source_document']
        earlier_stamp = stamps[earlier_id]
        if not earlier_stamp or not any(
            chunk['page'] == direction['page'] and chunk['block'] == direction['block']
            and direction['text'] in chunk['text']
            for chunk in _body_chunks(documents[earlier_id])
        ):
            continue
        for edge in incoming[work_by_copy[earlier_id]]:
            later_id = edge['source_document']
            later_stamp = stamps[later_id]
            if not later_stamp or later_stamp['date'] <= earlier_stamp['date']:
                continue
            later_document = documents[later_id]
            if later_id not in passages:
                passages[later_id] = list(_body_passages(later_document))
            matched = _find_passage(passages[later_id], direction['text'])
            if not matched:
                continue
            chunk, excerpt, terms = matched
            matches.append({
                'direction': direction,
                'later_excerpt': _anchor(later_document, chunk, excerpt),
                'earlier_stamp': earlier_stamp,
                'later_stamp': later_stamp,
                'citation': _citation_anchor(edge),
                'matched_terms': terms,
            })
            if len(matches) >= MAX_EXCERPTS:
                return matches
    return matches


def collection_review(docs):
    graph = build_graph(docs)
    nodes, edges = graph['nodes'], graph['edges']
    names = {node['id']: node.get('title') or node['name'] for node in nodes}
    anchors = [
        {'source': names[edge['source']], 'target': names[edge['target']], **_citation_anchor(edge)}
        for edge in edges[:MAX_EXCERPTS]
    ]
    directions = []
    for document in docs:
        for chunk in _body_chunks(document):
            quote = next(
                (sentence.strip() for sentence in SENTENCE_BOUNDARY.split(chunk['text'])
                 if FUTURE.search(sentence) and 25 <= len(sentence.strip()) <= 650),
                None,
            )
            if quote:
                directions.append(_anchor(document, chunk, quote))
                break

    summary = f'当前活动库有 {len(docs)} 份 PDF，按识别出的作品合并为 {len(nodes)} 个节点，其中 {len(edges)} 条有向引用可定位至本地原页。'
    if edges:
        summary += '以下列出可回查的引用关系。引用不等于认同，图中缺边也不是没有引用的证明。'
    else:
        summary += '未找到可核对的本地引用边，不能据此推断观点共识。'
    return {
        'summary': summary,
        'edges': anchors,
        'future_mentions': directions[:MAX_EXCERPTS],
        'later_candidates': _later_candidates(docs, graph, directions),
        'limits': '仅检查当前活动库。未来工作语句可能转述他人观点，未查证问题今天是否仍然开放。',
    }
