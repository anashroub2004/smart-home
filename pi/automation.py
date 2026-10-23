"""
Automation — the Pi's "brain" (stage 1d). Decides by itself: the per-device rules from /config (1d-1) and
the AI (1d-2): the real ai.runtime.AIRuntime that the simulator and the digital twin run — plan every 15 min,
tick every 5 s, questions in the app, retraining every night at 03:00.

It sees the house the same way mqtt_ingest does — it listens to the nodes itself (home/#) and builds the
picture with the same code (house_state.py) — so it keeps deciding without the internet and without the
other services. It switches through MQTT like commands.py (mqtt_exec.MqttExecutor, src "rule" / "ai") and
writes to the History why it did it. Firebase is for the app only: if it is unreachable, decisions go on.

The rules (the same as the simulator's apply_rules, docs/contract.md "rules"):
    off_when_empty   off when the room has been empty for thresholds.empty_room_off_min
                     (waits while the AI guards a pre-cooling or counts its own smart off)
    on_when_dark     on when someone has just come in (90 s) and the room is below thresholds.light_on_lux
                     (only for lights the AI does not decide yet: ai.handles_entry)
    follow_temp      on above thresholds.fan_on_temp while someone is in the room, off below fan_off_temp
Manual control (a wall button or the app: state src "button" / "web") = hands off that device for
thresholds.override_pause_min (2 h) or until the room is empty, for the rules and the AI (ai.on_manual).
The app's pause switch (/ai_pause) too.

The AI learns from the SQLite file mqtt_ingest writes (ai/data/home.db) and keeps its models in
ai/models/home (its own "notebook", apart from the twin's). A hub with less than 3 weeks of readings gets
the simulated person's routine (ai/personas) as history BEFORE its first real reading (labelled
"simulated"), so the AI decides from the first minute (Iyad, 2026-10-23: a model house, no real resident).

Run (Mosquitto + Firebase emulators + mqtt_ingest + fake nodes running):
    python pi/automation.py
    python pi/automation.py --train-now     # retrain at start instead of waiting for 03:00
    python pi/automation.py --no-ai         # the rules only
    python pi/automation.py --cloud KEY.json
"""
import argparse
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit('paho-mqtt is missing: pip install "paho-mqtt>=2.0,<3"')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import house_state  # noqa: E402
import house_words  # noqa: E402
from firebase_writer import Writer, now_ms  # noqa: E402
from mqtt_exec import MqttExecutor  # noqa: E402

TICK_S = 5.0              # rules + AI tick every 5 s
PLAN_S = 900.0            # the AI's plan for the next hour every 15 min ("Next hour" in the app)
NODE_TIMEOUT_S = 5.0      # like commands.py
ENTRY_WINDOW_S = 90       # "on when dark" only right after someone came in (a sleeper stays in the dark)
SLOW_S = 10.0             # /ai_pause, /home_state/scene and the answers to the AI's questions
CONFIG_EVERY_S = 30.0
AI_MAX_ERRORS = 5         # an AI that keeps failing is switched off for this run: the rules go on
MODELS = ROOT / "ai" / "models" / "home"
AI_LOG = ROOT / "ai" / "data" / "ai_errors.log"

PRINT_LOCK = threading.Lock()


def say(*parts):
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


def next_3am(now=None):
    t = datetime.fromtimestamp(now or time.time()).replace(hour=3, minute=0, second=0, microsecond=0)
    return (t if t.timestamp() > (now or time.time()) else t + timedelta(days=1)).timestamp()


