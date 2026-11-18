"""Opt-in refresh of one public arXiv category while the workbench runs."""

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from time import time

import arxiv_tracking

CADENCES = {"daily": 86400, "weekly": 7 * 86400}
FIRST_RETRY = 900
MAX_RETRY = 21600
POLL = 15
SCHEMA = """
    CREATE TABLE IF NOT EXISTS arxiv_schedule (
        id INTEGER PRIMARY KEY CHECK (id=1), enabled INTEGER NOT NULL,
        category TEXT NOT NULL, cadence TEXT NOT NULL, next_due REAL,
        last_attempt REAL, last_refresh REAL, failures INTEGER NOT NULL,
        result TEXT
    )
"""
LOG = logging.getLogger(__name__)


def _timestamp(value):
    return None if value is None else datetime.fromtimestamp(value, timezone.utc).isoformat()


class TrackingSchedule:
    def __init__(self, library, *, clock=time, refresh=None):
        self.library = library
        self.clock = clock
        self.refresh = refresh or arxiv_tracking.refresh
        self.lock = Lock()
        self.wake = Event()
        self.stopping = Event()
        self.thread = Thread(target=self._loop, name="arxiv-schedule", daemon=True)
        with closing(self._connect()) as db, db:
            db.execute(SCHEMA)

    def _connect(self):
        db = sqlite3.connect(self.library.db)
        db.row_factory = sqlite3.Row
        return db

    def start(self):
        self.thread.start()

    def close(self):
        self.stopping.set()
        self.wake.set()
        if self.thread.is_alive():
            self.thread.join()

    def _loop(self):
        while not self.stopping.is_set():
            try:
                self.run_due()
            except sqlite3.Error:
                LOG.exception("Could not update the local arXiv schedule")
            self.wake.wait(POLL)
            self.wake.clear()

    def state(self):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM arxiv_schedule WHERE id=1").fetchone()
        if row is None:
            return {"enabled": False, "category": None, "cadence": None,
                    "next_due": None, "last_attempt": None, "last_refresh": None, "result": None}
        return {
            "enabled": bool(row["enabled"]), "category": row["category"], "cadence": row["cadence"],
            "next_due": _timestamp(row["next_due"]), "last_attempt": _timestamp(row["last_attempt"]),
            "last_refresh": _timestamp(row["last_refresh"]),
            "result": json.loads(row["result"]) if row["result"] is not None else None,
        }

    def subscribe(self, category, cadence):
        with self.lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO arxiv_schedule VALUES (1, 1, ?, ?, ?, NULL, NULL, 0, NULL) "
                "ON CONFLICT(id) DO UPDATE SET enabled=1, category=excluded.category, "
                "cadence=excluded.cadence, next_due=excluded.next_due, last_attempt=NULL, "
                "last_refresh=NULL, failures=0, result=NULL "
                "WHERE arxiv_schedule.enabled=0 OR arxiv_schedule.category<>excluded.category "
                "OR arxiv_schedule.cadence<>excluded.cadence",
                (category, cadence, self.clock()),
            )
        self.wake.set()
        return self.state()

    def stop(self):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("UPDATE arxiv_schedule SET enabled=0, next_due=NULL WHERE id=1")
        return self.state()

    def run_due(self):
        with self.lock:
            start = self.clock()
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT category, cadence, failures FROM arxiv_schedule WHERE id=1 "
                    "AND enabled=1 AND next_due<=?", (start,)
                ).fetchone()
                if row is None or self.stopping.is_set():
                    return False
                interval = CADENCES[row["cadence"]]
                category = row["category"]
                # Persist the claim before network access so a restart cannot replay it.
                db.execute("UPDATE arxiv_schedule SET last_attempt=?, next_due=? WHERE id=1",
                           (start, start + interval))
            try:
                attempt = self.refresh(category, self.library.records(), {}, consent=True)
            except Exception:
                LOG.exception("Scheduled arXiv refresh failed")
                attempt = {"items": [], "error": {"code": "local_error", "message": "定期刷新未完成。"}}
            finished = self.clock()
            failed = bool(attempt["error"])
            failures = min(row["failures"] + 1, 6) if failed else 0
            delay = min(FIRST_RETRY * 2 ** (failures - 1), MAX_RETRY) if failed else interval
            with closing(self._connect()) as db, db:
                db.execute(
                    "UPDATE arxiv_schedule SET next_due=?, failures=?, result=?, "
                    "last_refresh=coalesce(?, last_refresh) WHERE id=1 AND enabled=1 AND last_attempt=?",
                    (finished + delay, failures, json.dumps(attempt, ensure_ascii=False),
                     None if failed else finished, start),
                )
            return True
