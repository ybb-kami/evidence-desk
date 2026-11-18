"""Adapter for the separate four-seat judge_entry.py start(runtime, port)."""
import json
import os
from pathlib import Path
import re
import stat


def _config(path):
    info = path.lstat()
    if (not path.is_absolute() or not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path
            or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022 or info.st_size > 16_384):
        raise ValueError('invalid budget metadata file')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate budget metadata field')
            result[key] = value
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def serve_four_seats(previous_serve, slots, port, origin, *, local_test=False,
                     anonymous=True, rotate_slot, ttl=3600, model_bridge=None):
    """Called with the unchanged four workers, origin, rotation and cookie settings."""
    options = dict(local_test=local_test, anonymous=anonymous, rotate_slot=rotate_slot, ttl=ttl)
    if model_bridge is not None or len(slots) != 4 or not anonymous or ttl != 3600:
        raise ValueError('unexpected legacy bridge or judge slot configuration')
    mode = os.environ.get('EVIDENCE_JUDGE_PROVIDER', 'off')
    if mode == 'off':
        return previous_serve(slots, port, origin, **options, model_bridge=None)
    if mode not in ('offline_fixture', 'approved_unix'):
        raise ValueError('unrecognized judge provider mode')
    # "gateway" may already refer to the live file. Import the pinned candidate by its package name.
    from candidate.gateway.gateway import serve
    from operator_runtime import private_runtime
    config = _config(Path(os.environ['EVIDENCE_JUDGE_BUDGET_CONFIG']))
    uid = os.environ['EVIDENCE_JUDGE_BROKER_UID']
    if not re.fullmatch('[0-9]{1,10}', uid):
        raise ValueError('invalid broker UID')
    bridges = private_runtime(config, socket_path=os.environ['EVIDENCE_JUDGE_SOCKET'],
                              broker_uid=int(uid), provider_mode=mode)
    return serve(slots, port, origin, **options, **bridges)
