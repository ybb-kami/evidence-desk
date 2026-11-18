"""Opt-in local category schedule. No access to documents or feedback."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time

import arxiv_tracking

CATEGORIES = arxiv_tracking.CATEGORIES
INTERVALS = (6, 12, 24)
GAP = 4
HISTORY = 1024
INBOX = 60
CAP = 640 * 1024


class SubscriptionError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _default():
    return dict(enabled=False, categories=[], interval_hours=24,
                due={}, seen={}, diagnostics={}, delivered=[], inbox=[], next_request=0, simulated=None)


def _pull(category):
    return arxiv_tracking.refresh(category, [], {}, consent=True)


def _id(value):
    return isinstance(value, str) and arxiv_tracking.ARXIV_ID.fullmatch(value) is not None


def _time(value):
    return type(value) in (int,float) and 0<=value<=10**12


def _observation(row, category):
    if not isinstance(row,dict) or not _id(row.get("id")) or category not in CATEGORIES:
        return None
    title, date = row.get("title"),row.get("published")
    if not isinstance(title,str) or not title.strip() or not isinstance(date,str) or not arxiv_tracking._date(date):
        return None
    ident = row["id"]
    return dict(id=ident, title=" ".join(title.split())[:400], category=category,
                published=date,url="https://arxiv.org/abs/"+ident)


class Subscriptions:
    def __init__(self, path, *, fetch=None, clock=time.time):
        self.path = Path(path)
        self.fetch, self.clock = fetch if fetch is not None else _pull, clock
        self.simulated = fetch is not None
        self.condition = threading.Condition()
        self.state = self._load()
        self.closed, self.busy, self.worker, self.generation = False, False, None, 0

    def _load(self):
        try:
            with self.path.open("rb") as fd:
                raw=fd.read(CAP+1)
            if len(raw)>CAP:
                return _default()
            data=json.loads(raw)
            if set(data)!=set(_default()) or type(data["enabled"]) is not bool:
                return _default()
            if data["simulated"] is not self.simulated:
                return _default()
            if data["interval_hours"] not in INTERVALS or not _time(data["next_request"]):
                return _default()
            categories=data["categories"]
            if (not isinstance(categories,list) or any(c not in CATEGORIES for c in categories)
                    or len(set(categories))!=len(categories)
                    or (data["enabled"] and not categories)):
                return _default()
            for field in ("due","seen","diagnostics"):
                if not isinstance(data[field],dict) or set(data[field])-set(CATEGORIES):
                    return _default()
            if set(categories)-set(data["due"]) or not all(_time(x) for x in data["due"].values()):
                return _default()
            for ids in [data["delivered"],*data["seen"].values()]:
                if not isinstance(ids,list) or len(ids)>HISTORY or any(not _id(i) for i in ids):
                    return _default()
            if not isinstance(data["inbox"],list) or len(data["inbox"])>INBOX:
                return _default()
            data["inbox"]=[i for row in data["inbox"] if isinstance(row,dict)
                           and (i:=_observation(row,row.get("category")))]
            # Diagnostics are informational and are limited to trusted scalar fields.
            data["diagnostics"]={c:dict(last_success_at=info.get("last_success_at") if _time(info.get("last_success_at")) else None,
                                        error=info.get("error") if isinstance(info.get("error"),str) and len(info["error"])<40 else None)
                                 for c,info in data["diagnostics"].items() if isinstance(info,dict)}
            return data
        except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
            return _default()

    def _save(self,new):
        temp=None
        try:
            encoded=json.dumps(new,ensure_ascii=True,separators=(",",":")).encode()
            if len(encoded)>CAP:
                raise OSError("state too large")
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix=".subscriptions-",delete=False) as fd:
                temp=Path(fd.name)
                fd.write(encoded); fd.flush(); os.fsync(fd.fileno())
            os.replace(temp,self.path)
        except OSError:
            raise SubscriptionError(503,"订阅本机设置无法写入；操作未生效。") from None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def _status(self):
        s=self.state
        return dict(enabled=s["enabled"], categories=s["categories"][:],
                    available_categories=list(CATEGORIES), interval_hours=s["interval_hours"],
                    allowed_intervals=list(INTERVALS), busy=self.busy, inbox=copy.deepcopy(s["inbox"]),
                    source_mode="simulation" if self.simulated else "live_arxiv_when_enabled",
                    schedule={c:dict(due=s["due"][c],baseline=c in s["seen"],**s["diagnostics"].get(c,{}))
                              for c in s["categories"]})

    def status(self):
        with self.condition:
            return self._status()

    def configure(self, req):
        if (not isinstance(req,dict) or set(req)-{"enabled","categories","interval_hours","consent"}
                or type(req.get("enabled")) is not bool):
            raise SubscriptionError(400,"订阅设置无效。")
        enabled=req["enabled"]
        if enabled and req.get("consent") is not True:
            raise SubscriptionError(403,"请明确同意定时发送所选公开 arXiv 分类。")
        with self.condition:
            if self.closed:
                raise SubscriptionError(503,"订阅服务已关闭。")
            s=copy.deepcopy(self.state)
            cats=req.get("categories",s["categories"] if not enabled else None)
            hours=req.get("interval_hours",s["interval_hours"] if not enabled else None)
            if (not isinstance(cats,list) or len(cats)>len(CATEGORIES)
                    or any(not isinstance(c,str) or c not in CATEGORIES for c in cats)
                    or len(set(cats))!=len(cats) or (enabled and not cats)
                    or type(hours) is not int or hours not in INTERVALS):
                raise SubscriptionError(400,"请选择允许的分类和 6、12 或 24 小时刷新周期。")
            now=self.clock()
            for c in cats:
                s["due"].setdefault(c,now)
                last=s["diagnostics"].get(c,{}).get("last_success_at")
                if hours!=s["interval_hours"] and last is not None:
                    s["due"][c]=last+hours*3600
            s.update(enabled=enabled,categories=cats,interval_hours=hours,simulated=self.simulated)
            self._save(s)
            self.state=s
            self.generation+=1
            self.condition.notify_all()
            return self._status()

    def mark_read(self):
        with self.condition:
            s=copy.deepcopy(self.state)
            s["inbox"]=[]
            s["simulated"]=self.simulated
            self._save(s)
            self.state=s
            return self._status()

    def _next(self):
        s=self.state
        return min((s["due"][c],c) for c in s["categories"]) if s["enabled"] else None

    def tick(self):
        """Handle at most one due category. Inject fake feed/clock in tests."""
        with self.condition:
            next_=self._next()
            now=self.clock()
            if self.closed or self.busy or next_ is None or max(next_[0],self.state["next_request"])>now:
                return False
            category=next_[1]
            s=copy.deepcopy(self.state)
            s["due"][category]=now+s["interval_hours"]*3600
            s["next_request"]=now+arxiv_tracking.TIMEOUT+GAP
            # Write attempt before network so restarting does not bypass cadence.
            try:
                self._save(s)
            except SubscriptionError:
                self.state["enabled"]=False
                return False
            self.state=s
            token=self.generation
            self.busy=True
        try:
            result=self.fetch(category)
            rows=result["items"]
            if not isinstance(rows,list):
                raise ValueError
            raw_error=result.get("error")
            code=raw_error.get("code") if isinstance(raw_error,dict) else None
            error=code if code in {"busy","cooldown","rate_limited","timeout","http_error",
                                   "redirect_refused","invalid_response","network_error"} else (
                                       "network_error" if raw_error is not None else None)
            items=[item for row in rows[:arxiv_tracking.ROWS] if (item:=_observation(row,category))]
            if error is None and rows and not items:
                error="invalid_response"
        except Exception:
            error,items="network_error",[]
        with self.condition:
            self.busy=False
            self.condition.notify_all()
            if self.closed or token!=self.generation or not self.state["enabled"]:
                return True
            s=copy.deepcopy(self.state)
            now=self.clock()
            s["next_request"]=now+GAP
            diagnostic=s["diagnostics"].setdefault(category,{})
            diagnostic["error"]=error
            if error in ("busy","cooldown"):
                s["due"][category]=now+60
            elif error=="rate_limited":
                s["next_request"]=now+3600
            if error is None:
                seen=set(s["seen"].get(category,[]))
                delivered=set(s["delivered"])
                if category in s["seen"]:
                    new=[]
                    for item in items:
                        if item["id"] not in seen and item["id"] not in delivered:
                            delivered.add(item["id"])
                            s["delivered"].append(item["id"])
                            new.append(item)
                    s["inbox"]=(new+s["inbox"])[:INBOX]
                s["seen"][category]=list(dict.fromkeys(s["seen"].get(category,[])+[i["id"] for i in items]))[-HISTORY:]
                s["delivered"]=s["delivered"][-HISTORY:]
                diagnostic["last_success_at"]=now
            try:
                self._save(s)
            except SubscriptionError:
                self.state["enabled"]=False
            else:
                self.state=s
            self.condition.notify_all()
        return True

    def _loop(self):
        while True:
            with self.condition:
                if self.closed:
                    return
                next_=self._next()
                if next_ is None:
                    self.condition.wait()
                    continue
                delay=max(next_[0],self.state["next_request"])-self.clock()
                if delay>0 or self.busy:
                    self.condition.wait(timeout=min(max(delay,1),3600))
                    continue
            self.tick()

    def start(self):
        with self.condition:
            if not self.closed and self.worker is None:
                self.worker=threading.Thread(target=self._loop,name="evidence-arxiv-subscriptions",daemon=True)
                self.worker.start()

    def close(self):
        with self.condition:
            self.closed=True
            self.condition.notify_all()
            worker=self.worker
        if worker is not None:
            worker.join(timeout=arxiv_tracking.TIMEOUT+2)
