"""pi/outbox.py start_config (mqtt_ingest.py, commands.py, automation.py): a service always starts.
Run: python -m unittest discover -s pi/tests"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import outbox  # noqa: E402
from ai import store  # noqa: E402
from outbox import keep_config, kept_config, start_config  # noqa: E402


class RefreshError(Exception):
    """Like google.auth.exceptions.RefreshError: not an OSError. Google raises it when the Pi boots with an old
    clock (no RTC battery): "invalid_grant: Invalid JWT ... iat and exp"."""


KEPT = {"version": 3, "rooms": {"living": {}}}
NEW = {"version": 4, "rooms": {"living": {}, "kitchen": {}}}


class StartConfigTest(unittest.TestCase):
    def setUp(self):
        self.said = []
        outbox.say = lambda *a: self.said.append(" ".join(map(str, a)))
        self.con = store.connect(":memory:")
        keep_config(self.con, KEPT)

    def broken(self, exc):
        def read():
            raise exc
        return read

    def test_firebase_answers_and_the_answer_is_kept(self):
        self.assertEqual(start_config(self.con, lambda: NEW, "test"), NEW)
        self.assertEqual(kept_config(self.con), NEW)

    def test_a_token_refused_because_of_the_clock_does_not_crash(self):
        cfg = start_config(self.con, self.broken(RefreshError("invalid_grant: Invalid JWT")), "test")
        self.assertEqual(cfg, KEPT)
        self.assertIn("RefreshError", self.said[-1])

    def test_no_internet_still_uses_the_kept_one(self):
        self.assertEqual(start_config(self.con, self.broken(OSError("name resolution")), "test"), KEPT)

    def test_an_empty_database_is_returned_as_is(self):
        self.assertIsNone(start_config(self.con, lambda: None, "test"))
        self.assertEqual(kept_config(self.con), KEPT)


if __name__ == "__main__":
    unittest.main()