class SafeSink:
    """The AI writes /ai_schedule, /suggestions, /ai_insights, /events and /alerts through this. Without the
    internet those writes fail: that must not count as an AI error (it would switch the AI off)."""

    def __init__(self, sink):
        self.sink = sink

    def __getattr__(self, name):
        fn = getattr(self.sink, name)

        def call(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                say("!! Firebase (AI):", name, type(e).__name__, e)
                return None
        return call


class Automation(house_state.Listener):
    def __init__(self, writer, config, args, clock=None):
        self.w, self.args = writer, args
        self.lock = threading.Lock()
        self.client = self.ex = None
        self.sim = clock                                        # hub_clock.SharedClock with --sim-clock
        self.clock = clock.now if clock else time.time          # the house's time (rules, AI, entry windows)
        self.house = house_state.HouseState(config, self, self.clock)
        self.entered = {}           # room -> True until the rules have looked at this entry once
        self.manual_at = {}         # device -> ts of the last manual control (button / app)
        self.override_until = {}    # device -> hands off until (manual control)
        self.manual_q = []          # (device, v) manual controls for the AI (it is called from the main loop only)
        self.ai_pause = {}          # device -> ms (the app's pause switch)
        self.scene = None
        self.door_entry_at = self.door_exit_at = None
        start = self.clock()        # room -> ts it became empty; nobody seen yet = empty from now on
        self.empty_since = {rid: start for rid in self.house.rooms}
        self.ai, self.ai_errors, self.ai_ready = None, 0, False
        self.next_train = next_3am(self.clock())

    @property
    def config(self):
        return self.house.config

    @property
    def th(self):
        return self.house.th

    def safe(self, fn, *a, **kw):
        """Firebase may be unreachable (no internet): the house keeps deciding, only the log is missing."""
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            say("!! Firebase:", type(e).__name__, e)
            return None

    # ------------------------------------------------------------------ the AI
    def start_ai(self):
        """Load the real AI. Training (or the first simulated history) runs in the background: the rules
        decide meanwhile."""
        try:
            from ai import store
            from ai.dispatch import FirebaseSink
            from ai.persona import Persona
            from ai.runtime import AIRuntime
        except ImportError as e:
            say(f"! AI disabled ({e}). Install it with:  pip install -r ai/requirements.txt")
            return
        con = store.connect(self.args.db)
        sink = SafeSink(FirebaseSink(self.w, room_of=lambda d: self.house.dev_room.get(d),
                                     room_name=lambda r: self.config["rooms"].get(r, {}).get("name", r)))
        models = MODELS.with_name("home_sim") if self.sim else MODELS   # fast-clock tests keep their own notebook
        self.ai = AIRuntime(self.config, con, sink, model_dir=models, speed=1, record=False,
                            clock=self.clock, persona=Persona.load(None))
        self.clean_suggestions()

        def boot():
            t0 = time.time()
            if self.sim:
                self.sim.wait_ready()
                self.sim.hold("automation")                     # the world's day waits while the AI gets ready
            try:
                self.ai_safe(self.ai.bootstrap_if_needed)
                if self.args.train_now:
                    self.ai_safe(self.ai.retrain)
            finally:
                if self.sim:
                    self.sim.hold("automation", False)
            if self.ai:
                self.next_train = next_3am(self.clock())
                self.ai_ready = True
                say(f"AI ready in {time.time() - t0:.0f} s · models in ai/models/{models.name} · "
                    f"{self.ai.report.get('data_source', '?')} data · next training {datetime.fromtimestamp(self.next_train):%a %H:%M}")
        threading.Thread(target=boot, daemon=True).start()
        say("AI starting (simulated history + training if needed) — the rules decide meanwhile")

    def ai_safe(self, fn, *a, **kw):
        """The AI is an extra layer: an error is logged (ai/data/ai_errors.log), never allowed to stop the house.
        After AI_MAX_ERRORS in a row it is switched off for this run."""
        if not self.ai:
            return None
        try:
            out = fn(*a, **kw)
            self.ai_errors = 0
            return out
        except Exception as e:  # noqa: BLE001
            self.ai_errors += 1
            try:
                AI_LOG.parent.mkdir(parents=True, exist_ok=True)
                with open(AI_LOG, "a", encoding="utf-8") as fh:
                    fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} automation.py\n")
                    fh.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
            except OSError:
                pass
            say(f"! AI error ({type(e).__name__}: {e})" + (" — AI switched off, the rules go on"
                                                          if self.ai_errors >= AI_MAX_ERRORS else ""))
            if self.ai_errors >= AI_MAX_ERRORS:
                self.ai = None
            return None

    def ai_on(self):
        return bool(self.ai) and self.ai_ready and not self.ai_safe(self.ai.training)

    def snapshot(self):
        """The house as the AI reads it (the same shape as the simulator's house_snapshot)."""
        return {"rooms": self.house.rooms, "devices": self.house.devices, "paused": self.ai_pause,
                "door_entry_at": self.door_entry_at, "door_exit_at": self.door_exit_at,
                "away": self.scene == "away"}

    def ai_plan(self):
        if self.ai_on():
            with self.lock:
                house = self.snapshot()
                self.ai_safe(self.ai.plan, house)

    def ai_tick(self, dt):
        if not self.ai_on():
            return
        with self.lock:
            manual, self.manual_q = self.manual_q, []
            for dev, v in manual:
                self.ai_safe(self.ai.on_manual, dev, v=v)
            actions = self.ai_safe(self.ai.tick, self.snapshot(), dt=dt) or []
        for a in actions:
            dev = a["device"]
            if dev not in self.house.dev_room or self.ex.busy(dev) \
                    or not self.house.nodes.get(self.house.node_of(dev), {}).get("online"):
                continue
            body = {"v": a["v"], "level": a.get("level") if a["v"] else None}
            self.switch(dev, self.house.dev_room[dev], body, a.get("short"), a.get("why"), a.get("saved_wh"),
                        src="ai", src_label=a.get("src_label", "AI"), title=a.get("title"),
                        confidence=a.get("confidence"))

    def ai_night(self):
        """Retraining at 03:00 in the background; the new models are installed when it is done."""
        if not (self.ai and self.ai_ready):
            return
        if self.ai_safe(self.ai.training):
            done = False
            try:
                if self.ai.finish_retrain() is not None:
                    done = True
                    say("AI retrained · " + str(self.ai.report.get("data_source", "?")) + " data")
                    self.clean_suggestions()
                    self.ai_plan()
            except Exception as e:  # noqa: BLE001 — keep the previous models
                done = True
                say(f"! AI retraining failed ({type(e).__name__}: {e}) — keeping the previous models")
            if done and self.sim:
                self.sim.hold("automation", False)
            return
        if self.clock() >= self.next_train:
            self.next_train = next_3am(self.clock())
            if self.sim:
                self.sim.hold("automation")                     # the world's night waits for the new models
            if not self.ai_safe(self.ai.start_retrain):
                self.ai_safe(self.ai.retrain, quiet=True)
                if self.sim:
                    self.sim.hold("automation", False)
            say("AI nightly retraining started")

    def handle_answers(self):
        """The app's Yes / No to the AI's questions -> the trust ladder (ai.on_answer). A Yes also arrives as a
        /commands entry (via "suggestion"), which commands.py executes."""
        if not self.ai_on():
            return
        for sid, s in (self.safe(self.w.get_suggestions) or {}).items():
            if not isinstance(s, dict) or not s.get("response") or s.get("handled"):
                continue
            accepted = s["response"] == "accept"
            with self.lock:
                self.ai_safe(self.ai.on_answer, sid, s, accepted)
            if not accepted:
                pct = round(s.get("confidence", 0) * 100)
                self.safe(self.w.log_event, "ai", "ai", f"Suggestion dismissed: {s.get('title', '').rstrip('?')}",
                          "web", f"AI · {pct}%", device=s.get("device"), node="hub", by_label="Owner (web app)",
                          short="The AI will learn from this (asks less at this time)", why=f"{s.get('why')}",
                          change="No change", tags=["ai", "manual"])
            self.safe(self.w.mark_suggestion, sid, {"handled": True, "done_text": (
                "Done." if accepted else "Okay. Your home will learn from this.")})

    def clean_suggestions(self):
        """Questions left open from an earlier run can't be tracked any more; answered ones older than a day go."""
        day_ago = now_ms() - 86400 * 1000
        for sid, s in (self.safe(self.w.get_suggestions) or {}).items():
            if isinstance(s, dict) and ((s.get("handled") and s.get("at", 0) < day_ago) or not s.get("response")):
                self.safe(self.w.delete_suggestion, sid)

    def ask_now(self, dev):
        """Test hook (--ask DEVICE): the AI asks about this device right now, through the normal path."""
        if not self.ai_on() or dev not in self.house.dev_room:
            return None
        with self.lock:
            return self.ai_safe(self.ai.force_suggestion, dev, "suggest_on", 0.65, self.snapshot())

    # ------------------------------------------------------------------ MQTT
    def connect(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-automation")
        c.on_connect = self.on_connect
        c.on_message = self.on_message
        self.client, self.ex = c, MqttExecutor(c, NODE_TIMEOUT_S)    # ready before the first message arrives
        c.connect(self.args.host, self.args.port, keepalive=30)
        c.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            say("!! MQTT connect failed:", reason_code)
            return
        if self.sim:                                            # the time first: the rules and the AI need it
            self.sim.attach(client, then=lambda: self.listen(client))
        else:
            self.listen(client)

    def listen(self, client):
        client.subscribe("home/#", qos=1)
        say("listening to home/# on", f"{self.args.host}:{self.args.port}"
            + (f" · shared clock x{self.sim.speed:g}" if self.sim else ""))

    def on_message(self, client, userdata, msg):
        topic, payload = msg.topic, msg.payload.decode("utf-8", "replace")
        try:
            with self.lock:
                self.house.handle(topic, payload)
        except (ValueError, TypeError, AttributeError) as e:
            say("!! bad message", topic, payload, f"({e})")

    # ------------------------------------------------------------------ what changed (house_state.Listener)
    def occ_changed(self, rid, occ):
        now = self.clock()
        if occ:
            self.empty_since.pop(rid, None)
            self.entered[rid] = True
        else:
            self.empty_since.setdefault(rid, now)
            self.entered.pop(rid, None)
            # you left: the hands-off after manual control ends with your visit
            for dev in (self.config["rooms"].get(rid, {}).get("devices") or {}):
                self.override_until.pop(dev, None)

    def device_changed(self, dev, before, src, changed):
        st = self.house.devices[dev]
        if changed and src in ("button", "web"):
            now = self.clock()
            self.manual_at[dev] = now
            self.override_until[dev] = now + float(self.th.get("override_pause_min", 120)) * 60
            self.manual_q.append((dev, st.get("v")))
            say(f"{dev}: manual control ({src}) — hands off for {self.th.get('override_pause_min', 120)} min")
        self.ex.answer(dev, dict(st, src=src))

    def door_event(self, ev):
        """For the AI's "someone is home": a door entry counts until someone goes out through the door."""
        if ev.get("ok") and ev.get("type") in ("fingerprint", "keypad"):
            self.door_entry_at = self.clock()
        elif ev.get("type") == "exit_button":
            self.door_exit_at = self.clock()

    # ------------------------------------------------------------------ pauses, scene
    def user_paused(self, dev):
        """The app's pause switch in the device sheet (/ai_pause/{device} = until ms)."""
        return time.time() * 1000 < int(self.ai_pause.get(dev) or 0)       # the app writes real time

    def paused(self, dev):
        return self.clock() < self.override_until.get(dev, 0) or self.user_paused(dev)

    def sync_slow(self):
        """/ai_pause and the scene (Away = nobody home) from Firebase. Without the internet: the last known."""
        scene = self.safe(self.w.db.get, "home_state/scene")
        if scene is not None:
            self.scene = scene
        new = self.safe(self.w.get_ai_pause)
        if new is None:
            return
        for dev in set(new) | set(self.ai_pause):
            if dev not in self.house.dev_room or new.get(dev) == self.ai_pause.get(dev):
                continue
            paused_now = bool(new.get(dev)) and int(new[dev]) > time.time() * 1000
            until = time.strftime("%H:%M", time.localtime(int(new[dev]) / 1000)) if paused_now else None
            self.safe(self.w.log_event, "config", "manual",
                      f"AI {'paused' if paused_now else 'resumed'} for {house_words.label(self.config, dev).lower()}",
                      "web", "App", device=dev, room=self.house.dev_room[dev], node="hub",
                      by_label="Owner (web app)", why="Switch in the device panel",
                      short=f"Until {until}" if paused_now else "AI can control it again",
                      change="Active → Paused" if paused_now else "Paused → Active", tags=["manual", "ai"])
        self.ai_pause = new

    # ------------------------------------------------------------------ the rules
    def apply_rules(self):
        """Per-device rules from /config (device.rules), only for devices with control.rules = true."""
        t, th, h = self.clock(), self.th, self.house
        ai = self.ai if self.ai_on() else None
        todo = []
        with self.lock:
            for rid, room in self.config["rooms"].items():
                s = h.rooms.get(rid)
                if s is None or not h.nodes.get(room.get("node"), {}).get("online"):
                    continue
                for dev, cfg in (room.get("devices") or {}).items():
                    caps, ctrl, r = cfg.get("caps") or {}, cfg.get("control") or {}, cfg.get("rules") or {}
                    if caps.get("power") != "write" or not ctrl.get("rules") or self.ex.busy(dev):
                        continue
                    st = h.devices[dev]
                    empty_min = float(th.get("empty_room_off_min", 10))
                    # counted from the later of "room empty" and your last manual control: switching a light on
                    # in an empty room (wall button, app) is not undone a second later
                    empty_for = t - max(self.empty_since.get(rid, t), self.manual_at.get(dev, 0))
                    lux = h.room_lux(rid)
                    # AI pre-cooling (its waste guard decides) or the AI's smart-off timer is running: the rule waits
                    guarded = bool(ai and (self.ai_safe(ai.guarding, dev) or self.ai_safe(ai.counting_off, dev)))
                    if r.get("off_when_empty") and st.get("v") and not s.get("occ") and not self.user_paused(dev) \
                            and not guarded and empty_for > empty_min * 60:
                        saved = round(float(st.get("watts") or cfg.get("watts", 0)), 2)  # over the next hour
                        if ai:
                            self.ai_safe(ai.ledger.record_saved, "rule", saved)
                        todo.append((dev, rid, {"v": 0}, f"Empty for {empty_min:g} min · saved {saved} Wh",
                                     f'Rule "Turn off after empty for {empty_min:g} min"', saved))
                    elif self.paused(dev) or not s.get("occ"):
                        continue
                    elif r.get("on_when_dark") and not st.get("v") and (lux if lux is not None else 999) \
                            < th.get("light_on_lux", 150) and self.entered.get(rid) \
                            and t - (s.get("occ_since") or 0) / 1000 < ENTRY_WINDOW_S \
                            and not (ai and self.ai_safe(ai.handles_entry, dev)):
                        # fallback only: when the AI is ready for this light it decides from your habits.
                        # Only when someone has just come in: a person sleeping or who switched it off stays dark
                        todo.append((dev, rid, {"v": 1, "level": self.mid_level(cfg)},
                                     f"Light below {th.get('light_on_lux', 150)} lx",
                                     f'Rule "On below {th.get("light_on_lux", 150)} lx" · light {lux} lx · '
                                     f"someone in the room", None))
                    elif r.get("follow_temp") and not st.get("v") and (s.get("temp") or 0) > th.get("fan_on_temp", 28):
                        todo.append((dev, rid, {"v": 1, "level": self.mid_level(cfg)},
                                     f"Above {th.get('fan_on_temp', 28)}°C",
                                     f'Rule "On above {th.get("fan_on_temp", 28)}°C" · room {s.get("temp")}°C · '
                                     f"someone in the room", None))
                    elif r.get("follow_temp") and st.get("v") and (s.get("temp") or 99) < th.get("fan_off_temp", 26.5):
                        todo.append((dev, rid, {"v": 0}, f"Below {th.get('fan_off_temp', 26.5)}°C",
                                     f'Rule "Off below {th.get("fan_off_temp", 26.5)}°C" · room {s.get("temp")}°C',
                                     None))
                if s.get("occ") and (not ai or self.ai_entry_seen(rid)):
                    self.entered.pop(rid, None)         # each entry is looked at once
        for dev, rid, body, short, why, saved in todo:
            self.switch(dev, rid, body, short, why, saved)

    def ai_entry_seen(self, rid):
        """The AI decides about a light within ENTRY_WINDOW of an entry (its own window): keep the entry
        open for the rule until then, in case the AI is not ready for this light after all."""
        occ_since = (self.house.rooms.get(rid) or {}).get("occ_since") or 0
        return self.clock() - occ_since / 1000 >= ENTRY_WINDOW_S

    @staticmethod
    def mid_level(cfg):
        lv = (cfg.get("caps") or {}).get("level")
        return lv["steps"][len(lv["steps"]) // 2] if lv else None

    def switch(self, dev, rid, body, short, why, saved=None, src="rule", src_label="Rule", title=None,
               confidence=None):
        """Send a rule's or the AI's decision to the node; the History line is written once the node answered."""
        body = {k: v for k, v in body.items() if v is not None}
        cfg, before = self.house.dev_cfg(dev), dict(self.house.devices[dev])
        node, label = self.house.node_of(dev), house_words.label(self.config, dev)
        what = "turned on" if body["v"] else "turned off"
        title = title or f"{label} {what}"
        group, by_label = ("ai", "AI (Gradient Boosting)") if src == "ai" else ("rule", "Automation")
        say(f"{dev}: {src} -> {what} ({short})")

        def done(d, st, latency_ms):
            self.safe(self.w.log_event, "device", group, title, src, src_label, device=d, room=rid,
                      node=node, by_label=by_label, why=why, short=short, saved_wh=saved, confidence=confidence,
                      change=f"{house_words.state_label(cfg, before)} → {house_words.state_label(cfg, st)}",
                      from_={k: before[k] for k in ("v", "level", "mode") if before.get(k) is not None},
                      to={k: st[k] for k in ("v", "level", "mode") if st.get(k) is not None},
                      latency_ms=latency_ms, tags=[group])
            if saved is not None:
                room_name = self.config["rooms"][rid]["name"]
                where = (f"{room_name} · empty for {self.th.get('empty_room_off_min', 10)} min · saved {saved} Wh"
                         if src == "rule" else f"{room_name} · empty room · saves ~{saved} Wh")
                self.safe(self.w.push_alert, "good", f"Waste stopped: {label.lower()} off", where, "energy")
            say(f"{d}: {src} done in {latency_ms} ms")

        def failed(d, reason):
            self.safe(self.w.log_event, "device", group, title, src, src_label, result="failed",
                      device=d, room=rid, node=node, by_label=by_label, why=why, short=reason,
                      change="(unchanged)", tags=[group, "failed"])
            say(f"{d}: {src} FAILED — {reason}")

        self.ex.send(dev, rid, body, src, done=done, failed=failed, node=node)

    def check_holds(self):
        with self.lock:
            self.house.check_holds()

    def check_config(self):
        cfg = self.safe(self.w.get_config)
        if cfg and cfg.get("version") != self.config.get("version"):
            say(f"/config changed: v{self.config.get('version')} -> v{cfg.get('version')}")
            with self.lock:
                self.house.load_config(cfg)
                if self.ai:
                    self.ai_safe(self.ai.reload, cfg)


def open_firebase(args):
    if args.cloud:
        from cloud_db import cloud_db
        w = Writer(cloud_db(args.cloud))
        return w, f"REAL Firebase · project {w.db.project_id}"
    from rest_db import RestDB, PROJECT_ID
    return Writer(RestDB()), f"emulator · project {PROJECT_ID}"


def main():
    from ai import settings as AIS
    ap = argparse.ArgumentParser(description="Rules + the AI deciding over MQTT — the Pi's brain")
    ap.add_argument("--host", default="localhost", help="MQTT broker")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--db", default=str(AIS.DB_PATH), help="SQLite file mqtt_ingest writes (default ai/data/home.db)")
    ap.add_argument("--cloud", metavar="KEY_JSON", help="the REAL Firebase project with this service-account key")
    ap.add_argument("--no-ai", action="store_true", help="the rules only")
    ap.add_argument("--train-now", action="store_true", help="retrain the AI at start (else every night at 03:00)")
    ap.add_argument("--ask", metavar="DEVICE", help="test: once the AI is ready it asks about this device")
    ap.add_argument("--sim-clock", action="store_true",
                    help="test only: use the world's fast clock (fake_nodes.py --world --speed N, hub/clock)")
    args = ap.parse_args()

    sim = None
    if args.sim_clock:
        import firebase_writer
        from hub_clock import SharedClock
        sim = SharedClock()
        firebase_writer.set_clock(sim.now)                     # History timestamps follow the world's time
    w, where = open_firebase(args)
    try:
        config = w.get_config()
    except (RuntimeError, OSError) as e:
        sys.exit(f"Cannot reach Firebase ({where}): {e}\nStart the emulators first:  firebase emulators:start")
    if not config:
        sys.exit("/config is empty: start mqtt_ingest.py first (it seeds an empty emulator)")
    auto = Automation(w, config, args, clock=sim)
    try:
        auto.connect()
    except OSError as e:
        sys.exit(f"cannot reach the MQTT broker at {args.host}:{args.port} ({e}) — is Mosquitto running?")
    th = auto.th
    say(f"Firebase: {where} · rules every {TICK_S:.0f} s · empty {th.get('empty_room_off_min')} min · "
        f"dark < {th.get('light_on_lux')} lx · fan {th.get('fan_on_temp')}/{th.get('fan_off_temp')}°C · "
        f"hands off {th.get('override_pause_min')} min")
    if not args.no_ai:
        auto.start_ai()

    # real seconds for Firebase / MQTT chores, the house's time (auto.clock) for the rules and the AI
    last = {"slow": 0.0, "config": time.time(), "tick": auto.clock(), "plan": 0.0}
    asked, started = False, time.time()
    try:
        while True:
            now, house_now = time.time(), auto.clock()
            auto.ex.check_timeouts()
            auto.check_holds()
            if now - last["slow"] >= SLOW_S:
                auto.sync_slow()
                auto.handle_answers()
                auto.ai_night()
                last["slow"] = now
            if house_now - last["tick"] >= TICK_S or (sim and house_now > last["tick"]):
                auto.ai_tick(house_now - last["tick"])         # a fast clock: every loop (like the twin's tick)
                auto.apply_rules()
                last["tick"] = house_now
            if auto.ai_on() and house_now - last["plan"] >= PLAN_S:
                auto.ai_plan()
                last["plan"] = house_now
            if args.ask and not asked and auto.ai_on() and now - started > 75:   # after the first heartbeats
                asked = True
                say(f"test question about {args.ask}:", auto.ask_now(args.ask) or "the AI did not ask (sensors)")
            if now - last["config"] >= CONFIG_EVERY_S:
                auto.check_config()
                last["config"] = now
            time.sleep(0.25 if sim else 0.5)
    except KeyboardInterrupt:
        pass
    auto.client.loop_stop()
    auto.client.disconnect()
    say("automation stopped")


if __name__ == "__main__":
    main()
