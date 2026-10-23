"""
Automation — the Pi's "brain" (stage 1d). Decides by itself: the per-device rules from /config now (1d-1),
the AI (ai.runtime.AIRuntime: plan, tick, questions, nightly training) next (1d-2).

It sees the house the same way mqtt_ingest does — it listens to the nodes itself (home/#) and builds the
picture with the same code (house_state.py) — so it keeps deciding without the internet and without the
other services. It switches through MQTT like commands.py (mqtt_exec.MqttExecutor, src "rule") and writes
to the History why it did it.

The rules (the same as the simulator's apply_rules, docs/contract.md "rules"):
    off_when_empty   off when the room has been empty for thresholds.empty_room_off_min
    on_when_dark     on when someone has just come in (90 s) and the room is below thresholds.light_on_lux
    follow_temp      on above thresholds.fan_on_temp while someone is in the room, off below fan_off_temp
Manual control (a wall button or the app: state src "button" / "web") = hands off that device for
thresholds.override_pause_min (2 h) or until the room is empty. The app's pause switch (/ai_pause) too.
Only devices with control.rules = true and power "write".

Run (Mosquitto + Firebase emulators + mqtt_ingest + fake nodes running):
    python pi/automation.py
    python pi/automation.py --cloud KEY.json
"""
import argparse
import sys
import threading
import time
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

TICK_S = 5.0              # rules are checked every 5 s
NODE_TIMEOUT_S = 5.0      # like commands.py
ENTRY_WINDOW_S = 90       # "on when dark" only right after someone came in (a sleeper stays in the dark)
PAUSE_EVERY_S = 10.0      # /ai_pause (the app's switch) is re-read every 10 s
CONFIG_EVERY_S = 30.0

PRINT_LOCK = threading.Lock()


