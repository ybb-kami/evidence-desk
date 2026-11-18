"""Extract explicitly stated fields from bounded original exercise text."""

from dataclasses import dataclass
import re
import unicodedata

from evidence_context import sentences
from question_parts import split_question


FLAGS = re.I
ASK = re.compile(r"\b(?:what|which|how many|how often|how much|identify|list|state|name)\b"
                 r"|什么|多少|哪(?:[一二两三四五六七八九十几0-9]+)?[个位条种类项些幅张对]|几个|列出|是什么|何种|何值", FLAGS)
TASK_ASK = re.compile(r"\b(?:ask(?:s|ed)?|require(?:s|d)?|instruct(?:s|ed)?|goal|task|objective)\b|要求|让我们|要我们|任务|目标", FLAGS)
RESULT = re.compile(r"\b(?:resulting|after (?:applying|transforming)|transformed (?:distribution|variable|output)|finally obtain|solution|solve|work out)\b"
                    r"|解答|答案|求解|算出|求得|变换后的(?:分布|变量|输出)|计算结果|最终得到", FLAGS)
COMPUTE = re.compile(r"\b(?:calculate|derive|prove|compute)\b|请(?:计算|推导|证明)|怎么解|怎么算", FLAGS)
REFERENCE = r"(?:fig(?:ure)?|table|eq(?:uation)?|algorithm|section)"
REFERENCE_ASK = re.compile(rf"\b(?:which|what)\s+(?:numbered\s+)?(?P<en>{REFERENCE})s?\b|(?:哪[幅张条个一]?|第几)(?P<zh>图|表|式|算法|节)", FLAGS)
REFERENCE_VALUE = re.compile(rf"\b(?P<kind>{REFERENCE})s?\.?\s*\(?\s*(?P<value>\d+(?:[.-]\d+)*[a-z]?)\b"
                             r"|(?P<kind_cn>图|表|式|算法|节)\s*[(（]?\s*(?P<cn_value>\d+(?:[.-]\d+)*[a-z]?)", FLAGS)
_CHN_REFERENCE = {'图': 'figure', '表': 'table', '式': 'equation', '算法': 'algorithm', '节': 'section'}
_REFERENCE_ALIAS = {'fig': 'figure', 'eq': 'equation', **_CHN_REFERENCE}
_COUNT_WORDS = ('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen '
                'fifteen sixteen seventeen eighteen nineteen').split()
_TENS = ('twenty thirty forty fifty sixty seventy eighty ninety').split()
_ORDINALS = ('first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth '
             'fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth').split()
_COUNT = rf"(?:\d{{1,6}}|(?:{'|'.join(_TENS)})(?:[- ](?:{'|'.join(_COUNT_WORDS[1:10])}))?|{'|'.join(_COUNT_WORDS)})"
_ORDINAL = rf"(?:{'|'.join(_ORDINALS)}|\d{{1,6}}(?:st|nd|rd|th)|{_COUNT})"
_VALUE = r"[+−-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+)?"
_SYMBOL = r"[A-Za-zα-ωΑ-Ωϕµ](?:[A-Za-zα-ωΑ-Ωϕµ0-9_²³₀-₉]{0,9})"
_MATOM = rf"(?:{_VALUE}|{_SYMBOL}(?:\s*\[[^\]\r\n.]{{1,24}}\]|\([^()\r\n.]{{1,24}}\))?)"
_EXPR = rf"{_MATOM}(?:\s*(?:\*\*|[=+×*/÷−–^·-])\s*{_MATOM}){{0,10}}"
_FORMULA = re.compile(rf"(?<![\w.])(?P<lhs>{_SYMBOL}(?:\[[^\]\r\n.]{{1,24}}\])?)\s*=\s*{_EXPR}(?!\w)")
_INTERVAL = r"[\[(]\s*[+−-]?[\w.∞]+\s*[,，]\s*[+−-]?[\w.∞]+\s*[\])]"
_PARAMETER = rf"(?:means?|variances?|standard deviations?|scales?|probabilities|weights?|offsets?|parameters?)(?:\s+(?!and\b|is\b|are\b|were\b){_SYMBOL})?"
_LAYER_COUNT = re.compile(rf"\b(?:network|model|encoder|decoder|architecture|stack)\s+(?:with|of|has|contains|uses|consists\s+of|comprises)"
                          rf"\s+(?P<value>{_COUNT})\s+(?P<kind>(?:hidden\s+)?layers?)\b|"
                          rf"\b(?P<prefix>{_COUNT})[- ]layer\s+(?:network|model|encoder|decoder|architecture)\b", FLAGS)
