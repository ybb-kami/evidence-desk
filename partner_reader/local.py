"""Offline CTranslate2 inference with local Argos-format model directories."""

import importlib.util
import os
import threading
from functools import lru_cache
from pathlib import Path

_lock = threading.Lock()
_members = ("model/model.bin", "model/config.json", "model/shared_vocabulary.json", "sentencepiece.model")


class Busy(Exception):
    pass


def root():
    return Path(
        os.environ.get(
            "EVIDENCE_TRANSLATION_MODELS",
            Path.home() / ".local/share/evidence-desk/models",
        )
    )


def available():
    if not all(
        importlib.util.find_spec(name) for name in ("ctranslate2", "sentencepiece")
    ):
        return []
    return [
        key
        for key in ("en-zh", "zh-en")
        if all((root() / key / name).is_file() for name in _members)
    ]


@lru_cache(maxsize=2)
def model(path):
    import ctranslate2
    import sentencepiece

    processor = sentencepiece.SentencePieceProcessor(
        model_file=str(path / "sentencepiece.model")
    )
    translator = ctranslate2.Translator(
        str(path / "model"), device="cpu", compute_type="int8", intra_threads=2
    )
    return processor, translator


def translate(direction, text):
    if not _lock.acquire(blocking=False):
        raise Busy()
    try:
        processor, translator = model(root() / direction)
        tokens = processor.encode(text, out_type=str)
        if len(tokens) > 420:
            raise ValueError("Segment too long")
        result = translator.translate_batch(
            [tokens], beam_size=4, replace_unknowns=True, max_decoding_length=512
        )[0]
        if not result.hypotheses or not result.hypotheses[0]:
            raise ValueError("Empty output")
        return processor.decode_pieces(result.hypotheses[0]).replace("▁", " ").strip()
    finally:
        _lock.release()
