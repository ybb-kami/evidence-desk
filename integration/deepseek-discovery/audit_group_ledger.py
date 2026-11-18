"""Read-only aggregate postflight; never prints identities, tokens, prompts or request hashes."""
from pathlib import Path
import argparse,sqlite3,json
from group_budget import CAP,GROUPS,NANO,Denied
def audit(path):
    with sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True) as c:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        if c.execute('PRAGMA quick_check').fetchone()!=('ok',) or c.execute('PRAGMA foreign_key_check').fetchone():raise Denied('ledger integrity')
        groups=c.execute('SELECT COUNT(*) FROM groups_').fetchone()[0]
        ops={};total=0
        for kind,n,low,hi,charge in c.execute('SELECT op,count(*),min(nano),max(nano),sum(nano) FROM spend GROUP BY op'):
            if kind not in NANO or not low==hi==NANO[kind] or n>groups:raise Denied('unknown or altered rate or operation')
            ops[kind]=n;total+=charge
        if groups>GROUPS or total>CAP or c.execute('SELECT 1 FROM spend GROUP BY gid,op HAVING count(*)!=1 LIMIT 1').fetchone():raise Denied('cap or uniqueness violation')
        return {'groups_with_reservations':groups,'reserved_operations':ops,'reserved_nano_usd':total,'reserved_usd':f'{total/1e9:.9f}','provider_invoiced_amount':'not_observed'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('db',type=Path);print(json.dumps(audit(p.parse_args().db)))
