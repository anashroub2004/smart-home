"""A hub whose Pi services already record real readings (stage 1, 2026-10-23): the simulated weeks go BEFORE the
first real reading, the real readings are never touched, and it happens once."""
import tempfile
import time
import unittest
from pathlib import Path

from ai import settings as S
from ai import store
from ai.runtime import AIRuntime

from .helpers import RecSink, config, memory_db

NOW = int(time.time()) // 900 * 900


class Bootstrap(unittest.TestCase):
    def rt(self, con):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        rt = AIRuntime(config(), con, RecSink(), model_dir=Path(tmp.name), clock=lambda: NOW)
        rt.retrain = lambda now=None, quiet=False: None          # the filling is what we test, not the training
        return rt

    def test_simulated_weeks_go_before_the_first_real_reading(self):
        con = memory_db()
        real = [(NOW - 2 * 86400, "living/temp", 27.0), (NOW - 600, "living/temp", 28.0), (NOW - 600, "device/x", 1)]
        store.write(con, real)
        self.rt(con).bootstrap_if_needed(NOW, quiet=True)
        first_real = NOW - 2 * 86400
        before = con.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM readings WHERE ts < ?", (first_real,)).fetchone()
        self.assertGreater(before[2], 1000)                                         # weeks of simulated life
        self.assertGreater(first_real - before[0], (S.WINDOW_DAYS - 7) * 86400)
        self.assertLessEqual(float(store.get_meta(con, "synth_until")), first_real)  # no overlap with real readings
        kept = con.execute("SELECT ts, key, value FROM readings WHERE ts >= ? ORDER BY ts, key",
                           (first_real,)).fetchall()
        self.assertEqual(sorted(kept), sorted(real))                                 # real readings untouched
        n = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        self.rt(con).bootstrap_if_needed(NOW, quiet=True)                           # restart: not again
        self.assertEqual(con.execute("SELECT COUNT(*) FROM readings").fetchone()[0], n)

    def test_enough_real_history_gets_nothing(self):
        con = memory_db()
        store.write(con, [(NOW - (S.MIN_DAYS + 1) * 86400, "living/temp", 27.0), (NOW - 600, "living/temp", 28.0)])
        self.rt(con).bootstrap_if_needed(NOW, quiet=True)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 2)
        self.assertIsNone(store.get_meta(con, "synth_until"))


if __name__ == "__main__":
    unittest.main()
