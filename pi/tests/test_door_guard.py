"""The door node's own lockout (pi/door_guard.py, docs/contract.md "Door security").
Run: python -m unittest discover -s pi/tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from door_guard import DoorGuard  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class DoorGuardTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.g = DoorGuard(clock=self.clock)

    def wrong(self, n, kind="keypad"):
        out = None
        for _ in range(n):
            out = self.g.attempt(kind, False)
        return out

    def test_fifth_wrong_locks_for_60_s(self):
        opens, events = self.wrong(4)
        self.assertFalse(opens)
        self.assertEqual(events, [{"type": "keypad", "ok": False, "n": 4}])
        opens, events = self.wrong(1)
        self.assertEqual(events[-1], {"type": "lockout", "secs": 60, "round": 1})
        self.assertTrue(self.g.locked)

    def test_unknown_finger_counts_on_the_same_counter(self):
        self.wrong(3)
        _, events = self.wrong(2, "fingerprint")
        self.assertEqual(events[-1]["type"], "lockout")

    def test_right_pin_refused_while_locked_and_not_counted(self):
        self.wrong(5)
        opens, events = self.g.attempt("keypad", True)
        self.assertFalse(opens)
        self.assertEqual(events, [{"type": "keypad", "ok": False, "locked": True}])
        self.assertEqual(self.g.failed, 0)

    def test_known_finger_exit_button_and_app_still_open(self):
        self.wrong(5)
        self.assertTrue(self.g.attempt("exit_button", True)[0])
        self.assertTrue(self.g.locked)                       # the exit button does not end the lockout
        self.assertTrue(self.g.attempt("fingerprint", True, 1)[0])
        self.assertEqual((self.g.locked, self.g.round), (False, 0))
        self.wrong(5)
        self.g.opened_remotely()
        self.assertEqual((self.g.locked, self.g.round, self.g.failed), (False, 0, 0))

    def test_lockout_doubles_up_to_the_max(self):
        secs = []
        for _ in range(6):
            secs.append(self.wrong(5)[1][-1]["secs"])
            self.clock.t += 2000                              # wait it out: the round is NOT reset
        self.assertEqual(secs, [60, 120, 240, 480, 900, 900])

    def test_keypad_works_again_after_the_lockout(self):
        self.wrong(5)
        self.clock.t += 59
        self.assertFalse(self.g.attempt("keypad", True)[0])
        self.clock.t += 2
        self.assertTrue(self.g.attempt("keypad", True)[0])
        self.assertEqual(self.g.round, 0)

    def test_power_cut_keeps_the_lockout_and_restarts_it(self):
        with tempfile.TemporaryDirectory() as d:
            flash = Path(d) / "door.json"
            g = DoorGuard(clock=self.clock, path=flash)
            for _ in range(5):
                g.attempt("keypad", False)
            g.attempt("keypad", False)                        # refused, locked
            self.clock.t += 50                                # unplugged 10 s before the end
            g2 = DoorGuard(clock=self.clock, path=flash)
            self.assertTrue(g2.locked)
            self.assertEqual(g2.status()["left_s"], 60)       # starts again from the beginning
            g2.attempt("keypad", False)
            self.clock.t += 61
            for _ in range(5):
                g2.attempt("keypad", False)
            self.assertEqual(g2.lockout_secs(), 120)          # the round survived too

    def test_partial_count_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            flash = Path(d) / "door.json"
            g = DoorGuard(clock=self.clock, path=flash)
            for _ in range(4):
                g.attempt("keypad", False)
            _, events = DoorGuard(clock=self.clock, path=flash).attempt("keypad", False)
            self.assertEqual(events[-1]["type"], "lockout")

    def test_settings_from_the_pi_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            flash = Path(d) / "door.json"
            DoorGuard(clock=self.clock, path=flash).configure(
                {"lockout_attempts": 3, "lockout_s": 30, "lockout_max_s": 300})
            g = DoorGuard(clock=self.clock, path=flash)       # after a restart, without the Pi
            for _ in range(3):
                _, events = g.attempt("keypad", False)
            self.assertEqual(events[-1], {"type": "lockout", "secs": 30, "round": 1})


if __name__ == "__main__":
    unittest.main()
