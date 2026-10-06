"""AIRuntime — the AI as a running part of the house.

The automation service on the Pi (and the simulator) owns one AIRuntime and calls:

    plan(house)             every 15 min  -> predictions, /ai_schedule, suggestions
    tick(house, dt)         every few s   -> list of actions to execute (switch on / off)
    on_manual(device)                     the user touched a device (app or wall button)
    on_answer(id, sugg, accepted)         the user answered a suggestion
    retrain()               nightly 03:00 -> train.py, then /ai_insights

The runtime never talks to an ESP32 itself: it returns actions, the caller executes them through its
normal path (MQTT on the Pi, set_device in the simulator) so every change is logged the same way.

Decision pipeline (agreed):  predict -> sensor gate -> waste guard -> smart off.
Priority: manual > safety > rules > AI.

`house` is a dict:
    rooms   {rid: {occ, mmwave?, pir?, temp?, lux?}}
    devices {id: {v, level?, watts?, src?}}
    paused  {id: until_ms}           (/ai_pause from the app)
    door_entry_at  unix seconds of the last front-door entry (or None)
    away    True while the Away scene is on
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from . import anomaly, energy, explain, gate, policy, presence, store, train
from . import settings as S
from .features import CORE, build_features, load_slots, local_now
from .spec import ai_devices, device_label, energy_devices, room_presence_keys, visible_rooms


def local(ts):
    """unix seconds -> home wall-clock time (Asia/Hebron), whatever the machine's own time zone is."""
    return pd.Timestamp(ts, unit="s", tz="UTC").tz_convert(S.TZ)


def hhmm(ts):
    return local(ts).strftime("%H:%M")


def iso(ts):
    return local(ts).strftime("%Y-%m-%dT%H:%M")


