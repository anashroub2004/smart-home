"""AIRuntime end to end with fixed-probability models:
predict -> sensor gate -> waste guard -> smart off, suggestions, expiry, manual override, feedback."""
import tempfile
import time
import unittest
from pathlib import Path

from ai import store, synth
from ai.runtime import AIRuntime

from .helpers import ConstModel, RecSink, config, house, memory_db

NOW = int(time.time()) // 900 * 900


class Runtime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.con = memory_db()
        synth.fill(cls.con, cls.cfg, days=9, end_ts=NOW - 600, seed=7)

    def make(self, p):
        """Runtime whose models always answer probability p (dict per device or one number)."""
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        sink = RecSink()
        rt = AIRuntime(self.cfg, self.con, sink, model_dir=Path(tmp.name), clock=lambda: NOW)
        from ai.features import build_features, load_slots
        slots = load_slots(self.con, NOW - 9 * 86400, NOW)
        for dev, spec in rt.specs.items():
            X, _ = build_features(slots, spec)
            pp = p.get(dev, 0.0) if isinstance(p, dict) else p
            rt.bundles[dev] = dict(model=ConstModel(pp), features=list(X.columns), typical={}, drift=False, levels={})
        return rt, sink

    def test_fan_preccools_only_when_someone_home(self):
        rt, sink = self.make({"bedroom_fan": 0.95})
        decisions = {d["device"]: d for d in rt.plan(house(temp=30), NOW)}
        d = decisions["bedroom_fan"]
        self.assertEqual(d["action"], "schedule_on")
        self.assertIn("Pre-cooling", d["why"])
        start = rt.pending["bedroom_fan"]["start"]
        self.assertEqual(start, NOW + 45 * 60)                         # target 60 min - lead 15 min
        # nobody home at execution time -> nothing happens
        self.assertEqual(rt.tick(house(temp=30), start + 1), [])
        # still nobody when the window closes -> skipped and logged, never switched on
        rt.tick(house(temp=30), start + 31 * 60)
        self.assertNotIn("bedroom_fan", rt.pending)
        self.assertTrue(any("Skipped" in e.get("short", "") for e in sink.events))

    def test_fan_switches_on_when_gate_passes(self):
        rt, _ = self.make({"bedroom_fan": 0.95})
        rt.plan(house(temp=30), NOW)
        start = rt.pending["bedroom_fan"]["start"]
        acts = rt.tick(house({"living": 1}, temp=30), start + 1)       # someone home (living room)
        self.assertEqual([(a["device"], a["v"]) for a in acts], [("bedroom_fan", 1)])
        self.assertIn("checked: room 30.0°C and someone home", acts[0]["why"])
        self.assertIn("bedroom_fan", rt.guards)

    def test_waste_guard_turns_off_and_learns(self):
        rt, sink = self.make({"bedroom_fan": 0.95})
        rt.plan(house(temp=30), NOW)
        start = rt.pending["bedroom_fan"]["start"]
        rt.tick(house({"living": 1}, temp=30), start + 1)
        on = {"bedroom_fan": {"v": 1, "src": "ai", "watts": 1.7}}
        # 10 minutes after the target nobody entered the bedroom -> AI switches it off itself
        acts = rt.tick(house({"living": 1}, temp=30, devices=on), start + (15 + 11) * 60)
        self.assertEqual([(a["device"], a["v"]) for a in acts], [("bedroom_fan", 0)])
        self.assertIn("Prediction missed", acts[0]["short"])
        rows = self.con.execute("SELECT value FROM readings WHERE key='ai_miss/bedroom_fan' ORDER BY ts").fetchall()
        self.assertEqual([r[0] for r in rows][-2:], [1.0, 0.0])
        self.assertEqual(rt.state["stats"]["ai_miss"], 1)

    def test_waste_guard_hit(self):
        rt, _ = self.make({"bedroom_fan": 0.95})
        rt.plan(house(temp=30), NOW)
        start = rt.pending["bedroom_fan"]["start"]
        rt.tick(house({"living": 1}, temp=30), start + 1)
        on = {"bedroom_fan": {"v": 1, "src": "ai", "watts": 1.7}}
        self.assertEqual(rt.tick(house({"bedroom": 1}, temp=30, devices=on), start + 600), [])
        self.assertNotIn("bedroom_fan", rt.guards)
        self.assertEqual(rt.state["stats"]["ai_hit"], 1)

    def test_light_never_preswitched(self):
        rt, _ = self.make({"living_light": 0.95})
        d = {x["device"]: x for x in rt.plan(house(), NOW)}["living_light"]
        self.assertEqual(d["action"], "schedule_on")
        self.assertEqual(rt.tick(house(), NOW + 60), [])                  # empty room: stays off
        acts = rt.tick(house({"living": 1}, lux=30), NOW + 120)           # you walk in, it is dark
        self.assertEqual([(a["device"], a["v"]) for a in acts], [("living_light", 1)])

    def test_smart_off_needs_both_sensors(self):
        rt, sink = self.make(0.5)
        on = {"living_light": {"v": 1, "src": "web", "watts": 1.2}}
        # PIR empty but radar still sees someone sitting -> never off
        h = house({"living": 1}, pir={"living": 0}, devices=on)
        for k in range(0, 20 * 60, 60):
            self.assertEqual(rt.tick(h, NOW + k), [])
        # really empty: off after 5 minutes (lights)
        rt2, _ = self.make(0.5)
        empty = house(devices=on)
        self.assertEqual(rt2.tick(empty, NOW), [])
        self.assertEqual(rt2.tick(empty, NOW + 4 * 60), [])
        acts = rt2.tick(empty, NOW + 5 * 60 + 1)
        self.assertEqual([(a["device"], a["v"]) for a in acts], [("living_light", 0)])
        self.assertGreater(acts[0]["saved_wh"], 0)

    def test_manual_override_pauses_two_hours(self):
        rt, _ = self.make({"living_fan": 0.95})
        rt.on_manual("living_fan", NOW)
        d = {x["device"]: x for x in rt.plan(house(temp=30), NOW + 60)}["living_fan"]
        self.assertEqual(d["action"], "paused_by_override")
        on = {"living_fan": {"v": 1, "src": "web", "watts": 1.7}}
        self.assertEqual(rt.tick(house(devices=on), NOW + 30 * 60), [])  # no smart off either
        d = {x["device"]: x for x in rt.plan(house(temp=30), NOW + 2 * 3600 + 60)}["living_fan"]
        self.assertEqual(d["action"], "schedule_on")

    def test_user_pause_from_app(self):
        rt, _ = self.make({"living_fan": 0.95})
        h = house(temp=30, paused={"living_fan": (NOW + 3600) * 1000})
        d = {x["device"]: x for x in rt.plan(h, NOW)}["living_fan"]
        self.assertEqual(d["action"], "paused_by_user")

    def test_suggestion_only_when_sensors_agree_and_expires(self):
        rt, sink = self.make({"living_light": 0.55})
        rt.plan(house(), NOW)                                               # nobody there: no suggestion
        self.assertEqual(sink.suggestions, {})
        rt.plan(house({"living": 1}, lux=30), NOW)
        self.assertEqual(len(sink.suggestions), 1)
        sid = next(iter(sink.suggestions))
        rt.tick(house({"living": 1}, lux=30), NOW + 60)
        self.assertEqual(sink.expired, [])
        rt.tick(house(), NOW + 120)                                         # you left -> withdrawn
        self.assertEqual(sink.expired[0][0], sid)
        self.assertIn("room changed", sink.expired[0][2])

    def test_suggestion_ttl(self):
        rt, sink = self.make({"living_light": 0.55})
        rt.plan(house({"living": 1}, lux=30), NOW)
        rt.tick(house({"living": 1}, lux=30), NOW + 16 * 60)
        self.assertIn("no answer", sink.expired[0][2])

    def test_answers_adapt_threshold(self):
        rt, sink = self.make({"living_light": 0.55})
        rt.plan(house({"living": 1}, lux=30), NOW)
        sid = next(iter(sink.suggestions))
        before = rt.state["offsets"].get(next(iter(rt.state["offsets"]), ""), 0)
        rt.on_answer(sid, {"device": "living_light"}, accepted=False, now=NOW)
        self.assertGreater(sum(rt.state["offsets"].values()), before)
        rt.plan(house({"living": 1}, lux=30), NOW + 60)                    # snoozed: no new suggestion
        self.assertEqual(len(sink.suggestions), 1)

    def test_waste_ledger_written(self):
        rt, sink = self.make(0.0)
        on = {"living_fan": {"v": 1, "src": "web", "watts": 1.7}}
        rt.tick(house(devices=on), NOW, dt=60)
        rt.tick(house(devices=on), NOW + 61, dt=60)
        day, per, totals = sink.waste
        self.assertIn("living_fan", per)
        self.assertGreater(totals["wasted_wh"], 0)

    def test_new_device_is_learning(self):
        cfg = config()
        cfg["rooms"]["living"]["devices"]["living_heater"] = {
            "name": "Heater", "icon": "heater", "caps": {"power": "write", "energy": "estimate"},
            "control": {"app": True, "ai": True}, "rules": {}, "watts": 800}
        rt, _ = self.make(0.9)
        rt.reload(cfg)
        d = {x["device"]: x for x in rt.plan(house(), NOW)}["living_heater"]
        self.assertEqual(d["action"], "learning")
        self.assertEqual(rt.specs["living_heater"].kind, "thermal")


class Recorder(unittest.TestCase):
    def test_on_change_and_heartbeat(self):
        con = memory_db()
        r = store.Recorder(con, heartbeat_s=900)
        self.assertEqual(r.observe({"living/temp": 27.0, "device/x": 1}, 1000), 2)
        self.assertEqual(r.observe({"living/temp": 27.1, "device/x": 1}, 1060), 0)     # small move, no change
        self.assertEqual(r.observe({"living/temp": 27.5, "device/x": 0}, 1120), 2)
        self.assertEqual(r.observe({"living/temp": 27.5, "device/x": 0}, 2100), 2)     # heartbeat


if __name__ == "__main__":
    unittest.main()
