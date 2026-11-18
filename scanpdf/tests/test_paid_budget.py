from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from paid_budget import Budget, BudgetExceeded, LIMIT_NUSD, MAX_MODEL_CALLS, MAX_ROUNDS


def _attempt_batch(path, attempts, cost):
    budget = Budget(path)
    accepted = 0
    for _ in range(attempts):
        try:
            budget.start_round().reserve("model", cost)
        except BudgetExceeded as denied:
            if denied.reason != "money_limit":
                raise
        else:
            accepted += 1
    return accepted


def _crash(path, committed):
    if committed:
        Budget(path).start_round().reserve("model", 51)
    else:
        db = sqlite3.connect(path, isolation_level=None)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO paid_rounds VALUES ('uncommitted')")
        db.execute("INSERT INTO paid_reservations VALUES ('uncommitted', 'search', 1, 33)")
    os._exit(23)


class PaidBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "spend #1.sqlite"
        self.budget = Budget(self.path)

    def assert_denied(self, reason, ticket, kind, cost):
        with self.assertRaises(BudgetExceeded) as denied:
            ticket.reserve(kind, cost)
        self.assertEqual(denied.exception.reason, reason)

    def test_only_successful_reservation_spends_money_and_a_round(self):
        ticket = self.budget.start_round()
        self.assertEqual(self.budget.status()["rounds"], 0)
        for cost in (False, 0, -1, 1.5, "200"):
            with self.subTest(cost=cost):
                self.assert_denied("invalid_cost", ticket, "model", cost)
        self.assert_denied("unknown_price", ticket, "model", None)
        self.assert_denied("money_limit", ticket, "search", LIMIT_NUSD + 1)
        self.assertEqual(self.budget.status()["rounds"], 0)
        ticket.reserve("search", LIMIT_NUSD - 1)
        ticket.reserve("model", 1)
        self.assert_denied("money_limit", self.budget.start_round(), "fetch", 1)
        self.assertEqual(Budget(self.path).status(), {
            "spent_nusd": LIMIT_NUSD, "remaining_nusd": 0, "rounds": 1,
            "model_calls": 1, "search_calls": 1, "fetch_calls": 0,
            "limit_nusd": LIMIT_NUSD, "max_rounds": MAX_ROUNDS, "max_model_calls": MAX_MODEL_CALLS,
        })

    def test_round_and_model_caps_are_independent(self):
        for _ in range(MAX_MODEL_CALLS // 2):
            ticket = self.budget.start_round()
            ticket.reserve("model", 1)
            ticket.reserve("model", 1)
        self.assert_denied("model_limit", self.budget.start_round(), "model", 1)
        for _ in range(MAX_ROUNDS - MAX_MODEL_CALLS // 2):
            ticket = self.budget.start_round()
            ticket.reserve("search", 1)
        self.assert_denied("round_limit", self.budget.start_round(), "fetch", 1)
        # An already counted round may still spend within its own limits.
        ticket.reserve("fetch", 1)
        self.assertEqual(self.budget.status()["model_calls"], MAX_MODEL_CALLS)
        self.assertEqual(self.budget.status()["rounds"], MAX_ROUNDS)

    def test_parallel_actions_share_the_same_per_round_slots(self):
        ticket = self.budget.start_round()

        def attempt(kind):
            try:
                ticket.reserve(kind, 19)
            except BudgetExceeded as denied:
                self.assertEqual(denied.reason, "round_kind_limit")
                return False
            return True

        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(attempt, ["model", "search", "fetch"] * 10)), 4)
        status = self.budget.status()
        self.assertEqual(status["rounds"], 1)
        self.assertEqual(status["spent_nusd"], 4 * 19)

    def test_parallel_processes_cannot_overspend(self):
        cost = LIMIT_NUSD // 24 + 1
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
            attempts = [pool.submit(_attempt_batch, self.path, 12, cost) for _ in range(4)]
            self.assertEqual(sum(attempt.result(timeout=20) for attempt in attempts), 23)
        status = self.budget.status()
        self.assertEqual(status["spent_nusd"], 23 * cost)
        self.assertEqual(status["rounds"], 23)

    def test_process_crash_preserves_commits_and_rolls_back_partial_rounds(self):
        context = multiprocessing.get_context("spawn")
        for committed in (True, False):
            process = context.Process(target=_crash, args=(self.path, committed))
            process.start()
            process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join()
            self.assertEqual(process.exitcode, 23)
        status = Budget(self.path).status()
        self.assertEqual((status["rounds"], status["spent_nusd"], status["fetch_calls"]), (1, 51, 0))

    def test_storage_error_rolls_back_and_fails_closed(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON paid_reservations
                          BEGIN SELECT RAISE(ABORT, 'injected error'); END""")
        ticket = self.budget.start_round()
        self.assert_denied("budget_unavailable", ticket, "search", 18)
        self.assertEqual(self.budget.status()["rounds"], 0)
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TRIGGER fail_insert")
        ticket.reserve("search", 18)
        self.assertEqual(self.budget.status()["spent_nusd"], 18)
        self.path.rename(self.path.with_suffix(".moved"))
        self.assert_denied("budget_unavailable", ticket, "fetch", 1)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
