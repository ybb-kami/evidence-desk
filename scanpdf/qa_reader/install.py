"""Install the verified MobileBERT question-answering model to a user-selected directory."""
import argparse
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

from .assets import MODEL_FILENAME, MODEL_SHA256, MODEL_SIZE, MODEL_URL, verified_vocabulary


def install(directory, source=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / MODEL_FILENAME
    if target.exists():
        verified_vocabulary(target)
        return target
    incoming = Path(source).open('rb') if source else urlopen(MODEL_URL, timeout=90)
    with incoming, tempfile.NamedTemporaryFile(dir=directory) as staged:
        expected = MODEL_SIZE
        while expected:
            block = incoming.read(min(expected, 256 * 1024))
            if not block:
                raise ValueError('MobileBERT QA model transfer was incomplete.')
            staged.write(block)
            expected -= len(block)
        if incoming.read(1):
            raise ValueError('MobileBERT QA model transfer exceeded the expected size.')
        staged.flush()
        verified_vocabulary(staged.name)
        try:
            os.link(staged.name, target)
        except FileExistsError:
            verified_vocabulary(target)
    return target


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('directory', type=Path)
    cli.add_argument('--source', type=Path, help='Verify and install an existing local copy instead of downloading')
    args = cli.parse_args()
    output = install(args.directory, args.source)
    print(f'{MODEL_SHA256}  {output}')
