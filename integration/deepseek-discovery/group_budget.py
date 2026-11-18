"""One persistent admission ledger for one approved trial; no credentials, networking or retries.
Trusted server must direct all three kinds of call through ONE ledger and ONE exact wire sender.
Never reinitialize a lost ledger. Only provision() creates one (operator-only, exclusive).
"""
import hashlib,json,os,re,sqlite3,time
from pathlib import Path
from discovery import academic_url
class Denied(ValueError):pass
MODEL='deepseek-flash'
VERSION='DeepSeek-V4.1-Flash-1017'
ENDPOINT={'model':'https://api.deepseek.com/chat/completions','search':'https://api.deepseek.com/v1/tools/search','fetch':'https://api.deepseek.com/v1/tools/fetch'}
LEGACY={'model':16384*300+512*1200,'search':750000,'fetch':750000}
NANO={'model':(2**20)*300+512*1200,'search':750000,'fetch':750000}
CAP=710000000; GROUPS=100
def _id(s):
    return isinstance(s,str) and re.fullmatch(r'[0-9a-f]{64}',s)
def _pairs(pairs):
    d={}
    for k,v in pairs:
        if k in d:raise Denied('duplicate field')
        d[k]=v
    return d
class GroupBudget:
    def __init__(self,path,*,approval_sha,price_sha,verified_epoch,expiry_epoch,seat_groups=100,input_bound=None,version=VERSION,clock=time.time):
        self.path=Path(path).absolute()
        if not _id(approval_sha) or not _id(price_sha) or version!=VERSION or type(seat_groups) is not int or not 1<=seat_groups<=100 or type(verified_epoch) is not int or type(expiry_epoch) is not int or not 1<=expiry_epoch-verified_epoch<=86400:raise Denied('invalid approval/price')
        self.clock,self.checked,self.expiry,self.seat_groups=clock,verified_epoch,expiry_epoch,seat_groups
        self.input_bound=input_bound # trusted provider/tokenizer count upper bound, inclusive of all framing
        fingerprint=lambda rates:hashlib.sha256(json.dumps([approval_sha,price_sha,verified_epoch,expiry_epoch,seat_groups,version,rates,CAP,GROUPS],sort_keys=True).encode()).hexdigest()
        self.config,self.legacy=fingerprint(NANO),fingerprint(LEGACY)
    def migrate_tool_only(self):
        self._time()
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT hash FROM conf').fetchall()!=[(self.legacy,)]:raise Denied('not the matching old ledger')
            if c.execute("SELECT 1 FROM spend WHERE op NOT IN ('search','fetch') OR nano!=750000 LIMIT 1").fetchone():raise Denied('prior model or unrecognized charge: manual reconciliation required')
            if c.execute('PRAGMA quick_check').fetchone()!=('ok',) or c.execute('PRAGMA foreign_key_check').fetchone():raise Denied('bad ledger')
            c.execute('UPDATE conf SET hash=? WHERE hash=?',(self.config,self.legacy))
    def _time(self):
        if not self.checked<=self.clock()<self.expiry:raise Denied('stale price or approval')
    def provision(self):
        self._time()
        fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        # Partial or failed provisioning stays closed; never silently replace.
        with self._connect() as c:
            c.executescript('CREATE TABLE conf(hash TEXT NOT NULL);CREATE TABLE groups_(gid TEXT PRIMARY KEY NOT NULL,seat TEXT NOT NULL);CREATE TABLE spend(rid TEXT PRIMARY KEY NOT NULL,gid TEXT NOT NULL,op TEXT NOT NULL,nano INTEGER NOT NULL,wire_sha TEXT NOT NULL, UNIQUE(gid,op), FOREIGN KEY(gid) REFERENCES groups_(gid));')
            c.execute('INSERT INTO conf VALUES (?)',(self.config,))
    def _connect(self):
        if self.path.is_symlink() or not self.path.is_file():raise Denied('ledger absent or unsafe')
        c=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=8)
        c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA foreign_keys=ON')
        return c
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
    def reserve(self,*,seat,gid,op,rid,endpoint,wire):
        if not all(map(_id,[seat,gid,rid])):raise Denied('server-minted scope required')
        self._time();self._wire(op,endpoint,wire)
        digest=hashlib.sha256(wire).hexdigest()
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._time()
            if c.execute('SELECT hash FROM conf').fetchall()!=[(self.config,)]:raise Denied('configuration does not bind ledger')
            group=c.execute('SELECT seat FROM groups_ WHERE gid=?',(gid,)).fetchone()
            if group and group[0]!=seat:raise Denied('wrong seat')
            if not group:
                n,k=c.execute('SELECT COUNT(*),COALESCE(SUM(CASE WHEN seat=? THEN 1 ELSE 0 END),0) FROM groups_',(seat,)).fetchone()
                if n>=GROUPS or k>=self.seat_groups:raise Denied('group cap')
                c.execute('INSERT INTO groups_ VALUES(?,?)',(gid,seat))
            amount=c.execute('SELECT COALESCE(SUM(nano),0) FROM spend').fetchone()[0]
            if amount+NANO[op]>CAP:raise Denied('hard cap')
            try:c.execute('INSERT INTO spend VALUES(?,?,?,?,?)',(rid,gid,op,NANO[op],digest))
            except sqlite3.IntegrityError as e:raise Denied('duplicate action or request') from e
        return {'request_sha256':digest,'max_nano_usd':NANO[op],'endpoint':endpoint}
