"""Test scenarios for the digital twin — each one checks one agreed AI rule in the running simulator
and gives PASS / FAIL with the reason. Results are also written to pi/twin/reports/ (for the report's test chapter).

Every scenario picks its devices from the configuration (never hard-coded): the first AI light / fan in a room
that has a radar. It runs on the virtual clock (60x by default) and puts the house back as it was afterwards.
"""
import time
from datetime import datetime
from pathlib import Path

from person import OUT


def json_copy(d):
    return dict(d)

REPORTS = Path(__file__).resolve().parent / "twin" / "reports"

CATALOG = [
    ("forgot_light", "Forgot the light",
     "You leave the room with the light on. The AI must switch it off after 5 min of confirmed vacancy (radar + PIR)."),
    ("still_person", "Sitting still",
     "You sit still for 15 min: the PIR sees nothing, the radar still sees you. Nothing may be switched off."),
    ("guard_miss", "Pre-cooling, nobody came",
     "The AI pre-cools a room for you, you never go there. It must switch it off by itself and learn from it."),
    ("guard_hit", "Pre-cooling, you came",
     "The AI pre-cools a room and you walk in on time. It stays on and counts as a correct prediction."),
    ("light_waits", "Lights never early",
     "The AI expects you in a dark room. The light must stay off until you actually walk in."),
    ("suggestion_expiry", "Question withdrawn",
     "The AI asks to switch the fan on, then you leave home. The question must disappear."),
    ("manual_override", "Hands off after manual control",
     "You press the wall button. The AI must leave that device alone for 2 hours."),
    ("power_fault", "Fan drawing double power",
     "The fan's motor is blocked (2x power). The AI must raise a power-fault alert within minutes."),
    ("trust_ladder", "Fell asleep with the light on",
     "At night you lie still with the light on. The AI must ask first; after you said yes 3 times it switches it off "
     "by itself and tells you (trust ladder)."),
    ("full_day", "A whole day",
     "Runs 24 hours of the routine at 600x and reports what the AI did: switched on, correct, missed, saved, wasted."),
]


