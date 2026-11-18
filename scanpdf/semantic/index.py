"""Embed existing PDF evidence chunks on the CPU and rank them by cosine similarity."""

from collections import OrderedDict
import hashlib
import importlib.util
import json
from pathlib import Path
import threading

from .assets import FILES, REVISION, TOKENIZER, WEIGHTS, model_directory, validate
from .persistence import VectorCache


DIMENSIONS = 384
MODEL_TOKENS = 512
BATCH_SIZE = 8
INLINE_CHUNKS = 750
MAX_SCOPE_CHUNKS = 12_000
MAX_CACHE_BYTES = 64 * 1024**2
MAX_QUERY_CHARS = 500
MAX_RESULTS = 50
MIN_PROSE_CHUNKS = 32
MAX_SCORE_TEXTS = 32
MAX_SCORE_CHARS = 800
PASSAGE_PREFIX = "passage: "
# Bump this when token preparation, pooling, normalization, or row selection changes.
PREPROCESSING_VERSION = 1


MESSAGES = {
    "model_missing": "本地语义检索模型尚未安装。",
    "model_invalid": "本地语义检索模型校验失败。",
    "dependencies_missing": "本地语义检索运行依赖尚未安装。",
    "index_preparing": "正在后台生成本地语义索引，请稍后重试。",
    "scope_too_large": "语义检索范围超过 12,000 段，请缩小文献范围。",
    "busy": "本地语义检索正在处理另一项请求，请稍后重试。",
    "inference_failed": "本地语义检索推理失败。",
}


class DenseUnavailable(Exception):
    def __init__(self, reason):
        super().__init__(MESSAGES[reason])
        self.reason = reason


def _check(directory, *, checksum):
    try:
        validate(directory, checksum=checksum)
    except FileNotFoundError as exc:
        raise DenseUnavailable("model_missing") from exc
    except (OSError, ValueError) as exc:
        raise DenseUnavailable("model_invalid") from exc
    if any(importlib.util.find_spec(name) is None for name in ("numpy", "onnxruntime", "tokenizers")):
        raise DenseUnavailable("dependencies_missing")


class _Encoder:
    def __init__(self, directory):
        _check(directory, checksum=True)
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            ort.disable_telemetry_events()
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(directory / WEIGHTS), sess_options=options,
                                                providers=["CPUExecutionProvider"])
            self.tokenizer = Tokenizer.from_file(str(directory / TOKENIZER))
            self.tokenizer.enable_truncation(max_length=MODEL_TOKENS)
            self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("<pad>"),
                                          pad_token="<pad>")
            self.np = np
        except ImportError as exc:
            raise DenseUnavailable("dependencies_missing") from exc
        except Exception as exc:
            raise DenseUnavailable("inference_failed") from exc

    def _batch(self, texts, prefix):
        np = self.np
        rows = self.tokenizer.encode_batch([prefix + " ".join(text.split()) for text in texts])
        ids = np.asarray([row.ids for row in rows], dtype=np.int64)
        mask = np.asarray([row.attention_mask for row in rows], dtype=np.int64)
        types = np.asarray([row.type_ids for row in rows], dtype=np.int64)
        hidden, = self.session.run(["last_hidden_state"], {
            "input_ids": ids, "attention_mask": mask, "token_type_ids": types,
        })
        summed = np.einsum("btd,bt->bd", hidden, mask.astype(np.float32))
        # Masked mean and masked sum have the same unit vector.
        norms = np.linalg.norm(summed, axis=1, keepdims=True)
        if not np.isfinite(norms).all() or (norms <= 0).any():
            raise ValueError("The encoder returned an invalid vector")
        return summed / norms

    def encode(self, texts, prefix):
        # Group passages by character length to limit padding at inference time.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vectors = self.np.empty((len(texts), DIMENSIONS), dtype=self.np.float32)
        try:
            for start in range(0, len(order), BATCH_SIZE):
                batch = order[start:start + BATCH_SIZE]
                vectors[batch] = self._batch([texts[i] for i in batch], prefix)
        except Exception as exc:
            raise DenseUnavailable("inference_failed") from exc
        return vectors


