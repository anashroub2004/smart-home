"""The night summer time ends (2026-10-24 in Asia/Hebron) the hour 01:00-02:00 happens twice. The CI ran at
01:14 local that night and three tests failed in synth.generate (pandas could not floor an ambiguous local
time). Generating history that ends inside the repeated hour must work, in both of its halves."""
import unittest

import pandas as pd

from ai import synth

from .helpers import config


class RepeatedHour(unittest.TestCase):
    def test_history_can_end_in_the_repeated_hour(self):
        cfg = config()
        first = int(pd.Timestamp("2026-10-23 22:10", tz="UTC").timestamp())    # 01:10 summer time (UTC+3)
        second = int(pd.Timestamp("2026-10-23 23:10", tz="UTC").timestamp())   # 01:10 winter time (UTC+2)
        for end_ts in (first, second):
            rows = synth.generate(cfg, days=2, end_ts=end_ts, seed=1)
            self.assertTrue(rows)
            self.assertLessEqual(max(r[0] for r in rows), end_ts)
            self.assertGreater(max(r[0] for r in rows), end_ts - 600)            # floored to 5 min, not an hour off


if __name__ == "__main__":
    unittest.main()
