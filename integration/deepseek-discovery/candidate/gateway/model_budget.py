"""Durable admission gate for a narrowly configured DeepSeek request.

No network or credential handling. Approval and price evidence are trusted
server-side inputs, not values a browser may supply. A reservation charges the
worst case irrevocably, whether or not the caller manages to send or complete.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
_PROVIDER = "DeepSeek"
_SCALE = 1_000_000_000  # integer nano-units in the specified currency
_MAX = 10**15  # SQLite sums are checked within this configured maximum
_MAX_COUNTER = 1_000_000
_MAX_BODY = 65_536
_MAX_WINDOW = 366 * 24 * 60 * 60
_MAX_PRICE_AGE = 24 * 60 * 60
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_KEY = re.compile(r"[-A-Za-z0-9_.:]{16,128}\Z")


class BudgetDenied(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedPrice:
    provider: str
    model: str
    currency: str
    source_url: str
    source_sha256: str
    verified_epoch: int
    expires_epoch: int
    input_miss_per_million: str
    output_per_million: str
    additional_per_request_upper: str
    billable_input_tokens_upper: int
    billable_output_tokens_limit: int
    output_cap_covers_all_billed_tokens: bool
    bounds_and_fees_verified: bool

    @property
    def fingerprint(self):
        return _fingerprint(asdict(self))


@dataclass(frozen=True)
class ApprovedLimits:
    approval_id: str
    user_approval_sha256: str
    price_fingerprint: str
    currency: str
    approved_epoch: int
    expires_epoch: int
    total_amount: str
    period_amount: str
    session_amount: str
    per_request_amount: str
    total_requests: int
    period_requests: int
    session_requests: int
    period_seconds: int
    max_input_bytes: int
    max_output_tokens: int


@dataclass(frozen=True)
class Reservation:
    request_id: str
    session_id: str
    endpoint: str
    model: str
    currency: str
    precharged_nano: int
    body_sha256: str
    reserved_epoch: int

    @property
    def precharged_amount(self):
        return format(Decimal(self.precharged_nano)/_SCALE,"f")


def _fingerprint(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def _bounded_int(value, low, high):
    return type(value) is int and low<=value<=high


def _number(value):
    # No exponents, signs, floats, NaN, or provider-sized SQL overflow.
    if not isinstance(value,str) or not re.fullmatch(r"\d{1,12}(?:\.\d{1,12})?",value):
        raise BudgetDenied("invalid_price_or_approval")
    return Decimal(value)


def _cap(value):
    units=int((_number(value)*_SCALE).to_integral_value(rounding=ROUND_FLOOR))
    if not 0<units<=_MAX:
        raise BudgetDenied("invalid_price_or_approval")
    return units


def _is_key(value, regex):
    return isinstance(value,str) and bool(regex.fullmatch(value))


def _unique(pairs):
    data={}
    for key,value in pairs:
        if key in data:
            raise ValueError("duplicate JSON key")
        data[key]=value
    return data


class BudgetGate:
    def __init__(self, db_path, approval=None, price=None, *, clock=time.time):
        self.path = Path(db_path)
        self.approval=approval
        self.price=price
        self.clock=clock

    def _rules(self,now):
        a,p=self.approval,self.price
        if not isinstance(a,ApprovedLimits) or not isinstance(p,VerifiedPrice):
            raise BudgetDenied("default_denied")
        if (p.provider!=_PROVIDER or p.source_url!=PRICE_SOURCE or not _is_key(p.source_sha256,_SHA)
                or not _is_key(a.user_approval_sha256,_SHA) or not _is_key(a.approval_id,_KEY)
                or not isinstance(p.model,str) or not re.fullmatch(r"deepseek-[a-z0-9-]{1,48}",p.model)
                or p.currency not in ("USD","CNY") or a.currency!=p.currency
                or p.output_cap_covers_all_billed_tokens is not True or p.bounds_and_fees_verified is not True
                or not _bounded_int(p.billable_input_tokens_upper,1,10_000_000)
                or not _bounded_int(p.billable_output_tokens_limit,1,1_000_000)):
            raise BudgetDenied("unverified_price_or_model")
        if (not all(_bounded_int(k,0,1<<53) for k in (p.verified_epoch,p.expires_epoch,a.approved_epoch,a.expires_epoch))
                or not p.verified_epoch<=a.approved_epoch<=now<min(p.expires_epoch,a.expires_epoch)
                or not 0<p.expires_epoch-p.verified_epoch<=_MAX_PRICE_AGE):
            raise BudgetDenied("expired_or_unverified_authorization")
        if a.price_fingerprint!=p.fingerprint:
            raise BudgetDenied("approval_does_not_bind_price")
        if (not _bounded_int(a.period_seconds,1,_MAX_WINDOW) or not _bounded_int(a.max_input_bytes,1,_MAX_BODY)
                or not _bounded_int(a.max_output_tokens,1,p.billable_output_tokens_limit)
                or not _bounded_int(a.total_requests,1,_MAX_COUNTER)
                or not _bounded_int(a.session_requests,1,a.total_requests)
                or not _bounded_int(a.period_requests,1,a.total_requests)):
            raise BudgetDenied("invalid_price_or_approval")
        total,period,session,one=map(_cap,(a.total_amount,a.period_amount,a.session_amount,a.per_request_amount))
        if not one<=min(period,session) or max(period,session)>total:
            raise BudgetDenied("invalid_price_or_approval")
        ir,orr,fee=map(_number,(p.input_miss_per_million,p.output_per_million,p.additional_per_request_upper))
        if ir<=0 or orr<=0:
            raise BudgetDenied("unverified_price_or_model")
        return (total,period,session,one,ir,orr,fee)

    @staticmethod
    def _body(raw,model,a):
        if not isinstance(raw,bytes) or not raw or len(raw)>a.max_input_bytes:
            raise BudgetDenied("input_size_exceeded")
        try:
            obj=json.loads(raw.decode("utf-8"),object_pairs_hook=_unique)
        except (ValueError,UnicodeError,RecursionError):
            raise BudgetDenied("invalid_request") from None
        allowed={"model","messages","max_tokens","stream","thinking","response_format","temperature"}
        required={"model","messages","max_tokens","stream","thinking"}
        if not isinstance(obj,dict) or not required<=obj.keys() or not obj.keys()<=allowed:
            raise BudgetDenied("unsupported_request_shape")
        if obj["model"]!=model:
            raise BudgetDenied("unverified_price_or_model")
        if obj["stream"] is not False or obj["thinking"]!={"type":"disabled"}:
            raise BudgetDenied("unsupported_request_shape")
        if not _bounded_int(obj["max_tokens"],1,a.max_output_tokens):
            raise BudgetDenied("output_limit_exceeded")
        messages=obj["messages"]
        if (not isinstance(messages,list) or not 1<=len(messages)<=8
                or not all(isinstance(m,dict) and set(m)=={"role","content"} and
                           m["role"] in ("user","system") and isinstance(m["content"],str) and m["content"].strip()
                           for m in messages)):
            raise BudgetDenied("unsupported_request_shape")
        if "response_format" in obj and obj["response_format"] not in ({"type":"json_object"},{"type":"text"}):
            raise BudgetDenied("unsupported_request_shape")
        if "temperature" in obj and (type(obj["temperature"]) not in (int,float) or not 0<=obj["temperature"]<=2):
            raise BudgetDenied("unsupported_request_shape")
        return obj["max_tokens"]

    def _now(self):
        try:
            return int(self.clock())
        except (ValueError,TypeError,OverflowError):
            raise BudgetDenied("clock_untrusted") from None

    def _db(self):
        return sqlite3.connect(self.path.resolve().as_uri()+"?mode=rw",
                               uri=True,timeout=2,isolation_level=None)

    def initialize(self):
        """Explicit one-time operator provisioning; never call during request or restart."""
        now=self._now()
        self._rules(now)
        deployed=_fingerprint({"approval":asdict(self.approval),"price":asdict(self.price)})
        try:
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.close(fd)
        except FileExistsError:
            raise BudgetDenied("ledger_already_exists") from None
        except OSError:
            raise BudgetDenied("ledger_unavailable") from None
        db=None
        try:
            db=self._db()
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE budget_config (singleton INTEGER PRIMARY KEY CHECK(singleton=1), fingerprint TEXT NOT NULL, last_seen INTEGER NOT NULL)")
            db.execute("""CREATE TABLE budget_ledger (
                request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                epoch INTEGER NOT NULL, precharged_nano INTEGER NOT NULL CHECK(precharged_nano>0),
                body_sha TEXT NOT NULL)""")
            db.execute("CREATE INDEX idx_budget_epoch ON budget_ledger(epoch)")
            db.execute("CREATE INDEX idx_budget_session ON budget_ledger(session_id)")
            db.execute("INSERT INTO budget_config VALUES (1,?,?)",(deployed,now))
            db.execute("COMMIT")
        except sqlite3.Error:
            raise BudgetDenied("ledger_unavailable") from None
        finally:
            if db is not None:
                db.close()

    def reserve(self, *, request_id, session_id, request_body):
        """Perform only a durable reservation; caller sends these exact bytes once."""
        # The caller must derive stable request IDs and authentic session IDs;
        # browser-chosen IDs would defeat the session cap or replay control.
        if not _is_key(request_id,_KEY) or not _is_key(session_id,_KEY):
            raise BudgetDenied("invalid_server_identity")
        now=self._now()
        total,period,session,one,ir,orr,fee=self._rules(now)
        a,p=self.approval,self.price
        out=self._body(request_body,p.model,a)
        with localcontext() as ctx:
            ctx.prec=60
            estimated=(Decimal(p.billable_input_tokens_upper)*ir+Decimal(out)*orr)/1_000_000+fee
            amount=int((estimated*_SCALE).to_integral_value(rounding=ROUND_CEILING))
        if not 0<amount<=one or amount>_MAX:
            raise BudgetDenied("per_request_amount_exceeded")
        body_sha=hashlib.sha256(request_body).hexdigest()
        deployed=_fingerprint({"approval":asdict(a),"price":asdict(p)})
        db=None
        try:
            db=self._db()
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone()[0] not in ("delete","truncate","persist","wal"):
                raise BudgetDenied("ledger_unavailable")
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT fingerprint,last_seen FROM budget_config WHERE singleton=1").fetchone()
            if old is None:
                raise BudgetDenied("ledger_unavailable")
            if old[0]!=deployed:
                raise BudgetDenied("budget_configuration_changed")
            if now<old[1]:
                raise BudgetDenied("clock_rollback")
            if db.execute("SELECT 1 FROM budget_ledger WHERE request_id=?",(request_id,)).fetchone():
                raise BudgetDenied("request_already_reserved")
            for label,clause,param,amount_cap,count_cap in (
                ("total","",(),total,a.total_requests),
                ("period"," WHERE epoch>?",(now-a.period_seconds,),period,a.period_requests),
                ("session"," WHERE session_id=?",(session_id,),session,a.session_requests),
            ):
                count,spent=db.execute("SELECT COUNT(*),COALESCE(SUM(precharged_nano),0) FROM budget_ledger"+clause,param).fetchone()
                if count>=count_cap:
                    raise BudgetDenied(label+"_count_exceeded")
                if spent>amount_cap-amount:
                    raise BudgetDenied(label+"_amount_exceeded")
            db.execute("INSERT INTO budget_ledger VALUES (?,?,?,?,?)",(request_id,session_id,now,amount,body_sha))
            db.execute("UPDATE budget_config SET last_seen=? WHERE singleton=1",(now,))
            db.execute("COMMIT")
        except sqlite3.Error:
            # A damaged/locked/inaccessible ledger never permits a send.
            raise BudgetDenied("ledger_unavailable") from None
        finally:
            if db is not None:
                db.close()
        return Reservation(request_id,session_id,DEEPSEEK_ENDPOINT,p.model,p.currency,amount,body_sha,now)
