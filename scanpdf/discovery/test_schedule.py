from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest import TestCase
from unittest.mock import Mock

from app import Bad, Library
from . import routes
from .schedule import CADENCES, FIRST_RETRY, TrackingSchedule
from .test_download import Handler

SUCCESS = {"items": [], "error": None}
FAILURE = {"items": [], "error": {"code": "network_error", "message": "offline"}}
PATH = "/api/download/subscription"


def epoch(timestamp):
    return datetime.fromisoformat(timestamp).timestamp()


class ScheduleTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.library = Library(Path(directory.name))
        self.now = 1700000000
        self.refresh = Mock(return_value=SUCCESS)
        self.schedule = self.open_schedule()

    def open_schedule(self):
        return TrackingSchedule(self.library, clock=lambda: self.now, refresh=self.refresh)

    def test_daily_weekly_and_restart(self):
        self.assertFalse(self.schedule.run_due())
        self.assertFalse(self.schedule.state()["enabled"])
        self.schedule.subscribe("cs.AI", "daily")
        self.assertTrue(self.schedule.run_due())
        self.refresh.assert_called_once_with("cs.AI", [], {}, consent=True)
        first = self.schedule.state()
        self.assertEqual(epoch(first["last_refresh"]), self.now)
        self.assertEqual(epoch(first["next_due"]), self.now + CADENCES["daily"])
        self.schedule = self.open_schedule()
        self.assertEqual(self.schedule.state(), first)
        self.assertEqual(self.schedule.subscribe("cs.AI", "daily"), first)
        self.now += CADENCES["daily"] - 1
        self.assertFalse(self.schedule.run_due())
        self.now += 1
        self.assertTrue(self.schedule.run_due())
        self.schedule.subscribe("cs.CL", "weekly")
        self.assertTrue(self.schedule.run_due())
        self.refresh.assert_called_with("cs.CL", [], {}, consent=True)
        self.assertEqual(epoch(self.schedule.state()["next_due"]), self.now + CADENCES["weekly"])

    def test_failure_backoff_persists_across_restart_and_resets_on_success(self):
        self.schedule.subscribe("cs.AI", "daily")
        self.refresh.side_effect = [FAILURE, FAILURE, SUCCESS]
        self.assertTrue(self.schedule.run_due())
        first = self.schedule.state()
        self.assertIsNone(first["last_refresh"])
        self.assertEqual(epoch(first["last_attempt"]), self.now)
        self.assertEqual(epoch(first["next_due"]), self.now + FIRST_RETRY)
        self.assertEqual(first["result"], FAILURE)
        self.schedule = self.open_schedule()
        self.assertFalse(self.schedule.run_due())
        self.now += FIRST_RETRY
        self.assertTrue(self.schedule.run_due())
        self.assertEqual(epoch(self.schedule.state()["next_due"]), self.now + 2 * FIRST_RETRY)
        self.now += 2 * FIRST_RETRY
        self.assertTrue(self.schedule.run_due())
        self.assertEqual(epoch(self.schedule.state()["next_due"]), self.now + CADENCES["daily"])

    def test_running_worker_waits_for_explicit_subscription_and_stop_persists(self):
        requested = Event()
        self.refresh.side_effect = lambda *args, **kwargs: (requested.set(), SUCCESS)[1]
        self.schedule.start()
        self.addCleanup(self.schedule.close)
        self.refresh.assert_not_called()
        self.schedule.subscribe("cs.AI", "weekly")
        self.assertTrue(requested.wait(2))
        stopped = self.schedule.stop()
        self.assertFalse(stopped["enabled"])
        self.assertIsNone(stopped["next_due"])
        self.now += 10 * CADENCES["weekly"]
        self.assertFalse(self.open_schedule().run_due())
        self.refresh.assert_called_once()

    def test_persistent_claim_prevents_replay_before_response(self):
        self.schedule.subscribe("cs.AI", "daily")
        entered, release = Event(), Event()

        def pending(*args, **kwargs):
            entered.set()
            release.wait(2)
            return SUCCESS

        self.refresh.side_effect = pending
        worker = Thread(target=self.schedule.run_due)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            restart = self.open_schedule()
            self.assertEqual(epoch(restart.state()["last_attempt"]), self.now)
            self.assertFalse(restart.run_due())
            self.refresh.assert_called_once()
        finally:
            release.set()
            worker.join()

    def test_only_explicit_valid_request_activates_subscription(self):
        handler = Handler(self.library)
        self.assertTrue(routes.handle(handler, False, "/download"))
        self.assertEqual(handler.response[0], 200)
        with self.assertRaises(Bad) as error:
            routes.handle(handler, False, PATH)
        self.assertEqual(error.exception.code, 503)
        handler.server.tracking_schedule = self.schedule
        request = {"action": "subscribe", "category": "cs.AI", "cadence": "weekly"}
        for additions, status in [({}, 403), ({"consent": 1}, 403),
                                  ({"consent": True, "category": "other"}, 400),
                                  ({"consent": True, "cadence": []}, 400),
                                  ({"consent": True, "text": "local"}, 400)]:
            with self.subTest(additions=additions), self.assertRaises(Bad) as error:
                handler.data = request | additions
                routes.handle(handler, True, PATH)
            self.assertEqual(error.exception.code, status)
        self.assertFalse(self.schedule.state()["enabled"])
        handler.data = request | {"consent": True}
        self.assertTrue(routes.handle(handler, True, PATH))
        self.assertTrue(handler.response[1]["enabled"])
        handler.data = {"action": "stop"}
        self.assertTrue(routes.handle(handler, True, PATH))
        self.assertFalse(handler.response[1]["enabled"])
        self.refresh.assert_not_called()
