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

    def test_entry_in_the_dark_switches_the_light_on(self):
        rt, sink = self.rt(use=0.9)
        self.assertTrue(rt.handles_entry("bedroom_light"))
        rt.tick(house({}), NOW, dt=10)
        acts = rt.tick(house({"bedroom": 1}, lux=10), NOW + 10, dt=10)
        self.assertIn(("bedroom_light", 1, "AI · habit 90%"), [(a["device"], a["v"], a["src_label"]) for a in acts])
        # going to bed, the light is usually off by now: it still switches it on (Iyad's decision 2026-10-19);
        # the learned "getting ready" time switches it off once you lie still
        rt2, sink2 = self.rt(use=0.05)
        rt2.tick(house({}), NOW, dt=10)
        acts = rt2.tick(house({"bedroom": 1}, lux=10), NOW + 10, dt=10)
        self.assertIn(("bedroom_light", 1, "AI · dark room"), [(a["device"], a["v"], a["src_label"]) for a in acts])
        acts = rt2.tick(house({"bedroom": 1}, lux=10), NOW + 20, dt=10)   # one decision per entry
        self.assertNotIn("bedroom_light", [a["device"] for a in acts])

    def test_back_to_bed_at_night_leaves_the_light_off(self):
        rt, sink = self.rt(use=0.05)                               # you usually sleep in the bedroom now
        rt.tick(house({"bedroom": 1}, lux=1, devices={"bedroom_light": {"v": 1}}), NOW, dt=60)
        rt.tick(house({"bathroom": 1}, lux=1), NOW + 60, dt=60)                 # 3 am: to the bathroom
        acts = rt.tick(house({"bedroom": 1}, lux=1), NOW + 6 * 60, dt=60)       # back after 5 min
        self.assertNotIn("bedroom_light", [a["device"] for a in acts])
        self.assertTrue(any("left off" in e["title"] for e in sink.events))
        rt.tick(house({"bathroom": 1}, lux=1), NOW + 7 * 60, dt=60)
        acts = rt.tick(house({"bedroom": 1}, lux=1), NOW + 30 * 60, dt=60)      # away 23 min: not a short trip
        self.assertIn(("bedroom_light", 1), [(a["device"], a["v"]) for a in acts])

    def test_entry_in_a_windowless_room_decides(self):
        # the bathroom has no window and no BH1750: it is dark, not "waiting for a reading"
        rt, _ = self.rt(use=0.9)
        self.assertTrue(rt.handles_entry("bathroom_light"))
        rt.tick(house({}), NOW, dt=10)
        acts = rt.tick(house({"bathroom": 1}), NOW + 10, dt=10)
        self.assertIn(("bathroom_light", 1), [(a["device"], a["v"]) for a in acts])
        rt2, sink2 = self.rt(use=0.05)                             # even when you usually keep it off now
        rt2.tick(house({}), NOW, dt=10)
        acts = rt2.tick(house({"bathroom": 1}), NOW + 10, dt=10)
        self.assertIn(("bathroom_light", 1), [(a["device"], a["v"]) for a in acts])

    def test_going_out_ends_the_door_entry_window(self):
        rt, _ = self.rt(use=0.9)
        came = house({}, door_entry_at=NOW - 300)
        self.assertTrue(rt.someone_home(came, NOW))                        # just came in, no sensor sees you yet
        left = {**came, "door_exit_at": NOW - 120}
        self.assertFalse(rt.someone_home(left, NOW))                       # then went out again
        back = {**house({}, door_entry_at=NOW - 60), "door_exit_at": NOW - 120}
        self.assertTrue(rt.someone_home(back, NOW))                        # out, then back in
        seen = {**house({"living": 1}, door_entry_at=NOW - 300), "door_exit_at": NOW - 120}
        self.assertTrue(rt.someone_home(seen, NOW))                        # a sensor always wins

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

    def test_learned_prep_time_replaces_the_fixed_ten_minutes(self):
        on = {"bedroom_light": {"v": 1, "src": "ai", "watts": 1.2}}
        lying = lambda: house({"bedroom": 1}, pir={"bedroom": 0}, devices=on)
        asked = lambda rt: any(sg.get("kind") == "off_still" for sg in rt.suggestions.values())
        rt, _ = self.rt(use=0.05)
        rt.habits["self_off"] = {"bedroom_light": {"nights": 6, "prep_min": 4.0, "prep_nights": 6}}
        rt.tick(lying(), NOW, dt=60)
        rt.tick(lying(), NOW + 3 * 60, dt=60)
        self.assertFalse(asked(rt))
        rt.tick(lying(), NOW + 5 * 60, dt=60)                      # 5 min still > your 4 min
        self.assertTrue(asked(rt))
        self.assertIn("about 4 min after lying down (6 nights)",
                      next(iter(rt.suggestions.values()))["title"] + rt.sink.suggestions[next(iter(rt.suggestions))]["why"])
        rt2, _ = self.rt(use=0.05)                                 # not learned yet: the fixed 10 min
        rt2.tick(lying(), NOW, dt=60)
        rt2.tick(lying(), NOW + 5 * 60, dt=60)
        self.assertFalse(asked(rt2))
        rt2.tick(lying(), NOW + 11 * 60, dt=60)
        self.assertTrue(asked(rt2))

    def test_undo_tonight_one_retry_an_hour_later_once_quiet(self):
        rt, sink = self.rt(use=0.05)
        trust.from_history(rt.state, "bedroom_light", "off_still", 9)          # it acts and tells you
        on = {"bedroom_light": {"v": 1, "src": "rule", "watts": 1.2}}
        lying = lambda pir=0: house({"bedroom": 1}, pir={"bedroom": pir}, devices=on)
        off = lambda acts: ("bedroom_light", 0) in [(a["device"], a["v"]) for a in acts]
        rt.tick(lying(), NOW, dt=60)
        self.assertTrue(off(rt.tick(lying(), NOW + 11 * 60, dt=60)))
        undo = NOW + 12 * 60
        rt.on_manual("bedroom_light", now=undo, v=1)                           # reading on the phone: undo
        rt.override_at.pop("bedroom_light")                                   # (past the 2 h hands-off)
        rt.quiet_since.clear()
        # he reads, then falls asleep and turns over now and then (never 60 min without any movement)
        turns = {8, 19, 27, 41, 52, 63, 70}
        for m in range(1, 80):                                                 # last turn at 70: quiet 10 min at 80
            acts = rt.tick(lying(1 if m in turns else 0), undo + m * 60, dt=60)
            self.assertFalse(off(acts), f"minute {m}")
            self.assertFalse(rt.suggestions)
        n_alerts = len(sink.alerts)
        acts = rt.tick(lying(), undo + 81 * 60, dt=60)                         # > 1 h since the undo + quiet 10 min
        self.assertTrue(off(acts))                                             # one more try, no question
        self.assertFalse(rt.suggestions)
        self.assertGreater(len(sink.alerts), n_alerts)                         # ... and it tells you
        self.assertIn("seem asleep", next(a for a in acts if a["device"] == "bedroom_light")["why"])
        t2 = undo + 82 * 60
        rt.on_manual("bedroom_light", now=t2, v=1)                             # undone again: done for tonight
        rt.override_at.pop("bedroom_light")
        rt.quiet_since.clear()
        for k in range(60, 3 * 3600, 300):
            self.assertFalse(off(rt.tick(lying(), t2 + k, dt=60)))
        t = NOW + 86400                                                        # the next night
        rt.quiet_since.clear()
        rt.tick(lying(), t, dt=60)
        rt.tick(lying(), t + 11 * 60, dt=60)
        self.assertTrue(rt.suggestions)                                        # level went down: it asks again

    def test_sitting_still_is_not_resting(self):
        rt, sink = self.rt(use=0.05, rest=0.6)                     # e.g. TV: you sit still but this is not sleep
        on = {"living_light": {"v": 1, "src": "rule", "watts": 1.2}}
        for k in range(0, 30 * 60, 60):
            acts = rt.tick(house({"living": 1}, pir={"living": 0}, devices=on), NOW + k, dt=60)
            self.assertEqual(acts, [])
        self.assertFalse(sink.suggestions)


class PrepTime(unittest.TestCase):
    def test_median_plus_margin_within_limits(self):
        self.assertIsNone(habits.prep_minutes([10] * 4))                       # fewer than 5 nights
        self.assertEqual(habits.prep_minutes([8, 10, 12, 40, 9]), 12.0)        # median 10 + 2, one odd night ignored
        self.assertEqual(habits.prep_minutes([0.5] * 5), 2.5)
        self.assertEqual(habits.prep_minutes([1] * 5), 3.0)
        self.assertEqual(habits.prep_minutes([30] * 5), 20.0)                  # upper limit
        self.assertEqual(habits.prep_minutes([0] * 5), 2.0)                    # lower limit

    def test_still_before_the_press(self):
        from ai import store
        con = memory_db()
        t = 1_800_000_000
        store.write(con, [(t - 900, "bedroom/pir", 1), (t - 840, "bedroom/pir", 0),   # last movement 15..14 min ago
                          (t - 10, "bedroom/pir", 1)])                                  # the press itself
        self.assertAlmostEqual(habits.still_before(con, "bedroom/pir", t), 14.0)
        self.assertEqual(habits.still_before(con, "living/pir", t), float(S.PREP_LOOK_MIN))


if __name__ == "__main__":
    unittest.main()
