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


def full_config():
    """seed 2: the full studio (hood, vent, kettle, fridge, SHT31, PIR) — the AI still handles all of them."""
    from pathlib import Path
    return json.loads((Path(__file__).parent / "seed_full_v2.json").read_text(encoding="utf-8"))["config"]


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
        cls.cfg = full_config()
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


class MockupLife(unittest.TestCase):
    """docs/seed.json (seed 3): the mockup's parts — LD2410C radars, FC-51 at the kitchen / bathroom doors, DHT11."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = seed_config()
        cls.con = store.connect(":memory:")
        cls.res = lifesim.simulate(cls.cfg, Persona.load(), days=7, end_date=date(2026, 7, 20), con=cls.con)

    def keys(self):
        return {k for (k,) in self.con.execute("SELECT DISTINCT key FROM readings")}

    def test_the_mockups_signals(self):
        k = self.keys()
        for key in ("living/mmwave", "bedroom/mmwave", "kitchen/pir", "bathroom/pir", "living/temp", "power/washer"):
            self.assertIn(key, k)
        self.assertIn("living/pir", k)                                       # the radar's "moving target"
        for key in ("bathroom/hum", "power/fridge", "device/kitchen_hood"):
            self.assertNotIn(key, k)

    def test_dht11_whole_degrees(self):
        temps = [v for (v,) in self.con.execute("SELECT value FROM readings WHERE key='living/temp'")]
        self.assertTrue(temps and all(t == int(t) for t in temps))

    def test_doorway_sensor_is_on_while_inside(self):
        rows = self.con.execute("SELECT ts, key, value FROM readings WHERE key IN ('bathroom/pir', 'bathroom/occ') "
                                "ORDER BY ts").fetchall()
        last, at = {}, {}
        for ts, key, v in rows:
            last[key] = v
            at[ts] = dict(last)                                             # the state once each minute is written
        states = [s for s in at.values() if len(s) == 2]
        self.assertGreater(sum(s["bathroom/occ"] for s in states), 10)      # he does go to the bathroom
        self.assertTrue(all(s["bathroom/pir"] == s["bathroom/occ"] for s in states))

    def test_ai_devices_of_the_mockup(self):
        specs = ai_devices(self.cfg)
        self.assertEqual(sorted(specs), ["bathroom_light", "bedroom_fan", "bedroom_light", "kitchen_light",
                                         "living_fan", "living_light"])
        self.assertEqual(specs["bathroom_light"].presence_keys, ["bathroom/pir"])
        self.assertEqual(sorted(specs["living_light"].presence_keys), ["living/mmwave", "living/pir"])


if __name__ == "__main__":
    unittest.main()
