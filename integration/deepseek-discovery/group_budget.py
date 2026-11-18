"""One persistent admission ledger for one approved trial; no credentials, networking or retries.
Trusted server must direct all three kinds of call through ONE ledger and ONE exact wire sender.
Never reinitialize a lost ledger. Only provision() creates one (operator-only, exclusive).
"""
import hashlib,json,os,re,sqlite3,time
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from discovery import academic_url
class Denied(ValueError):pass
MODEL='deepseek-flash'
VERSION='DeepSeek-V4.1-Flash-1017'
ENDPOINT={'model':'https://api.deepseek.com/chat/completions','search':'https://api.deepseek.com/v1/tools/search','fetch':'https://api.deepseek.com/v1/tools/fetch'}
LEGACY={'model':16384*300+512*1200,'search':750000,'fetch':750000}
NANO={'model':(2**20)*300+512*1200,'search':750000,'fetch':750000}
CAP=710000000; GROUPS=100
CAP_POLICY_SCHEMA=1
_CNY_LIMIT_NANO=20_000_000_000
_FX_FIELDS={'cny_per_usd_upper','fx_source_sha256','fx_verified_epoch','fx_expiry_epoch'}
_RATE_TEXT=re.compile(r'(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,9})?',re.ASCII)
def _id(s):
    return isinstance(s,str) and re.fullmatch(r'[0-9a-f]{64}',s)
def _pairs(pairs):
    d={}
    for k,v in pairs:
        if k in d:raise Denied('duplicate field')
        d[k]=v
    return d

def _cap(policy):
    if policy is None:
        return CAP,None
    if not isinstance(policy,dict) or set(policy)!=_FX_FIELDS:
        raise Denied('invalid exchange-rate policy')
    rate,source,verified,expiry=(policy[k] for k in ('cny_per_usd_upper','fx_source_sha256',
                                                    'fx_verified_epoch','fx_expiry_epoch'))
    if (not isinstance(rate,str) or not _RATE_TEXT.fullmatch(rate) or not _id(source)
            or type(verified) is not int or type(expiry) is not int
            or not 0<=verified<expiry<=2**53 or expiry-verified>86400):
        raise Denied('invalid exchange-rate policy')
    decimal=Decimal(rate)
    if not decimal.is_finite() or decimal<=0:
        raise Denied('invalid exchange-rate upper bound')
    # An integer ratio makes the floor independent of the Decimal context.
    numerator,denominator=decimal.as_integer_ratio()
    cap=_CNY_LIMIT_NANO*denominator//numerator
    if not 1<=cap<2**63:
        raise Denied('exchange-rate cap out of range')
    canonical=rate.rstrip('0').rstrip('.') if '.' in rate else rate
    return cap,dict(policy,cny_per_usd_upper=canonical)