_CHECKPOINT = re.compile(rf"\bevery\s+(?P<value>{_ORDINAL})\s+(?P<kind>(?:hidden\s+)?layers?|steps?|epochs?)\b", FLAGS)
_CHECKPOINT_CONTEXT = re.compile(r"\b(?:store|save|record|checkpoint)\w*\b|保存|存储|检查点", FLAGS)


def _pattern(pattern):
    return re.compile(pattern, FLAGS)


# Source expressions look for the requested role explicitly. A number or a
# symbol elsewhere in the statement cannot populate another field.
_FIELDS = {
    'distribution': ('分布', 'Distribution',
        _pattern(r"\b(?:what|which)\s+(?:(?:is the|kind of)\s+)?distribution\b|什么分布|哪种分布|何种分布"),
        _pattern(r"\b(?P<value>(?:an?\s+)?(?:(?:independent|standard|multivariate|univariate|isotropic|discrete|continuous)\s+)*"
                 r"(?:normal|Gaussian|uniform|Bernoulli|categorical|multinomial|binomial|Poisson|exponential|Laplace|beta|gamma|log.normal) distribution)\b")),
    'predicted': ('预测的参数', 'Predicted parameters',
        _pattern(r"\b(?:what|which)\s+(?:model\s+)?(?:parameters?|variables?|quantities)\b[^?]{0,95}"
                 r"\b(?:predict\w*|estimate\w*|(?:the )?(?:model|network)\s+outputs?)\b|"
                 r"(?:预测|估计)(?:了)?(?:哪些|什么)参数|(?:什么|哪些)参数[^？]{0,16}(?:预测|估计)"),
        _pattern(rf"\b(?P<value>{_PARAMETER}(?:\s+and\s+{_PARAMETER})?)\s+(?:is|are|were)\s+(?:predicted|estimated)\s+by\s+(?:the\s+)?(?:model|network)|"
                 rf"\b(?:model|network)\s+(?:predicts?|estimates?|outputs?)\s+(?:the\s+)?(?P<active>{_PARAMETER}(?:\s+and\s+{_PARAMETER})?)")),
    'relationship': ('题目说明的关系', 'Relationship stated in the exercise',
        _pattern(r"\b(?:what|which)\s+(?:statistical\s+)?relationship\b|\bhow\s+(?:are|do)\b[^?]{0,60}\brelat\w*\b|什么关系|关系是什么|是否(?:相关|独立|正交|相等)"),
        _pattern(r"\b(?:are|is|remain|be)\s+(?P<value>(?:(?:mutually|statistically|linearly|exactly|not)\s+)?(?:uncorrelated|correlated|independent|dependent|orthogonal|proportional|equal|identical|different))\b")),
    'interval': ('题设区间', 'Interval specified by the statement',
        _pattern(r"\b(?:what|which)\s+(?:closed\s+|open\s+)?interval\b|\bwhat\s+(?:is\s+the\s+)?domain\b|哪个区间|什么区间|定义域(?:是什么|为多少)"),
        _pattern(rf"(?<!\w)(?P<var>{_SYMBOL})\s*(?:∈|\\in)\s*(?P<value>{_INTERVAL})|"
                 rf"\b(?:defined on|lies in|over the interval|in the interval|in the range)\s*(?P<range>{_INTERVAL})")),
    'comparison': ('题目要求比较', 'The exercise asks you to compare',
        _pattern(r"\b(?:what|which)\b[^?]{0,110}\b(?:compare|contrast)\b|\bcomparison\b[^?]{0,35}\b(?:asked|specified)\b|(?:要求|要|让我们|让你)(?:比较|对比)|(?:比较|对比)(?:哪些|什么|哪[两几]种|哪两个)"),
        _pattern(r"\b(?:compare|contrast)\s+(?P<value>.{8,330})$|(?:比较|对比)\s*(?P<active>.{4,180})$")),
    'premise': ('题目给出的条件', 'Condition stated in the exercise',
        _pattern(r"\b(?:what|which)\b[^?]{0,90}\b(?:condition|assumption|constraint|premise)s?\b|(?:什么|哪些|哪[个项条])(?:假设|条件|约束)|(?:题设|假设|条件)(?:是什么|有哪些)"),
        _pattern(r"\b(?:assume|suppose|given that|under the assumption that)\s+(?P<value>.{5,240})$|(?:已知|假设)\s*(?P<active>.{3,130})$")),
    'task': ('题目要求', 'The exercise asks',
        _pattern(r"\bwhat\b[^?]{0,90}\b(?:ask|require)\w*\b[^?]{0,35}\b(?:to|do|prove|derive|calculate|find|show)\b|(?:要求|让我们|让你)(?:做什么|证明什么|推导什么|求什么|解释什么|计算什么)|(?:任务|目标)是什么"),
        _pattern(r"(?:^|\b)(?P<value>(?:show|prove|derive|determine|calculate|compute|find|explain|discuss|"
                 r"evaluate|verify|write|draw|give|devise|construct|implement)\b.{4,285})$")),
}