class AIRuntime:
    def __init__(self, config, con, sink, model_dir=None, speed=1, record=False, clock=time.time):
        self.con, self.sink, self.speed, self.clock = con, sink, max(1, speed), clock
        self.model_dir = model_dir or S.MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.recorder = store.Recorder(con) if record else None
        self.pending = {}         # device -> scheduled switch-on waiting for the gate
        self.guards = {}          # device -> waste guard after an AI switch-on
        self.armed = {}           # device -> True while schedule_on / keep_on (hysteresis)
        self.last_p = {}          # device -> latest probability
        self.override_at = {}     # device -> ts of the last manual control
        self.suggestions = {}     # suggestion id -> info (open suggestions created by us)
        self.snooze = {}          # device -> no new suggestion before this ts
        self.vacant_since = {}    # room -> ts
        self.house_rooms = {}     # latest room readings (for borrowed temperature / light)
        self.ledger = energy.WasteLedger()
        self.anomalies = []
        self.last_flush = 0
        self.last_waste = None
        self.state = train.load_json(self.model_dir / "state.json",
                                     {"offsets": {}, "stats": {}})
        self.reload(config)

    # ------------------------------------------------------------------ setup
    def reload(self, config):
        self.config = config
        self.th = config.get("thresholds") or {}
        self.specs = ai_devices(config)
        self.labels = {d: device_label(config, d) for r in config["rooms"].values() for d in (r.get("devices") or {})}
        self.energy_devs = energy_devices(config)
        self.room_keys = {rid: room_presence_keys(rid, room) for rid, room in config["rooms"].items()}
        self.bundles = {d: self.safe_load(d) for d in self.specs}
        self.report = train.load_json(self.model_dir / "report.json", {})
        old = getattr(self, "detector", None)
        self.detector = anomaly.Detector(train.load_json(self.model_dir / "baselines.json", {}))
        if old:                                   # keep the "held for 3 min / once per 6 h" memory
            self.detector.since, self.detector.alerted = old.since, old.alerted
        p = self.safe_load("presence")
        self.presence_model = p["model"] if p else None
        for d in list(self.pending) + list(self.guards):
            if d not in self.specs:
                self.pending.pop(d, None)
                self.guards.pop(d, None)

    def safe_load(self, name):
        """A model file that can't be read (corrupt, or saved by another scikit-learn version) counts as
        missing: the device shows 'learning' until the next training instead of crashing the service."""
        try:
            bundle = train.load_bundle(self.model_dir, name)
        except Exception as e:  # noqa: BLE001 — any unpickling problem
            print(f"AI: could not load model {name} ({e}); it will be retrained")
            return None
        if bundle and bundle.get("sklearn") != train.sklearn.__version__:
            print(f"AI: model {name} was trained with scikit-learn {bundle.get('sklearn')}; it will be retrained")
            return None
        return bundle

    def minutes(self, m):
        return m * 60 / self.speed

    def env(self, spec, rooms):
        """temperature / light for a device, borrowed from another room when its own room has no sensor."""
        temp = gate.value(rooms.get(spec.temp.split("/")[0]), "temp") if spec.temp else None
        lux = gate.value(rooms.get(spec.lux.split("/")[0]), "lux") if spec.lux else None
        return temp, lux

    def thresholds(self, spec, hour, drift=False):
        anchor = self.act_anchor()
        gap = anchor - float(self.th.get("ai_suggest_at", anchor - S.SUGGEST_GAP))
        return energy.thresholds(spec, anchor, self.state["offsets"], hour, drift=drift, gap=max(0.05, gap))

    def save_state(self):
        try:
            (self.model_dir / "state.json").write_text(json.dumps(self.state, indent=2))
        except OSError:
            pass

    def stat(self, key, n=1):
        st = self.state.setdefault("stats", {})
        st[key] = st.get(key, 0) + n

    def act_anchor(self):
        return float(self.th.get("ai_act_at", S.ACT_AT))

    def override_pause_s(self):
        return self.minutes(self.th.get("override_pause_min", S.OVERRIDE_PAUSE_MIN))

    def paused(self, dev, house, now):
        if now - self.override_at.get(dev, -1e18) < self.override_pause_s():
            return "override"
        if int((house.get("paused") or {}).get(dev) or 0) > now * 1000:
            return "user"
        return None

    def someone_home(self, house, now):
        rooms = house.get("rooms") or {}
        if any(gate.room_occupied(rooms.get(r)) for r in visible_rooms(self.config)):
            return True
        entry = house.get("door_entry_at")
        return bool(entry and now - entry < self.minutes(S.HOME_RECENT_ENTRY_MIN))

    # ------------------------------------------------------------------ training
    def bootstrap_if_needed(self, now=None, quiet=False):
        """A fresh hub has no history: fill simulated history (clearly labelled 'simulated') and train."""
        from . import synth
        now = now or self.clock()
        if self.con.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0:   # never wipe real readings
            if not quiet:
                print("AI: empty history — generating 10 weeks of simulated history (labelled 'simulated')")
            synth.fill(self.con, self.config, days=S.WINDOW_DAYS + 14, end_ts=now - 300, replace=False)
        trained = self.report.get("trained_at", 0) / 1000
        devices_changed = set(self.report.get("devices", {})) != set(self.specs)
        missing = any(self.bundles.get(d) is None and (self.report.get("devices", {}).get(d) or {}).get("status")
                      in ("ready", "relearning") for d in self.specs)
        if not self.report or devices_changed or missing or now - trained > 86400:
            self.retrain(now, quiet=quiet)
        else:
            self.publish_insights(now)

    def retrain(self, now=None, quiet=False):
        now = now or self.clock()
        t0 = time.time()
        self.report = train.train_all(self.config, self.con, self.model_dir, now_ts=now, labels=self.labels, quiet=quiet)
        self.report["train_seconds"] = round(time.time() - t0, 1)
        self.reload(self.config)
        self.publish_insights(now)
        return self.report

    # ------------------------------------------------------------------ insights (/ai_insights)
    def publish_insights(self, now=None):
        now = now or self.clock()
        r = self.report or {}
        doc = {"status": r.get("status", "learning"), "model": "Gradient Boosting",
               "data_source": r.get("data_source", "real"), "learned": r.get("learned", []),
               "devices": {}, "presence": r.get("presence", {}), "wear": r.get("wear", {}),
               "anomalies": self.anomalies[-10:], "energy": self.ledger.totals(), "stats": self.state.get("stats", {})}
        if r.get("summary"):
            s = r["summary"]
            doc["metrics"] = {"within_15": s["within_15"], "exact": s["exact"], "f1": s["f1"],
                              "baseline_f1": s["baseline_f1"], "retrained_at": r.get("trained_at"),
                              "model": "Gradient Boosting", "devices": len(self.specs),
                              "data_source": r.get("data_source", "real")}
        hour = local_now(now).hour
        for dev, spec in self.specs.items():
            m = (r.get("devices") or {}).get(dev, {"status": "learning", "days": 0})
            th = self.thresholds(spec, hour, drift=bool((self.bundles.get(dev) or {}).get("drift")))
            doc["devices"][dev] = {k: m.get(k) for k in ("status", "days", "f1", "baseline_f1", "within_15", "exact",
                                                          "brier", "importance", "calibration") if m.get(k) is not None}
            doc["devices"][dev].update(act=th["act"], suggest=th["suggest"], watts=spec.watts, kind=spec.kind)
        self.sink.write_insights(doc)
        return doc

    # ------------------------------------------------------------------ plan (every 15 minutes)
    def plan(self, house, now=None):
        now = now or self.clock()
        self.house_rooms = house.get("rooms") or {}
        if self.recorder:
            self.record(house, now)
        slots = load_slots(self.con, since_ts=now - 8 * 86400 - 3600, until_ts=now)
        hour = local_now(now).hour
        target = now + self.minutes(S.HORIZON_MIN)
        decisions = []
        for dev, spec in self.specs.items():
            d = self.plan_device(dev, spec, slots, house, now, hour, target)
            decisions.append(d)
        self.sink.write_schedule(decisions)
        self.plan_presence(slots, house, now)
        self.save_state()
        return decisions

    def plan_device(self, dev, spec, slots, house, now, hour, target):
        label = self.labels.get(dev, dev)
        st_now = int(((house.get("devices") or {}).get(dev) or {}).get("v") or 0)
        bundle = self.bundles.get(dev)
        base = {"device": dev, "action": "learning", "p_on": 0.0, "status": "learning",
                "predicted_for": iso(target), "time": hhmm(target)}
        if not bundle or spec.state not in slots:
            days = ((self.report.get("devices") or {}).get(dev) or {}).get("days", 0)
            base.update(why=f"Learning your routine ({days} of {S.MIN_DAYS} days)")
            return base
        X, _ = build_features(slots, spec)
        X["now"] = X["now"].fillna(st_now)
        for f in bundle["features"]:
            if f not in X:
                X[f] = np.nan                       # a feature that disappeared (device removed) -> NaN
        x = X[bundle["features"]].iloc[[-1]].copy()
        x["now"] = st_now                          # live state wins over the slot average
        if x[CORE].isna().any(axis=None):
            base.update(why="Not enough recent data")
            return base
        p = float(bundle["model"].predict_proba(x)[0, 1])
        self.last_p[dev] = p
        drift = bool(bundle.get("drift"))
        th = self.thresholds(spec, hour, drift=drift)
        paused = self.paused(dev, house, now)
        action = policy.decide(p, st_now, th, armed=self.armed.get(dev, False), paused=paused)
        contrib = explain.local_contributions(bundle["model"], x, bundle.get("typical", {}))
        why = explain.why_text(spec, x, contrib, p, action, self.labels)
        d = dict(base, action=action, p_on=round(p, 2), status="relearning" if drift else "ready",
                 act_at=th["act"], suggest_at=th["suggest"], why=why)
        self.armed[dev] = action in policy.ON_ACTIONS
        room = (house.get("rooms") or {}).get(spec.room) or {}
        home = self.someone_home(house, now)
        if action == "schedule_on":
            level = self.preferred_level(spec, bundle, hour)
            old = self.pending.get(dev)
            if old:                        # already planned: keep the original time (re-planning must not
                target = old["target"]     # push it 15 min later every round)
            if spec.kind == "light":       # never pre-switched: armed window, switches on when you walk in
                start, deadline = target - self.minutes(S.LIGHT_WINDOW_MIN[0]), target + self.minutes(S.LIGHT_WINDOW_MIN[1])
                start = min(start, now)
                d.update(title=f"{label} on when you come in", time=hhmm(max(now, start)))
            else:
                start = target - self.minutes(spec.lead_min)
                deadline = target + self.minutes(spec.grace_min)
                d.update(title=f"{label} turns on", time=hhmm(max(now, start)), execute_at=iso(max(now, start)))
                if spec.lead_min:
                    d["why"] = f"Pre-{'heating' if spec.heating else 'cooling'} · " + why[:1].lower() + why[1:]
            if old:
                start, deadline = min(start, old["start"]), max(deadline, old["deadline"])
            d["predicted_for"] = iso(target)
            self.pending[dev] = dict(start=start, target=target, deadline=deadline, p=p, why=d["why"], level=level,
                                     title=d["title"])
        elif action == "keep_on":
            d.update(title=f"{label} stays on")
        elif action in ("suggest_on", "suggest_off"):
            self.pending.pop(dev, None)
            self.maybe_suggest(dev, spec, action, p, d["why"], room, home, now)
        else:
            self.pending.pop(dev, None)
        if paused:
            d["why"] = ("You changed it by hand — the AI leaves it alone for "
                        f"{int(self.th.get('override_pause_min', S.OVERRIDE_PAUSE_MIN) / 60)} h") if paused == "override" \
                else "Paused from the app"
        return d

    def preferred_level(self, spec, bundle, hour):
        if not spec.levels:
            return None
        lv = (bundle or {}).get("levels", {}).get(str(hour // S.ADAPT_BUCKET_H))
        return lv or spec.levels[len(spec.levels) // 2]

    def maybe_suggest(self, dev, spec, action, p, why, room, home, now):
        if any(s["device"] == dev for s in self.suggestions.values()) or now < self.snooze.get(dev, 0):
            return
        if action == "suggest_on":
            temp, lux = self.env(spec, self.house_rooms)
            ok, _ = gate.check_on(spec, room, home, self.th.get("light_on_lux", 150), temp, lux)
            if not ok:                      # never suggest something the sensors already contradict
                return
        elif spec.kind == "light" and gate.room_occupied(room):
            return                          # don't suggest switching the light off on someone sitting there
        label = self.labels.get(dev, dev).lower()
        on = action == "suggest_on"
        conf = p if on else 1 - p
        expires = now + self.minutes(S.SUGGESTION_TTL_MIN)
        title = f"Turn {'on' if on else 'off'} the {label}?"
        sid = self.sink.push_suggestion(dev, "on" if on else "off", conf, title, why, int(expires * 1000))
        if sid:
            self.suggestions[sid] = dict(device=dev, action="on" if on else "off", at=now, expires=expires,
                                         occ=bool(gate.room_occupied(room)), title=title, conf=conf)
            self.stat("suggested")

    def plan_presence(self, slots, house, now):
        if self.presence_model is None or slots.empty:
            return
        doors = train.door_slots(self.con, now - 8 * 86400)
        X, _ = presence.build(slots, doors)
        x = X.iloc[[-1]].copy()
        x["home"] = int(self.someone_home(house, now))
        if x[["lag1", "yday"]].isna().any(axis=None):
            return
        p = float(self.presence_model.predict_proba(x)[0, 1])
        self.sink.patch_insights({"presence_now": {"home": bool(x["home"].iloc[0]), "p_home_60": round(p, 2),
                                                   "at": int(now * 1000)}})

    # ------------------------------------------------------------------ tick (every few seconds)
    def tick(self, house, now=None, dt=None):
        now = now or self.clock()
        dt = dt if dt is not None else 0
        if self.recorder:
            self.record(house, now)
        rooms = house.get("rooms") or {}
        self.house_rooms = rooms
        devices = house.get("devices") or {}
        home = self.someone_home(house, now)
        lux_limit = self.th.get("light_on_lux", 150)
        actions = []
        for rid, room in rooms.items():
            if gate.room_vacant(room, self.room_keys.get(rid)):
                self.vacant_since.setdefault(rid, now)
            else:
                self.vacant_since.pop(rid, None)

        # 1. scheduled switch-ons waiting for the sensor gate
        for dev in list(self.pending):
            pend, spec = self.pending[dev], self.specs.get(dev)
            st = devices.get(dev) or {}
            if spec is None or st.get("v"):
                self.pending.pop(dev)                       # already on (you, a rule, or the wall button)
                continue
            if self.paused(dev, house, now):
                self.pending.pop(dev)
                continue
            if now < pend["start"]:
                continue
            room = rooms.get(spec.room) or {}
            ok, reason = gate.check_on(spec, room, home, lux_limit, *self.env(spec, rooms))
            pend["reason"] = reason
            if ok:
                self.pending.pop(dev)
                pct = round(pend["p"] * 100)
                actions.append(dict(device=dev, v=1, level=pend["level"], confidence=round(pend["p"], 2),
                                    src_label=f"AI · {pct}%", short=self.short_on(spec),
                                    why=f"{pend['why']} · checked: {reason}"))
                target = max(pend["target"], now)
                self.guards[dev] = dict(on_at=now, deadline=target + self.minutes(spec.grace_min), p=pend["p"])
                self.stat("ai_on")
            elif now > pend["deadline"]:
                self.pending.pop(dev)
                self.stat("skipped")
                self.sink.log("ai", "ai", f"{self.labels.get(dev, dev)}: planned, not switched on", device=dev,
                              room=spec.room, short=f"Skipped: {reason}",
                              why=f"Predicted ({round(pend['p'] * 100)}%) but the sensors said: {reason}",
                              change="No change", confidence=round(pend["p"], 2), src_label="AI", tags=["ai"])

        # 2. waste guard: the AI switched it on — did anyone come?
        for dev in list(self.guards):
            g, spec = self.guards[dev], self.specs.get(dev)
            st = devices.get(dev) or {}
            if g["on_at"] == now:
                continue                                    # just switched on in this tick
            if spec is None or not st.get("v") or st.get("src") not in ("ai", None):
                self.guards.pop(dev)                        # you or a rule took over
                continue
            room = rooms.get(spec.room) or {}
            if gate.room_occupied(room):
                self.guards.pop(dev)
                self.stat("ai_hit")
                continue
            if now > g["deadline"]:
                self.guards.pop(dev)
                self.stat("ai_miss")
                waited = max(1, round((now - g["on_at"]) * self.speed / 60))      # minutes in house time
                wasted = round(self.watts(st, spec) * (now - g["on_at"]) / 3600, 2)  # real energy
                store.write(self.con, [(int(g["on_at"]), f"ai_miss/{dev}", 1), (int(now), f"ai_miss/{dev}", 0)])
                actions.append(dict(device=dev, v=0, confidence=round(g["p"], 2), src_label="AI · guard",
                                    short="Prediction missed — nobody came",
                                    why=f"Switched on for you {waited} min ago but nobody came · "
                                        f"{wasted} Wh used · the AI learns from this",
                                    title=f"{self.labels.get(dev, dev)} turned off (prediction missed)"))

        # 3. smart off: vacancy confirmed by every presence sensor in the room
        acted = {a["device"] for a in actions}
        for dev, spec in self.specs.items():
            st = devices.get(dev) or {}
            if not st.get("v") or dev in self.guards or dev in acted or self.paused(dev, house, now):
                continue
            since = self.vacant_since.get(spec.room)
            if since is None:
                continue
            factor = 2.0 if self.armed.get(dev) else (0.5 if self.last_p.get(dev, 0.5) <= S.SUGGEST_OFF_AT else 1.0)
            wait_min = max(2.0, spec.off_after_min * factor)
            if now - since >= self.minutes(wait_min):
                saved = energy.saved_estimate(self.watts(st, spec))
                self.ledger.record_saved("ai", saved)
                self.stat("smart_off")
                self.armed[dev] = False
                note = (" (waited longer: you usually come back)" if factor > 1 else
                        " (sooner: you usually don't need it now)" if factor < 1 else "")
                actions.append(dict(device=dev, v=0, src_label="AI · empty room", saved_wh=saved,
                                    short=f"Room empty {round(wait_min)} min · saves ~{saved} Wh",
                                    why=f"Radar and motion sensor both saw nobody for {round(wait_min)} min{note} · "
                                        f"~{saved} Wh saved over the next hour (estimate)",
                                    title=f"{self.labels.get(dev, dev)} turned off — room empty"))

        # 4. suggestions expire after 15 min or when the situation changed
        for sid in list(self.suggestions):
            sg = self.suggestions[sid]
            dev, spec = sg["device"], self.specs.get(sg["device"])
            st = devices.get(dev) or {}
            room = rooms.get(spec.room) if spec else {}
            reason = None
            if spec is not None and bool(st.get("v")) == (sg["action"] == "on"):
                # already done — usually because you said Yes (the answer is read separately), or by hand
                self.suggestions.pop(sid)
                self.sink.expire_suggestion(sid, dev, sg["title"], "already done", quiet=True)
                continue
            if spec is None:
                reason = "device removed"
            elif now > sg["expires"]:
                reason = f"no answer in {S.SUGGESTION_TTL_MIN} min"
            elif sg["action"] == "on":
                ok, why_not = gate.check_on(spec, room or {}, home, lux_limit, *self.env(spec, rooms))
                if not ok:
                    reason = f"the room changed ({why_not})"
            elif sg["action"] == "off" and spec.kind == "light" and gate.room_occupied(room) and not sg["occ"]:
                reason = "someone came back"
            if reason:
                self.suggestions.pop(sid)
                if self.sink.expire_suggestion(sid, dev, sg["title"], reason) is not False:
                    self.stat("expired")

        # 5. energy faults + 6. waste ledger
        day = local_now(now).strftime("%Y-%m-%d")
        ledger_in = {}
        for dev, (rid, cfg) in self.energy_devs.items():
            st = devices.get(dev) or {}
            watts = st.get("watts")
            if watts is not None:
                a = self.detector.check(now, dev, st.get("v"), watts, st.get("level"), self.speed)
                if a:
                    self.report_anomaly(a, cfg, st.get("level"))
            ledger_in[dev] = dict(v=st.get("v"), watts=watts or 0, must_run=bool((cfg.get("rules") or {}).get("alert_if_off_min")),
                                  room_empty=gate.room_vacant(rooms.get(rid) or {}, self.room_keys.get(rid)))
        self.ledger.step(day, dt, ledger_in, lambda d: self.waste_cause(d, devices, house, now))
        if now - self.last_flush >= max(30.0, self.minutes(5)):      # Firebase: at most every 5 min, only changes
            self.last_flush = now
            snap = self.ledger.snapshot()
            if snap != self.last_waste:
                self.last_waste = snap
                self.sink.write_waste(day, snap, self.ledger.totals())
        return actions

    @staticmethod
    def watts(st, spec):
        try:
            w = float(st.get("watts") if st.get("watts") is not None else spec.watts)
        except (TypeError, ValueError):
            w = spec.watts
        return w if np.isfinite(w) else spec.watts

    def short_on(self, spec):
        return {"light": "You came in and it is dark", "thermal": "Pre-cooling before you need it"}.get(
            spec.kind, "Your usual time")

    def waste_cause(self, dev, devices, house, now):
        st = devices.get(dev) or {}
        if dev in self.guards or st.get("src") == "ai":
            return "ai"
        cfg = self.energy_devs.get(dev, (None, {}))[1]
        rules_off = (cfg.get("rules") or {}).get("off_when_empty") and (cfg.get("control") or {}).get("rules")
        ai_off = dev in self.specs and not self.paused(dev, house, now)
        return "rule_delay" if (rules_off or ai_off) else "forgotten"

    def report_anomaly(self, a, cfg, level=None):
        dev = a["device"]
        label = self.labels.get(dev, dev)
        lv = (cfg.get("caps") or {}).get("level") or {}
        lv_label = None
        if lv and level is not None and a["kind"] != "standby":
            i = min(range(len(lv["steps"])), key=lambda k: abs(lv["steps"][k] - level))
            lv_label = (lv.get("labels") or [])[i] if i < len(lv.get("labels") or []) else f"{lv['steps'][i]}%"
        title, line = anomaly.describe(a, label, lv_label)
        a.update(title=title, text=line)
        self.anomalies.append(a)
        self.anomalies = self.anomalies[-20:]
        self.stat("anomalies")
        self.sink.alert("warning", title, line, device=dev)
        self.sink.patch_insights({"anomalies": self.anomalies[-10:]})

    # ------------------------------------------------------------------ feedback from the user
    def on_manual(self, dev, now=None):
        now = now or self.clock()
        self.override_at[dev] = now
        self.pending.pop(dev, None)
        self.armed[dev] = False
        if self.guards.pop(dev, None):
            self.stat("ai_corrected")
        store.write(self.con, [(int(now), f"override/{dev}", 1)])

    def on_answer(self, sid, suggestion, accepted, now=None):
        now = now or self.clock()
        info = self.suggestions.pop(sid, None) or {}
        dev = suggestion.get("device") or info.get("device")
        if not dev:
            return
        hour = local_now(info.get("at", now)).hour
        new = energy.adapt(self.state["offsets"], dev, hour, accepted)
        self.stat("accepted" if accepted else "dismissed")
        if not accepted:
            self.snooze[dev] = now + self.minutes(60)
        self.save_state()
        return new

    # ------------------------------------------------------------------ readings (simulator only; on the Pi the ingest service does this)
    def record(self, house, now):
        vals = {}
        for rid, r in (house.get("rooms") or {}).items():
            for k in ("temp", "lux", "occ", "mmwave", "pir"):
                if r.get(k) is not None:
                    vals[f"{rid}/{k}"] = r[k]
        for dev, st in (house.get("devices") or {}).items():
            vals[f"device/{dev}"] = int(st.get("v") or 0)
            if st.get("level") is not None:
                vals[f"level/{dev}"] = st["level"]
            if st.get("watts") is not None and dev in self.energy_devs:
                vals[f"power/{dev}"] = st["watts"]
        vals["mode/away"] = int(bool(house.get("away")))
        self.recorder.observe(vals, now)

    def summary(self):
        s = self.state.get("stats", {})
        answered = s.get("accepted", 0) + s.get("dismissed", 0)
        ons = s.get("ai_hit", 0) + s.get("ai_miss", 0)
        return {**s, "acceptance": round(s.get("accepted", 0) / answered, 2) if answered else None,
                "hit_rate": round(s.get("ai_hit", 0) / ons, 2) if ons else None, **self.ledger.totals()}
