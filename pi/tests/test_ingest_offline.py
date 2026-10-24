"""mqtt_ingest without the internet: the house goes on, nothing is lost, the app catches up later.
Run: python -m unittest discover -s pi/tests"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import firebase_writer  # noqa: E402
import mqtt_ingest  # noqa: E402
import outbox  # noqa: E402
from ai import store  # noqa: E402
from firebase_writer import Writer  # noqa: E402
from test_security import FlakyDB  # noqa: E402

CONFIG = json.loads((HERE.parent / "docs" / "seed.json").read_text(encoding="utf-8"))["config"]


class IngestOfflineTest(unittest.TestCase):
    def setUp(self):
        mqtt_ingest.say = outbox.say = lambda *a: None
        self.db = FlakyDB()
        self.ing = mqtt_ingest.Ingest(Writer(self.db), store.connect(":memory:"), CONFIG,
                                      SimpleNamespace(verbose=False, memory=False))
        self.msg("home/living/status", "online")
        self.assertTrue(self.ing.flush())

    def msg(self, topic, payload):
        self.ing.on_message(None, None, SimpleNamespace(topic=topic, payload=payload.encode()))

    def events(self):
        return list((self.db.root.get("events") or {}).values())          # look inside, even "offline"

    def test_home_state_waits_and_catches_up(self):
        self.db.up = False
        self.msg("home/living/living_fan/state", json.dumps({"v": 1, "level": 70}))
        self.assertFalse(self.ing.flush())                      # no crash: kept for the next try
        self.msg("home/living/living_fan/state", json.dumps({"v": 1, "level": 100}))
        self.db.up = True
        self.assertTrue(self.ing.flush())
        self.assertEqual(self.db.get("home_state/devices/living_fan/level"), 100)   # the value of that moment

    def test_button_and_node_offline_wait_in_sqlite_with_their_time(self):
        self.db.up = False
        t0 = firebase_writer.now_ms()
        self.msg("home/living/living_light/state", json.dumps({"v": 1, "src": "button"}))   # the MQTT thread
        self.msg("home/bedroom/status", "online")
        self.msg("home/bedroom/status", "offline")
        self.assertFalse(self.ing.out.flush())
        self.assertGreaterEqual(self.ing.out.pending(), 3)
        self.assertEqual(self.events(), [])
        self.db.up = True
        self.assertTrue(self.ing.out.flush())
        titles = [e["title"] for e in self.events()]
        self.assertIn("Living room lights turned on", titles)
        self.assertTrue(any("offline" in t for t in titles))
        self.assertTrue(all(t0 - 1000 <= e["at"] <= t0 + 5000 for e in self.events()))

    def test_summaries_wait_too(self):
        self.ing.count_energy()
        self.db.up = False
        self.msg("home/living/temp", "27.5")
        self.ing.each_minute()
        self.assertFalse(self.ing.out.flush())
        self.db.up = True
        self.ing.out.flush()
        day = (self.db.get("summaries/living") or {})
        self.assertEqual([v["temp"] for d in day.values() for v in d.values()], [27.5])

    def test_restart_without_internet_never_writes_a_smaller_energy_total(self):
        today = firebase_writer.day_key()
        self.db.put(f"energy_daily/{today}", {"_base": 10.0})    # before the restart
        self.db.up = False
        self.ing.count_energy()                                  # starts counting at once
        self.ing.energy_t -= 60                                  # one minute passes
        self.ing.count_energy()
        self.ing.each_minute()
        self.assertFalse(self.ing.send_energy())
        self.assertEqual(self.db.root["energy_daily"][today]["_base"], 10.0)
        self.db.up = True
        self.assertTrue(self.ing.send_energy())                  # reads the 10 Wh, adds what it counted
        self.ing.minute = None
        self.ing.each_minute()
        self.assertTrue(self.ing.send_energy())
        self.assertAlmostEqual(self.db.get(f"energy_daily/{today}/_base"), 10.0 + 6.8 / 60, places=2)

    def test_a_broken_listener_does_not_stop_the_mqtt_thread(self):
        def boom(*a, **k):
            raise OSError("no internet")
        self.ing.door_event = boom
        self.msg("home/door/event", json.dumps({"type": "keypad", "ok": True}))   # must not raise


if __name__ == "__main__":
    unittest.main()
