"""Learned habits + trust ladder (2026-10-09): the AI decides about NOW from what you usually do, asks before it
switches something off on you, and does it by itself only after you said yes often enough."""
import time
import unittest

from ai import habits, synth, trust
from ai import settings as S
from ai.features import load_slots, local_now
from ai.spec import ai_devices

from .helpers import config, house, memory_db
from .test_review_regressions import NOW, runtime


def flat(v):
    return {"days": 99, "weekday": [v] * 96, "weekend": [v] * 96}


class Ladder(unittest.TestCase):
    def test_three_yes_then_five_ok_then_down(self):
        st = {}
        for _ in range(2):
            trust.answered(st, "lamp", "off_still", True)
        self.assertEqual(trust.level(st, "lamp", "off_still"), S.TRUST_ASK)
        self.assertEqual(trust.answered(st, "lamp", "off_still", True), (S.TRUST_ASK, S.TRUST_NOTIFY))
        for _ in range(S.TRUST_SILENT_AFTER):
            trust.acted_ok(st, "lamp", "off_still")
        self.assertEqual(trust.level(st, "lamp", "off_still"), S.TRUST_SILENT)
        self.assertEqual(trust.undone(st, "lamp", "off_still"), (S.TRUST_SILENT, S.TRUST_NOTIFY))
        trust.answered(st, "lamp", "off_still", False)
        self.assertEqual(trust.level(st, "lamp", "off_still"), S.TRUST_ASK)
        self.assertEqual(trust.level(st, "other", "off_still"), S.TRUST_ASK)      # per device


class Habits(unittest.TestCase):
    def test_learned_from_the_routine(self):
        cfg, con, now = config(), memory_db(), int(time.time())
        synth.fill(con, cfg, days=42, end_ts=now, seed=3)
        h = habits.learn(load_slots(con, now - 42 * 86400, now), ai_devices(cfg), local_now(now))
        night = local_now(now).normalize() + __import__("pandas").Timedelta(hours=27)     # 03:00 tomorrow
        ts = night.tz_localize(S.TZ).timestamp()
        self.assertLess(habits.use_now(h, "bedroom_light", ts), S.HABIT_OFF_AT)    # asleep: light off
        self.assertGreater(habits.rest_now(h, "bedroom", ts), S.REST_AT)           # ... and lying still
        evening = ts - 6 * 3600                                                    # 21:00: TV in the living room
        self.assertGreater(habits.use_now(h, "living_light", evening), S.HABIT_ON_AT)
        self.assertLess(habits.rest_now(h, "living", evening), S.REST_AT)          # sitting is not sleeping

    def test_needs_a_week(self):
        self.assertIsNone(habits.use_now({"use": {"x": {"days": 3, "weekday": [1] * 96, "weekend": [1] * 96}}}, "x", NOW))


class Runtime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.con = memory_db()
        synth.fill(cls.con, cls.cfg, days=9, end_ts=NOW - 600, seed=7)

    def rt(self, use, rest=0.95):
        rt, sink, tmp = runtime(self.cfg, self.con, 0.5)
        self.addCleanup(tmp.cleanup)
        rt.habits = {"use": {d: flat(use) for d in rt.specs}, "rest": {"bedroom": flat(rest), "living": flat(rest)}}
        return rt, sink

    def test_entry_in_the_dark_follows_the_habit(self):
        rt, sink = self.rt(use=0.9)
        self.assertTrue(rt.handles_entry("bedroom_light"))
        rt.tick(house({}), NOW, dt=10)
        acts = rt.tick(house({"bedroom": 1}, lux=10), NOW + 10, dt=10)
        self.assertIn(("bedroom_light", 1), [(a["device"], a["v"]) for a in acts])
        rt2, sink2 = self.rt(use=0.05)                             # going to sleep: you usually keep it off
        rt2.tick(house({}), NOW, dt=10)
        acts = rt2.tick(house({"bedroom": 1}, lux=10), NOW + 10, dt=10)
        self.assertNotIn("bedroom_light", [a["device"] for a in acts])
        self.assertTrue(any("left off" in e["title"] for e in sink2.events))
        acts = rt2.tick(house({"bedroom": 1}, lux=10), NOW + 20, dt=10)   # one decision per entry
        self.assertEqual(sum("left off" in e["title"] for e in sink2.events), 1)

    def test_no_habits_means_the_rule_decides(self):
        rt, _ = self.rt(use=0.9)
        rt.habits = {"use": {}, "rest": {}}
        self.assertFalse(rt.handles_entry("bedroom_light"))

    def test_resting_with_the_light_on_asks_then_acts_then_undo(self):
        rt, sink = self.rt(use=0.05)
        on = {"bedroom_light": {"v": 1, "src": "rule", "watts": 1.2}}
        lying = lambda: house({"bedroom": 1}, pir={"bedroom": 0}, devices=on)
        t = NOW
        rt.tick(lying(), t, dt=60)
        for k in range(1, 4):                                      # 3 nights: it asks, you say yes
            t += 11 * 60
            rt.tick(lying(), t, dt=60)
            sid = next(s for s, sg in rt.suggestions.items() if sg.get("kind") == "off_still")
            rt.on_answer(sid, sink.suggestions[sid], True, now=t)
        self.assertEqual(trust.level(rt.state, "bedroom_light", "off_still"), S.TRUST_NOTIFY)
        t += 60
        acts = rt.tick(lying(), t, dt=60)                          # night 4: it switches it off itself
        self.assertIn(("bedroom_light", 0), [(a["device"], a["v"]) for a in acts])
        self.assertTrue(sink.alerts)                               # ... and tells you (level 1)
        rt.on_manual("bedroom_light", now=t + 60, v=1)             # you switch it back on: "that was wrong"
        self.assertEqual(trust.level(rt.state, "bedroom_light", "off_still"), S.TRUST_ASK)

    def test_sitting_still_is_not_resting(self):
        rt, sink = self.rt(use=0.05, rest=0.6)                     # e.g. TV: you sit still but this is not sleep
        on = {"living_light": {"v": 1, "src": "rule", "watts": 1.2}}
        for k in range(0, 30 * 60, 60):
            acts = rt.tick(house({"living": 1}, pir={"living": 0}, devices=on), NOW + k, dt=60)
            self.assertEqual(acts, [])
        self.assertFalse(sink.suggestions)


if __name__ == "__main__":
    unittest.main()