_NUMERIC = {
    'variance': ('方差', 'Variance', r'variance|方差', r'variance(?:\s+Var\s*\[[^\]]{1,12}\])?|Var\s*\[[^\]]{1,12}\]'),
    'mean': ('均值', 'Mean', r'mean|均值|平均值', r'mean(?:\s+E\s*\[[^\]]{1,12}\])?'),
    'discount': ('折扣因子', 'Discount factor', r'discount factor|折扣因子', r'discount factor(?:\s+[γɣ])?'),
    'probability': ('概率', 'Probability', r'probability|概率', r'probability(?:\s+{p})?'.replace('{p}', _SYMBOL)),
}
_NUMBER_ASK = _pattern(r"\b(?:how many|how much|what (?:is|are|was|were|value|number)|numeric(?:al)?(?: value)?)\b|多少|数值|几[个层]|何值")
_FORMULA_ASK = _pattern(r"\b(?:what|which)\b[^?]{0,70}\b(?:function|formula|equation|mapping)\b|(?:什么|哪个|哪条)(?:映射|函数|公式|式子)|(?:函数|公式|映射)(?:是什么|怎么写)")
_FORMULA_SOLVE = _pattern(r"\b(?:value of|evaluate at|root|solutions?|resulting|derive)\b|求值|求根|求解|计算结果|推导")
_VARIABLE_ASK = _pattern(r"\b(?:what|which)\b[^?]{0,75}\b(?:variables?|symbols?)\b|什么变量|哪个变量|什么符号|哪个符号")
_VARIABLE = _pattern(rf"\b(?P<role>input|output)\s+(?:variable\s+)?(?P<value>{_SYMBOL})(?![\w[])")
_LAYERS_ASK = _pattern(r"\b(?:how many|number of)\s+(?:hidden\s+)?layers\b|一共多少层|有多少(?:个)?(?:隐藏)?层|多少(?:个)?隐藏层|(?:总|深度)层数")
_INTERVAL_ASK = _pattern(r"\b(?:how often|every how many|how many (?:hidden )?(?:layers|steps) between|checkpoint interval)\b|每隔多少|多大间隔|间隔(?:多少|是多少)")


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    value: str | None
    hits: tuple
    reason: str | None = None
    observation: str | None = None


def _literal(text, *, form='NFC'):
    text = re.sub(r"(?<=[a-z])[-\u00ad][ \t]*\n\s*(?=[a-z])", "", text, flags=FLAGS)
    return " ".join(unicodedata.normalize(form, text).split())


