"""pi/outbox.py LocalFirstWriter (automation.py, commands.py): their own work never waits for the internet.
Run: python -m unittest discover -s pi/tests"""
import sqlite3
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import outbox  # noqa: E402
from firebase_writer import Writer  # noqa: E402
from outbox import LocalFirstWriter, Offline, Outbox  # noqa: E402
from test_security import FlakyDB  # noqa: E402


class SlowDB(FlakyDB):
    """A real outage: every call hangs before it fails (here 0.3 s instead of 10 s)."""
    calls = 0

    def _check(self):
        if not self.up:
            SlowDB.calls += 1
            time.sleep(0.3)
        super()._check()


class LocalFirstTest(unittest.TestCase):
    def setUp(self):
        outbox.say = lambda *a: None
        self.db = SlowDB()
        SlowDB.calls = 0
        w = Writer(self.db)
        self.lf = LocalFirstWriter(w, Outbox(sqlite3.connect(":memory:", check_same_thread=False), w),
                                   fallback=("ack_command",))

    def test_history_waits_and_keeps_its_time(self):
        self.db.up = False
        t0 = int(time.time() * 1000)
        self.lf.log_event("rule", "rule", "Living room fan turned on", "rule", "Rule")
        self.assertEqual(self.lf.out.pending(), 1)
        self.db.up = True
        self.assertTrue(self.lf.out.flush())
        ev = list(self.db.get("events").values())[0]
        self.assertEqual(ev["title"], "Living room fan turned on")
        self.assertLess(abs(ev["at"] - t0), 1000)

    def test_after_one_failure_calls_fail_at_once(self):
        self.db.up = False
        with self.assertRaises(Exception):
            self.lf.get_ai_pause()                          # the first one finds out (it hangs once)
        t = time.time()
        for _ in range(20):
            with self.assertRaises(Offline):
                self.lf.get_ai_pause()
            with self.assertRaises(Offline):
                self.lf.db.get("home_state/scene")
        self.assertLess(time.time() - t, 0.1)               # 40 calls, none waited for the network
        self.assertEqual(SlowDB.calls, 1)

    def test_done_goes_straight_when_online_and_waits_when_not(self):
        self.lf.ack_command("living_fan", True)
        self.assertEqual(self.db.get("commands/living_fan/status"), "done")
        self.assertEqual(self.lf.out.pending(), 0)
        self.db.up = False
        self.lf.ack_command("bedroom_fan", False, "No response")     # no exception for the caller
        self.assertEqual(self.lf.out.pending(), 1)
        self.db.up = True
        self.lf.out.flush()
        self.assertEqual(self.db.get("commands/bedroom_fan/status"), "failed")

    def test_sender_notices_firebase_is_back(self):
        self.db.up = False
        with self.assertRaises(Exception):
            self.lf.get_ai_pause()
        self.assertTrue(self.lf.down)
        self.lf.retry_s = 0.05
        stop, t = self.lf.start_sender()
        self.db.up = True
        deadline = time.time() + 3
        while self.lf.down and time.time() < deadline:
            time.sleep(0.05)
        stop.set()
        t.join(2)
        self.assertFalse(self.lf.down)
        self.assertIsNone(self.lf.get_ai_pause() or None)  # works again


if __name__ == "__main__":
    unittest.main()
