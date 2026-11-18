"""Read-only aggregate postflight; never prints identities, tokens, prompts or request hashes."""
from contextlib import closing
from pathlib import Path
import argparse,sqlite3,json
from group_budget import CAP,GROUPS,NANO,Denied,GroupBudget,_pairs


def _usd(nano):
    integral,fraction=divmod(nano,1_000_000_000)
    return f'{integral}.{fraction:09d}'


def audit(path,*,budget=None):
    """An FX ledger requires its current operator configuration to verify the applied cap."""
    path=Path(path).absolute()
    if path.is_symlink() or not path.is_file():raise Denied('ledger absent or unsafe')
    if budget is not None and (not isinstance(budget,GroupBudget) or budget.path!=path):
        raise Denied('budget configuration must identify this ledger')
    cap=CAP if budget is None else budget.cap
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            if budget is None:
                if c.execute('PRAGMA user_version').fetchone()!=(0,):
                    raise Denied('operator configuration required for cap policy')
            else:
                budget._time()
                budget._check_cap_marker(c)
                if c.execute('SELECT hash FROM conf').fetchall()!=[(budget.config,)]:
                    raise Denied('configuration does not bind ledger')
            if c.execute('PRAGMA quick_check').fetchone()!=('ok',) or c.execute('PRAGMA foreign_key_check').fetchone():raise Denied('ledger integrity')
            groups=c.execute('SELECT COUNT(*) FROM groups_').fetchone()[0]
            ops={};total=0
            for kind,n,low,hi,charge in c.execute('SELECT op,count(*),min(nano),max(nano),sum(nano) FROM spend GROUP BY op'):
                if kind not in NANO or not low==hi==NANO[kind] or n>groups:raise Denied('unknown or altered rate or operation')
                ops[kind]=n;total+=charge
            if groups>GROUPS or total>cap or c.execute('SELECT 1 FROM spend GROUP BY gid,op HAVING count(*)!=1 LIMIT 1').fetchone():raise Denied('cap or uniqueness violation')
            return {'groups_with_reservations':groups,'reserved_operations':ops,'reserved_nano_usd':total,
                    'reserved_usd':_usd(total),'limit_nano_usd':cap,'provider_invoiced_amount':'not_observed'}
    except sqlite3.Error:
        raise Denied('ledger integrity') from None


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('db',type=Path)
    p.add_argument('--config',type=Path,help='Operator JSON used to bind the active cap and fingerprint')
    args=p.parse_args()
    budget=None
    if args.config is not None:
        try:
            config=json.loads(args.config.read_text(),object_pairs_hook=_pairs)
        except (OSError,ValueError,RecursionError):
            p.error('could not read operator configuration')
        fields={'path','approval_sha','price_sha','verified_epoch','expiry_epoch','seat_groups'}
        if not isinstance(config,dict) or not fields<=set(config)<=fields|{'cap_policy'}:
            p.error('invalid operator configuration fields')
        try:
            budget=GroupBudget(**config)
        except (Denied,TypeError):
            p.error('invalid operator configuration')
    try:
        print(json.dumps(audit(args.db,budget=budget)))
    except Denied as error:
        p.error(str(error))
