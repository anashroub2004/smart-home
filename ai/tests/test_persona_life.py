"""Routine file -> days -> a simulated year (2026-10-09): every day different, still learnable."""
import json
import unittest
from datetime import date, timedelta

from ai import lifesim, store
from ai.persona import OUT, Persona
from ai.spec import ai_devices
from ai import settings as S


def seed_config():
    return json.loads(S.CONFIG_PATH.read_text(encoding="utf-8"))["config"]


class PersonaDays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Persona.load()

    def test_days_cover_24h_without_gaps_and_repeat_exactly(self):
        for k in range(120):
            d = date(2025, 10, 1) + timedelta(days=k)
            s = self.p.day(d).segments
            self.assertAlmostEqual(s[0][0], 0.0)
            self.assertAlmostEqual(s[-1][1], 24.0)
            for a, b in zip(s, s[1:]):
                self.assertAlmostEqual(a[1], b[0], places=6)
        d = date(2026, 3, 3)
        self.assertEqual(Persona.load().day(d).segments, self.p.day(d).segments)   # same seed + date = same day

    def test_calendar_shapes_the_days(self):
        exams = [self.p.day(date(2025, 12, 20) + timedelta(days=k)) for k in range(14)]
        semester = [self.p.day(date(2025, 10, 5) + timedelta(days=k)) for k in range(14)]
        study = lambda days: sum(e - a for dp in days for a, e, r, act, _ in dp.segments if act == "Studying") / len(days)
        self.assertGreater(study(exams), study(semester))                     # exams: more studying at home
        ram = self.p.day(date(2026, 3, 1))
        self.assertIn("ramadan", ram.tags)
        self.assertTrue(any(act == "Suhoor" for *_, act, _ in ram.segments))
        away = [self.p.day(date(2026, 8, 6) + timedelta(days=k)) for k in range(20)]
        self.assertTrue(any(dp.away for dp in away))                          # the summer trip
        self.assertTrue(all(s[2] == OUT for dp in away if dp.away for s in dp.segments))

    def test_sleeps_at_night(self):
        asleep = sum(self.p.at(self.p.day(date(2025, 11, 1) + timedelta(days=k)), 4.0)[3] in ("Sleeping", "Away")
                     for k in range(60))
        self.assertGreaterEqual(asleep, 52)                                    # insomnia / all-nighters now and then


class Life(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = seed_config()
        cls.con = store.connect(":memory:")
        cls.res = lifesim.simulate(cls.cfg, Persona.load(), days=28, end_date=date(2026, 7, 20), con=cls.con)

    def test_every_day_different_but_with_a_shape(self):
        v = lifesim.variety(self.res["diary"])
        self.assertGreaterEqual(v["distinct_day_types"], 18)
        self.assertGreater(v["wake_sd_min"], 30)
        self.assertGreater(v["manual_per_day"], 5)

    def test_hub_keys_and_manual_commands(self):
        keys = {k for (k,) in self.con.execute("SELECT DISTINCT key FROM readings")}
        for k in ("bathroom/hum", "kitchen/occ", "device/kitchen_hood", "device/bathroom_vent", "power/kettle",
                  "override/bathroom_light", "door/entry", "living/mmwave", "living/pir"):
            self.assertIn(k, keys)
        hum = [v for (v,) in self.con.execute("SELECT value FROM readings WHERE key='bathroom/hum'")]
        self.assertGreater(max(hum), 80)                                      # showers

    def test_new_devices_reach_the_ai(self):
        specs = ai_devices(self.cfg)
        self.assertEqual(specs["kitchen_hood"].kind, "generic")
        self.assertEqual(specs["bathroom_vent"].kind, "generic")
        self.assertIsNone(specs["bathroom_light"].lux)                        # windowless: always dark
        self.assertEqual(specs["kitchen_light"].lux, "living/lux")            # open kitchen borrows the living room
        self.assertEqual(specs["bathroom_vent"].hum, "bathroom/hum")
        self.assertNotIn("kettle", specs)                                     # monitor only


if __name__ == "__main__":
    unittest.main()
