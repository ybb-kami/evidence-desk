"""Run MobileBERT QA against a bounded set of original, locally retrieved text chunks."""
from bisect import bisect_right
import heapq
from itertools import islice
from pathlib import Path
import re
from threading import Lock

from .assets import verified_vocabulary

TOKEN_WIDTH = 384
MAX_QUESTION_TOKENS = 64
MAX_QUESTION_CHARS = 600
MAX_HIT_CHARS = 4000
MAX_WINDOWS_PER_HIT = 2
WINDOW_OVERLAP = 60
MAX_ANSWER_TOKENS = 24
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+|(?<=[。！？])\s*')


class SpanReader:
    """Rank verbatim spans. Scores from the model are uncalibrated and do not establish answerability."""

    def __init__(self, model_path, *, threads=2, max_hits=4):
        if type(threads) is not int or type(max_hits) is not int or not 1 <= threads <= 8 or not 1 <= max_hits <= 8:
            raise ValueError('Choose between 1 and 8 MobileBERT CPU threads and candidate passages.')
        import numpy as np
        from ai_edge_litert.interpreter import Interpreter
        from tokenizers import Tokenizer
        from tokenizers.models import WordPiece
        from tokenizers.normalizers import BertNormalizer
        from tokenizers.pre_tokenizers import BertPreTokenizer

        path = Path(model_path)
        vocab = {text: i for i, text in enumerate(verified_vocabulary(path))}
        tokenizer = Tokenizer(WordPiece(vocab=vocab, unk_token='[UNK]'))
        tokenizer.normalizer = BertNormalizer(lowercase=True)
        tokenizer.pre_tokenizer = BertPreTokenizer()
        self._tokenizer = tokenizer
        self._cls, self._sep, self._pad = (vocab[token] for token in ('[CLS]', '[SEP]', '[PAD]'))
        self._interpreter = Interpreter(model_path=str(path), num_threads=threads)
        self._interpreter.allocate_tensors()
        self._inputs = self._tensors(self._interpreter.get_input_details(), ('input_ids', 'input_mask', 'segment_ids'), np.int32)
        self._outputs = self._tensors(self._interpreter.get_output_details(), ('start_logits', 'end_logits'), np.float32)
        self._np = np
        self._lock = Lock()
        self.max_hits = max_hits

    @staticmethod
    def _tensors(details, names, dtype):
        found = {item['name']: item for item in details}
        if set(found) != set(names) or any(item['shape'].tolist() != [1, TOKEN_WIDTH] or item['dtype'] != dtype
                                         for item in found.values()):
            raise ValueError('Unexpected MobileBERT input or output tensors.')
        return tuple(found[name]['index'] for name in names)

    def _infer(self, query_ids, context_ids):
        prefix = [self._cls, *query_ids, self._sep]
        real_length = len(prefix) + len(context_ids) + 1
        pad = TOKEN_WIDTH - real_length
        inputs = ([*prefix, *context_ids, self._sep, *([self._pad] * pad)],
                  [1] * real_length + [0] * pad,
                  [0] * len(prefix) + [1] * (len(context_ids) + 1) + [0] * pad)
        with self._lock:
            for tensor_index, values in zip(self._inputs, inputs):
                self._interpreter.set_tensor(tensor_index, self._np.asarray([values], dtype=self._np.int32))
            self._interpreter.invoke()
            start, end = (self._interpreter.get_tensor(index)[0] for index in self._outputs)
        if not self._np.isfinite(start).all() or not self._np.isfinite(end).all():
            raise ValueError('MobileBERT returned non-finite scores.')
        span_logits = slice(len(prefix), len(prefix) + len(context_ids))
        return start[span_logits], end[span_logits], float(start[0] + end[0])

    @staticmethod
    def _span_candidates(text, tokens, offsets, starts, ends, sentences):
        sentence_starts = [start for start, _ in sentences]
        for first, (answer_start, _) in enumerate(offsets):
            if tokens[first].startswith('##'):
                continue
            context_start, context_end = sentences[bisect_right(sentence_starts, answer_start) - 1]
            for last in range(first, min(first + MAX_ANSWER_TOKENS, len(offsets))):
                _, answer_end = offsets[last]
                if answer_end > context_end:
                    break
                if answer_end <= answer_start or (last + 1 < len(tokens) and tokens[last + 1].startswith('##')):
                    continue
                answer = text[answer_start:answer_end]
                if any(char.isalnum() for char in answer):
                    yield float(starts[first] + ends[last]), answer_start, answer_end, context_start, context_end

    def _read_hit(self, query_ids, hit, index):
        text = hit.get('text')
        if not isinstance(text, str) or not text.strip():
            return []
        if len(text) > MAX_HIT_CHARS:
            prefix = text[:MAX_HIT_CHARS].rsplit(maxsplit=1)
            if len(prefix) == 1:
                return []
            text = prefix[0]
        encoded = self._tokenizer.encode(text, add_special_tokens=False)
        ids, tokens, offsets = encoded.ids, encoded.tokens, encoded.offsets
        if not ids:
            return []
        marks = list(_SENTENCE_BOUNDARY.finditer(text))
        sentences = list(zip([0, *(mark.end() for mark in marks)], [*(mark.start() for mark in marks), len(text)]))
        width = TOKEN_WIDTH - len(query_ids) - 3
        step = width - WINDOW_OVERLAP
        ranked = []
        for first in islice(range(0, len(ids), step), MAX_WINDOWS_PER_HIT):
            stop = min(first + width, len(ids))
            # Keep complete WordPieces at window boundaries so a candidate cannot cut a word.
            while first < stop and tokens[first].startswith('##'):
                first += 1
            while stop < len(ids) and tokens[stop].startswith('##'):
                stop -= 1
            if first < stop:
                starts, ends, null_score = self._infer(query_ids, ids[first:stop])
                spans = self._span_candidates(text, tokens[first:stop], offsets[first:stop], starts, ends, sentences)
                ranked.extend((score - null_score, score, null_score, a, b, c, d)
                              for score, a, b, c, d in heapq.nlargest(12, spans))
            if stop == len(ids):
                break
        candidates = []
        for margin, score, null, a, b, c, d in sorted(ranked, reverse=True):
            if any(a < row['answer_end'] and row['answer_start'] < b for row in candidates):
                continue
            candidates.append({
                'hit_index': index, 'id': hit.get('id'), 'page': hit.get('page'), 'block': hit.get('block'),
                'part': hit.get('part', 1), 'hit_offset': hit.get('offset', 0),
                'answer': text[a:b], 'answer_start': a, 'answer_end': b,
                'context': text[c:d], 'context_start': c, 'context_end': d,
                'span_score': score, 'null_score': null, 'span_over_null': margin,
            })
            if len(candidates) == 2:
                break
        return candidates

    def read(self, question_en, hits, *, limit=4):
        """Inspect ranked hits. Offsets are zero-based Python string indices in each original hit."""
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError('Choose between 1 and 16 MobileBERT answer candidates.')
        if not isinstance(question_en, str) or not question_en.strip() or len(question_en) > MAX_QUESTION_CHARS:
            return []
        query_ids = self._tokenizer.encode(question_en, add_special_tokens=False).ids
        if not 1 <= len(query_ids) <= MAX_QUESTION_TOKENS:
            return []
        candidates = [candidate for index, hit in enumerate(islice(hits, self.max_hits))
                      for candidate in self._read_hit(query_ids, hit, index)]
        return sorted(candidates, key=lambda candidate: -candidate['span_over_null'])[:limit]
