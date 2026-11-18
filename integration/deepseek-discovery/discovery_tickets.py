"""Offline adapter reference; operator-provisioned test ledger, no network client or keys.
The fixed tool allocation must exclude funds reserved by any generation budget.
"""
import json,secrets,sqlite3,time,threading
from discovery import run,academic_url
class Denied(ValueError):pass
class Ledger:
    def __init__(self,path,*,tool_limit_micro,seat_limit_micro,valid_until,clock=time.time):
        self.path,self.total,self.seat,self.end,self.clock=path,tool_limit_micro,seat_limit_micro,valid_until,clock
        if any(type(x) is not int or not 0<x<=100000000 for x in [self.total,self.seat]) or self.seat>self.total:raise Denied('invalid allocation')
    def initialize_for_test(self):
        with sqlite3.connect(self.path) as c:c.execute('CREATE TABLE IF NOT EXISTS spend(id TEXT PRIMARY KEY,seat TEXT NOT NULL,action TEXT NOT NULL,amount INTEGER NOT NULL)')
    def reserve(self,seat,key,action,amount):
        if action not in ('search','fetch') or amount!=750 or self.clock()>=self.end:raise Denied('price/approval expired')
        # mode=rw refuses a missing persistent ledger, never resets on absence.
        with sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=4) as c:
            c.execute('BEGIN IMMEDIATE')
            a,b=c.execute('SELECT COALESCE(SUM(amount),0),COALESCE(SUM(CASE WHEN seat=? THEN amount ELSE 0 END),0) FROM spend',(seat,)).fetchone()
            if a+amount>self.total or b+amount>self.seat:raise Denied('tool allocation exhausted')
            c.execute('INSERT INTO spend VALUES(?,?,?,?)',(key,seat,action,amount))
class Tickets:
    def __init__(self,ledger=None,transport=None,clock=time.monotonic):
        self.ledger,self.transport,self.clock=ledger,transport,clock
        self.lock=threading.Lock();self.pending={};self.offers={};self.caller=None;self.groups={}
    @classmethod
    def grouped(cls,caller):
        from group_dispatch import ApprovedGroupCaller
        if not isinstance(caller,ApprovedGroupCaller):raise Denied("invalid dispatcher")
        obj=cls();obj.caller=caller;return obj
    def group_for(self,seat,op=None):
        """Pick a group for a new preview. Existing tickets keep their group."""
        if op is not None and op not in ('model','search','fetch'):
            raise Denied('unsupported operation')
        with self.lock:
            return self._group_for_locked(seat,op)
    def _group_for_locked(self,seat,op):
        now=self.clock()
        self.groups={k:v for k,v in self.groups.items() if v[0]>now}
        current=self.groups.get(seat)
        if current is not None:
            already_reserved=(op is not None and self.caller is not None
                              and self.caller.ledger.has_reservation(seat=seat,gid=current[1],op=op))
            if not already_reserved:
                return current[1]
        elif len(self.groups)>=16:
            raise Denied('group capacity')
        gid=secrets.token_hex(32)
        self.groups[seat]=(now+600,gid)
        return gid
    def preview(self,seat,action,value):
        with self.lock:
            now=self.clock()
            self.pending={k:v for k,v in self.pending.items() if v[0]>now}
            self.offers={k:v for k,v in self.offers.items() if v[0]>now}
            if len(self.pending)>=16 and seat not in self.pending:raise Denied('busy')
            if action=='search':
                if not isinstance(value,str) or not 1<=len(value)<=128 or not value.strip() or not value.isprintable():raise Denied('invalid query')
                gid=self._group_for_locked(seat,'search')
                self.offers.pop(seat,None)
                body={'query':value,'count':5,'need_content':False}
            elif action=='fetch':
                opt=self.offers.get(seat,(0,()))[1]
                if type(value) is not int or not 0<=value<len(opt):raise Denied('no authorized result')
                gid=self.offers[seat][2];value=opt[value];body={'url':value}
            else:raise Denied('unsupported operation')
            key=secrets.token_hex(24);self.pending[seat]=(now+120,key,action,value,gid)
        return {'token':key,'action':action,'body':body,'max_micro_usd':750,'group':gid,'online':bool(self.caller or (self.transport and self.ledger))}
    def confirm(self,seat,key,consent):
        if consent is not True or not(self.caller or (self.transport and self.ledger)):raise Denied('not enabled or not consented')
        with self.lock:
            row=self.pending.get(seat)
            if not row or type(key) is not str or not key.isascii() or not secrets.compare_digest(row[1],key) or row[0]<=self.clock():raise Denied('stale or wrong seat')
            del self.pending[seat]
        _,key,action,value,gid=row
        if self.caller:
            result=run(action,value,confirmed=True,allowed_results=(value,) if action=='fetch' else (),reserve=lambda _:None,send=lambda _,body:self.caller.call(seat,gid,action,secrets.token_hex(32),body))
        else:
            result=run(action,value,confirmed=True,allowed_results=(value,) if action=='fetch' else (),reserve=lambda micro:self.ledger.reserve(seat,key,action,micro),send=self.transport)
        if action=='search':
            with self.lock:
                self.offers={k:v for k,v in self.offers.items() if v[0]>self.clock()}
                if len(self.offers)>=16 and seat not in self.offers:raise Denied('result capacity')
                self.offers[seat]=(self.clock()+120,[r['url'] for r in result if academic_url(r['url'])],gid)
        return result
    def handle(self,action,seat_digest,body):
        if not isinstance(seat_digest,str) or len(seat_digest)!=64 or any(c not in '0123456789abcdef' for c in seat_digest) or not isinstance(body,dict):return 400,{'error':'invalid scope'}
        try:
            if action=='preview' and set(body)=={'action','value'}:
                return 200,self.preview(seat_digest,body['action'],body['value'])
            if action=='confirm' and set(body)=={'token','consent'}:
                result=self.confirm(seat_digest,body['token'],body['consent'])
                return 200,{'result':result,'verified_local_pdf':False}
            return 400,{'error':'invalid request'}
        except (ValueError,TypeError,KeyError):return 409,{'error':'declined, expired, invalid selection or allocation exhausted'}
        except (OSError,sqlite3.Error):return 503,{'error':'unavailable; no automatic retry'}
