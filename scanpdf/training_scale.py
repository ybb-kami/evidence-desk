"""Verify reported training quantities for one named model using the selected PDF."""
import re
from decimal import Decimal

from evidence_context import sentences


_MODEL = r"(?P<model>[A-Za-z][A-Za-z0-9.-]*(?:\s+[0-9]+(?:\.[0-9]+)*)?)"
_UNITS = r"tokens?|parameters?|hours? of (?:audio|speech)(?: data)?|image[-– ]text pairs?"
_QUESTIONS = tuple(re.compile(x, re.I) for x in (
    rf"how many (?:pre[- ]?training |training )?(?P<unit>{_UNITS}) (?:are|were) (?:reported for|used (?:to (?:pre[- ]?)?train|in (?:pre[- ]?)?training)|used by) {_MODEL}",
    rf"how many (?P<unit>{_UNITS}) (?:was|is) {_MODEL} (?:pre[- ]?)?trained (?:on|with)",
    rf"how many (?P<unit>parameters?) does (?:the (?P<largest>largest) )?{_MODEL}(?: model)? have",
    rf"{_MODEL}\s*(?:的)?\s*(?:预训练|训练)\s*(?:使用了?|用了?|用到|包括)?\s*多少(?:个)?\s*(?P<unit>tokens?|词元|图文对|小时(?:的)?音频)",
    rf"{_MODEL}\s*(?:的)?\s*(?P<largest>最大)?模型(?:有|含有|拥有|包含)?多少(?:个)?(?P<unit>参数)",
    rf"{_MODEL}\s*(?:的)?\s*(?P<unit>图文配对|训练音频小时数)(?:训练集)?(?:规模|数量)?(?:是|有)?多少",
))
_UNCERTAIN = re.compile(r"\b(?:if|unless|could|would|might|not|never|hypothetical|excluding|except|instead|budgeted|projected|simulated|baseline|ablations?|fine[- ]?tuning|validation|held[- ]out|per (?:batch|epoch|step|document|sample|second)|et al|according to|prior work|previous work)\b", re.I)
_NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?"
_UNIT = re.compile(r"(?<![\w,.+−–-])(?P<qual>(?:(?:at (?:least|most)|up to|more than|over|about|approximately|roughly)\s+)?)"
                   rf"(?P<num>{_NUMBER})\s*(?P<scale>hundred|thousand|million|billion|trillion)?\s*"
                   r"(?P<unit>tokens?|parameters?|hours?|\(image,\s*text\)\s+pairs?|image[-– ]text pairs?)\b", re.I)
_SCALE = {'': 1, 'hundred': 100, 'thousand': 1000, 'million': 10**6, 'billion': 10**9, 'trillion': 10**12}


def _quantity(reading):
    return Decimal(reading['num'].replace(',', '')) * _SCALE[(reading['scale'] or '').lower()]


def _field(unit):
    unit = unit.lower()
    if unit in {'词元', 'token', 'tokens'}:
        return 'tokens'
    if unit.startswith('param') or unit == '参数':
        return 'parameters'
    if '小时' in unit or 'hour' in unit:
        return 'audio hours'
    return 'image-text pairs'


def _identity(doc, subject):
    # Authorial statements can omit the model name. Bind them to an explicit
    # introduction near the start of this PDF or to a model-titled paper.
    title = (doc.get('bibliographic_identity') or {}).get('title') or ''
    if re.match(r'^\s*' + subject + r'\s*(?::|$)', title, re.I):
        return True
    introduced = re.compile(r'\bwe (?:introduce|present|propose|train|call (?:our|this) (?:approach|model))\s+' + subject, re.I)
    return any(int(c['page']) <= 2 and introduced.search(' '.join(c['text'].split())) for c in doc['chunks'])