class GroupBudget:
    def __init__(self,path,*,approval_sha,price_sha,verified_epoch,expiry_epoch,seat_groups=100,input_bound=None,version=VERSION,clock=time.time,cap_policy=None):
        self.path=Path(path).absolute()
        if not _id(approval_sha) or not _id(price_sha) or version!=VERSION or type(seat_groups) is not int or not 1<=seat_groups<=100 or type(verified_epoch) is not int or type(expiry_epoch) is not int or not 1<=expiry_epoch-verified_epoch<=86400:raise Denied('invalid approval/price')
        self.clock,self.checked,self.expiry,self.seat_groups=clock,verified_epoch,expiry_epoch,seat_groups
        self.input_bound=input_bound # trusted provider/tokenizer count upper bound, inclusive of all framing
        self._cap_nano,self._cap_policy=_cap(cap_policy)
        self.policy_version=CAP_POLICY_SCHEMA if self._cap_policy is not None else 0

        def fingerprint(rates):
            values=[approval_sha,price_sha,verified_epoch,expiry_epoch,seat_groups,version,rates,self.cap,GROUPS]
            if self._cap_policy is not None:
                values.extend(['cny-20-upper-bound-v1',self._cap_policy])
            return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()

        self.config,self.legacy=fingerprint(NANO),fingerprint(LEGACY)

    @property
    def cap(self):
        return self._cap_nano

    @property
    def cap_policy(self):
        return None if self._cap_policy is None else dict(self._cap_policy)

    def _check_cap_marker(self,c):
        if c.execute('PRAGMA user_version').fetchone()!=(self.policy_version,):
            raise Denied('cap policy does not bind ledger')

    def migrate_tool_only(self):
        self._time()
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._check_cap_marker(c)
            if c.execute('SELECT hash FROM conf').fetchall()!=[(self.legacy,)]:raise Denied('not the matching old ledger')
            if c.execute("SELECT 1 FROM spend WHERE op NOT IN ('search','fetch') OR nano!=750000 LIMIT 1").fetchone():raise Denied('prior model or unrecognized charge: manual reconciliation required')
            if c.execute('PRAGMA quick_check').fetchone()!=('ok',) or c.execute('PRAGMA foreign_key_check').fetchone():raise Denied('bad ledger')
            c.execute('UPDATE conf SET hash=? WHERE hash=?',(self.config,self.legacy))
    def _time(self):
        now=self.clock()
        if type(now) not in (int,float) or not self.checked<=now<self.expiry:raise Denied('stale price or approval')
        if self._cap_policy is not None and not self._cap_policy['fx_verified_epoch']<=now<self._cap_policy['fx_expiry_epoch']:
            raise Denied('stale exchange rate')
    def provision(self):
        self._time()
        fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        # Partial or failed provisioning stays closed; never silently replace.
        with self._connect() as c:
            c.executescript('CREATE TABLE conf(hash TEXT NOT NULL);CREATE TABLE groups_(gid TEXT PRIMARY KEY NOT NULL,seat TEXT NOT NULL);CREATE TABLE spend(rid TEXT PRIMARY KEY NOT NULL,gid TEXT NOT NULL,op TEXT NOT NULL,nano INTEGER NOT NULL,wire_sha TEXT NOT NULL, UNIQUE(gid,op), FOREIGN KEY(gid) REFERENCES groups_(gid));')
            c.execute('BEGIN IMMEDIATE')
            c.execute(f'PRAGMA user_version={self.policy_version}')
            c.execute('INSERT INTO conf VALUES (?)',(self.config,))
    def _connect(self):
        if self.path.is_symlink() or not self.path.is_file():raise Denied('ledger absent or unsafe')
        c=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=8)
        c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA foreign_keys=ON')
        return c
    def has_reservation(self, *, seat, gid, op):
        if not _id(seat) or not _id(gid) or not isinstance(op, str) or op not in ENDPOINT:
            raise Denied('server-minted scope required')
        if self.path.is_symlink() or not self.path.is_file():
            raise Denied('ledger absent or unsafe')
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=8)) as c:
            c.execute('BEGIN')
            self._time()
            self._check_cap_marker(c)
            if c.execute('SELECT hash FROM conf').fetchall() != [(self.config,)]:
                raise Denied('configuration does not bind ledger')
            group = c.execute('SELECT seat FROM groups_ WHERE gid=?', (gid,)).fetchone()
            if group is None:
                return False
            if group != (seat,):
                raise Denied('wrong seat')
            return c.execute('SELECT 1 FROM spend WHERE gid=? AND op=?', (gid, op)).fetchone() is not None
    def _wire(self,op,endpoint,wire):
        if op not in ENDPOINT or endpoint!=ENDPOINT[op] or not isinstance(wire,bytes) or not 1<=len(wire)<=65536:raise Denied('wire/endpoint')
        b=json.loads(wire,object_pairs_hook=_pairs)
        if not isinstance(b,dict):raise Denied('invalid body')
        if op=='search':
            if set(b)!={'query','count','need_content'} or not isinstance(b['query'],str) or not 1<=len(b['query'])<=128 or not b['query'].isprintable() or not b['query'].strip() or type(b['count']) is not int or not 1<=b['count']<=5 or b['need_content'] is not False:raise Denied('search params')
        elif op=='fetch':
            if set(b)!={'url'} or not academic_url(b['url']):raise Denied('unapproved URL')
        else:
            if set(b)!={'model','messages','max_tokens','stream','thinking','response_format'} or b['model']!=MODEL or b['stream'] is not False or b['thinking']!={'type':'disabled'} or b['response_format']!={'type':'json_object'} or type(b['max_tokens']) is not int or not 1<=b['max_tokens']<=512:raise Denied('unapproved model/options')
            ms=b['messages']
            if not isinstance(ms,list) or not 1<=len(ms)<=8 or not all(isinstance(m,dict) and set(m)=={'role','content'} and m['role'] in ('system','user') and isinstance(m['content'],str) for m in ms):raise Denied('text only')
            upper=self.input_bound(wire) if self.input_bound else None
            if type(upper) is not int or not 1<=upper<=16384:raise Denied('no proven input bound')
    def reserve(self,*,seat,gid,op,rid,endpoint,wire,private_dispatch=False):
        if not all(map(_id,[seat,gid,rid])):raise Denied('server-minted scope required')
        if type(private_dispatch) is not bool:raise Denied('invalid dispatch mode')
        self._time();self._wire(op,endpoint,wire)
        digest=hashlib.sha256(wire).hexdigest()
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._time()
            self._check_cap_marker(c)
            if c.execute('SELECT hash FROM conf').fetchall()!=[(self.config,)]:raise Denied('configuration does not bind ledger')
            group=c.execute('SELECT seat FROM groups_ WHERE gid=?',(gid,)).fetchone()
            if group and group[0]!=seat:raise Denied('wrong seat')
            if not group:
                n,k=c.execute('SELECT COUNT(*),COALESCE(SUM(CASE WHEN seat=? THEN 1 ELSE 0 END),0) FROM groups_',(seat,)).fetchone()
                if n>=GROUPS or k>=self.seat_groups:raise Denied('group cap')
                c.execute('INSERT INTO groups_ VALUES(?,?)',(gid,seat))
            amount=c.execute('SELECT COALESCE(SUM(nano),0) FROM spend').fetchone()[0]
            if amount+NANO[op]>self.cap:raise Denied('hard cap')
            try:
                c.execute('INSERT INTO spend VALUES(?,?,?,?,?)',(rid,gid,op,NANO[op],digest))
                if private_dispatch:
                    c.execute('CREATE TABLE IF NOT EXISTS private_pending(rid TEXT PRIMARY KEY REFERENCES spend(rid))')
                    c.execute('INSERT INTO private_pending VALUES (?)',(rid,))
            except sqlite3.IntegrityError as e:
                raise Denied('duplicate action or request') from e
        return {'request_sha256':digest,'max_nano_usd':NANO[op],'endpoint':endpoint}
