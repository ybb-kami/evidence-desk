"""All feed/cadence tests inject fake time and transport; no real requests."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from subscriptions import GAP, SubscriptionError, Subscriptions, _pull


def setup(categories=None,interval=6):
    return {"enabled":True,"categories":categories or ["cs.AI"],"interval_hours":interval,"consent":True}


def row(ident):
    return {"id":ident,"title":"A fake feed entry "+ident,"published":"2025-07-01T00:00:00Z",
            "url":"https://untrusted.invalid/file.pdf","abstract":"not retained","feedback":"not forwarded"}


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/"setting.json"
        self.now=1_700_000_000
        self.calls=[]
        self.items=[row("2507.00001")]
        self.error=None
        self.manager=self.make()

    def feed(self,category):
        self.calls.append(category)
        return {"items":self.items,"error":self.error}

    def make(self):
        service=Subscriptions(self.path,fetch=self.feed,clock=lambda:self.now)
        self.addCleanup(service.close)
        return service

    def test_default_off_invalid_consent_and_whitelist(self):
        self.assertFalse(self.manager.status()["enabled"])
        self.assertEqual(self.manager.status()["source_mode"],"simulation")
        self.assertFalse(self.manager.tick())
        for bad,status in [
            ({**setup(),"consent":False},403),
            (setup(["https://untrusted.invalid"]),400),
            (setup(["cs.AI","cs.AI"]),400),
            (setup(interval=1),400),
            ({**setup(),"feedback":{"2507.00001":1}},400),
        ]:
            with self.assertRaises(SubscriptionError) as rejected:
                self.manager.configure(bad)
            self.assertEqual(rejected.exception.code,status)
        self.assertFalse(self.manager.tick())
        self.assertEqual(self.calls,[])

    def test_new_ids_only_deduplicated_across_categories_and_restart(self):
        m=self.manager
        m.configure(setup(["cs.AI","cs.CL"]))
        self.assertTrue(m.tick())
        self.assertFalse(m.tick())
        self.now+=GAP
        self.assertTrue(m.tick())
        self.assertEqual(self.calls,["cs.AI","cs.CL"])
        self.assertEqual(m.status()["inbox"],[])
        self.items=[row("2507.00002"),row("2507.00002"),row("2507.00001")]
        self.now+=6*3600
        self.assertTrue(m.tick())
        self.now+=GAP
        self.assertTrue(m.tick())
        self.assertEqual(len(m.status()["inbox"]),1)
        saved,=m.status()["inbox"]
        self.assertEqual(saved["url"],"https://arxiv.org/abs/2507.00002")
        self.assertNotIn("feedback",saved)
        m.mark_read()
        m.close()
        restarted=self.make()
        self.assertTrue(restarted.status()["enabled"])
        self.assertFalse(restarted.tick())
        self.now+=6*3600
        self.assertTrue(restarted.tick())
        self.now+=GAP
        self.assertTrue(restarted.tick())
        self.assertEqual(restarted.status()["inbox"],[])
        restarted.configure({"enabled":False})
        restarted.close()
        self.now+=6*3600
        self.assertFalse(self.make().tick())
        self.assertEqual(len(self.calls),6)

    def test_limited_feed_respects_global_backoff_without_new_alert(self):
        m=self.manager
        m.configure(setup(["cs.AI","cs.CL"]))
        self.error={"code":"rate_limited"}
        self.assertTrue(m.tick())
        self.now+=GAP
        self.assertFalse(m.tick())
        self.now+=3600
        self.error=None
        self.assertTrue(m.tick())
        self.assertEqual(m.status()["schedule"]["cs.AI"]["error"],"rate_limited")
        self.assertFalse(m.status()["schedule"]["cs.AI"]["baseline"])
        self.assertEqual(m.status()["inbox"],[])

    def test_stop_while_fetch_in_progress_discards_result(self):
        entered, release=threading.Event(),threading.Event()
        self.manager.fetch=lambda category: (entered.set(),release.wait(2),self.feed(category))[2]
        self.manager.start()
        self.assertFalse(entered.wait(.05))
        self.manager.configure(setup())
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.manager.status()["busy"])
        self.manager.configure({"enabled":False})
        release.set()
        self.manager.close()
        status=self.manager.status()
        self.assertFalse(status["enabled"]); self.assertEqual(status["inbox"],[])
        self.assertFalse(status["schedule"]["cs.AI"]["baseline"])

    def test_simulated_state_never_activates_real_transport(self):
        self.manager.configure(setup())
        with patch("subscriptions._pull", side_effect=AssertionError("egress")) as network:
            production=Subscriptions(self.path,clock=lambda:self.now)
            self.addCleanup(production.close)
            self.assertFalse(production.status()["enabled"])
            self.assertFalse(production.tick())
            network.assert_not_called()

    def test_live_adapter_passes_only_category_to_transport_no_libraries_or_votes(self):
        with patch("arxiv_tracking.refresh",return_value={"items":[],"error":None}) as mocked:
            _pull("cs.AI")
        mocked.assert_called_once_with("cs.AI",[],{},consent=True)


if __name__=="__main__":
    unittest.main()
