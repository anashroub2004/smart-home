"""A newer docs/seed.json adds rooms/devices to an existing /config without wiping what the user changed,
and (seed 3, the mockup's parts) retires devices and updates the parts."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pi"))

import sim_house  # noqa: E402

OLD = json.loads((Path(__file__).parent / "old_seed_v1.json").read_text(encoding="utf-8"))["config"]
NEW = json.loads((Path(__file__).parent / "seed_full_v2.json").read_text(encoding="utf-8"))["config"]   # seed 2
SEED3 = json.loads((ROOT / "docs" / "seed.json").read_text(encoding="utf-8"))
CUR, RETIRED = SEED3["config"], SEED3.get("retired_devices") or []


class ConfigUpgrade(unittest.TestCase):
    def old(self):
        cfg = json.loads(json.dumps(OLD))
        cfg["version"] = 7                                                  # edited in the app a few times
        cfg["rooms"]["living"]["devices"]["living_light"]["name"] = "Ceiling"  # a user rename
        return cfg

    def test_adds_new_devices_keeps_user_changes(self):
        new, added, skipped = sim_house.upgrade_config(self.old(), NEW)
        for d in ("kitchen_light", "kitchen_hood", "fridge", "kettle", "bathroom_light", "bathroom_vent"):
            self.assertIn(d, added)
            self.assertTrue(any(d in (r.get("devices") or {}) for r in new["rooms"].values()))
        self.assertEqual(skipped, [])
        self.assertEqual(new["rooms"]["living"]["devices"]["living_light"]["name"], "Ceiling")
        self.assertIn("washer", new["rooms"]["bathroom"]["devices"])
        self.assertIn("hum", new["rooms"]["bathroom"]["sensors"])
        self.assertTrue(new["rooms"]["bathroom"]["windowless"])
        self.assertIn("0x45", new["nodes"]["bedroom"]["i2c"])
        self.assertEqual(new["version"], 8)                                 # nodes download it
        self.assertEqual(new["seed"], NEW["seed"])
        self.assertIsNone(sim_house.upgrade_config(new, NEW))               # only once

    def test_never_takes_a_pin_you_used(self):
        cfg = self.old()
        cfg["rooms"]["living"]["devices"]["my_lamp"] = {"name": "Lamp", "icon": "light",
                                                        "hw": {"out": "relay", "pin": 27, "button": 33}}
        new, added, skipped = sim_house.upgrade_config(cfg, NEW)
        self.assertIn("kitchen_light", skipped)                             # it wanted pin 27
        self.assertNotIn("kitchen_light", new["rooms"]["kitchen"]["devices"])
        self.assertIn("my_lamp", new["rooms"]["living"]["devices"])

    def test_current_seed_needs_nothing(self):
        self.assertIsNone(sim_house.upgrade_config(json.loads(json.dumps(CUR)), CUR, RETIRED))

    def seed2_home(self):
        cfg = json.loads(json.dumps(NEW))
        cfg["version"] = 3
        cfg["rooms"]["living"]["devices"]["living_light"]["name"] = "Ceiling"            # a user rename
        cfg["rooms"]["bedroom"]["devices"]["bedroom_fan"]["control"]["ai"] = False      # a user setting
        cfg["rooms"]["living"]["devices"]["my_lamp"] = {"name": "Lamp", "icon": "light",  # a device you added
                                                         "hw": {"out": "relay", "pin": 5, "button": 15}}
        return cfg

    def test_seed3_retires_devices_and_updates_parts(self):
        new, changes, skipped = sim_house.upgrade_config(self.seed2_home(), CUR, RETIRED)
        devs = {d for r in new["rooms"].values() for d in (r.get("devices") or {})}
        for d in ("fridge", "kettle", "kitchen_hood", "bathroom_vent"):
            self.assertNotIn(d, devs)
            self.assertIn(f"removed {d}", changes)
        self.assertEqual(new["rooms"]["living"]["devices"]["living_fan"]["hw"]["out"], "servo")
        self.assertEqual(new["rooms"]["living"]["devices"]["living_light"]["caps"]["energy"], "estimate")
        self.assertIn("LD2410C mmWave", new["rooms"]["living"]["hardware"])
        self.assertEqual(new["rooms"]["bathroom"]["sensors"], ["occ"])                  # no DHT11 in the bathroom
        self.assertEqual(new["nodes"]["bedroom"]["i2c"], CUR["nodes"]["bedroom"]["i2c"])
        self.assertEqual(new["rooms"]["living"]["devices"]["living_light"]["name"], "Ceiling")   # yours: kept
        self.assertFalse(new["rooms"]["bedroom"]["devices"]["bedroom_fan"]["control"]["ai"])
        self.assertIn("my_lamp", new["rooms"]["living"]["devices"])
        self.assertEqual(skipped, [])
        self.assertEqual((new["seed"], new["version"]), (CUR["seed"], 4))
        self.assertIsNone(sim_house.upgrade_config(new, CUR, RETIRED))                  # only once

    def test_seed3_keeps_your_pin(self):
        cfg = self.seed2_home()
        cfg["rooms"]["bathroom"]["devices"]["my_pump"] = {"name": "Pump", "icon": "pump",   # you used GPIO 13
                                                           "hw": {"out": "relay", "pin": 13}}
        new, changes, skipped = sim_house.upgrade_config(cfg, CUR, RETIRED)
        self.assertIn("washer", skipped)                                                 # its new pin is yours
        self.assertEqual(new["rooms"]["bathroom"]["devices"]["washer"]["hw"]["out"], "pwm")   # left as it was


if __name__ == "__main__":
    unittest.main()
