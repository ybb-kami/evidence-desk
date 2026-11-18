"""Exercise the real local inference endpoint with passages from the workbench retriever."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine import find_evidence, parse_pdf


PDF_SHA = '0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476'

SCHEMA = {
    'type': 'object',
    'properties': {
        'status': {'type': 'string', 'enum': ['answered', 'insufficient']},
        'answer': {'type': 'string'},
        'source': {'type': 'integer'},
        'quote': {'type': 'string'},
    },
    'required': ['status', 'answer', 'source', 'quote'],
    'additionalProperties': False,
}
SYSTEM = {
    'en': (
        'Answer only from the numbered paper passages. Give a short answer in English. '
        'When a passage states the answer, set status to answered, source to its number, '
        'and quote to the shortest exact passage substring that supports the answer. '
        'If no passage states the requested fact, set status to insufficient, source to 0, '
        'and answer and quote to empty strings. Do not infer a missing number from other numbers.'
    ),
    'zh': (
        '你是论文问答助手。只依据编号原文，answer 字段用包含中文词语的简体中文完整句子简短作答。'
        '原文明确给出答案时，'
        'status 填 answered，source 填该段编号，quote 逐字复制能支持答案的最短原文片段。'
        '原文缺少所问事实时，status 填 insufficient，source 填 0，answer 和 quote 填空字符串。'
        '不要用其他数值推断缺失的数字。'
    ),
}
CASES = (
    (
        'en', 'How many NVIDIA P100 GPUs were used for training?',
        None, (7, 3), (r'\b(?:eight|8)\b',), '8 NVIDIA P100 GPUs',
    ),
    (
        'zh', '训练 beta1 beta2是多少？',
        None, (7, 5), (r'0\.9\b', r'0\.98\b', r'[\u4e00-\u9fff]'), 'β1 = 0.9, β2 = 0.98',
    ),
    (
        'en', 'What serial number is reported for the NVIDIA P100 GPUs?',
        'How many NVIDIA P100 GPUs were used for training?', None, (), '',
    ),
)


def ask(endpoint, model, language, question, passages):
    labels = {'en': ('Passages', 'Question'), 'zh': ('原文段落', '问题')}
    passages_label, question_label = labels[language]
    excerpts = '\n\n'.join(f'[{i}]\n{row["text"]}' for i, row in enumerate(passages, 1))
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM[language]},
            {'role': 'user', 'content': f'{passages_label}:\n{excerpts}\n\n{question_label}: {question}'},
        ],
        'temperature': 0,
        'seed': 42,
        'max_tokens': 200,
        'stream': False,
        'response_format': {
            'type': 'json_schema',
            'json_schema': {'name': 'paper_answer', 'strict': True, 'schema': SCHEMA},
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json'},
    )
    started = time.monotonic()
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    elapsed = round(time.monotonic() - started, 3)
    value = json.loads(payload['choices'][0]['message']['content'])
    return value, elapsed, payload['usage'], payload.get('timings', {})


def exercise(pdf, endpoint, model):
    data = pdf.read_bytes()
    if hashlib.sha256(data).hexdigest() != PDF_SHA:
        raise ValueError('复测使用赛题包中的公开 1706.03762v1.pdf。')
    document = parse_pdf(data, pdf.name)
    outputs = []
    for language, question, retrieval_query, expected_location, patterns, expected_quote in CASES:
        passages = find_evidence([document], retrieval_query or question)[:4]
        answer, seconds, usage, timings = ask(endpoint, model, language, question, passages)
        anchors = [(row['page'], row['block']) for row in passages]
        source = answer['source']
        if expected_location:
            valid = (
                answer['status'] == 'answered'
                and 1 <= source <= len(anchors)
                and anchors[source - 1] == expected_location
                and expected_quote in answer['quote']
                and answer['quote'] in passages[source - 1]['text']
                and all(re.search(pattern, answer['answer'], re.I) for pattern in patterns)
            )
        else:
            valid = answer == {'status': 'insufficient', 'source': 0, 'answer': '', 'quote': ''}
        receipt = dict(
            question=question, anchors=anchors, valid=bool(valid), result=answer,
            seconds=seconds, completion_tokens=usage['completion_tokens'],
            output_tokens_per_second=round(timings.get('predicted_per_second', 0), 2),
        )
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
        outputs.append(receipt)
    return outputs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--port', type=int, default=39348)
    parser.add_argument('--model', default='Qwen3-1.7B-Q4_K_M.gguf')
    args = parser.parse_args()
    endpoint = f'http://127.0.0.1:{args.port}/v1/chat/completions'
    results = exercise(args.pdf, endpoint, args.model)
    sys.exit(0 if all(row['valid'] for row in results) else 1)