def _sentences(hits):
    for hit in hits:
        original = hit['text']
        if not 0 <= hit['start'] < hit['end'] <= len(original):
            continue
        shown = original[hit['start']:hit['end']]
        for a, b in sentences(shown):
            yield _literal(shown[a:b]), dict(hit, start=hit['start'] + a, end=hit['start'] + b)


def _as_number(value):
    token = value.casefold().replace('-', ' ')
    if token in _ORDINALS:
        return str(_ORDINALS.index(token) + 1)
    numbers = {v: i for i, v in enumerate(_COUNT_WORDS)} | {v: 20 + i * 10 for i, v in enumerate(_TENS)}
    if all(word in numbers for word in token.split()):
        return str(sum(numbers[word] for word in token.split()))
    return re.sub(r'(?:st|nd|rd|th)$', '', value, flags=FLAGS)


def _matches(source, pattern, *, cue=None):
    for sentence, hit in source:
        if cue and not cue.search(sentence):
            continue
        for match in pattern.finditer(sentence):
            yield match, sentence, hit


def _unique(key, label, rows):
    found = {}
    for value, hit in rows:
        value = value.strip(' .。;；')
        if value:
            found.setdefault(value.casefold(), (value, hit))
    if len(found) == 1:
        value, hit = next(iter(found.values()))
        return Field(key, label, value, (hit,))
    return Field(key, label, None, tuple(hit for _, hit in list(found.values())[:3]),
                 'ambiguous' if found else 'absent')


def _fixed_fields(question, source, zh):
    for key, (label_zh, label_en, ask, binding) in _FIELDS.items():
        if not ask.search(question):
            continue
        rows = []
        for match, sentence, hit in _matches(source, binding):
            if key == 'relationship' and re.search(r'\bbranch(?:es)?\b|分支', question, FLAGS) and not re.search(r'\bbranch(?:es)?\b|分支', sentence, FLAGS):
                continue
            if key == 'interval' and re.search(r'\b(?:base (?:density|distribution)|input)\b|基密度|输入', question, FLAGS) and not re.search(r'\b(?:base|input)\b|基密度|输入', sentence, FLAGS):
                continue
            if key == 'interval' and re.search(r'\boutput\b|输出', question, FLAGS) and not re.search(r'\boutput\b|输出', match[0], FLAGS):
                continue
            values = match.groupdict()
            rows.append((values.get('value') or values.get('active') or values.get('range'), hit))
        yield _unique(key, label_zh if zh else label_en, rows)


def _numeric_fields(question, source, zh):
    if _INTERVAL_ASK.search(question) and re.search(r'checkpoint|store|sav(?:e|ing)|保存|检查点|存储|预激活', question, FLAGS):
        units = {'hidden layer': '个隐藏层', 'layer': '层', 'step': '步', 'epoch': '轮'}
        rows = [(f"每隔 {_as_number(m['value'])} {units[m['kind'].lower().rstrip('s')]}" if zh
                 else f"every {_as_number(m['value'])} {m['kind'].rstrip('s')}s", hit)
                for m, sentence, hit in _matches(source, _CHECKPOINT, cue=_CHECKPOINT_CONTEXT)
                if not re.search(r'forward pass|前向', question, FLAGS) or re.search(r'forward pass|前向', sentence, FLAGS)]
        yield _unique('checkpoint_interval', '保存间隔' if zh else 'Storage interval', rows)
    elif _LAYERS_ASK.search(question):
        rows = [(_as_number(m['value'] or m['prefix']), hit) for m, _, hit in _matches(source, _LAYER_COUNT)
                if not re.search(r'\bhidden layers?\b|隐藏层', question, FLAGS) or (m['kind'] and 'hidden' in m['kind'].lower())]
        yield _unique('layers', '层数' if zh else 'Layers', rows)
    if not _NUMBER_ASK.search(question):
        return
    for key, (label_zh, label_en, topic, subject) in _NUMERIC.items():
        if not re.search(rf'(?<![a-z])(?:{topic})(?![a-z])', question, FLAGS):
            continue
        label = label_zh if zh else label_en
        binding = _pattern(rf"(?<!\w)(?:{subject})\s*(?:=|is|of|为|等于)\s*(?P<value>{_VALUE}|{_COUNT}|{_EXPR})(?!\w|\.\d|\s*[=+*/÷−^])")
        rows = [(m['value'], hit) for m, _, hit in _matches(source, binding)]
        candidates = [(_as_number(value), hit) for value, hit in rows
                      if re.fullmatch(rf'{_VALUE}|{_COUNT}', value, FLAGS)]
        field = _unique(key, label, candidates)
        if rows and not candidates:
            values = tuple(dict.fromkeys(value for value, _ in rows))
            if len(values) == 1:
                field = Field(key, label, None, (rows[0][1],), 'symbolic', values[0])
        yield field