class ScenarioRunner:
    def __init__(self, house, clock):
        self.h, self.clock = house, clock
        self.cur = None          # running scenario state
        self.results = []

    # ------------------------------------------------------------------ helpers
    @property
    def ai(self):
        return self.h.ai

    def pick(self, kind):
        """First AI device of this kind in a room with a radar (from the config)."""
        if not self.ai:
            return None
        for dev, spec in self.ai.specs.items():
            if spec.kind == kind and any(k.endswith("/mmwave") for k in spec.presence_keys):
                return spec
        return None

    def other_room(self, rid):
        for r, rc in self.h.config["rooms"].items():
            if r != rid and not rc.get("hidden") and "occ" in (rc.get("sensors") or []):
                return r
        return OUT

    def now(self):
        return self.clock.now()

    def set_dev(self, dev, v):
        """Switch a device as 'the scenario' (not a manual action, so the AI is not paused)."""
        self.h.set_device(dev, v, self.h.mid_level(dev) if v else None, source="system", src_label="Scenario",
                          group="system", by_label="Test scenario", why="Set up by the scenario", tags=["system"])

    def clear_pause(self, dev):
        self.h.override_until.pop(dev, None)
        if self.ai:
            self.ai.override_at.pop(dev, None)
            self.ai.pending.pop(dev, None)
            self.ai.guards.pop(dev, None)

    def events_since(self, t0):
        ms = t0 * 1000
        return [e for e in self.h.recent if e.get("at", 0) >= ms]

    def view(self):
        c = self.cur
        return {"catalog": [{"id": i, "title": t, "desc": d} for i, t, d in CATALOG],
                "running": None if not c else {"id": c["id"], "title": c["title"], "step": c.get("step", ""),
                                               "elapsed_min": round((self.now() - c["t0"]) / 60, 1)},
                "results": self.results[-12:]}

    # ------------------------------------------------------------------ lifecycle
    def start(self, sid):
        if self.cur:
            self.stop()
        meta = next((m for m in CATALOG if m[0] == sid), None)
        if not meta:
            return
        if not self.ai:
            return self.finish_now(sid, meta[1], False, "The AI is not running (install ai/requirements.txt)")
        saved = dict(speed=self.clock.speed, temp=self.h.temp_delta, faults=dict(self.h.faults),
                     habits=self.h.person.habits)
        self.cur = {"id": sid, "title": meta[1], "t0": self.now(), "saved": saved, "stage": 0}
        self.h.person.habits = False
        self.clock.set_speed(600 if sid == "full_day" else 60)
        if self.clock.paused:
            self.clock.resume()
        getattr(self, f"setup_{sid}")(self.cur)

    def stop(self, note="stopped"):
        if self.cur:
            self.done(False, note)

    def finish_now(self, sid, title, ok, detail):
        self.results.append({"id": sid, "title": title, "ok": ok, "detail": detail,
                             "at": datetime.fromtimestamp(self.now()).strftime("%H:%M"), "minutes": 0})
        self.write_report(self.results[-1])

    def done(self, ok, detail):
        c, self.cur = self.cur, None
        s = c["saved"]
        self.h.temp_delta = s["temp"]
        self.h.faults.clear()
        self.h.faults.update(s["faults"])
        self.h.person.habits = s["habits"]
        self.h.person.give_back()
        self.clock.set_speed(s["speed"])
        res = {"id": c["id"], "title": c["title"], "ok": ok, "detail": detail,
               "at": datetime.fromtimestamp(self.now()).strftime("%H:%M"),
               "minutes": round((self.now() - c["t0"]) / 60)}
        self.results.append(res)
        self.write_report(res)

    def write_report(self, res):
        try:
            REPORTS.mkdir(parents=True, exist_ok=True)
            f = REPORTS / f"{time.strftime('%Y-%m-%d')}_scenarios.md"
            new = not f.exists()
            with f.open("a", encoding="utf-8") as fh:
                if new:
                    fh.write("# Digital twin — scenario results\n\n| time (house) | scenario | result | minutes | detail |\n"
                             "|---|---|---|---|---|\n")
                fh.write(f"| {res['at']} | {res['title']} | {'PASS' if res['ok'] else 'FAIL'} | {res['minutes']} | "
                         f"{res['detail']} |\n")
        except OSError:
            pass

    def tick(self):
        c = self.cur
        if not c:
            return
        try:
            out = getattr(self, f"check_{c['id']}")(c, (self.now() - c["t0"]) / 60)
        except Exception as e:  # noqa: BLE001 — a broken scenario must not stop the house
            out = (False, f"scenario error: {e}")
        if out:
            self.done(*out)

    # ------------------------------------------------------------------ 1. forgot the light
    def setup_forgot_light(self, c):
        spec = self.pick("light")
        if not spec:
            return self.done(False, "no AI light in a room with a radar")
        c["dev"], c["room"] = spec.id, spec.room
        self.clear_pause(spec.id)
        self.h.person.take_over(spec.room, "Reading")
        self.set_dev(spec.id, 1)
        c["step"] = "you are in the room with the light on"

    def check_forgot_light(self, c, m):
        st = self.h.devices[c["dev"]]
        if c["stage"] == 0 and m >= 1:
            c["stage"], c["left"] = 1, self.now()
            self.h.person.take_over(self.other_room(c["room"]))
            c["step"] = "you left without switching it off — waiting for the AI"
        if c["stage"] == 1:
            gone = (self.now() - c["left"]) / 60
            view = (self.ai.twin_view(self.h.house_snapshot(), self.now()).get("smart_off") or {}).get(c["dev"])
            if view:
                # 5 min for lights; x2 if the model expects you back soon, x0.5 if you usually don't need it now
                # (the factor follows the latest 15-minute plan, so keep the last value seen)
                c["expected"] = (view["off_at"] - view["since"]) / 60
            if not st["v"]:
                exp = c.get("expected", 5)
                why = {2.5: " (x0.5: the model says you usually don't need it now)",
                       10.0: " (x2: the model expects you back soon)"}.get(round(exp, 1), "")
                if st.get("src") == "ai":
                    # 5 min x (0.5 .. 2) depending on the latest plan -> between 2.5 and 10 min
                    return (2.0 <= gone <= 11.0,
                            f"AI switched it off {gone:.1f} min after you left (5 min for lights, x0.5..x2 from the "
                            f"model; last seen {exp:.1f} min{why}); radar and PIR both empty")
                return False, f"switched off by {st.get('src')} after {gone:.1f} min, not by the AI"
            if gone > 15:
                return False, "still on 15 min after you left"

    # ------------------------------------------------------------------ 2. sitting still
    def setup_still_person(self, c):
        spec = self.pick("light")
        if not spec:
            return self.done(False, "no AI light in a room with a radar")
        c["dev"] = spec.id
        self.clear_pause(spec.id)
        self.h.person.take_over(spec.room, "Reading, not moving")
        self.h.person.force_still = True
        self.set_dev(spec.id, 1)
        c["step"] = "sitting still: PIR empty, radar sees you"

    def check_still_person(self, c, m):
        if not self.h.devices[c["dev"]]["v"]:
            return False, f"switched off after {m:.1f} min while you were there"
        if m >= 15:
            return True, "light stayed on for 15 min although the PIR saw no movement (radar confirmed you)"

    # ------------------------------------------------------------------ trust ladder
    def setup_trust_ladder(self, c):
        from ai import trust
        spec = None
        for dev, sp in self.ai.specs.items():           # a light in a room with radar + PIR where you usually sleep
            keys = sp.presence_keys
            if sp.kind == "light" and any(k.endswith("/mmwave") for k in keys) and any(k.endswith("/pir") for k in keys) \
                    and self.ai.bundles.get(dev):
                spec = sp
                break
        if not spec:
            return self.done(False, "no AI light in a room with radar + PIR and learned habits")
        self.clock.jump_to("01:30")
        # pin the habits for this test ("asleep here at night, light off"): the test checks the ladder itself,
        # not whatever this computer's twin history happens to contain
        c["habits"] = self.ai.habits
        pinned = {"use": dict(self.ai.habits.get("use") or {}), "rest": dict(self.ai.habits.get("rest") or {})}
        flat = lambda v: {"days": 99, "weekday": [v] * 96, "weekend": [v] * 96}
        pinned["use"][spec.id], pinned["rest"][spec.room] = flat(0.05), flat(0.95)
        self.ai.habits = c["pinned"] = pinned
        c.update(dev=spec.id, round=1, trust_saved=json_copy(trust.entry(self.ai.state, spec.id, "off_still")), log=[])
        trust.entry(self.ai.state, spec.id, "off_still").update(level=0, yes=0, ok=0, history_off=True)  # answers only
        self.clear_pause(spec.id)
        self.ai.snooze.pop(spec.id, None)                 # earlier tests may have left "don't ask again yet"
        for sid in [k for k, sg in self.ai.suggestions.items() if sg["device"] == spec.id]:
            self.ai.suggestions.pop(sid)
        self.h.person.take_over(spec.room, "Sleeping")
        self.h.person.force_still = True
        self.set_dev(spec.id, 1)
        c["since"] = self.now()
        c["step"] = "night 1: asleep, light on — the AI should ask"

    def check_trust_ladder(self, c, m):
        from ai import trust
        if self.ai.training():
            return None                                   # nightly retraining: house time waits
        self.ai.habits = c["pinned"]                      # a retrain reloads habits.json — keep the test's ones
        dev, st = c["dev"], self.h.devices[c["dev"]]
        waited = (self.now() - c["since"]) / 60
        ask = next((sid for sid, sg in self.ai.suggestions.items() if sg["device"] == dev and sg.get("kind") == "off_still"), None)
        if c["round"] <= 3:
            if ask:
                c["log"].append(f"night {c['round']}: asked after {waited:.0f} min, you said yes")
                self.h.answer_suggestion(ask, True, "test scenario")
                self.h.handle_suggestion_answers()
                c["round"] += 1
                self.set_dev(dev, 1)
                c["since"] = self.now()
                c["step"] = f"night {c['round']}: light on again" + (" — 3 yes: it should act by itself now" if c["round"] == 4 else "")
            elif not st["v"]:
                return self._ladder_end(c, False, f"night {c['round']}: switched off without asking first")
            elif waited > 20:
                return self._ladder_end(c, False, f"night {c['round']}: no question after 20 min")
            return None
        if ask:
            return self._ladder_end(c, False, "still asking after 3 yes")
        if not st["v"]:
            ok = st.get("src") == "ai" and trust.level(self.ai.state, dev, "off_still") >= 1
            c["log"].append(f"night 4: switched off by itself after {waited:.0f} min and told you")
            return self._ladder_end(c, ok, "; ".join(c["log"]) if ok else f"switched off by {st.get('src')}")
        if waited > 20:
            return self._ladder_end(c, False, "night 4: did not act by itself")
        return None

    def _ladder_end(self, c, ok, detail):
        from ai import trust
        e = trust.entry(self.ai.state, c["dev"], "off_still")
        e.clear()
        e.update(c["trust_saved"])                                                   # leave your real ladder as it was
        self.ai.habits = c["habits"]
        self.ai.auto.pop(c["dev"], None)
        self.ai.save_state()
        return ok, detail

    # ------------------------------------------------------------------ 3/4. waste guard
    def _guard_setup(self, c):
        spec = self.pick("thermal")
        if not spec:
            return self.done(False, "no AI fan in a room with a radar")
        c["dev"], c["room"] = spec.id, spec.room
        self.clear_pause(spec.id)
        if self.h.devices[spec.id]["v"]:
            self.set_dev(spec.id, 0)
        self.h.person.take_over(self.other_room(spec.room))         # someone home, but not in that room
        hot_needed = (spec.temp_on or 28) + 2 - (self.h.rooms.get(spec.room, {}).get("temp") or 25)
        self.h.temp_delta += max(0.0, hot_needed)
        c["target"] = self.now() + 10 * 60
        c["misses"] = self.ai.state.get("stats", {}).get("ai_miss", 0)
        c["hits"] = self.ai.state.get("stats", {}).get("ai_hit", 0)
        self.ai.inject_plan(spec.id, c["target"], 0.9, "Scenario: the AI expects you there in 10 min")
        c["step"] = "the AI plans to pre-cool the room"
        return spec

    def setup_guard_miss(self, c):
        self._guard_setup(c)

    def check_guard_miss(self, c, m):
        st = self.h.devices[c["dev"]]
        if st["v"] and st.get("src") == "ai" and "on_at" not in c:
            c["on_at"] = m
            c["step"] = "pre-cooling on — you are not coming"
        misses = self.ai.state.get("stats", {}).get("ai_miss", 0)
        if "on_at" in c and not st["v"] and misses > c["misses"]:
            return True, (f"switched on at {c['on_at']:.0f} min, nobody came, switched off at {m:.0f} min "
                          f"and recorded as a wrong prediction")
        if m > 45:
            return False, ("never switched on (gate refused?)" if "on_at" not in c else "never switched off")

    def setup_guard_hit(self, c):
        self._guard_setup(c)

    def check_guard_hit(self, c, m):
        st = self.h.devices[c["dev"]]
        if st["v"] and st.get("src") == "ai" and "on_at" not in c:
            c["on_at"] = m
            c["step"] = "pre-cooling on — you are on your way"
        if "on_at" in c and "walked" not in c and self.now() >= c["target"] - 120:
            c["walked"] = m
            self.h.person.take_over(c["room"])
            c["step"] = "you walked in"
        if "walked" in c and m >= c["walked"] + 2:
            hits = self.ai.state.get("stats", {}).get("ai_hit", 0)
            ok = st["v"] == 1 and hits > c["hits"]
            return ok, ("on before you arrived, still on, counted as a correct prediction" if ok
                        else f"device {'off' if not st['v'] else 'on'}, hit counted: {hits > c['hits']}")
        if m > 40:
            return False, "never switched on"

    # ------------------------------------------------------------------ 5. lights never early
    def setup_light_waits(self, c):
        spec = self.pick("light")
        if not spec:
            return self.done(False, "no AI light in a room with a radar")
        c["dev"], c["room"] = spec.id, spec.room
        self.clear_pause(spec.id)
        self.clock.jump_to("21:00")                                 # dark outside
        c["t0"] = self.now()
        if self.h.devices[spec.id]["v"]:
            self.set_dev(spec.id, 0)
        self.h.person.take_over(self.other_room(spec.room))
        self.ai.inject_plan(spec.id, self.now() + 5 * 60, 0.9, "Scenario: the AI expects you there in 5 min")
        c["step"] = "the AI expects you in the room — you are elsewhere"

    def check_light_waits(self, c, m):
        st = self.h.devices[c["dev"]]
        if c["stage"] == 0:
            if st["v"]:
                return False, f"light switched on at {m:.1f} min while nobody was in the room"
            if m >= 12:
                c["stage"], c["entered"] = 1, m
                self.h.person.take_over(c["room"])
                c["step"] = "you walk in"
        elif st["v"]:
            return True, f"stayed off for 12 min while the room was empty, on {m - c['entered']:.1f} min after you walked in ({st.get('src')})"
        elif m - c["entered"] > 3:
            return False, "did not switch on after you walked in"

    # ------------------------------------------------------------------ 6. suggestion withdrawn
    def setup_suggestion_expiry(self, c):
        spec = self.pick("thermal")
        if not spec:
            return self.done(False, "no AI fan in a room with a radar")
        c["dev"] = spec.id
        self.clear_pause(spec.id)
        if self.h.devices[spec.id]["v"]:
            self.set_dev(spec.id, 0)
        self.h.person.take_over(spec.room, "Relaxing")
        self.h.temp_delta += max(0.0, (spec.temp_on or 28) + 2 - (self.h.rooms.get(spec.room, {}).get("temp") or 25))
        self.h.update_sensors()
        c["sid"] = self.ai.force_suggestion(spec.id, "suggest_on", 0.65, self.h.house_snapshot())
        c["step"] = "the AI asks: turn the fan on?" if c["sid"] else "the AI did not ask"

    def check_suggestion_expiry(self, c, m):
        if not c.get("sid"):
            return False, "the AI refused to ask (sensors disagreed)"
        if c["stage"] == 0 and m >= 1:
            c["stage"], c["left"] = 1, m
            self.h.person.take_over(OUT)
            c["step"] = "you left home"
        if c["stage"] == 1:
            if c["sid"] not in self.ai.suggestions:
                return True, f"question withdrawn {m - c['left']:.1f} min after you left (the room changed)"
            if m - c["left"] > 5:
                return False, "question still open 5 min after you left"

    # ------------------------------------------------------------------ 7. manual override
    def setup_manual_override(self, c):
        spec = self.pick("thermal") or self.pick("light")
        if not spec:
            return self.done(False, "no AI device")
        c["dev"] = spec.id
        self.clear_pause(spec.id)
        self.h.person.take_over(spec.room)
        self.h.press_button(spec.id)
        c["step"] = "you pressed the wall button"

    def check_manual_override(self, c, m):
        house = self.h.house_snapshot()
        if m >= 1 and c["stage"] == 0:
            c["stage"] = 1
            self.ai.plan(house, self.now())
            d = next((x for x in self.ai.last_decisions if x["device"] == c["dev"]), {})
            c["action"] = d.get("action")
            if c["action"] != "paused_by_override":
                return False, f"the AI still planned '{c['action']}'"
            self.clock.forward(2 * 3600 + 60)                         # 2 hours later
            c["step"] = "two hours later"
        elif c["stage"] == 1:
            p = self.ai.paused(c["dev"], house, self.now())
            return (not p, "AI left it alone for 2 h after the button, then took part again" if not p
                    else "still paused after 2 h")

    # ------------------------------------------------------------------ 8. power fault
    def setup_power_fault(self, c):
        spec = self.pick("thermal")
        if not spec:
            return self.done(False, "no AI fan")
        c["dev"] = spec.id
        if not self.ai.detector.baselines.get(spec.id):
            return self.done(False, "no learned power baseline for this device yet (train first)")
        self.clear_pause(spec.id)
        self.h.person.take_over(spec.room)
        # a hot room, so the "cool -> fan off" rule keeps it running while we watch the power
        self.h.temp_delta += max(0.0, (spec.temp_on or 28) + 3 - (self.h.rooms.get(spec.room, {}).get("temp") or 25))
        self.h.faults[spec.id] = "high"
        self.set_dev(spec.id, 1)
        c["n"] = len(self.ai.anomalies)
        c["step"] = "the motor is blocked: 2x power"

    def check_power_fault(self, c, m):
        if not self.h.devices[c["dev"]]["v"] and m < 1:
            self.set_dev(c["dev"], 1)
        new = self.ai.anomalies[c["n"]:]
        hit = [a for a in new if a["device"] == c["dev"] and a["kind"] == "high"]
        if hit:
            return True, f"alert after {m:.1f} min: {hit[0].get('title')}"
        if m > 15:
            return False, "no alert after 15 min"

    # ------------------------------------------------------------------ 9. a whole day
    def setup_full_day(self, c):
        self.h.person.habits = True
        self.h.person.give_back()
        self.clock.jump_to("06:00")
        c["t0"] = self.now()
        st = self.ai.state.get("stats", {})
        c["before"] = dict(st)
        c["counts"] = dict(self.h.counts)
        c["step"] = "running 24 hours of the routine"

    def check_full_day(self, c, m):
        if m < 24 * 60:
            c["step"] = f"hour {int(m // 60)} of 24"
            return None
        st = self.ai.state.get("stats", {})
        d = {k: round(st.get(k, 0) - c["before"].get(k, 0), 2) for k in
             ("ai_on", "ai_hit", "ai_miss", "smart_off", "suggested", "accepted", "dismissed", "expired", "skipped",
              "saved_wh", "missed_wh")}
        n = {k: self.h.counts.get(k, 0) - c["counts"].get(k, 0) for k in
             ("rule_on", "rule_off", "manual_on", "manual_off", "ai_on", "ai_off")}
        return True, (f"AI: switched on {d['ai_on']} (came {d['ai_hit']}, missed {d['ai_miss']}), skipped by sensors "
                      f"{d['skipped']}, smart off {d['smart_off']} (~{d['saved_wh']} Wh saved, {d['missed_wh']} Wh "
                      f"used by misses), asked {d['suggested']} (expired {d['expired']}). Rules: on {n['rule_on']}, "
                      f"off {n['rule_off']}. By hand: on {n['manual_on']}, off {n['manual_off']}.")