def _key(doc):
    digest = hashlib.sha256()
    for chunk in doc["chunks"]:
        anchor = [chunk.get(field) for field in ("page", "block", "part", "offset", "source", "arxiv_stamp")]
        digest.update(json.dumps(anchor, separators=(",", ":")).encode())
        text = chunk["text"].encode("utf-8")
        digest.update(len(text).to_bytes(8, "big"))
        digest.update(text)
    return doc["id"], digest.digest()


def _passages(chunks):
    seen, unique, prose = set(), [], []
    for chunk in chunks:
        text = " ".join(chunk["text"].split())
        if chunk.get("arxiv_stamp") or text in seen:
            continue
        seen.add(text)
        unique.append(chunk)
        letters = sum(char.isalpha() for char in text)
        if letters >= 25 and letters * 2 >= len(text):
            prose.append(chunk)
    # In a substantial document, isolated formula fragments, figure labels,
    # and mostly numeric rows give dense embeddings little textual context.
    return prose if len(prose) >= MIN_PROSE_CHUNKS else unique


class DenseIndex:
    """Local passage retrieval with optional, per-library vector persistence."""

    def __init__(self, model_dir=None, *, cache_dir=None):
        self.directory = Path(model_dir).expanduser() if model_dir is not None else model_directory()
        self._storage = None
        if cache_dir is not None:
            encoder = [REVISION, FILES, PREPROCESSING_VERSION, MODEL_TOKENS, BATCH_SIZE,
                       MIN_PROSE_CHUNKS, PASSAGE_PREFIX]
            signature = hashlib.sha256(json.dumps(encoder, sort_keys=True).encode()).hexdigest()
            self._storage = VectorCache(cache_dir, signature, DIMENSIONS)
        self._lock = threading.Lock()
        self._encoder = None
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._preparing = False
        self._background_failure = None

    def available(self):
        """Check file presence and importability without loading the model."""
        try:
            _check(self.directory, checksum=False)
        except DenseUnavailable:
            return False
        return True

    def _runtime(self):
        if self._encoder is None:
            self._encoder = _Encoder(self.directory)
        return self._encoder

    def _remember(self, key, vectors):
        self._cache[key] = vectors
        self._cache_bytes += vectors.nbytes
        while self._cache_bytes > MAX_CACHE_BYTES:
            _, oldest = self._cache.popitem(last=False)
            self._cache_bytes -= oldest.nbytes

    def _build(self, missing):
        for key, chunks in missing:
            vectors = self._runtime().encode([chunk["text"] for chunk in chunks], PASSAGE_PREFIX)
            self._remember(key, vectors)
            if self._storage is not None:
                self._storage.save(key, len(chunks), vectors)

    def _background(self, missing, scope):
        with self._lock:
            try:
                self._build(missing)
            except DenseUnavailable as exc:
                self._background_failure = (scope, exc.reason)
            finally:
                self._preparing = False

    def _scores(self, documents, query):
        encoder = self._runtime()
        np = encoder.np
        scored = []
        query_vector = encoder.encode([query], "query: ")[0]
        matrices = [self._cache[key] for key, _ in documents]
        rows = sum(len(matrix) for matrix in matrices)
        center = None
        if rows >= MIN_PROSE_CHUNKS:
            # Center both the query and passage vectors on the selected corpus.
            # This reduces similarity driven by directions shared across the PDF.
            mean = sum(matrix.sum(axis=0) for matrix in matrices) / rows
            shifted_query = query_vector - mean
            norm = np.linalg.norm(shifted_query)
            if norm > 1e-6:
                center = mean
                query_vector = shifted_query / norm
        for key, doc in documents:
            vectors = self._cache[key]
            if center is not None:
                vectors = vectors - center
                norms = np.linalg.norm(vectors, axis=1)
                scores = np.einsum("nd,d->n", vectors, query_vector)
                scores = np.divide(scores, norms, out=np.zeros_like(scores), where=norms > 1e-6)
            else:
                scores = np.einsum("nd,d->n", vectors, query_vector)
            scores = scores.clip(-1, 1)
            scored.extend((float(score), doc, chunk) for score, chunk in zip(scores, doc["chunks"]))
        return scored

    def search(self, docs, query, *, limit=8):
        """Return verbatim ranked passages from the caller's explicitly selected documents."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY_CHARS:
            return []
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise ValueError(f"Dense result limit must be 1–{MAX_RESULTS}")
        docs = [doc for doc in docs if doc["chunks"]]
        if not docs:
            return []
        if sum(len(doc["chunks"]) for doc in docs) > MAX_SCOPE_CHUNKS:
            raise DenseUnavailable("scope_too_large")

        if not self._lock.acquire(blocking=False):
            raise DenseUnavailable("index_preparing" if self._preparing else "busy")
        try:
            if self._preparing:
                raise DenseUnavailable("index_preparing")
            documents = []
            for doc in docs:
                chunks = _passages(doc["chunks"])
                if chunks:
                    documents.append((_key(doc), dict(doc, chunks=chunks)))
            if not documents:
                return []
            scope = frozenset(key for key, doc in documents)
            if self._background_failure and self._background_failure[0] == scope:
                _, reason = self._background_failure
                self._background_failure = None
                raise DenseUnavailable(reason)
            missing = {}
            for key, doc in documents:
                if key not in self._cache:
                    vectors = self._storage.load(key, len(doc["chunks"])) if self._storage is not None else None
                    if vectors is None:
                        missing[key] = doc["chunks"]
                        continue
                    self._remember(key, vectors)
                self._cache.move_to_end(key)
            if sum(map(len, missing.values())) > INLINE_CHUNKS:
                _check(self.directory, checksum=False)
                worker = threading.Thread(target=self._background, args=(tuple(missing.items()), scope),
                                          name="evidence-dense-index", daemon=True)
                self._preparing = True
                try:
                    worker.start()
                except RuntimeError as exc:
                    self._preparing = False
                    raise DenseUnavailable("inference_failed") from exc
                raise DenseUnavailable("index_preparing")
            self._build(missing.items())
            scored = self._scores(documents, query)
        finally:
            self._lock.release()

        scored.sort(key=lambda row: (-row[0], row[1]["id"], row[2]["page"], row[2]["block"], row[2]["part"]))
        results, blocks = [], set()
        for score, doc, chunk in scored:
            page = chunk["page"]
            key = doc["id"], page, chunk["block"]
            if key in blocks:
                continue
            blocks.add(key)
            labels = doc.get("page_labels", ())
            result = dict(chunk, id=doc["id"], name=doc["name"], score=score,
                          page_label=labels[page - 1] if 0 < page <= len(labels) else str(page))
            results.append(result)
            if len(results) == limit:
                break
        return results

    def score_texts(self, question, texts):
        """Score a short list of source excerpts in input order without caching them."""
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= MAX_QUERY_CHARS:
            raise ValueError(f"Dense question must have 1–{MAX_QUERY_CHARS} characters")
        if (not isinstance(texts, (list, tuple)) or len(texts) > MAX_SCORE_TEXTS
                or any(not isinstance(text, str) or not text.strip() or len(text) > MAX_SCORE_CHARS
                       for text in texts)):
            raise ValueError(f"Expected at most {MAX_SCORE_TEXTS} excerpts of 1–{MAX_SCORE_CHARS} characters")
        if not texts:
            return []
        if not self._lock.acquire(blocking=False):
            raise DenseUnavailable("index_preparing" if self._preparing else "busy")
        try:
            if self._preparing:
                raise DenseUnavailable("index_preparing")
            encoder = self._runtime()
            query_vector = encoder.encode([question], "query: ")[0]
            vectors = encoder.encode(texts, PASSAGE_PREFIX)
            scores = encoder.np.einsum("nd,d->n", vectors, query_vector)
            return scores.clip(-1, 1).tolist()
        finally:
            self._lock.release()
