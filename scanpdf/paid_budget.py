"""Persistent, atomic reservations for paid provider requests."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from uuid import uuid4


LIMIT_NUSD = 710_000_000
MAX_ROUNDS = 100
MAX_MODEL_CALLS = 100
_PER_ROUND = {"model": 2, "search": 1, "fetch": 1}

_TOTALS = """
SELECT (SELECT COUNT(*) FROM paid_rounds), COALESCE(SUM(cost_nusd), 0),
       COUNT(CASE WHEN kind = 'model' THEN 1 END),
       COUNT(CASE WHEN kind = 'search' THEN 1 END),
       COUNT(CASE WHEN kind = 'fetch' THEN 1 END)
FROM paid_reservations
"""


class BudgetExceeded(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Round:
    _budget: "Budget"
    _id: str

    def reserve(self, kind, cost_nusd):
        """Commit a maximum charge before making one call. Reservations are final."""
        self._budget._reserve(self._id, kind, cost_nusd)

    def check_model_capacity(self):
        totals = self._budget.status()
        if totals["remaining_nusd"] <= 0:
            raise BudgetExceeded("money_limit")
        if totals["model_calls"] >= MAX_MODEL_CALLS:
            raise BudgetExceeded("model_limit")


class Budget:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        try:
            with closing(self._connect(create=True)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS paid_rounds (
                        id TEXT PRIMARY KEY NOT NULL
                    ) WITHOUT ROWID
                """)
                db.execute(f"""
                    CREATE TABLE IF NOT EXISTS paid_reservations (
                        round_id TEXT NOT NULL REFERENCES paid_rounds(id),
                        kind TEXT NOT NULL CHECK (kind IN ('model', 'search', 'fetch')),
                        slot INTEGER NOT NULL CHECK (slot = 1 OR (kind = 'model' AND slot = 2)),
                        cost_nusd INTEGER NOT NULL CHECK (cost_nusd BETWEEN 1 AND {LIMIT_NUSD}),
                        PRIMARY KEY (round_id, kind, slot)
                    ) WITHOUT ROWID
                """)
        except sqlite3.Error as exc:
            raise BudgetExceeded("budget_unavailable") from exc

    def _connect(self, *, create=False):
        mode = "rwc" if create else "rw"
        db = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error:
            db.close()
            raise
        return db

    def start_round(self):
        """Allocate a request ticket. The first successful reservation counts the round."""
        return Round(self, uuid4().hex)

    def _reserve(self, round_id, kind, cost_nusd):
        if kind not in _PER_ROUND:
            raise ValueError("Unknown paid call kind")
        if cost_nusd is None:
            raise BudgetExceeded("unknown_price")
        if type(cost_nusd) is not int or cost_nusd <= 0:
            raise BudgetExceeded("invalid_cost")
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                slot = 1 + db.execute("""
                    SELECT COUNT(*) FROM paid_reservations WHERE round_id = ? AND kind = ?
                """, (round_id, kind)).fetchone()[0]
                if slot > _PER_ROUND[kind]:
                    raise BudgetExceeded("round_kind_limit")
                rounds, spent, models, _, _ = db.execute(_TOTALS).fetchone()
                if spent + cost_nusd > LIMIT_NUSD:
                    raise BudgetExceeded("money_limit")
                if kind == "model" and models >= MAX_MODEL_CALLS:
                    raise BudgetExceeded("model_limit")
                known = db.execute("SELECT 1 FROM paid_rounds WHERE id = ?", (round_id,)).fetchone()
                if known is None:
                    if rounds >= MAX_ROUNDS:
                        raise BudgetExceeded("round_limit")
                    db.execute("INSERT INTO paid_rounds (id) VALUES (?)", (round_id,))
                db.execute("""
                    INSERT INTO paid_reservations (round_id, kind, slot, cost_nusd) VALUES (?, ?, ?, ?)
                """, (round_id, kind, slot, cost_nusd))
        except sqlite3.Error as exc:
            raise BudgetExceeded("budget_unavailable") from exc

    def status(self):
        try:
            with closing(self._connect()) as db:
                rounds, spent, models, searches, fetches = db.execute(_TOTALS).fetchone()
        except sqlite3.Error as exc:
            raise BudgetExceeded("budget_unavailable") from exc
        return {"spent_nusd": spent, "remaining_nusd": LIMIT_NUSD - spent,
                "rounds": rounds, "model_calls": models, "search_calls": searches, "fetch_calls": fetches,
                "limit_nusd": LIMIT_NUSD, "max_rounds": MAX_ROUNDS, "max_model_calls": MAX_MODEL_CALLS}
