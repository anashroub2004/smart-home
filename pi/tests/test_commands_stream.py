"""commands.py listening to /commands as a stream (step d): what each server-sent event does, and the 10 s rule.
Run: python -m unittest discover -s pi/tests"""
import io
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import commands  # noqa: E402
import rest_db  # noqa: E402

CONFIG = json.loads((HERE.parent / "docs" / "seed.json").read_text(encoding="utf-8"))["config"]


def ms(ago_s=0):
    return int((time.time() - ago_s) * 1000)


class StreamTest(unittest.TestCase):
    def setUp(self):
        commands.say = lambda *a: None
        self.started, self.acks = [], []
        c = commands.Commands(SimpleNamespace(ack_command=lambda *a: self.acks.append(a)), None, CONFIG,
                              SimpleNamespace())
        c.start = lambda dev, cmd: self.started.append((dev, cmd.get("v")))
        self.c = c

    def ev(self, event, path, data):
        self.c.on_stream(event, {"path": path, "data": data})

    def test_new_command_runs_at_once(self):
        self.ev("put", "/", None)                                        # connected, /commands empty
        self.ev("put", "/living_fan", {"v": 1, "level": 70, "at": ms(), "status": "pending"})
        self.assertEqual(self.started, [("living_fan", 1)])

    def test_our_own_done_and_repeats_do_not_run_it_again(self):
        self.ev("put", "/", None)
        cmd = {"v": 1, "at": ms(), "status": "pending"}
        self.ev("put", "/living_fan", cmd)
        self.ev("patch", "/living_fan", {"status": "done"})              # our ack comes back as an event
        self.ev("put", "/", {"living_fan": {**cmd, "status": "done"}})   # reconnect
        self.ev("put", "/", {"living_fan": cmd})                         # the same pending one again
        self.assertEqual(len(self.started), 1)

    def test_old_pending_on_connect_is_expired_not_run(self):
        self.ev("put", "/", {"living_fan": {"v": 1, "at": ms(1800), "status": "pending"},     # 30 min old
                             "bedroom_light": {"v": 1, "at": ms(3), "status": "pending"}})    # 3 s old
        self.assertEqual(self.started, [("bedroom_light", 1)])
        self.assertEqual(self.acks[0][:2], ("living_fan", False))
        self.assertIn("Expired", self.acks[0][2])

    def test_a_live_command_is_never_expired_by_the_phone_clock(self):
        self.ev("put", "/", None)
        self.ev("put", "/living_fan", {"v": 1, "at": ms(60), "status": "pending"})   # the phone's clock is behind
        self.assertEqual(self.started, [("living_fan", 1)])

    def test_patch_at_the_top_and_nested_puts(self):
        self.ev("put", "/", {})
        self.ev("patch", "/", {"bedroom_fan": {"v": 0, "at": ms(), "status": "pending"}})
        self.ev("put", "/living_light", {"v": 1, "at": ms(), "status": "done"})
        self.ev("put", "/living_light/status", "pending")
        self.assertEqual(self.started, [("bedroom_fan", 0), ("living_light", 1)])
        self.ev("put", "/living_light", None)                            # deleted
        self.assertNotIn("living_light", self.c.cmds)


class SseParserTest(unittest.TestCase):
    """rest_db.RestDB.stream reads Firebase's server-sent events."""

    def stream(self, text):
        db = rest_db.RestDB(base_url="http://x", namespace="")
        orig = rest_db.urllib.request.urlopen
        rest_db.urllib.request.urlopen = lambda req, timeout: io.BytesIO(text.encode())
        try:
            return list(db.stream("commands"))
        finally:
            rest_db.urllib.request.urlopen = orig

    def test_events_keep_alive_and_token_end(self):
        out = self.stream('event: put\ndata: {"path":"/","data":null}\n\n'
                          'event: keep-alive\ndata: null\n\n'
                          'event: patch\ndata: {"path":"/living_fan","data":{"status":"done"}}\n\n'
                          'event: auth_revoked\ndata: credential is no longer valid\n\n'
                          'event: put\ndata: {"path":"/x","data":1}\n\n')
        self.assertEqual(out, [("put", {"path": "/", "data": None}),
                               ("patch", {"path": "/living_fan", "data": {"status": "done"}})])

    def test_cancel_raises(self):
        with self.assertRaises(RuntimeError):
            self.stream('event: cancel\ndata: permission denied\n\n')


if __name__ == "__main__":
    unittest.main()
