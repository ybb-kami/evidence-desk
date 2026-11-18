"""Render an optional diff without writing to the independently installed judge entry."""
import argparse
import ast
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path
import sys


def render(data, expected_sha):
    if sha256(data).hexdigest() != expected_sha:
        raise ValueError('entry differs from the supplied SHA-256')
    original = data.decode('utf-8')
    tree = ast.parse(original)
    start, = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'start']
    if [arg.arg for arg in start.args.args] != ['runtime', 'port']:
        raise ValueError('unrecognized start signature')
    calls = [n for n in ast.walk(start) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    slots, = [n for n in calls if n.func.id == 'demo_slots']
    gateway, = [n for n in calls if n.func.id == 'serve']
    if [ast.unparse(a) for a in slots.args] != ['DEFAULT_APP', "run_dir / 'sessions'", '4', 'loopback_origin'] or slots.keywords:
        raise ValueError('unexpected seat creation')
    settings = {'local_test': 'False', 'anonymous': 'True', 'rotate_slot': 'slots.rotate_slot',
                'ttl': '3600', 'model_bridge': 'None'}
    if ([ast.unparse(a) for a in gateway.args] != ['slots', 'port', 'PUBLIC_ORIGIN']
            or {k.arg: ast.unparse(k.value) for k in gateway.keywords} != settings
            or len(gateway.keywords) != len(settings)):
        raise ValueError('unexpected gateway or active legacy bridge')
    # Python AST columns are UTF-8 byte offsets. Edit only the two function tokens.
    lines = data.splitlines(keepends=True)
    marker = gateway.args[0]
    line = lines[marker.lineno - 1]
    lines[marker.lineno - 1] = line[:marker.col_offset] + b'serve, ' + line[marker.col_offset:]
    point = gateway.func
    line = lines[point.lineno - 1]
    lines[point.lineno - 1] = line[:point.col_offset] + b'serve_four_seats' + line[point.end_col_offset:]
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    first_line = min([start.lineno, *(n.lineno for n in start.decorator_list)]) - 1
    lines.insert(first_line, b'from judge_start import serve_four_seats' + newline + newline)
    updated = b''.join(lines).decode('utf-8')
    ast.parse(updated)
    return ''.join(unified_diff(original.splitlines(keepends=True), updated.splitlines(keepends=True),
                                fromfile='a/judge_entry.py', tofile='b/judge_entry.py'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('entry', type=Path)
    p.add_argument('--expect-sha256', required=True)
    args = p.parse_args()
    try:
        sys.stdout.write(render(args.entry.read_bytes(), args.expect_sha256))
    except (OSError, SyntaxError, ValueError) as error:
        p.exit(2, str(error) + '\n')
