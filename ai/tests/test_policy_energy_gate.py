"""Decision policy, energy thresholds, adaptive offsets, sensor gate, waste ledger."""
import unittest

from ai import energy, gate, policy
from ai import settings as S
from ai.spec import ai_devices

from .helpers import config, house

TH = dict(act=0.8, suggest=0.6, exit=0.7, off=0.2)


class Policy(unittest.TestCase):
    def test_table(self):
        self.assertEqual(policy.decide(0.85, 0, TH), "schedule_on")
        self.assertEqual(policy.decide(0.85, 1, TH), "keep_on")
        self.assertEqual(policy.decide(0.65, 0, TH), "suggest_on")
        self.assertEqual(policy.decide(0.65, 1, TH), "none")
        self.assertEqual(policy.decide(0.15, 1, TH), "suggest_off")
        self.assertEqual(policy.decide(0.15, 0, TH), "none")
        self.assertEqual(policy.decide(0.40, 0, TH), "none")

    def test_hysteresis(self):
        self.assertEqual(policy.decide(0.75, 0, TH, armed=False), "suggest_on")
        self.assertEqual(policy.decide(0.75, 0, TH, armed=True), "schedule_on")   # stays armed above 0.70
        self.assertEqual(policy.decide(0.69, 1, TH, armed=True), "none")

    def test_manual_and_pause_win(self):
        self.assertEqual(policy.decide(0.99, 0, TH, paused="override"), "paused_by_override")
        self.assertEqual(policy.decide(0.99, 0, TH, paused="user"), "paused_by_user")


class EnergyThresholds(unittest.TestCase):
    def test_scale_with_power(self):
        self.assertAlmostEqual(energy.energy_threshold(3), 0.70, delta=0.01)
        self.assertAlmostEqual(energy.energy_threshold(1500), 0.92, delta=0.01)
        self.assertLess(energy.energy_threshold(1.2), energy.energy_threshold(100))
        self.assertLessEqual(energy.energy_threshold(1e9), S.ACT_MAX)
        self.assertGreaterEqual(energy.energy_threshold(0), S.ACT_MIN)

    def test_anchor_moves_everything(self):
        self.assertGreater(energy.energy_threshold(3, 0.9), energy.energy_threshold(3, 0.8))

    def test_override_and_drift(self):
        spec = ai_devices(config())["living_fan"]
        base = energy.thresholds(spec, 0.8)
        spec.act_override = 0.9
        self.assertEqual(energy.thresholds(spec, 0.8)["act"], 0.9)
        spec.act_override = None
        self.assertAlmostEqual(energy.thresholds(spec, 0.8, drift=True)["act"], base["act"] + S.DRIFT_BUMP, places=3)
        self.assertLess(base["suggest"], base["act"])
        self.assertAlmostEqual(base["exit"], base["act"] - S.HYSTERESIS, places=3)

    def test_adaptive_offsets_clamped(self):
        offsets = {}
        for _ in range(20):
            energy.adapt(offsets, "living_fan", 20, accepted=False)
        self.assertEqual(offsets["living_fan:10"], S.ADAPT_MAX)
        for _ in range(40):
            energy.adapt(offsets, "living_fan", 21, accepted=True)       # same 2-hour block
        self.assertEqual(offsets["living_fan:10"], S.ADAPT_MIN)
        spec = ai_devices(config())["living_fan"]
        self.assertAlmostEqual(energy.thresholds(spec, 0.8, offsets, 20)["offset"], S.ADAPT_MIN)
        self.assertEqual(energy.thresholds(spec, 0.8, offsets, 9)["offset"], 0)


class Gate(unittest.TestCase):
    def setUp(self):
        self.specs = ai_devices(config())

    def test_light_needs_presence_and_dark(self):
        s = self.specs["living_light"]
        self.assertFalse(gate.check_on(s, house()["rooms"]["living"], True)[0])
        self.assertTrue(gate.check_on(s, house({"living": 1})["rooms"]["living"], True)[0])
        ok, why = gate.check_on(s, house({"living": 1}, lux=400)["rooms"]["living"], True)
        self.assertFalse(ok)
        self.assertIn("bright", why)

    def test_thermal_needs_heat_and_someone_home(self):
        s = self.specs["bedroom_fan"]
        room = house(temp=30)["rooms"]["bedroom"]
        self.assertFalse(gate.check_on(s, room, home=False)[0])                   # nobody home
        self.assertTrue(gate.check_on(s, room, home=True)[0])                     # pre-cooling, someone elsewhere
        self.assertFalse(gate.check_on(s, house(temp=26)["rooms"]["bedroom"], home=True)[0])   # not hot

    def test_vacancy_needs_every_sensor(self):
        keys = self.specs["living_light"].presence_keys
        self.assertEqual(sorted(keys), ["living/mmwave", "living/pir"])
        # sitting still: PIR sees nothing, radar still sees you -> NOT vacant
        still = house({"living": 1}, pir={"living": 0})["rooms"]["living"]
        self.assertFalse(gate.room_vacant(still, keys))
        self.assertTrue(gate.room_vacant(house()["rooms"]["living"], keys))
        self.assertFalse(gate.room_vacant({}, keys))                               # no data -> never vacant


class Ledger(unittest.TestCase):
    def test_causes_and_totals(self):
        lg = energy.WasteLedger()
        devs = {"a": dict(v=1, watts=10, room_empty=True), "b": dict(v=1, watts=10, room_empty=False),
                "c": dict(v=0, watts=1.0), "f": dict(v=1, watts=100, room_empty=True, must_run=True)}
        lg.step("2026-10-06", 3600, devs, lambda d: "forgotten")
        snap = lg.snapshot()
        self.assertEqual(snap["a"]["forgotten"], 10)
        self.assertNotIn("b", snap)                       # someone is in the room: not waste
        self.assertEqual(snap["c"]["standby"], 1.0)       # off but drawing 1 W
        self.assertNotIn("f", snap)                       # fridge must run
        lg.record_saved("ai", 5)
        lg.step("2026-10-06", 360, {"a": dict(v=1, watts=10, room_empty=True)}, lambda d: "ai")
        t = lg.totals()
        self.assertEqual(t["wasted_by_ai_wh"], 1.0)
        self.assertEqual(t["ai_net_wh"], 4.0)
        lg.step("2026-10-07", 0, {}, lambda d: "x")      # new day resets
        self.assertEqual(lg.totals()["wasted_wh"], 0)


if __name__ == "__main__":
    unittest.main()
