"""TensorFlow's MobileBERT QA fixture, identified by upstream SHA-256 declarations."""
import hashlib
from pathlib import Path
from zipfile import ZipFile

MODEL_FILENAME = 'mobilebert_qa.tflite'
MODEL_SHA256 = 'e79d3c70108bbdee02da657b679349cab46dbb859a05b599c76b53d98e82f272'
MODEL_SIZE = 100_670_436
VOCAB_SHA256 = '07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3'
VOCAB_SIZE = 231_508
MIRROR_COMMIT = 'e3497f95667121989884ecba69a4a46899dbc4ef'
MIRROR_PATH = 'models/mobilebert.tflite'
MODEL_URL = f'https://raw.githubusercontent.com/startlinglabs/ai-models/{MIRROR_COMMIT}/{MIRROR_PATH}'


def verified_vocabulary(path):
    """Verify the fixed model and its embedded WordPiece vocabulary before inference."""
    path = Path(path)
    with path.open('rb') as model:
        if path.stat().st_size != MODEL_SIZE or hashlib.file_digest(model, 'sha256').hexdigest() != MODEL_SHA256:
            raise ValueError('MobileBERT QA model does not match the verified TensorFlow artifact.')
        with ZipFile(model) as assets:
            info = assets.getinfo('vocab.txt')
            if info.file_size != VOCAB_SIZE:
                raise ValueError('MobileBERT QA vocabulary size does not match.')
            vocab = assets.read(info)
    if hashlib.sha256(vocab).hexdigest() != VOCAB_SHA256:
        raise ValueError('MobileBERT QA vocabulary does not match.')
    return vocab.decode('utf-8').splitlines()
