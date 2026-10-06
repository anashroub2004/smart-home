"""Training on simulated history: models, report, honesty label, presence, baselines."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from ai import store, synth, train

from .helpers import config, memory_db

NOW = int(time.time()) // 900 * 900


class Training(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.con = memory_db()
        synth.fill(cls.con, cls.cfg, days=45, end_ts=NOW - 600, seed=11)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.report = train.train_all(cls.cfg, cls.con, cls.dir, now_ts=NOW, quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_models_and_report(self):
        for dev in ("living_fan", "living_light", "bedroom_fan", "bedroom_light"):
            self.assertTrue((self.dir / f"{dev}.joblib").exists(), dev)
            m = self.report["devices"][dev]
            self.assertIn(m["status"], ("ready", "relearning"))
            for k in ("f1", "baseline_f1", "within_15", "brier", "calibration", "importance"):
                self.assertIn(k, m)
        self.assertNotIn("washer", self.report["devices"])            # control.ai = false
        self.assertEqual(self.report["data_source"], "simulated")    # honest label
        saved = json.loads((self.dir / "report.json").read_text())
        self.assertEqual(saved["status"], "ready")

    def test_beats_yesterday_baseline_on_average(self):
        s = self.report["summary"]
        self.assertGreater(s["f1"], s["baseline_f1"])

    def test_presence_and_baselines(self):
        self.assertTrue((self.dir / "presence.joblib").exists())
        self.assertEqual(self.report["presence"]["status"], "ready")
        b = json.loads((self.dir / "baselines.json").read_text())
        self.assertIn("living_fan", b)
        self.assertIn("70", b["living_fan"]["on"])                   # per level

    def test_learning_before_21_days(self):
        con = memory_db()
        synth.fill(con, self.cfg, days=12, end_ts=NOW - 600, seed=2)
        with tempfile.TemporaryDirectory() as d:
            r = train.train_all(self.cfg, con, Path(d), now_ts=NOW, quiet=True)
        self.assertEqual(r["status"], "learning")
        self.assertTrue(all(m["status"] == "learning" for m in r["devices"].values()))

    def test_real_data_label(self):
        con = memory_db()
        synth.fill(con, self.cfg, days=30, end_ts=NOW - 600, seed=2)
        store.set_meta(con, "synth_until", 0)                         # pretend it was all measured
        self.assertEqual(train.data_source(con, NOW - 56 * 86400, NOW), "real")


if __name__ == "__main__":
    unittest.main()