def say(*parts):
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Automation(house_state.Listener):
    def __init__(self, writer, config, args):
        self.w, self.args = writer, args
        self.lock = threading.Lock()
        self.client = self.ex = None
        self.house = house_state.HouseState(config, self)
        self.entered = {}           # room -> True until the rules have looked at this entry once
        self.manual_at = {}         # device -> ts of the last manual control (button / app)
        self.override_until = {}    # device -> hands off until (manual control)
        self.ai_pause = {}          # device -> ms (the app's pause switch)
        start = time.time()         # room -> ts it became empty; nobody seen yet = empty from now on
        self.empty_since = {rid: start for rid in self.house.rooms}

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

    # ------------------------------------------------------------------ MQTT
    def connect(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-automation")
        c.on_connect = self.on_connect
        c.on_message = self.on_message
        c.connect(self.args.host, self.args.port, keepalive=30)
        c.loop_start()
        self.client = c
        self.ex = MqttExecutor(c, NODE_TIMEOUT_S)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            say("!! MQTT connect failed:", reason_code)
            return
        client.subscribe("home/#", qos=1)
        say("listening to home/# on", f"{self.args.host}:{self.args.port}")

    def on_message(self, client, userdata, msg):
        topic, payload = msg.topic, msg.payload.decode("utf-8", "replace")
        try:
            with self.lock:
                self.house.handle(topic, payload)
        except (ValueError, TypeError, AttributeError) as e:
            say("!! bad message", topic, payload, f"({e})")

    # ------------------------------------------------------------------ what changed (house_state.Listener)
    def occ_changed(self, rid, occ):
        now = time.time()
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
            now = time.time()
            self.manual_at[dev] = now
            self.override_until[dev] = now + float(self.th.get("override_pause_min", 120)) * 60
            say(f"{dev}: manual control ({src}) — hands off for {self.th.get('override_pause_min', 120)} min")
        self.ex.answer(dev, dict(st, src=src))

    # ------------------------------------------------------------------ pauses
    def user_paused(self, dev):
        """The app's pause switch in the device sheet (/ai_pause/{device} = until ms)."""
        return now_ms() < int(self.ai_pause.get(dev) or 0)

    def paused(self, dev):
        return time.time() < self.override_until.get(dev, 0) or self.user_paused(dev)

    def sync_ai_pause(self):
        new = self.safe(self.w.get_ai_pause)
        if new is None:
            return
        for dev in set(new) | set(self.ai_pause):
            if dev not in self.house.dev_room or new.get(dev) == self.ai_pause.get(dev):
                continue
            paused_now = bool(new.get(dev)) and int(new[dev]) > now_ms()
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
        t, th, h = time.time(), self.th, self.house
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
                    if r.get("off_when_empty") and st.get("v") and not s.get("occ") and not self.user_paused(dev) \
                            and empty_for > empty_min * 60:
                        saved = round(float(st.get("watts") or cfg.get("watts", 0)), 2)  # over the next hour
                        todo.append((dev, rid, {"v": 0}, f"Empty for {empty_min:g} min · saved {saved} Wh",
                                     f'Rule "Turn off after empty for {empty_min:g} min"', saved))
                    elif self.paused(dev) or not s.get("occ"):
                        continue
                    elif r.get("on_when_dark") and not st.get("v") and (lux if lux is not None else 999) \
                            < th.get("light_on_lux", 150) and self.entered.get(rid) \
                            and t - (s.get("occ_since") or 0) / 1000 < ENTRY_WINDOW_S:
                        # only when someone has just come in: a person sleeping or who switched it off stays dark
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
                if s.get("occ"):
                    self.entered.pop(rid, None)         # each entry is looked at once
        for dev, rid, body, short, why, saved in todo:
            self.switch(dev, rid, body, short, why, saved)

    @staticmethod
    def mid_level(cfg):
        lv = (cfg.get("caps") or {}).get("level")
        return lv["steps"][len(lv["steps"]) // 2] if lv else None

    def switch(self, dev, rid, body, short, why, saved=None):
        """Send the rule's decision to the node; the History line is written once the node has answered."""
        body = {k: v for k, v in body.items() if v is not None}
        cfg, before = self.house.dev_cfg(dev), dict(self.house.devices[dev])
        node, label = self.house.node_of(dev), house_words.label(self.config, dev)
        what = "turned on" if body["v"] else "turned off"
        say(f"{dev}: rule -> {what} ({short})")

        def done(d, st, latency_ms):
            self.safe(self.w.log_event, "device", "rule", f"{label} {what}", "rule", "Rule", device=d, room=rid,
                      node=node, by_label="Automation", why=why, short=short, saved_wh=saved,
                      change=f"{house_words.state_label(cfg, before)} → {house_words.state_label(cfg, st)}",
                      from_={k: before[k] for k in ("v", "level", "mode") if before.get(k) is not None},
                      to={k: st[k] for k in ("v", "level", "mode") if st.get(k) is not None},
                      latency_ms=latency_ms, tags=["rule"])
            if saved is not None:
                room_name = self.config["rooms"][rid]["name"]
                self.safe(self.w.push_alert, "good", f"Waste stopped: {label.lower()} off",
                          f"{room_name} · empty for {self.th.get('empty_room_off_min', 10)} min · saved {saved} Wh",
                          "energy")
            say(f"{d}: rule done in {latency_ms} ms")

        def failed(d, reason):
            self.safe(self.w.log_event, "device", "rule", f"{label} {what}", "rule", "Rule", result="failed",
                      device=d, room=rid, node=node, by_label="Automation", why=why, short=reason,
                      change="(unchanged)", tags=["rule", "failed"])
            say(f"{d}: rule FAILED — {reason}")

        self.ex.send(dev, rid, body, "rule", done=done, failed=failed, node=node)

    def check_holds(self):
        with self.lock:
            self.house.check_holds()

    def check_config(self):
        cfg = self.safe(self.w.get_config)
        if cfg and cfg.get("version") != self.config.get("version"):
            say(f"/config changed: v{self.config.get('version')} -> v{cfg.get('version')}")
            with self.lock:
                self.house.load_config(cfg)


def open_firebase(args):
    if args.cloud:
        from cloud_db import cloud_db
        w = Writer(cloud_db(args.cloud))
        return w, f"REAL Firebase · project {w.db.project_id}"
    from rest_db import RestDB, PROJECT_ID
    return Writer(RestDB()), f"emulator · project {PROJECT_ID}"


def main():
    ap = argparse.ArgumentParser(description="Rules (+ the AI, stage 1d-2) deciding over MQTT — the Pi's brain")
    ap.add_argument("--host", default="localhost", help="MQTT broker")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--cloud", metavar="KEY_JSON", help="the REAL Firebase project with this service-account key")
    args = ap.parse_args()

    w, where = open_firebase(args)
    try:
        config = w.get_config()
    except (RuntimeError, OSError) as e:
        sys.exit(f"Cannot reach Firebase ({where}): {e}\nStart the emulators first:  firebase emulators:start")
    if not config:
        sys.exit("/config is empty: start mqtt_ingest.py first (it seeds an empty emulator)")
    auto = Automation(w, config, args)
    try:
        auto.connect()
    except OSError as e:
        sys.exit(f"cannot reach the MQTT broker at {args.host}:{args.port} ({e}) — is Mosquitto running?")
    th = auto.th
    say(f"Firebase: {where} · rules every {TICK_S:.0f} s · empty {th.get('empty_room_off_min')} min · "
        f"dark < {th.get('light_on_lux')} lx · fan {th.get('fan_on_temp')}/{th.get('fan_off_temp')}°C · "
        f"hands off {th.get('override_pause_min')} min")

    last = {"pause": 0.0, "config": time.time(), "tick": time.time()}
    try:
        while True:
            now = time.time()
            auto.ex.check_timeouts()
            auto.check_holds()
            if now - last["pause"] >= PAUSE_EVERY_S:
                auto.sync_ai_pause()
                last["pause"] = now
            if now - last["tick"] >= TICK_S:
                auto.apply_rules()
                last["tick"] = now
            if now - last["config"] >= CONFIG_EVERY_S:
                auto.check_config()
                last["config"] = now
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    auto.client.loop_stop()
    auto.client.disconnect()
    say("automation stopped")


if __name__ == "__main__":
    main()
