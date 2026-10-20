"""History counts as answers (2026-10-10): a year of switching the bedroom light off yourself after lying down
lets the AI do it itself (and tell you) from day one — until your first "no" / undo. After that, 3 later nights of
switching it off yourself bring it back (2026-10-20)."""
import time
import unittest

from ai import habits, lifesim, trust
from ai import settings as S
from ai.features import load_slots
from ai.persona import Persona
from ai.spec import ai_devices

from .helpers import memory_db


class FromHistory(unittest.TestCase):
    def test_ladder_starts_at_notify_until_no(self):
        st = {}
        self.assertFalse(trust.from_history(st, "lamp", "off_still", S.SELF_OFF_MIN_NIGHTS - 1))
        self.assertTrue(trust.from_history(st, "lamp", "off_still", 20))
        self.assertEqual(trust.level(st, "lamp", "off_still"), S.TRUST_NOTIFY)
        trust.undone(st, "lamp", "off_still")                     # you switched it back on: "that was wrong"
        self.assertEqual(trust.level(st, "lamp", "off_still"), S.TRUST_ASK)
        self.assertFalse(trust.from_history(st, "lamp", "off_still", 28))   # now only your answers count
        for _ in range(S.TRUST_PROMOTE_YES):
            trust.answered(st, "lamp", "off_still", True)
        self.assertEqual(trust.level(st, "lamp", "off_still"), S.TRUST_NOTIFY)

    def test_history_counts_again_after_three_nights(self):
        # Sunday you read in bed and switched it back on; the history only counts again from Monday on
        st = {}
        trust.from_history(st, "lamp", "off_still", 20)
        trust.undone(st, "lamp", "off_still", "2026-10-18")
        self.assertEqual(trust.level(st, "lamp", "off_still"), S.TRUST_ASK)
        nights = ["2026-10-10", "2026-10-18", "2026-10-19", "2026-10-20"]   # old nights + the undo night don't count
        self.assertFalse(trust.from_history(st, "lamp", "off_still", 28, nights))
        nights.append("2026-10-21")                                         # 3rd night after the undo
        self.assertTrue(trust.from_history(st, "lamp", "off_still", 28, nights))
        e = trust.entry(st, "lamp", "off_still")
        self.assertEqual((e["level"], e["nights"], e["back"]), (S.TRUST_NOTIFY, S.HISTORY_BACK_NIGHTS, True))
        self.assertNotIn("history_off", e)
        trust.undone(st, "lamp", "off_still", "2026-10-22")                 # undone again: count from the start
        self.assertFalse(trust.from_history(st, "lamp", "off_still", 28, nights + ["2026-10-23"]))

    def test_no_night_means_answers_only(self):
        st = {}
        trust.from_history(st, "lamp", "off_still", 20)
        trust.undone(st, "lamp", "off_still")                     # older callers / the trust_ladder scenario
        self.assertFalse(trust.from_history(st, "lamp", "off_still", 28, ["2030-01-01"] * 5))

    def test_student_switches_the_bedroom_light_off_himself(self):
        import json
        from pathlib import Path
        cfg = json.loads((Path(__file__).resolve().parents[2] / "docs" / "seed.json").read_text("utf-8"))["config"]
        con, now = memory_db(), int(time.time())
        lifesim.fill(con, cfg, Persona.load(), days=35, end_ts=now)
        slots = load_slots(con, now - 35 * 86400, now)
        out = habits.self_off(con, slots, ai_devices(cfg), now)
        self.assertGreaterEqual(out["bedroom_light"]["nights"], S.SELF_OFF_MIN_NIGHTS)
        self.assertEqual(len(out["bedroom_light"]["night_list"]), out["bedroom_light"]["nights"])
        self.assertLess(out.get("kitchen_light", {}).get("nights", 0), S.SELF_OFF_MIN_NIGHTS)


if __name__ == "__main__":
    unittest.main()
