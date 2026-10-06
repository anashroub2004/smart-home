"""Regression tests for the issues found in the code review (2026-10-06)."""
import math
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ai import gate, store, synth
from ai.dispatch import FirebaseSink
from ai.runtime import AIRuntime, hhmm
from ai.spec import ai_devices

from .fakedb import FakeDB
from .helpers import ConstModel, RecSink, config, house, memory_db

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pi"))
from firebase_writer import Writer  # noqa: E402

NOW = int(time.time()) // 900 * 900


def runtime(cfg, con, p):
    tmp = tempfile.TemporaryDirectory()
    sink = RecSink()
    rt = AIRuntime(cfg, con, sink, model_dir=Path(tmp.name), clock=lambda: NOW)
    from ai.features import build_features, load_slots
    slots = load_slots(con, NOW - 9 * 86400, NOW)
    for dev, spec in rt.specs.items():
        X, _ = build_features(slots, spec)
        pp = p.get(dev, 0.0) if isinstance(p, dict) else p
        rt.bundles[dev] = dict(model=ConstModel(pp), features=list(X.columns), typical={}, drift=False, levels={})
    return rt, sink, tmp


class Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.con = memory_db()
        synth.fill(cls.con, cls.cfg, days=9, end_ts=NOW - 600, seed=7)

    def test_replanning_does_not_push_the_fan_back(self):
        """Bug 1: plan every 15 min + tick every minute for 3 h -> the fan must actually switch on."""
        rt, sink, tmp = runtime(self.cfg, self.con, {"bedroom_fan": 0.95})
        self.addCleanup(tmp.cleanup)
        switched = []
        devices = {}
        for minute in range(0, 180):
            t = NOW + minute * 60
            h = house({"living": 1}, temp=30, devices=devices)
            if minute % 15 == 0:
                rt.plan(h, t)
            for a in rt.tick(h, t, dt=60):
                switched.append((minute, a["device"], a["v"]))
                devices[a["device"]] = {"v": a["v"], "src": "ai", "watts": 1.7}
        on = [m for m, d, v in switched if d == "bedroom_fan" and v == 1]
        self.assertTrue(on, "the fan never switched on")
        self.assertEqual(on[0], 45)                  # target 60 min - lead 15 min, from the FIRST plan

    def test_room_without_radar_is_never_vacant(self):
        """Bug 2: no presence sensors (or PIR only) must not count as an empty room."""
        self.assertFalse(gate.room_vacant({"occ": 0}, []))
        self.assertFalse(gate.room_vacant({"occ": 0, "pir": 0}, ["bathroom/pir"]))
        self.assertTrue(gate.room_vacant({"occ": 0, "mmwave": 0, "pir": 0}, ["x/mmwave", "x/pir"]))
        self.assertFalse(gate.room_vacant({"occ": 0, "pir": 0}, ["x/mmwave", "x/pir"]))   # radar reading missing

    def test_sensorless_room_device_not_switched_off(self):
        cfg = config()
        cfg["rooms"]["store"] = {"name": "Store room", "node": "living", "sensors": [], "hardware": [],
                                 "devices": {"store_heater": {"name": "Heater", "icon": "heater", "watts": 800,
                                                              "caps": {"power": "write", "energy": "estimate"},
                                                              "control": {"app": True, "ai": True}, "rules": {}}}}
        rt, _, tmp = runtime(cfg, self.con, 0.5)
        self.addCleanup(tmp.cleanup)
        h = house(devices={"store_heater": {"v": 1, "src": "web", "watts": 800}})
        h["rooms"]["store"] = {"occ": 0}
        for k in range(0, 30 * 60, 60):
            self.assertEqual(rt.tick(h, NOW + k, dt=60), [])

    def test_heater_gate_wants_cold(self):
        cfg = config()
        cfg["rooms"]["living"]["devices"]["living_heater"] = {
            "name": "Heater", "icon": "heater", "watts": 800, "caps": {"power": "write", "energy": "estimate"},
            "control": {"app": True, "ai": True}, "rules": {}}
        spec = ai_devices(cfg)["living_heater"]
        self.assertTrue(spec.heating)
        self.assertEqual(spec.temp_on, 20)
        self.assertFalse(gate.check_on(spec, {"temp": 25, "occ": 1}, True)[0])
        self.assertTrue(gate.check_on(spec, {"temp": 17, "occ": 1}, True)[0])

    def test_nan_and_missing_readings_never_pass(self):
        spec = ai_devices(config())["bedroom_fan"]
        self.assertFalse(gate.check_on(spec, {"temp": float("nan")}, True)[0])
        self.assertFalse(gate.check_on(spec, {}, True)[0])

    def test_null_values_in_device_ai_settings(self):
        cfg = config()
        cfg["rooms"]["living"]["devices"]["living_fan"]["ai"] = {"lead_min": None, "act": "x", "off_after_min": None}
        spec = ai_devices(cfg)["living_fan"]
        self.assertEqual(spec.lead_min, 15)
        self.assertIsNone(spec.act_override)

    def test_accepted_suggestion_is_not_expired(self):
        """Bug 4: the answer must win over 'already done'."""
        w = Writer(FakeDB())
        sink = FirebaseSink(w, room_of=lambda d: "living")
        sid = w.push_suggestion("living_light", "on", 0.6, "Turn on the living room lights?", "why", NOW * 1000)
        w.mark_suggestion(sid, {"response": "accept"})
        self.assertFalse(sink.expire_suggestion(sid, "living_light", "Turn on?", "no answer"))
        self.assertIsNotNone(w.db.get(f"suggestions/{sid}"))          # still there for the answer handler
        sid2 = w.push_suggestion("living_light", "on", 0.6, "Turn on?", "why", NOW * 1000)
        self.assertTrue(sink.expire_suggestion(sid2, "living_light", "Turn on?", "no answer"))
        self.assertIsNone(w.db.get(f"suggestions/{sid2}"))
        self.assertTrue(any("expired" in e["title"] for e in (w.db.get("events") or {}).values()))

    def test_bootstrap_never_wipes_real_readings(self):
        """Bug 5: a hub with a few days of real data keeps them."""
        con = memory_db()
        store.write(con, [(NOW - 3 * 86400, "living/temp", 27.0), (NOW, "living/temp", 27.5)])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rt = AIRuntime(config(), con, RecSink(), model_dir=Path(tmp.name), clock=lambda: NOW)
        rt.bootstrap_if_needed(NOW, quiet=True)
        n = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        self.assertEqual(n, 2)
        self.assertEqual(rt.report["status"], "learning")

    def test_times_are_home_time(self):
        """Bug 8: schedule times use Asia/Hebron whatever the machine's time zone."""
        ts = 1791280800          # 2026-10-06 10:00 UTC
        self.assertEqual(hhmm(ts), "13:00")

    def test_firebase_waste_writes_are_rare(self):
        rt, sink, tmp = runtime(self.cfg, self.con, 0.0)
        self.addCleanup(tmp.cleanup)
        calls = []
        sink.write_waste = lambda *a: calls.append(a)
        on = {"living_fan": {"v": 1, "src": "web", "watts": 1.7}}
        for k in range(0, 20 * 60, 5):
            rt.tick(house(devices=on), NOW + k, dt=5)
        self.assertLessEqual(len(calls), 5)          # every 5 minutes at most
        self.assertFalse(math.isnan(calls[-1][2]["wasted_wh"]))


if __name__ == "__main__":
    unittest.main()
