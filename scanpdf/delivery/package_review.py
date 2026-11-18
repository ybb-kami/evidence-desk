"""Package the tracked judge-facing materials at HEAD and bind them to a source commit."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
REPO_URL = 'https://github.com/ybb-kami/evidence-desk'
FILES = ('SUBMISSION-CHECKLIST.md', 'evidence-desk-kokoro-mandarin-1080p.mp4', 'technical-report-zh.pdf',
         'mandarin-transcript.md', 'VOICE-NOTES.md', 'bilingual-samples/p1-1706.03762v1.pdf-en-zh.html',
         'bilingual-samples/p4-1706.03762v1.pdf-en-zh.html', 'bilingual-samples/p75-2501.12948v2.pdf-zh-en.html')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def package(output):
    commit = git('rev-parse', '--verify', 'HEAD').decode().strip()
    sums = []
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        archive = Path(folder) / 'review.zip'
        with zipfile.ZipFile(archive, 'w') as target:
            def write(name, body):
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED if name.endswith('.mp4') else zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                target.writestr(info, body)
                sums.append(f'{hashlib.sha256(body).hexdigest()}  {name}')

            for name in FILES:
                data = git('show', f'{commit}:scanpdf/delivery/{name}')
                if name == 'SUBMISSION-CHECKLIST.md':
                    data = data.replace('封包或推送后填写最终提交哈希 ________'.encode(), commit.encode())
                write(name, data)
            result_files = git('ls-tree', '--name-only', f'{commit}:scanpdf/acceptance/results').decode().splitlines()
            for name in result_files:
                if name.endswith('.json'):
                    write('verification/' + name, git('show', f'{commit}:scanpdf/acceptance/results/{name}'))
            pointer = f'证据台 · 可溯源科研文献助手\n青海大学 · 队伍 26CZB0001406 · 袁瑞\n\n源代码及 Git 历史\n{REPO_URL}/tree/{commit}\n\n提交前待参赛者填写及确认的项目见 SUBMISSION-CHECKLIST.md。\n'
            write('SOURCE.txt', pointer.encode())
            write('SHA256SUMS.txt', ('\n'.join(sums) + '\n').encode())
        with zipfile.ZipFile(archive) as checkout:
            if checkout.testzip() is not None:
                raise ValueError('ZIP 校验未通过。')
        os.link(archive, output)
    print(commit, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New ZIP in an existing directory')
    package(parser.parse_args().output)
