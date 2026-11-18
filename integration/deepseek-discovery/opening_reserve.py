"""Offline, one-time commissioning for documented project costs before the first ledger."""
import argparse
from contextlib import closing
from decimal import Decimal
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from group_budget import GROUPS, MODEL, VERSION, Denied, GroupBudget, _pairs

SCHEMA = 'evidence-desk-opening-v1'
BOTH_RUNS = {'direct-test', 'app-smoke'}
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
DECIMAL = re.compile(r'(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,9})?\Z')
RUN = re.compile(r'[a-z][a-z0-9-]{0,31}\Z')
SUMMARY_LIMIT = 65536
SOURCE_LIMIT = 16 * 1024**2
WITNESS = '.evidence-desk-opening-authority.json'


def require(condition, message):
    if not condition:
        raise Denied(message)


def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def integer(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def ratio(text):
    require(isinstance(text, str) and DECIMAL.fullmatch(text) and Decimal(text) > 0,
            'expected a positive plain decimal with at most nine fractional digits')
    return Decimal(text).as_integer_ratio()


def ceil_div(numerator, denominator):
    return (numerator + denominator - 1) // denominator


def run_names(names):
    require(isinstance(names, list) and 1 <= len(names) <= GROUPS and
            all(isinstance(name, str) and RUN.fullmatch(name) for name in names)
            and len(names) == len(set(names)), 'invalid historical run inventory')
    return set(names)


def validate_evidence(data):
    """Validate a redacted operator inventory. The operator reconciles the source documents."""
    fields = {'schema', 'ledger_path_sha256', 'opening_fx_cny_per_usd_upper', 'runs', 'records'}
    require(isinstance(data, dict) and set(data) == fields and data['schema'] == SCHEMA
            and digest(data['ledger_path_sha256']), 'invalid opening evidence header')
    fx_num, fx_den = ratio(data['opening_fx_cny_per_usd_upper'])
    expected = run_names(data['runs'])
    require(BOTH_RUNS <= expected, 'include both known historical runs')
    records = data['records']
    require(isinstance(records, list) and 1 <= len(records) <= len(expected), 'historical evidence required')
    attempts = usd = cny = 0
    covered = set()
    sources = set()
    common = {'kind','runs','source_sha256','attempts_upper','includes_all_attempts_and_pending'}
    for record in records:
        require(isinstance(record, dict) and common <= record.keys(), 'incomplete historical record')
        names = run_names(record['runs'])
        count = record['attempts_upper']
        require(names <= expected and covered.isdisjoint(names) and integer(count, len(names), GROUPS)
                and record['includes_all_attempts_and_pending'] is True, 'overlapping or unbounded historical attempts')
        source = record['source_sha256']
        require(digest(source) and source not in sources, 'each source must occur once; combine its covered runs')
        sources.add(source)
        covered.update(names)
        attempts += count
        if record['kind'] == 'provider_billing_upper':
            require(set(record) == common | {'currency','amount_upper'} and record['currency'] in ('USD','CNY'),
                    'invalid provider billing bound')
            num, den = ratio(record['amount_upper'])
            amount = ceil_div(num * 10**9, den)
            if record['currency'] == 'USD':
                usd += amount
            else:
                cny += amount
        elif record['kind'] == 'provider_usage_upper':
            extra = {'model_release','prompt_tokens','completion_tokens','pricing_source_sha256',
                     'input_nano_usd_per_token_upper','output_nano_usd_per_token_upper',
                     'other_nano_usd_per_attempt_upper','usage_covers_all_billable_tokens'}
            require(set(record) == common | extra and count == len(names) == 1 and
                    record['model_release'] == VERSION and digest(record['pricing_source_sha256'])
                    and record['usage_covers_all_billable_tokens'] is True, 'incomplete per-request usage coverage')
            prompt, completion = record['prompt_tokens'], record['completion_tokens']
            in_price, out_price = record['input_nano_usd_per_token_upper'], record['output_nano_usd_per_token_upper']
            fees = record['other_nano_usd_per_attempt_upper']
            require(integer(prompt,0,10**8) and integer(completion,0,10**7) and prompt+completion>0
                    and integer(in_price,300,10**7) and integer(out_price,1200,10**7)
                    and integer(fees,0,10**12), 'invalid usage totals or peak rates')
            usd += prompt*in_price + completion*out_price + fees
        elif record['kind'] == 'request_cost_upper':
            extra = {'model_release','input_tokens_per_attempt_upper','output_tokens_per_attempt_upper',
                     'input_nano_usd_per_token_upper','output_nano_usd_per_token_upper',
                     'other_nano_usd_per_attempt_upper','pricing_source_sha256'}
            require(set(record) == common | extra and record['model_release'] == VERSION
                    and digest(record['pricing_source_sha256']), 'unsupported or unverified historical model or price')
            tokens_in = record['input_tokens_per_attempt_upper']
            tokens_out = record['output_tokens_per_attempt_upper']
            in_price = record['input_nano_usd_per_token_upper']
            out_price = record['output_nano_usd_per_token_upper']
            fees = record['other_nano_usd_per_attempt_upper']
            require(integer(tokens_in, 2**20, 10**8) and integer(tokens_out, 1, 10**7)
                    and integer(in_price, 300, 10**7) and integer(out_price, 1200, 10**7)
                    and integer(fees, 0, 10**12), 'invalid full-window bounds, peak prices or fees')
            usd += count * (tokens_in * in_price + tokens_out * out_price + fees)
        else:
            raise Denied('unsupported historical evidence')
    require(covered == expected and attempts <= GROUPS, 'uncovered historical run or too many attempts')
    opening = usd + ceil_div(cny * fx_den, fx_num)
    require(0 < opening < 2**63 and cny < 2**63, 'opening reserve out of range')
    return dict(reserved_nano_usd=opening, historical_requests=attempts, usd_component_nano=usd,
                cny_component_nano=cny, opening_fx_cny_per_usd_upper=data['opening_fx_cny_per_usd_upper'])


def bounded_read(path, limit):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as f:
        info = os.fstat(f.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit, 'invalid or oversized evidence file')
        raw = f.read(limit + 1)
        require(len(raw) == info.st_size and len(raw) <= limit, 'file changed during read')
        return raw


def load_json(path):
    return json.loads(bounded_read(path, SUMMARY_LIMIT), object_pairs_hook=_pairs)


def inspect_evidence(evidence, proof_dir):
    totals = validate_evidence(evidence)
    require(len(canonical_bytes(evidence)) <= SUMMARY_LIMIT, 'historical summary too large')
    sources = {record[k] for record in evidence['records'] for k in ['source_sha256','pricing_source_sha256'] if k in record}
    usage_records = {r['source_sha256']:r for r in evidence['records'] if r['kind']=='provider_usage_upper'}
    request_ids = set()
    for source in sources:
        data = bounded_read(Path(proof_dir)/(source + '.proof'), SOURCE_LIMIT)
        require(sha256(data).hexdigest() == source, 'historical source digest mismatch')
        if source in usage_records:
            record = usage_records[source]
            response = json.loads(data, object_pairs_hook=_pairs)
            require(isinstance(response,dict) and 'error' not in response and
                    response.get('model') in (MODEL, VERSION) and isinstance(response.get('usage'),dict),
                    'expected a completed Flash usage response')
            request_id = response.get('id')
            require(isinstance(request_id,str) and 1<=len(request_id)<=256 and request_id.strip()
                    and request_id not in request_ids, 'missing or duplicate provider request identity')
            request_ids.add(request_id)
            usage = response['usage']
            expected = dict(prompt_tokens=record['prompt_tokens'], completion_tokens=record['completion_tokens'],
                            total_tokens=record['prompt_tokens']+record['completion_tokens'])
            require(all(type(usage.get(k)) is int and usage[k]==v for k,v in expected.items()),
                    'provider token usage differs from submitted evidence')
    return {**totals, 'evidence_sha256':sha256(canonical_bytes(evidence)).hexdigest(),
            'provider_invoiced_amount':'not_observed_by_this_tool'}


def witness_path(path):
    return Path(path).parent / WITNESS


def _instance(path, evidence_sha):
    require(isinstance(path, Path) and path.is_absolute() and path.resolve(strict=True) == path
            and path.is_file(), 'invalid opening ledger path')
    info = path.stat(follow_symlinks=False)
    return dict(schema='judge-opening-authority-v1', path_sha256=sha256(os.fsencode(path)).hexdigest(),
                device=info.st_dev, inode=info.st_ino, opening_evidence_sha256=evidence_sha)


def verify_witness_for(path, evidence_sha256):
    path = Path(path)
    try:
        require(load_json(witness_path(path)) == _instance(path, evidence_sha256),
                'opening authority does not bind this instance')
    except (OSError, ValueError, RecursionError):
        raise Denied('opening authority missing or changed') from None


def verify_witness(ledger):
    verify_witness_for(ledger.path, ledger.opening_evidence_sha256)


def provision_opening(ledger, evidence, *, proof_dir, no_existing_project_ledger=False, writers_stopped=False):
    """Operator-only. No network; no repair, overwrite, refund or second budget."""
    from legacy_ledger import require_migrated_policy, stamp_opening_policy
    require(no_existing_project_ledger is True and writers_stopped is True,
            'inventory all existing ledgers and stop all paid writers first')
    require(isinstance(ledger, GroupBudget) and ledger.opening_required and ledger.cap_policy is not None,
            'active 20 CNY opening configuration required')
    ledger._time()
    verified = inspect_evidence(evidence, proof_dir)
    path = ledger.path
    require(path == path.resolve(strict=False) and path.parent.is_dir() and path.name != WITNESS
            and sha256(os.fsencode(path)).hexdigest() == evidence['ledger_path_sha256'],
            'evidence must designate the sole canonical ledger path')
    require(verified['evidence_sha256'] == ledger.opening_evidence_sha256 and
            ratio(evidence['opening_fx_cny_per_usd_upper']) == ratio(ledger.cap_policy['cny_per_usd_upper']),
            'operator configuration does not bind the opening evidence or FX')
    require(verified['reserved_nano_usd'] <= ledger.cap, 'historical upper bound exceeds the entire project cap')
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(parent, fcntl.LOCK_EX)
        witness = witness_path(path)
        if os.path.lexists(witness):
            verify_witness(ledger)
            with closing(ledger._connect()) as db, db:
                db.execute('BEGIN')
                require_migrated_policy(ledger, db)
            return {**verified, 'action':'already_opened', 'paid_calls':0}
        # An existing file or SQLite sidecar belongs to a prior installation or
        # interrupted operation. Only the original servicing path may reconcile it.
        require(not any(os.path.lexists(str(path)+s) for s in ('','-wal','-shm','-journal')),
                'existing ledger or sidecar; use existing-ledger reconciliation')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with os.fdopen(os.open(witness, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600), 'wb') as f:
            f.write(canonical_bytes(_instance(path, verified['evidence_sha256'])))
            f.flush()
            os.fsync(f.fileno())
        os.fsync(parent)
        with closing(ledger._connect()) as db, db:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN IMMEDIATE')
            ledger._time()
            ledger._create_schema(db)
            db.execute('PRAGMA user_version=2')
            db.execute('INSERT INTO conf VALUES(?)',(ledger.config,))
            db.execute('''CREATE TABLE opening_reserve(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        evidence_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
                        reserved_nano_usd INTEGER NOT NULL, historical_requests INTEGER NOT NULL)''')
            db.execute('INSERT INTO opening_reserve VALUES(1,?,?,?,?)',
                       (canonical_bytes(evidence).decode(), verified['evidence_sha256'],
                        verified['reserved_nano_usd'], verified['historical_requests']))
            stamp_opening_policy(ledger, db)
            require_migrated_policy(ledger, db)
        os.fsync(parent)
        return {**verified,'action':'opened_with_historical_upper_bound','paid_calls':0}
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    prepare = subs.add_parser('prepare', help='Build a path-bound redacted inventory from reconciled records')
    prepare.add_argument('--ledger', type=Path, required=True)
    prepare.add_argument('--records', type=Path, required=True, help='JSON array of operator-reviewed monetary records')
    prepare.add_argument('--upper-rate', required=True)
    prepare.add_argument('--additional-run', action='append', default=[])
    prepare.add_argument('--proof-dir', type=Path, required=True)
    prepare.add_argument('--out', type=Path, required=True)
    for op in ['inspect','initialize']:
        arg = subs.add_parser(op)
        arg.add_argument('--evidence', type=Path, required=True)
        arg.add_argument('--proof-dir', type=Path, required=True)
        if op == 'initialize':
            arg.add_argument('--config', type=Path, required=True)
            arg.add_argument('--no-existing-project-ledger', action='store_true')
            arg.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            require(args.ledger.is_absolute() and args.ledger == args.ledger.resolve(strict=False),
                    'provide the approved absolute canonical ledger path')
            evidence = dict(schema=SCHEMA, ledger_path_sha256=sha256(os.fsencode(args.ledger)).hexdigest(),
                            opening_fx_cny_per_usd_upper=args.upper_rate,
                            runs=['direct-test','app-smoke',*args.additional_run], records=load_json(args.records))
            result = inspect_evidence(evidence, args.proof_dir)
            with os.fdopen(os.open(args.out, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600),'wb') as out:
                out.write(canonical_bytes(evidence))
                out.flush()
                os.fsync(out.fileno())
            print(json.dumps(result, ensure_ascii=False))
            return
        evidence = load_json(args.evidence)
        if args.command == 'inspect':
            result = inspect_evidence(evidence, args.proof_dir)
        else:
            cfg = load_json(args.config)
            fields = {'path','approval_sha','price_sha','verified_epoch','expiry_epoch','seat_groups',
                      'cap_policy','opening_evidence_sha256'}
            require(isinstance(cfg,dict) and set(cfg) == fields and isinstance(cfg['path'],str)
                    and Path(cfg['path']).is_absolute(), 'invalid persistent operator configuration')
            result = provision_opening(GroupBudget(**cfg), evidence, proof_dir=args.proof_dir,
                                       no_existing_project_ledger=args.no_existing_project_ledger,
                                       writers_stopped=args.writers_stopped)
        print(json.dumps(result, ensure_ascii=False))
    except Denied as error:
        parser.exit(1,'Opening declined: '+str(error)+'\n')
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError, RecursionError):
        parser.exit(1,'Opening declined: missing, unreadable or invalid financial evidence or ledger.\n')


if __name__ == '__main__':
    main()
