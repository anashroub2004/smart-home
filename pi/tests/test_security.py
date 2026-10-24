"""pi/security.py: the door's messages -> /access_log, History, alerts, /home_state/door, with an outbox
in SQLite while the internet is down. Run: python -m unittest discover -s pi/tests"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from firebase_writer import Writer  # noqa: E402
from memory_db import MemoryDB  # noqa: E402
import security  # noqa: E402

CONFIG = json.loads((HERE.parent / "docs" / "seed.json").read_text(encoding="utf-8"))["config"]


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class FlakyDB(MemoryDB):
    """MemoryDB whose "internet" can be cut."""
    up = True

    def _check(self):
        if not self.up:
            raise OSError("no internet")

    def get(self, path, **q):
        self._check()
        return super().get(path, **q)

    def put(self, path, value):
        self._check()
        return super().put(path, value)

    def patch(self, path, value):
        self._check()
        return super().patch(path, value)

    def post(self, path, value):
        self._check()
        return super().post(path, value)


def con():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    return c


class SecurityTest(unittest.TestCase):
    def setUp(self):
        security.say = lambda *a: None
        self.clock = Clock()
        self.db = FlakyDB()
        args = type("A", (), {"host": "x", "port": 0})()
        self.svc = security.Service(Writer(self.db), con(), CONFIG, args)
        self.svc.clock = self.svc.out.clock = self.svc.sec.clock = self.clock

    def send(self, *events):
        for ev in events:
            self.svc.sec.on_door_event(ev)
            self.clock.t += 2

    def items(self, path):
        return list((self.db.get(path) or {}).values())

    def five_wrong_and_lockout(self):
        self.send(*[{"type": "keypad", "ok": False, "n": n} for n in range(1, 6)],
                  {"type": "lockout", "secs": 60, "round": 1})

    def test_lockout_logs_alerts_and_home_state(self):
        self.five_wrong_and_lockout()
        self.assertTrue(self.svc.flush())
        access = self.items("access_log")
        self.assertEqual(len(access), 5)
        self.assertFalse(any(a["ok"] for a in access))
        levels = sorted(a["level"] for a in self.items("alerts"))
        self.assertEqual(levels, ["critical", "warning"])
        lockout_at = (self.clock.t - 2) * 1000
        self.assertEqual(self.db.get("home_state/door/lockout_until"), lockout_at + 60_000)
        self.assertIn("Keypad locked", [e["title"] for e in self.items("events")])

    def test_internet_down_keeps_everything_and_sends_it_later_with_the_original_time(self):
        self.db.up = False
        t0 = self.clock.t * 1000
        self.five_wrong_and_lockout()
        self.assertFalse(self.svc.flush())
        self.assertEqual(self.svc.out.pending(), 13)           # 5 access + 6 events + 2 alerts
        self.clock.t += 1200                                   # 20 min later the internet is back
        self.db.up = True
        self.assertTrue(self.svc.flush())
        self.assertEqual(self.svc.out.pending(), 0)
        access = sorted(a["at"] for a in self.items("access_log"))
        self.assertEqual(access[0], t0)                        # the time it happened, not the time it was sent
        self.assertTrue(self.db.get("home_state/door/lockout_until"))
        self.assertFalse(self.svc.flush() and self.svc.out.pending())   # nothing sent twice
        self.assertEqual(len(self.items("access_log")), 5)

    def test_known_finger_opens_and_ends_the_lockout(self):
        self.five_wrong_and_lockout()
        self.send({"type": "keypad", "ok": False, "locked": True},
                  {"type": "fingerprint", "ok": True, "id": 1})
        self.svc.flush()
        door = self.db.get("home_state/door")
        self.assertEqual(door["lockout_until"], 0)
        self.assertEqual(door["last_open"]["method"], "fingerprint")
        titles = [e["title"] for e in self.items("events")]
        self.assertIn("Keypad attempt during the lockout", titles)
        self.assertEqual(len(self.items("access_log")), 7)

    def test_exit_button_does_not_end_the_lockout(self):
        self.five_wrong_and_lockout()
        self.send({"type": "exit_button", "ok": True})
        self.svc.flush()
        self.assertGreater(self.db.get("home_state/door/lockout_until"), 0)

    def test_app_unlock_sets_last_open_without_logging_twice(self):
        self.svc.sec.on_lock_state({"v": 1, "src": "web"})
        self.svc.sec.on_lock_state({"v": 1, "src": "fingerprint"})   # logged from the door event instead
        self.svc.flush()
        self.assertEqual(self.db.get("home_state/door/last_open/method"), "web")
        self.assertEqual(self.items("access_log"), [])
        self.assertEqual(self.items("events"), [])

    def test_start_without_internet_uses_the_kept_config(self):
        c = con()
        w = Writer(self.db)
        self.db.put("config", {**CONFIG, "version": 7})
        self.assertEqual(security.read_config(w, c)["version"], 7)      # kept in SQLite
        self.db.up = False
        self.assertEqual(security.read_config(w, c)["version"], 7)
        self.assertEqual(security.read_config(w, con())["home_name"], CONFIG["home_name"])   # first start: seed


if __name__ == "__main__":
    unittest.main()
