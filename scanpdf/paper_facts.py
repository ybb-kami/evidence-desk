"""Small, source-anchored answers for bylines, training hardware and references."""
import re
import unicodedata

from byline import author_number


def byline_answer(doc, number, hits):
    if not number or len(hits) != 1:
        return None
    hit = hits[0]
    lines = hit['text'].strip().splitlines()
    if not 2 <= len(lines) <= 4 or '@' not in lines[-1]:
        return None
    name = lines[0].rstrip('∗*†‡0123456789¹²³⁴⁵⁶⁷⁸⁹ ')
    affiliation = '、'.join(lines[1:-1])
    answer = f'第 {number} 位署名作者为 {name}。'
    if affiliation:
        answer += f'署名机构为 {affiliation}。'
    else:
        answer += '该署名块未列出机构。'
    start = hit["text"].find(lines[0])
    end = hit["text"].find(lines[-1], start)
    evidence = [dict(hit, start=start, end=end) if 0 <= start < end else hit]
    for mark in ('†', '‡'):
        if mark not in lines[0][len(name):]:
            continue
        pattern = re.compile(r'^' + mark + r'\s*Work (?:performed|done) while at ([^\n.]{2,100})\.[ \t]*$', re.I | re.M)
        notes = [(chunk, pattern.search(chunk['text'])) for chunk in doc['chunks'] if chunk['page'] == hit['page']]
        matches = [(chunk,m) for chunk,m in notes if m]
        if len(matches) == 1:
            chunk, note = matches[0]
            org = note[1]
            answer += f'作者的 {mark} 脚注说明，这项工作是在 {org} 完成的。'
            evidence.append(dict(chunk, id=doc['id'], name=doc['name'], start=note.start(), end=note.end()))
    return {'answer':answer, 'hits':evidence}


def training_hardware(question, hits):
    if not re.search(r'\bGPUs?\b|显卡', question, re.I) or not re.search(r'多少|型号|几块|how many', question, re.I):
        return None
    variant = re.search(r'\b(base|big|large|small)\b',question,re.I)
    found = {}
    for hit in hits:
        text = ' '.join(unicodedata.normalize('NFKC',hit['text']).split())
        # This is evidence about model training. Ignore references to hardware used for evaluation or cited work.
        for clause in re.finditer(r'(?:\bWe trained (?:our|the) (?:[\w-]+ )?models?|\bOur (?:[\w-]+ )?models? (?:was|were) trained)\b([^.!?]{0,140})[.!?]',text,re.I):
            if variant and variant[1].casefold() not in clause[0].casefold():
                following = text[clause.end():].lstrip()
                if not re.match(r'For (?:our|the) ' + re.escape(variant[1]) + r' models?\b',following,re.I):
                    continue
                if not re.search(r'(?:We trained our models|Our models were trained)\b',clause[0],re.I):
                    continue
            units = re.findall(r'\b(\d{1,5})\s+((?:[A-Z][\w-]*\s+){0,2}[A-Z][\w-]*\d[\w-]*)\s+GPUs?\b',clause[0])
            if len(units) == 1:
                # Keep exact, PDF-native offsets. Normalization was used only
                # for recognition; a transformed string cannot supply offsets.
                sentence = re.compile(r"\s+".join(re.escape(word) for word in clause[0].split()), re.I)
                positions = list(sentence.finditer(hit["text"]))
                marked = hit
                if len(positions) == 1:
                    marked = dict(hit, start=positions[0].start(), end=positions[0].end())
                found.setdefault(units[0],marked)
    if len(found) == 1:
        (count,model), hit = next(iter(found.items()))
        return {'answer':f'文中报告的训练硬件为 {count} 块 {model} GPU。', 'hits':[hit]}
    return None


def reference_answer(question, hits):
    title = re.search(r'[“"《]([^”"》]{3,150})[”"》]',question)
    if not title or not re.search(r'参考文献|references?',question,re.I):
        return None
    result = []
    for hit in hits:
        text = ' '.join(hit['text'].split())
        match = re.match(r'^\[\d+\]\s+(.{3,310}?)\.\s+' + re.escape(title[1]) + r'\.\s+(.+)$',text,re.I)
        if match:
            years = set(re.findall(r'\b(?:19|20)\d{2}\b', match[2]))
            if len(years) == 1:
                result.append((match[1], next(iter(years)), hit))
    if len(result) == 1:
        authors,year,hit = result[0]
        return {'answer':f'《{title[1]}》的作者为 {authors}，年份为 {year}。', 'hits':[hit]}
    return None


def extract_facts(doc, query, hits):
    return byline_answer(doc,author_number(query),hits) or training_hardware(query,hits) or reference_answer(query,hits)
