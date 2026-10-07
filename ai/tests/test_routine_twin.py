"""The shared routine, the virtual clock and the simulated person (digital twin)."""
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from ai import routine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pi"))
from person import OUT, Person  # noqa: E402
from sim_clock import VirtualClock  # noqa: E402

from .helpers import config  # noqa: E402


class Routine(unittest.TestCase):
    def test_days_cover_24_hours_without_gaps(self):
        for i in range(30):
            segs = routine.segments(date(2026, 10, 1) + timedelta(days=i))
            self.assertAlmostEqual(segs[0][0], 0.0)
            self.assertAlmostEqual(segs[-1][1], 24.0)
            for a, b in zip(segs, segs[1:]):
                self.assertAlmostEqual(a[1], b[0], places=6)

    def test_sleeps_at_night_and_works_on_weekdays(self):
        out_days = 0
        for i in range(28):
            d = date(2026, 10, 1) + timedelta(days=i)
            segs = routine.segments(d)
            self.assertEqual(routine.at(segs, 3.0)[3], "Sleeping")
            if d.weekday() not in routine.WEEKEND_DAYS and routine.at(segs, 12.0)[2] == OUT:
                out_days += 1
        self.assertGreaterEqual(out_days, 15)          # most weekdays at work (a sick day now and then)

    def test_deterministic_per_day(self):
        d = date(2026, 10, 7)
        self.assertEqual(routine.segments(d), routine.segments(d))

    def test_routine_change_is_late(self):
        d = date(2026, 10, 20)
        normal = routine.segments(d)
        holiday = routine.segments(d, changed_from=date(2026, 10, 15))
        self.assertEqual(routine.at(normal, 9.0)[3] != "Sleeping", True)
        self.assertEqual(routine.at(holiday, 9.0)[3], "Sleeping")

    def test_missing_rooms_fall_back(self):
        segs = routine.segments(date(2026, 10, 7), rooms=("living", "bedroom"))
        self.assertTrue({s[2] for s in segs} <= {"living", "bedroom", OUT})


class Clock(unittest.TestCase):
    def test_speed_pause_jump(self):
        c = VirtualClock(speed=600)
        t0 = c.now()
        time.sleep(0.05)
        self.assertGreater(c.now() - t0, 20)            # 0.05 s real = 30 s virtual
        c.pause()
        p = c.now()
        time.sleep(0.02)
        self.assertEqual(c.now(), p)
        c.resume()
        target = c.jump_to("06:30")
        self.assertEqual(datetime.fromtimestamp(target).strftime("%H:%M"), "06:30")
        self.assertGreater(target, p)                   # never backwards


class PersonAgent(unittest.TestCase):
    def test_follows_routine_and_takeover(self):
        p = Person(config())
        ts = datetime(2026, 10, 7, 3, 0).timestamp()
        p.step(ts)
        self.assertEqual(p.room, "bedroom")
        self.assertEqual(p.activity, "Sleeping")
        p.take_over("kitchen")
        self.assertEqual(p.step(ts), ("bedroom", "kitchen"))
        p.force_still = True
        p.step(ts)
        self.assertGreater(p.still, 0.9)
        p.give_back()
        p.step(ts)
        self.assertEqual(p.room, "bedroom")


if __name__ == "__main__":
    unittest.main()