def read_scale(doc, question, anchor=None):
    q = ' '.join(question.strip(' \n\t?？。!').split())
    request = next((m for pattern in _QUESTIONS if (m := pattern.fullmatch(q))), None)
    if request is None:
        return None
    model = request['model']
    # Reject pronouns and generic nouns, which do not establish document identity.
    if model.casefold() in {'it', 'this', 'these', 'those', 'model', 'models', 'the', 'training'}:
        return None
    field = _field(request['unit'])
    subject = r'(?<![A-Za-z0-9-])' + r'\s+'.join(map(re.escape, model.split())) + r'(?![A-Za-z0-9-])'
    identified = _identity(doc, subject)
    train = r'(?:pre[- ]?)?train(?:ed)?'
    explicit = rf'(?:\bwe {train} {subject}|{subject} (?:was|is) {train})'
    own = rf'(?:\bwe {train}(?: (?:all (?:of )?our models|our models?|the model))?|\ball (?:our )?models were {train})'
    agent = rf'(?:{explicit}|{own})' if identified else explicit
    regimen = re.compile(agent + r'\s+(?:on|for|using|with)\s+(?:(?:a total of|a (?:training )?dataset of)\s+)?$', re.I)
    parameter = re.compile(rf'(?:\bwe (?:introduce|present|{train}) {subject},?\s*(?:an? [\w -]{{1,55}} model )?(?:with|of)|{subject}(?: model)? (?:has|contains|uses))\s*$', re.I)
    largest = bool(request.groupdict().get('largest'))
    largest_named = re.compile(rf'\b(?:our |the )?largest (?:model,? )?{subject}(?: model)?[,]? (?:has|contains|with)\s*$', re.I)
    matches = {}
    confirmations = {}
    for chunk in [anchor] if anchor is not None else doc['chunks']:
        raw = chunk['text']
        if chunk.get('arxiv_stamp') or not _UNIT.search(raw):
            continue
        for start, end in sentences(raw):
            line = ' '.join(raw[start:end].split())
            if len(line) > 900 or _UNCERTAIN.search(line):
                continue
            for measured in _UNIT.finditer(line):
                if _field(measured['unit']) != field:
                    continue
                left, right = line[:measured.start()], line[measured.end():]
                if field == 'parameters':
                    named = parameter.search(left)
                    if named and largest:
                        key = (_quantity(measured), measured['qual'].strip().lower())
                        confirmations.setdefault(key, dict(chunk, id=doc['id'], name=doc['name'], start=start, end=end))
                    accepted = largest_named.search(left) if largest else named
                    # In a range only the upper endpoint with an explicitly named final model supports a maximum.
                    if largest and re.search(r'\brang(?:e|ing)\b[^;]{0,120}\bfrom\s+', left, re.I):
                        lower = list(_UNIT.finditer(left))
                        endpoint = (len(lower) == 1 and _field(lower[0]['unit']) == field
                                    and re.fullmatch(r'\s+to\s+', left[lower[0].end():], re.I)
                                    and _quantity(lower[0]) < _quantity(measured))
                        accepted = endpoint and re.match(r'\s*,?\s*(?:with )?the (?:last|largest) (?:being )?(?:the model )?(?:we call |named )?' + subject, right, re.I)
                else:
                    accepted = regimen.search(left)
                    if field == 'audio hours' and not re.match(r'\s+of\s+(?:[\w-]+\s+){0,4}(?:audio|speech)\b', right, re.I):
                        accepted = False
                if not accepted or re.match(r'\s*(?:/|per\b|each\b|(?:and|or|to)\s+\d)', right, re.I):
                    continue
                value = _quantity(measured)
                key = (value, measured['qual'].strip().lower())
                hit = dict(chunk, id=doc['id'], name=doc['name'], start=start, end=end)
                matches.setdefault(key, (measured.group(), hit))
    if len(matches) > 1:
        gap = ("原文在多个训练设置下给出不同规模，请指定设置后再核对。" if re.search(r'[\u3400-\u9fff]', q)
               else "Several reported quantities match. Specify a training setting to select one.")
        return {'answer': None, 'gap': gap, 'hits': [hit for _, hit in list(matches.values())[:5]],
                'answer_method': 'source_fields', 'training_scale': {'model': model, 'field': field, 'status': 'ambiguous'}}
    if not matches:
        return None
    key, (text, hit) = next(iter(matches.items()))
    hits = [hit]
    confirmation = confirmations.get(key)
    if confirmation and confirmation != hit:
        hits.append(confirmation)
    return {'answer': text, 'gap': None, 'hits': hits, 'answer_method': 'source_fields',
            'training_scale': {'model': model, 'field': field, 'value_text': text}}
