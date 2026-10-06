"""Features (no leakage, AI mistakes, Away), anomaly detection, drift detection."""
import time
import unittest

import numpy as np
import pandas as pd

from ai import anomaly, drift, store, synth
from ai import settings as S
from ai.features import build_features, load_slots, usable
from ai.spec import ai_devices

from .helpers import config, memory_db

NOW = int(time.time()) // 900 * 900


class Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.con = memory_db()
        synth.fill(cls.con, cls.cfg, days=10, end_ts=NOW, seed=3)
        cls.slots = load_slots(cls.con, NOW - 11 * 86400, NOW)
        cls.spec = ai_devices(cls.cfg)["living_light"]

    def test_label_is_60_min_ahead(self):
        X, y = build_features(self.slots, self.spec)
        s = self.slots[self.spec.state]
        pd.testing.assert_series_equal(y, s.shift(-S.HORIZON), check_names=False)

    def test_no_future_leakage(self):
        """Changing the future must not change today's features."""
        X1, _ = build_features(self.slots, self.spec)
        cut = self.slots.index[-200]
        future = self.slots.copy()
        future.loc[future.index > cut] = 1 - future.loc[future.index > cut].fillna(0)
        X2, _ = build_features(future, self.spec)
        past = X1.index <= cut
        pd.testing.assert_frame_equal(X1[past].drop(columns=["occ_run"]), X2[past].drop(columns=["occ_run"]))

    def test_ai_mistakes_count_as_off(self):
        con = memory_db()
        store.write(con, [(NOW - 7200, "device/living_light", 1), (NOW - 3600, "ai_miss/living_light", 1),
                          (NOW - 1800, "ai_miss/living_light", 0), (NOW, "device/living_light", 1)])
        slots = load_slots(con, NOW - 7200, NOW)
        X, _ = build_features(slots, self.spec)
        miss = slots.index[(slots.index >= slots.index[-1] - pd.Timedelta(minutes=55)) &
                           (slots.index < slots.index[-1] - pd.Timedelta(minutes=30))]
        self.assertTrue((X.loc[miss, "now"] == 0).all())
        self.assertEqual(X["now"].iloc[0], 1)

    def test_away_slots_not_used(self):
        slots = self.slots.copy()
        slots["mode/away"] = 0.0
        slots.iloc[:50, slots.columns.get_loc("mode/away")] = 1.0
        self.assertEqual(int((~usable(slots)).sum()), 50)


class Anomaly(unittest.TestCase):
    def setUp(self):
        self.b = {"on": {"any": [1.7, 0.05, 500], "70": [1.7, 0.04, 300], "100": [2.4, 0.05, 200]},
                  "off": [0.0, 0.0, 500]}

    def test_classify(self):
        self.assertEqual(anomaly.classify(self.b, 1, 3.6, 70)[0], "high")
        self.assertIsNone(anomaly.classify(self.b, 1, 2.4, 100)[0])        # 2.4 W is normal at High
        self.assertEqual(anomaly.classify(self.b, 1, 0.05, 70)[0], "dead")
        self.assertEqual(anomaly.classify(self.b, 0, 0.9)[0], "standby")
        self.assertIsNone(anomaly.classify(self.b, 0, 0.0)[0])

    def test_persistence_and_repeat(self):
        d = anomaly.Detector({"fan": self.b})
        t = 1000.0
        self.assertIsNone(d.check(t, "fan", 1, 3.6, 70))
        self.assertIsNone(d.check(t + 60, "fan", 1, 3.6, 70))              # not 3 minutes yet
        a = d.check(t + 200, "fan", 1, 3.6, 70)
        self.assertEqual(a["kind"], "high")
        self.assertIsNone(d.check(t + 400, "fan", 1, 3.6, 70))             # no repeat within 6 h
        self.assertIsNone(d.check(t + 500, "fan", 1, 1.7, 70))             # back to normal resets
        title, line = anomaly.describe(a, "Bedroom fan", "Medium")
        self.assertIn("2.1x", title)

    def test_learn_baselines(self):
        con = memory_db()
        cfg = config()
        synth.fill(con, cfg, days=8, end_ts=NOW, seed=5)
        b = anomaly.learn_baselines(con, ["living_fan", "living_light"], NOW - 9 * 86400)
        self.assertIn("living_light", b)
        med = b["living_light"]["on"]["any"][0]
        self.assertAlmostEqual(med, 1.2, delta=0.1)


class Drift(unittest.TestCase):
    def test_detects_drop(self):
        idx = pd.date_range("2026-01-01", periods=14 * 96, freq="15min").values
        rng = np.random.default_rng(0)
        y = (rng.random(len(idx)) < 0.3).astype(int)
        good = y.copy()
        bad = y.copy()
        half = len(idx) // 2
        bad[half:] = 1 - bad[half:]                                       # last 7 days all wrong
        self.assertFalse(drift.detect(idx, y, good)["drift"])
        self.assertTrue(drift.detect(idx, y, bad)["drift"])


if __name__ == "__main__":
    unittest.main()