def _reference_kind(question):
    ask = REFERENCE_ASK.search(question)
    if not ask:
        return None
    name = (ask['en'] or ask['zh']).casefold()
    kind = _REFERENCE_ALIAS.get(name, name)
    if kind == 'equation' and not re.search(r'\b(?:number(?:ed)?|reference\w*|cites?|cited|refers? to)\b|编号|引用|第几式', question, FLAGS):
        return None
    return kind


def _references(question, source, zh):
    kind = _reference_kind(question)
    if not kind:
        return None
    label = {'figure': '图', 'table': '表', 'equation': '式', 'algorithm': '算法', 'section': '节'}[kind] if zh else kind.capitalize()
    rows = []
    for match, _, hit in _matches(source, REFERENCE_VALUE):
        actual = (match['kind'] or match['kind_cn']).casefold()
        if _REFERENCE_ALIAS.get(actual, actual) == kind:
            rows.append((match['value'] or match['cn_value'], hit))
    return _unique(kind, label, rows)


def _math_fields(question, source, zh):
    if _FORMULA_ASK.search(question) and not (_FORMULA_SOLVE.search(question) or _reference_kind(question)):
        left = re.search(rf'(?:defines?|gives?|to|for|成|得到)\s+(?P<var>{_SYMBOL})(?=\s*(?:[?？.,，。]|$))', question, FLAGS)
        rows = [(match[0], hit) for match, _, hit in _matches(source, _FORMULA)
                if not left or match['lhs'] == left['var']]
        yield _unique('formula', '题设原式' if zh else 'Formula given in the statement', rows)
    if _VARIABLE_ASK.search(question):
        for role, alias in (('input', '输入'), ('output', '输出')):
            if not re.search(rf'\b{role}\b|{alias}', question, FLAGS):
                continue
            rows = [(m['value'], hit) for m, _, hit in _matches(source, _VARIABLE)
                    if m['role'].casefold() == role and re.fullmatch(r'[A-Za-zα-ωΑ-Ωϕµ][0-9₀-₉]*', m['value'])]
            yield _unique(role, f'{alias}变量' if zh else f'{role.capitalize()} variable', rows)


def _fields(part, source, zh):
    if not ASK.search(part):
        return []
    if RESULT.search(part) or (COMPUTE.search(part) and not TASK_ASK.search(part)):
        return []
    reference = _references(part, source, zh)
    fields = [*_fixed_fields(part, source, zh), *_numeric_fields(part, source, zh), *_math_fields(part, source, zh)]
    if reference:
        fields.insert(0, reference)
    if any(field.name != 'task' for field in fields):
        fields = [field for field in fields if field.name != 'task']
    return fields


def statement_fields(question, hits):
    """Return deduplicated, original-statement fields and unhandled subquestions."""
    normalized = _literal(question, form='NFKC')
    zh = bool(re.search(r'[㐀-鿿]', normalized))
    source = tuple(_sentences(hits))
    fields, unhandled = {}, []
    for part in split_question(normalized):
        requested = _fields(part, source, zh)
        if requested:
            for field in requested:
                fields.setdefault(field.name, field)
        else:
            unhandled.append(part)
    return tuple(fields.values()), unhandled
