"""
House simulator — behaves exactly like the Raspberry Pi services, so the web app can be built
before any hardware exists. Writes through firebase_writer.py (the same code the Pi uses).

Run (with the Firebase emulators already running):
    python pi/sim_house.py                 # normal
    python pi/sim_house.py --fast          # rules/summaries/AI ~10x faster (demo mode)
    python pi/sim_house.py --offline living    # the living-room node is offline
    python pi/sim_house.py --fail-rate 0.3     # 30% of commands fail
    python pi/sim_house.py --lockout       # 5 wrong PINs at the door right away (security alert)
    python pi/sim_house.py --reset         # wipe the database and re-seed docs/seed.json
    python pi/sim_house.py --fault bedroom_fan        # the fan draws 2x its usual power (AI fault detection)
    python pi/sim_house.py --fault living_light:dead  # kinds: high (default) | dead | standby
    python pi/sim_house.py --no-ai         # run without the AI

What it does:
    - seeds /config from docs/seed.json and creates the owner login (owner@home.test / password123)
    - every tick: executes /commands (done/failed + event), writes /home_state and /nodes
    - rules: empty room -> off, dark + occupied -> lights on, hot + occupied -> fan on, cool -> fan off
    - AI: the REAL model from ai/ (ai.runtime.AIRuntime) — plan every 15 min, sensor gate, waste guard,
      smart off, suggestion expiry, energy faults, waste ledger. A fresh install gets simulated history
      (labelled "simulated") so the models are ready on day one. Needs: pip install -r ai/requirements.txt
    - door: fingerprint / keypad / exit button / wrong PINs / lockout, entrance motion + camera clips
    - /alerts for the Activity screen, /summaries + /energy_daily every minute
The simulator itself uses the Python standard library; the AI needs numpy/pandas/scikit-learn.
"""
import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from firebase_writer import Writer, day_key, now_ms  # noqa: E402
from rest_db import PROJECT_ID, RestDB  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SEED = ROOT / "docs" / "seed.json"
AUTH_URL = "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1"
OWNER_EMAIL, OWNER_PASSWORD = "owner@home.test", "password123"
TICK_S = 0.5
BASE_W = 6.8            # Raspberry Pi + 3 ESP32 nodes + sensors


def hhmm(dt=None):
    return (dt or datetime.now()).strftime("%H:%M")


# ---------------------------------------------------------------- setup
def ensure_owner(w):
    """Create the owner account in the Auth emulator and give it role=owner."""
    def call(endpoint, body):
        req = urllib.request.Request(f"{AUTH_URL}/{endpoint}?key=demo-key",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    body = {"email": OWNER_EMAIL, "password": OWNER_PASSWORD, "returnSecureToken": True}
    uid = None
    for attempt in range(15):          # the emulators may still be starting — wait up to ~30 s
        try:
            try:
                uid = call("accounts:signUp", body)["localId"]
            except urllib.error.HTTPError:  # EMAIL_EXISTS
                uid = call("accounts:signInWithPassword", body)["localId"]
            break
        except Exception as e:  # auth emulator not reachable (yet)
            if attempt == 0:
                print("waiting for the Auth emulator on 127.0.0.1:9099 …")
            last = e
            time.sleep(2)
    if uid is None:
        print(f"! could not reach the Auth emulator ({last}).\n"
              "  Login will not work. Is `firebase emulators:start` running and showing Authentication on 9099?")
        return None
    w.db.put(f"users/{uid}", {"role": "owner", "name": "Owner"})
    return uid


def seed(w, reset, cloud=False):
    seed_cfg = json.loads(SEED.read_text(encoding="utf-8"))["config"]
    config = None if reset else w.get_config()
    if cloud and config and config.get("schema") != seed_cfg.get("schema"):
        sys.exit("The real database has an older data shape. Run again with --reset (it asks before wiping).")
    if reset or (config and config.get("schema") != seed_cfg.get("schema")):
        w.db.put("", {})
        print("database wiped" + ("" if reset else " (data shape changed — re-seeding)"))
        config = None
    if not config:
        config = seed_cfg
        w.put_config(config)
        print(f"seeded /config from {SEED.relative_to(ROOT)}")
    return config


def backfill_energy(w, config):
    """6 days of fake energy history so the Energy chart is not empty on day one."""
    for d in range(1, 7):
        day = day_key(time.time() - d * 86400)
        if w.get_energy_day(day):
            continue
        per = {"_base": BASE_W * 24 * random.uniform(0.97, 1.03)}
        for room in config["rooms"].values():
            for dev, cfg in (room.get("devices") or {}).items():
                if cfg.get("watts"):
                    per[dev] = cfg["watts"] * random.uniform(1, 7)  # 1–7 h of use
        w.write_energy_day(day, per)


def load_ai(args):
    """The real AI (ai/). Returns (AIRuntime class, sink class, store module) or None if it can't run here."""
    if args.no_ai:
        return None
    try:
        from ai import store as ai_store
        from ai.dispatch import FirebaseSink
        from ai.runtime import AIRuntime
        return AIRuntime, FirebaseSink, ai_store
    except ImportError as e:
        print(f"! AI disabled ({e}). Install it with:  pip install -r ai/requirements.txt")
        return None


# ---------------------------------------------------------------- the house
class House:
    def __init__(self, w, config, args):
        self.w, self.args = w, args
        self.speedup = 10 if args.fast else 1
        self.offline = set(args.offline or [])
        self.override_until = {}         # device -> ts; manual control pauses AI/rules
        self.empty_since = {}            # room -> ts
        self.energy_day = day_key()
        self.energy = dict(w.get_energy_day(self.energy_day))
        self.failed_door = 0
        self.lock_at = None
        self.door = {}                   # lockout_until, last_open
        self.scene = None
        self.last = dict(state=0, summary=0, ai=0, random=0, door=0, motion=0, sugg=0)
        self.seen_cmd_at = {}
        self.ai_pause = {}               # device -> until ms (set from the web app)
        self.off_alerted = set()         # monitor-only devices we already alerted about
        self.node_version = {}           # node -> config version it confirmed
        self.door_entry_at = None        # last time someone came in (fingerprint / keypad / app)
        self.away = False                # Away scene on
        self.faults = dict(f.split(":", 1) if ":" in f else (f, "high") for f in (args.fault or []))
        self.ai = None                   # ai.runtime.AIRuntime (the real model) — set by start_ai()
        self.next_train = 0
        # the real Firebase free plan has a download quota, so poll it less often than the local emulator
        self.poll_s = 1.0 if args.cloud else TICK_S
        self.config_poll_s = 10.0 if args.cloud else TICK_S
        self.load_config(config)
        for n in config["nodes"]:
            self.node_version[n] = config["version"]

    def load_config(self, config):
        self.config = config
        self.th = config["thresholds"]
        self.dev_cfg, self.dev_room = {}, {}
        for rid, room in config["rooms"].items():
            for dev, cfg in (room.get("devices") or {}).items():
                self.dev_cfg[dev], self.dev_room[dev] = cfg, rid
        old = getattr(self, "devices", {})
        self.devices = {}
        for d, c in self.dev_cfg.items():
            st = old.get(d) or {"v": 0, "src": "system", "at": now_ms(), "watts": 0}
            lv = self.caps(d).get("level")
            if lv and "level" not in st:
                st["level"] = lv["steps"][len(lv["steps"]) // 2]
            if self.caps(d).get("mode") and "mode" not in st:
                st["mode"] = self.caps(d)["mode"]["options"][0]
            self.devices[d] = st
        locks = [d for d in self.dev_cfg if self.caps(d).get("lock")]
        self.lock_id = locks[0] if locks else None
        old_rooms = getattr(self, "rooms", {})
        self.rooms = {r: old_rooms.get(r, {"occ": 0}) for r in config["rooms"]}

    # ------------------------------------------------ capabilities (docs/contract.md → device model)
    def caps(self, d):
        return self.dev_cfg[d].get("caps") or {}

    def ctrl(self, d):
        return self.dev_cfg[d].get("control") or {}

    def drules(self, d):
        return self.dev_cfg[d].get("rules") or {}

    def writable(self, d):
        return self.caps(d).get("power") == "write"

    def is_lock(self, d):
        return bool(self.caps(d).get("lock"))

    def icon(self, d):
        return self.dev_cfg[d].get("icon", "generic")

    def level_label(self, d, value):
        lv = self.caps(d).get("level")
        if not lv:
            return None
        steps, labels = lv["steps"], lv["labels"]
        i = min(range(len(steps)), key=lambda k: abs(steps[k] - (value or steps[len(steps) // 2])))
        return labels[i] if i < len(labels) else f"{steps[i]}%"

    def mid_level(self, d):
        lv = self.caps(d).get("level")
        return lv["steps"][len(lv["steps"]) // 2] if lv else None

    # ------------------------------------------------ names
    def node_of(self, device):
        return self.config["rooms"][self.dev_room[device]]["node"]

    def room_name(self, rid):
        return self.config["rooms"][rid]["name"]

    def label(self, device):
        """'Living room fan', 'Bedroom lights', 'Washer', 'Front door'"""
        cfg = self.dev_cfg[device]
        room = self.room_name(self.dev_room[device])
        if self.icon(device) in ("washer", "lock") or cfg["name"].lower().startswith(room.lower()):
            return cfg["name"]
        name = cfg["name"] if cfg["name"].isupper() else cfg["name"].lower()   # keep "TV", "AC"
        return f"{room} {name}"

    def state_label(self, device, st):
        if not st.get("v"):
            return "Off"
        if self.caps(device).get("level"):
            return f"On · {self.level_label(device, st.get('level'))}"
        if self.caps(device).get("mode") and st.get("mode"):
            return f"On · {st['mode']}"
        return "On"

    def node_name(self, node):
        return self.config["nodes"].get(node, {}).get("name", node)

    # ------------------------------------------------ sensors
    def update_sensors(self):
        now = datetime.now()
        h = now.hour + now.minute / 60
        outdoor = 27 + 4 * math.sin((h - 9) / 24 * 2 * math.pi)
        daylight = max(0.0, math.sin((h - 6) / 13 * math.pi)) if 6 <= h <= 19 else 0
        for rid, room in self.config["rooms"].items():
            if room["node"] in self.offline:
                continue
            s = self.rooms[rid]
            sensors = room.get("sensors", [])
            if "occ" in sensors:
                # someone tends to be in the living room by day and the bedroom at night
                likely = 0.6 if (rid == "living" and 8 <= h < 23) or (rid == "bedroom" and (h >= 23 or h < 8)) else 0.12
                if random.random() < 0.03 * self.speedup:
                    new = int(random.random() < likely)
                    if new and not s.get("occ"):
                        s["motion_at"] = now_ms()
                    if new != s.get("occ"):
                        s["occ_since"] = now_ms()
                    s["occ"] = new
                    s["occ_by"] = ("mmwave" if "C1001 mmWave" in room.get("hardware", []) else "pir")
                hw = room.get("hardware", [])
                if "C1001 mmWave" in hw:
                    s["mmwave"] = s.get("occ", 0)               # the radar also sees people sitting still
                if "PIR" in hw:                                  # PIR only sees movement: it misses still people
                    s["pir"] = int(bool(s.get("occ")) and random.random() < 0.75)
            devs = room.get("devices") or {}
            if "temp" in sensors:
                fan_cool = -1.2 if any(self.devices[d]["v"] and self.icon(d) in ("fan", "ac") for d in devs) else 0
                s["temp"] = round(outdoor + (0.8 if rid == "bedroom" else 0) + fan_cool + random.gauss(0, 0.12), 1)
            if "hum" in sensors:
                s["hum"] = round(50 - (outdoor - 27) * 1.5 + random.gauss(0, 1))
            if "lux" in sensors:
                lights = 250 if any(self.devices[d]["v"] and self.icon(d) == "light" for d in devs) else 0
                s["lux"] = round(max(0, 600 * daylight + lights + random.gauss(0, 6)))

    def update_power(self, dt):
        total = BASE_W
        self.energy["_base"] = self.energy.get("_base", 0) + BASE_W * dt / 3600
        for d, st in self.devices.items():
            cfg = self.dev_cfg[d]
            measured = self.caps(d).get("energy", "estimate") != "none"
            fault = self.faults.get(d)
            if st["v"] and cfg.get("watts") and measured:
                factor = (st.get("level", 70) / 100) if self.caps(d).get("level") else 1
                st["watts"] = round(cfg["watts"] * factor * random.uniform(0.97, 1.03), 2)
                if fault == "high":
                    st["watts"] = round(st["watts"] * 2.1, 2)        # e.g. motor blocked
                elif fault == "dead":
                    st["watts"] = 0.02                                # e.g. burnt bulb
            else:
                st["watts"] = 1.1 if fault == "standby" and measured else 0
            total += st["watts"]
            if cfg.get("watts") and measured:
                self.energy[d] = self.energy.get(d, 0) + st["watts"] * dt / 3600
        return total

    # ------------------------------------------------ changing a device (single code path)
    def set_device(self, device, v, level=None, *, mode=None, source, src_label, group, why, by=None, by_label=None,
                   short=None, confidence=None, latency_ms=None, kind="device", tags=None, saved_wh=None,
                   title=None, change=None):
        caps = self.caps(device)
        st = self.devices[device]
        keys = ("v", "level", "mode")
        before = {k: st.get(k) for k in keys if st.get(k) is not None}
        before_label = self.state_label(device, st)
        level_only = bool(caps.get("level") and st["v"] and v and level and level != st.get("level"))
        mode_only = bool(caps.get("mode") and st["v"] and v and mode and mode != st.get("mode"))
        st.update(v=int(v), src=source, at=now_ms())
        if level is not None and caps.get("level"):
            st["level"] = int(level)
        if mode is not None and caps.get("mode"):
            st["mode"] = mode
        if caps.get("status"):
            st["status"] = "Running" if v else "Idle"
        after = {k: st.get(k) for k in keys if st.get(k) is not None}
        after_label = self.state_label(device, st)
        if source in ("web", "button"):
            self.override_until[device] = time.time() + self.th["override_pause_min"] * 60 / self.speedup
            if self.ai:
                self.ai_safe(self.ai.on_manual, device)
        if title is None:
            what = ("speed changed" if self.icon(device) == "fan" else "level changed") if level_only else \
                   "mode changed" if mode_only else "turned on" if v else "turned off"
            title = f"{self.label(device)} {what}"
        if level_only and short is None:
            short = f"{self.level_label(device, before.get('level'))} → {self.level_label(device, level)}"
        if mode_only and short is None:
            short = f"{before.get('mode')} → {mode}"
        self.w.log_event(kind, group, title, source, src_label, device=device, room=self.dev_room[device],
                         node=self.node_of(device), by=by, by_label=by_label, why=why, short=short,
                         change=change or f"{before_label} → {after_label}", confidence=confidence,
                         from_=before, to=after, latency_ms=latency_ms, saved_wh=saved_wh, tags=tags or [group])
        self.w.patch_device(device, st)

    # ------------------------------------------------ commands from the web app
    def handle_commands(self):
        for device, cmd in self.w.get_commands().items():
            if not isinstance(cmd, dict) or cmd.get("status") != "pending":
                continue
            key = (cmd.get("at"), cmd.get("v"), cmd.get("level"), cmd.get("mode"))
            if self.seen_cmd_at.get(device) == key:
                continue
            self.seen_cmd_at[device] = key
            if device not in self.dev_cfg:
                self.w.ack_command(device, False, "unknown device")
                continue
            cfg = self.dev_cfg[device]
            scene = cmd.get("scene")
            scene_name = (self.config.get("scenes", {}).get(scene) or {}).get("name", scene) if scene else None
            via_sugg = cmd.get("via") == "suggestion"
            pct = round((cmd.get("confidence") or 0) * 100)
            if via_sugg:
                meta = dict(source="ai", src_label=f"AI · {pct}% · approved", group="ai",
                            by_label="AI suggestion, approved by Owner", why="You said yes to an AI suggestion",
                            short="Suggestion approved", confidence=cmd.get("confidence"), tags=["ai", "manual"])
            elif scene:
                meta = dict(source="web", src_label=f"Scene · {scene_name}", group="manual",
                            by_label="Owner (web app)", why=f"{scene_name} scene", short=f"{scene_name} scene",
                            kind="scene", tags=["manual"])
            else:
                meta = dict(source="web", src_label="App", group="manual", by_label="Owner (web app)",
                            why="Tapped in the app", short="Owner · web app", tags=["manual"])
            if scene and scene != self.scene:
                self.scene = scene
                self.away = scene == "away"
            node = self.node_of(device)
            fail = None
            if not (self.writable(device) or self.is_lock(device)) or not self.ctrl(device).get("app", False):
                self.w.ack_command(device, False, "This device can't be controlled from the app")
                continue
            if node in self.offline:
                fail = f"No response from the {node} node"
            elif random.random() < self.args.fail_rate:
                fail = f"No response from the {node} node"
            if fail:
                time.sleep(0.3)
                self.w.ack_command(device, False, fail)
                st = self.devices[device]
                lock = self.is_lock(device)
                title = (f"Remote unlock failed" if lock else
                         f"{self.label(device)} {'turned on' if cmd.get('v') else 'turned off'}")
                self.w.log_event("door" if lock else "device",
                                 "door" if lock else meta["group"], title, meta["source"],
                                 meta["src_label"], result="failed", device=device, room=self.dev_room[device],
                                 node=node, by=cmd.get("by"), by_label=meta["by_label"], why=meta["why"],
                                 short=fail, change=f"{self.state_label(device, st)} (unchanged)",
                                 latency_ms=10000, tags=meta["tags"] + ["failed"])
                continue
            time.sleep(random.uniform(0.2, 0.6))  # network + ESP32 round trip
            latency = max(0, now_ms() - int(cmd.get("at") or now_ms()))
            if self.is_lock(device):
                self.open_door("web", "Owner", latency)
            else:
                self.set_device(device, cmd["v"], cmd.get("level"), mode=cmd.get("mode"), by=cmd.get("by"),
                                latency_ms=latency, **meta)
            self.w.ack_command(device, True)

    # ------------------------------------------------ door
    def open_door(self, method, who=None, latency=None, finger=None):
        if not self.lock_id:
            return
        titles = {"web": "Front door unlocked remotely", "exit_button": "Front door opened from inside"}
        labels = {"web": "App", "fingerprint": "Fingerprint", "keypad": "Keypad", "exit_button": "Exit button"}
        shorts = {"web": "Owner · web app", "fingerprint": f"{who} · finger #{finger}",
                  "keypad": "Correct PIN", "exit_button": "Opened from inside"}
        whys = {"web": "Remote unlock, confirmed in the app", "fingerprint": "Fingerprint matched on the R503 sensor",
                "keypad": "Correct PIN on the keypad", "exit_button": "Exit button next to the door"}
        by_labels = {"web": "Owner (web app)", "fingerprint": f"{who} (fingerprint #{finger})",
                     "keypad": "Someone with the PIN", "exit_button": "Someone at home"}
        self.failed_door = 0
        self.w.log_access(method, True, who)
        self.set_device(self.lock_id, 1, source=method, src_label=labels[method], group="door", kind="door",
                        title=titles.get(method, "Front door unlocked"), short=shorts[method], why=whys[method],
                        by_label=by_labels[method], change="Locked → Unlocked → Locked (5 s)",
                        latency_ms=latency or random.randint(150, 700), tags=["door"] + (["manual"] if method == "web" else []))
        self.door["last_open"] = {"at": now_ms(), "method": method, **({"who": who} if who else {})}
        if method in ("fingerprint", "keypad", "web"):
            self.door_entry_at = time.time()
            if self.ai and self.ai.recorder:
                self.ai_safe(self.ai.recorder.event, "door/entry")
        self.lock_at = time.time() + 5

    def fingerprints(self):
        """{finger id: name}. Firebase turns objects with keys "1","2",... into arrays, so accept both."""
        f = self.config.get("fingerprints") or {}
        if isinstance(f, list):
            f = {str(i): name for i, name in enumerate(f) if name}
        return f or {"1": "Owner"}

    def wrong_attempt(self, method):
        if now_ms() < self.door.get("lockout_until", 0):
            return
        self.failed_door += 1
        n, limit = self.failed_door, self.th["door_lockout_attempts"]
        what = "Wrong PIN" if method == "keypad" else "Unknown fingerprint"
        self.w.log_access(method, False)
        self.w.log_event("door", "door", f"{what} at the front door", method,
                         "Keypad" if method == "keypad" else "Fingerprint", result="denied",
                         device=self.lock_id, room=self.dev_room.get(self.lock_id, "entrance"), node="door", by_label="Unknown",
                         short="Door stayed locked", why=f"{what} (attempt {n} of {limit})",
                         change="No change", tags=["door"])
        if n < limit:
            if n == 1:
                self.w.push_alert("warning", f"{what} at the front door",
                                  f"Front door · {'keypad' if method == 'keypad' else 'fingerprint'} · door stayed locked", "security")
            return
        self.failed_door = 0
        secs = self.th.get("door_lockout_s", 60)
        self.door["lockout_until"] = now_ms() + secs * 1000
        self.w.log_event("door", "door", "Keypad locked", "keypad", "Keypad", result="failed",
                         device=self.lock_id, room=self.dev_room.get(self.lock_id, "entrance"), node="door", by_label="Unknown",
                         short=f"{limit} wrong PINs · locked {secs} s", why=f"{limit} wrong attempts in a row",
                         change=f"Keypad blocked for {secs} s", tags=["door", "security"])
        self.w.push_alert("critical", "Someone is trying to get in", f"Front door · keypad · {limit} wrong PINs",
                          "security", lines=[f"**{limit} wrong PIN attempts** at the front door",
                                             f"Today {hhmm()} · Keypad on the door node",
                                             f"Keypad is blocked for {secs} s. Motion clip was saved."])
        self.entrance_motion()

    def door_activity(self):
        if "door" in self.offline or not self.lock_id:
            return
        r = random.random()
        if r < 0.45:
            fingers = self.fingerprints()
            finger = random.choice(list(fingers))
            self.open_door("fingerprint", fingers[finger], finger=finger)
        elif r < 0.6:
            self.open_door("keypad")
        elif r < 0.8:
            self.open_door("exit_button")
        else:
            self.wrong_attempt(random.choice(["keypad", "keypad", "fingerprint"]))

    def entrance_motion(self):
        now = datetime.now()
        self.w.log_event("motion", "system", "Motion at the front door", "system", "Camera",
                         room="entrance", node="hub", by_label="Outdoor PIR on the hub",
                         short="15 s clip saved", why="Motion detected by the outdoor PIR",
                         change=f"Clip {now:%Y-%m-%d}/{now:%H%M%S}.mp4", tags=["door", "system"])
        self.w.push_alert("info", "Motion at the front door", "Entrance · camera · clip saved", "security")

    # ------------------------------------------------ automation rules
    def paused(self, device):
        """AI + comfort rules leave the device alone after manual control, or while the user paused it."""
        return time.time() < self.override_until.get(device, 0) or self.user_paused(device)

    def user_paused(self, device):
        """Explicit pause switch in the device sheet (/ai_pause/{device} = until ms)."""
        return now_ms() < int(self.ai_pause.get(device) or 0)

    def sync_ai_pause(self):
        new = self.w.get_ai_pause()
        for dev in set(new) | set(self.ai_pause):
            if dev not in self.dev_cfg or new.get(dev) == self.ai_pause.get(dev):
                continue
            paused_now = bool(new.get(dev)) and int(new[dev]) > now_ms()
            until = hhmm(datetime.fromtimestamp(int(new[dev]) / 1000)) if paused_now else None
            self.w.log_event("config", "manual", f"AI {'paused' if paused_now else 'resumed'} for {self.label(dev).lower()}",
                             "web", "App", device=dev, room=self.dev_room[dev], node="hub",
                             by_label="Owner (web app)", why="Switch in the device panel",
                             short=f"Until {until}" if paused_now else "AI can control it again",
                             change="Active → Paused" if paused_now else "Paused → Active", tags=["manual", "ai"])
        self.ai_pause = new

    def apply_rules(self):
        """Per-device rules from config (device.rules), only for devices with control.rules = true."""
        t = time.time()
        th = self.th
        for rid, room in self.config["rooms"].items():
            if room["node"] in self.offline:
                continue
            s = self.rooms[rid]
            if s.get("occ"):
                self.empty_since.pop(rid, None)
            else:
                self.empty_since.setdefault(rid, t)
            for dev, cfg in (room.get("devices") or {}).items():
                if not self.writable(dev) or not self.ctrl(dev).get("rules"):
                    continue
                st, r = self.devices[dev], self.drules(dev)
                empty_min = th["empty_room_off_min"]
                empty_for = t - self.empty_since.get(rid, t)
                rule = dict(source="rule", src_label="Rule", group="rule", by_label="Automation", tags=["rule"])
                if r.get("off_when_empty") and st["v"] and not self.user_paused(dev) \
                        and empty_for > empty_min * 60 / self.speedup:
                    saved = round(float(st.get("watts") or cfg.get("watts", 0)), 2)   # estimate over the next hour
                    if self.ai:
                        self.ai_safe(self.ai.ledger.record_saved, "rule", saved)
                    self.set_device(dev, 0, short=f"Empty for {empty_min} min · saved {saved} Wh",
                                    why=f'Rule "Turn off after empty for {empty_min} min"', saved_wh=saved, **rule)
                    self.w.push_alert("good", f"Waste stopped: {self.label(dev).lower()} off",
                                      f"{room['name']} · empty for {empty_min} min · saved {saved} Wh", "energy")
                elif self.paused(dev) or not s.get("occ"):
                    continue
                elif r.get("on_when_dark") and not st["v"] and s.get("lux", 999) < th["light_on_lux"]:
                    self.set_device(dev, 1, self.mid_level(dev), short=f"Light below {th['light_on_lux']} lx",
                                    why=f'Rule "On below {th["light_on_lux"]} lx" · light {s.get("lux")} lx · someone in the room', **rule)
                elif r.get("follow_temp") and not st["v"] and s.get("temp", 0) > th["fan_on_temp"]:
                    self.set_device(dev, 1, self.mid_level(dev), short=f"Above {th['fan_on_temp']}°C",
                                    why=f'Rule "On above {th["fan_on_temp"]}°C" · room {s.get("temp")}°C · someone in the room', **rule)
                elif r.get("follow_temp") and st["v"] and s.get("temp", 99) < th["fan_off_temp"]:
                    self.set_device(dev, 0, short=f"Below {th['fan_off_temp']}°C",
                                    why=f'Rule "Off below {th["fan_off_temp"]}°C" · room {s.get("temp")}°C', **rule)
        if self.lock_at and t >= self.lock_at and self.lock_id:
            self.lock_at = None
            st = self.devices[self.lock_id]
            st.update(v=0, at=now_ms())
            self.w.patch_device(self.lock_id, st)

    # ------------------------------------------------ monitor-only devices (power: "read")
    def simulate_monitored(self):
        """Devices we only READ (TV, fridge, ...): their on/off comes from the current they draw.
        Here we fake that current; on the Pi it comes from the INA226 (on when watts > hw.on_above_w)."""
        h = datetime.now().hour
        for dev, cfg in self.dev_cfg.items():
            if self.writable(dev) or self.is_lock(dev) or self.node_of(dev) in self.offline:
                continue
            st, rid = self.devices[dev], self.dev_room[dev]
            occ = self.rooms.get(rid, {}).get("occ", 0)
            icon = self.icon(dev)
            # this runs every state tick (5 s, or 0.5 s with --fast) — keep changes rare and "sticky"
            if icon == "fridge":     # always on; a rare power cut, back after a few minutes
                want = (0 if random.random() < 0.0004 * self.speedup else 1) if st["v"] else \
                       (1 if random.random() < 0.01 * self.speedup else 0)
            elif icon == "tv":       # evenings, when someone is in the room
                target = 1 if occ and (h >= 18 or h < 1) else 0
                want = target if random.random() < 0.01 * self.speedup else st["v"]
            else:
                want = 1 - st["v"] if random.random() < 0.002 * self.speedup else st["v"]
            if want != st["v"]:
                self.set_device(dev, want, source="system", src_label="Sensor", group="system",
                                by_label="Power sensor (INA226)", short="Detected from the current it draws",
                                why=f"Power {'above' if want else 'below'} {cfg.get('hw', {}).get('on_above_w', 0.5)} W",
                                tags=["system", "monitor"])
                if want:
                    self.off_alerted.discard(dev)
            limit = self.drules(dev).get("alert_if_off_min")
            if limit and not st["v"] and dev not in self.off_alerted \
                    and (now_ms() - st.get("at", now_ms())) / 60000 > limit / self.speedup:
                self.off_alerted.add(dev)
                self.w.push_alert("warning", f"{self.label(dev)} stopped",
                                  f"{self.room_name(rid)} · no power for {limit} min", "energy")
                self.w.log_event("alert", "system", f"{self.label(dev)} stopped", "system", "System", result="failed",
                                 device=dev, room=rid, node=self.node_of(dev), by_label="Power sensor (INA226)",
                                 short=f"Off for more than {limit} min", why="It should always be running",
                                 tags=["system", "alert"])

    # ------------------------------------------------ AI (the real model from ai/)
    def house_snapshot(self):
        return {"rooms": self.rooms, "devices": self.devices, "paused": self.ai_pause,
                "door_entry_at": self.door_entry_at, "away": self.away}

    def start_ai(self, ai_parts, cloud=False):
        """Create the AI runtime, bootstrap history if the hub is new, train if needed."""
        AIRuntime, FirebaseSink, ai_store = ai_parts
        name = "sim_cloud" if cloud else "sim"
        con = ai_store.connect(ROOT / "ai" / "data" / f"{name}.db")
        sink = FirebaseSink(self.w, room_of=lambda d: self.dev_room.get(d),
                            room_name=lambda r: self.config["rooms"].get(r, {}).get("name", r))
        self.ai = AIRuntime(self.config, con, sink, model_dir=ROOT / "ai" / "models" / name,
                            speed=self.speedup, record=True)
        self.clean_suggestions(keep_open=False)        # open ones from an earlier run can't be tracked
        t0 = time.time()
        self.ai_safe(self.ai.bootstrap_if_needed)
        self.next_train = time.time() + 86400 / self.speedup
        if self.ai:
            print(f"AI ready in {time.time() - t0:.0f} s · models in ai/models/{name} · "
                  f"{self.ai.report.get('data_source', '?')} data")

    def ai_safe(self, fn, *a, **kw):
        """The AI is an extra layer: an error in it is printed, never allowed to stop the house.
        After 5 errors in a row it is switched off for this run."""
        if not self.ai:
            return None
        try:
            out = fn(*a, **kw)
            self.ai_errors = 0
            return out
        except Exception as e:  # noqa: BLE001
            self.ai_errors = getattr(self, "ai_errors", 0) + 1
            print(f"! AI error ({type(e).__name__}: {e})" + ("" if self.ai_errors < 5 else " — AI switched off"))
            if self.ai_errors >= 5:
                self.ai = None
            return None

    def clean_suggestions(self, keep_open=True):
        """Remove answered suggestions older than a day (and, at start, open ones nobody tracks any more)."""
        day_ago = now_ms() - 86400 * 1000
        for sid, s in (self.w.get_suggestions() or {}).items():
            if not isinstance(s, dict):
                continue
            if (s.get("handled") and s.get("at", 0) < day_ago) or (not keep_open and not s.get("response")):
                self.w.delete_suggestion(sid)

    def run_ai(self):
        if not self.ai:
            return
        if time.time() >= self.next_train:            # nightly retraining (every 2.4 h with --fast)
            self.next_train = time.time() + 86400 / self.speedup
            self.ai_safe(self.ai.retrain, quiet=True)
            self.clean_suggestions()
        self.ai_safe(self.ai.plan, self.house_snapshot())

    def ai_tick(self, dt):
        if not self.ai:
            return
        for a in self.ai_safe(self.ai.tick, self.house_snapshot(), dt=dt) or []:
            dev = a["device"]
            if dev not in self.dev_cfg or self.node_of(dev) in self.offline:
                continue
            self.set_device(dev, a["v"], a.get("level") if a["v"] else None, source="ai", src_label=a["src_label"],
                            group="ai", by_label="AI (Gradient Boosting)", why=a["why"], short=a.get("short"),
                            confidence=a.get("confidence"), title=a.get("title"), saved_wh=a.get("saved_wh"),
                            tags=["ai"])
            if a.get("saved_wh"):
                rname = self.room_name(self.dev_room[dev])
                self.w.push_alert("good", f"Waste stopped: {self.label(dev).lower()} off",
                                  f"{rname} · empty room · saves ~{a['saved_wh']} Wh", "energy")

    def handle_suggestion_answers(self):
        for sid, s in self.w.get_suggestions().items():
            if not isinstance(s, dict) or not s.get("response") or s.get("handled"):
                continue
            pct = round(s.get("confidence", 0) * 100)
            accepted = s["response"] == "accept"
            if self.ai:
                self.ai_safe(self.ai.on_answer, sid, s, accepted)
            if not accepted:
                self.w.log_event("ai", "ai", f"Suggestion dismissed: {s.get('title', '').rstrip('?')}", "web",
                                 f"AI · {pct}%", device=s.get("device"), node="hub", by_label="Owner (web app)",
                                 short="The AI will learn from this (asks less at this time)", why=f"{s.get('why')}",
                                 change="No change", tags=["ai", "manual"])
            self.w.mark_suggestion(sid, {"handled": True,
                                         "done_text": ("Done." if accepted else "Okay. Your home will learn from this.")})

    # ------------------------------------------------ config changes from the web app
    def apply_config(self, cfg):
        old = self.config
        labels = {"fan_on_temp": ("Fan on above", "°C"), "fan_off_temp": ("Fan off below", "°C"),
                  "light_on_lux": ("Lights on below", " lx"), "empty_room_off_min": ("Turn off after empty for", " min")}
        for k, (label, unit) in labels.items():
            a, b = old["thresholds"].get(k), cfg["thresholds"].get(k)
            if a != b:
                self.w.log_event("config", "manual", f"Rule changed: {label.lower()} {b}{unit}", "web", "App",
                                 node="hub", by_label="Owner (web app)", why="Settings · automation rules",
                                 short=f"{a}{unit} → {b}{unit}", change=f"{a}{unit} → {b}{unit}", tags=["manual", "system"])
        old_devs = {d for r in old["rooms"].values() for d in (r.get("devices") or {})}
        self.load_config(cfg)
        if self.ai:
            self.ai_safe(self.ai.reload, cfg)
        for dev in set(self.dev_cfg) - old_devs:
            c, node = self.dev_cfg[dev], self.node_of(dev)
            ok = node not in self.offline
            self.w.log_event("config", "system", f"Device added: {c['name']}" if ok else f"Adding {c['name']} failed",
                             "web", "App", result="ok" if ok else "failed", device=dev, room=self.dev_room[dev],
                             node=node, by_label="Owner (web app)",
                             why="Settings · add device · " + (f"INA226 {c['hw']['ina']}" if c.get("hw", {}).get("ina") else "no power sensor"),
                             short=(f"{node} node" + (f" · GPIO {c['hw']['pin']}" if c.get("hw", {}).get("pin") is not None else " · monitor only"))
                             if ok else f"The {node} node didn't confirm",
                             change=(f"Config v{old['version']} → v{cfg['version']} · node confirmed" if ok
                                     else "Config not confirmed"), latency_ms=1400 if ok else 10000,
                             tags=["manual", "system"])
        time.sleep(1.0)   # the nodes download the new config and reply on home/<node>/config/ack
        for n in cfg["nodes"]:
            if n not in self.offline:
                self.node_version[n] = cfg["version"]

    # ------------------------------------------------ random life: buttons, entrance motion
    def random_life(self):
        if random.random() < 0.3:  # someone presses a wall button
            choices = [d for d in self.dev_cfg if self.writable(d) and self.ctrl(d).get("button")]
            if not choices:
                return
            dev = random.choice(choices)
            node = self.node_of(dev)
            if node not in self.offline:
                self.set_device(dev, 0 if self.devices[dev]["v"] else 1, None if self.devices[dev]["v"] else self.mid_level(dev),
                                source="button", src_label="Button",
                                group="manual", by_label="Someone at home (button)",
                                short=f"Wall button in the {self.room_name(self.dev_room[dev]).lower()}",
                                why=f"Physical button on the {node} node", tags=["manual"])

    # ------------------------------------------------ periodic writes
    def write_state(self, power):
        rooms = {r: s for r, s in self.rooms.items() if self.config["rooms"][r].get("sensors")}
        self.w.write_home_state({"rooms": rooms, "devices": self.devices, "power_w": round(power, 2),
                                 "base_w": BASE_W, "door": self.door, **({"scene": self.scene} if self.scene else {})})
        for node in self.config["nodes"]:
            self.w.set_node(node, node not in self.offline, config_version=self.node_version.get(node),
                            rssi=random.randint(-72, -48))

    def write_summary(self):
        ts = time.time()
        for rid, s in self.rooms.items():
            room = self.config["rooms"][rid]
            if not room.get("sensors") or room["node"] in self.offline:
                continue
            devs = room.get("devices") or {}
            data = {k: v for k, v in s.items() if k in ("temp", "hum", "lux", "occ")}
            data["watts"] = round(sum(self.devices[d]["watts"] for d in devs), 2)
            dv = {d: self.devices[d]["v"] for d in devs if not self.is_lock(d)}
            if dv:
                data["dev"] = dv
            self.w.write_summary(rid, ts, data)
        day = day_key()
        if day != self.energy_day:          # midnight rollover
            self.energy_day, self.energy = day, {}
        self.w.write_energy_day(day, self.energy)

    # ------------------------------------------------ main loop
    def run(self):
        print(f"house running  fast={self.args.fast}  offline={sorted(self.offline) or '-'}  "
              f"fail_rate={self.args.fail_rate}   Ctrl+C to stop")
        for node in self.offline:
            rooms = " & ".join(r["name"] for r in self.config["rooms"].values() if r["node"] == node and not r.get("hidden"))
            self.w.log_event("node", "system", f"{self.node_name(node)} went offline", "system", "System",
                             result="failed", node=node, by_label="Hub watchdog", short="No data for 2 min",
                             why="No MQTT heartbeat for 120 s", change="Online → Offline", tags=["system"])
            self.w.push_alert("warning", f"{self.node_name(node)} is offline", f"{rooms or node} · ESP32 node · now", "settings")
        if self.args.lockout:
            for _ in range(self.th["door_lockout_attempts"]):
                self.wrong_attempt("keypad")
        last_tick = time.time()
        sp = self.speedup
        while True:
            t = time.time()
            dt, last_tick = t - last_tick, t
            try:
                if t - self.last.get("cmd", 0) >= self.poll_s:
                    self.last["cmd"] = t
                    self.handle_commands()
                if t - self.last.get("cfg", 0) >= self.config_poll_s:
                    self.last["cfg"] = t
                    cfg = self.w.get_config()
                    if cfg and json.dumps(cfg, sort_keys=True) != json.dumps(self.config, sort_keys=True):
                        self.apply_config(cfg)
                if t - self.last["state"] >= 5 / sp:
                    self.last["state"] = t
                    self.sync_ai_pause()
                    self.update_sensors()
                    self.simulate_monitored()
                    self.apply_rules()
                    power = self.update_power(dt if dt < 60 else 0)
                    if self.ai and self.ai.suggestions:   # read answers first, so "Yes" is never seen as expired
                        self.handle_suggestion_answers()
                    self.ai_tick(t - self.last.get("ai_tick", t))
                    self.last["ai_tick"] = t
                    self.write_state(power)
                else:
                    self.update_power(dt if dt < 60 else 0)
                if t - self.last["sugg"] >= 2:
                    self.last["sugg"] = t
                    self.handle_suggestion_answers()
                if t - self.last["summary"] >= 60 / sp:
                    self.last["summary"] = t
                    self.write_summary()
                if t - self.last["ai"] >= 900 / sp:
                    self.last["ai"] = t
                    self.run_ai()
                if t - self.last["random"] >= 120 / sp:
                    self.last["random"] = t
                    self.random_life()
                if t - self.last["door"] >= 300 / sp:
                    self.last["door"] = t
                    self.door_activity()
                if t - self.last["motion"] >= 400 / sp and random.random() < 0.5:
                    self.last["motion"] = t
                    self.entrance_motion()
            except (RuntimeError, OSError) as e:
                hint = "check your internet / key" if self.args.cloud else "are the emulators running? `firebase emulators:start`"
                print(f"! {e}  ({hint})")
                time.sleep(3)
            time.sleep(TICK_S)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true", help="10x faster rules, summaries, AI and random events")
    ap.add_argument("--offline", action="append", metavar="NODE", help="simulate a node offline (door/living/bedroom)")
    ap.add_argument("--fail-rate", type=float, default=0.0, help="fraction of commands that fail (0..1)")
    ap.add_argument("--lockout", action="store_true", help="5 wrong PINs at the door right away")
    ap.add_argument("--reset", action="store_true", help="wipe the database and re-seed")
    ap.add_argument("--cloud", metavar="KEY_JSON",
                    help="write to the REAL Firebase project using this service-account key (instead of the emulator)")
    ap.add_argument("--owner-uid", metavar="UID",
                    help="with --cloud: the UID of the owner account you created in Firebase Authentication")
    ap.add_argument("--fault", action="append", metavar="DEVICE[:KIND]",
                    help="simulate an energy fault: high (2x power, default), dead (no power), standby (power while off)")
    ap.add_argument("--no-ai", action="store_true", help="run without the AI")
    args = ap.parse_args()

    if args.cloud:
        from cloud_db import cloud_db
        w = Writer(cloud_db(args.cloud))
        print(f"REAL Firebase · project {w.db.project_id} · {w.db.base}")
        if args.reset:
            if input("This wipes the REAL database. Type YES to continue: ") != "YES":
                sys.exit("cancelled")
    else:
        w = Writer(RestDB())
        print(f"project {PROJECT_ID} · database {w.db.base} · ns {w.db.ns}")
    try:
        config = seed(w, args.reset, cloud=bool(args.cloud))
    except (RuntimeError, OSError) as e:
        if args.cloud:
            sys.exit(f"Cannot reach the real database: {e}")
        sys.exit(f"Cannot reach the Database emulator: {e}\nStart it first:  firebase emulators:start")
    if args.cloud:
        if args.owner_uid:
            w.db.put(f"users/{args.owner_uid}", {"role": "owner", "name": "Owner"})
            print(f"owner role set for {args.owner_uid}")
        elif not w.db.get("users"):
            print("! no owner yet: run once with --owner-uid <UID from Firebase Authentication>, "
                  "otherwise you can log in but cannot change settings")
    else:
        uid = ensure_owner(w)
        if uid:
            print(f"owner login: {OWNER_EMAIL} / {OWNER_PASSWORD}")
    backfill_energy(w, config)
    house = House(w, config, args)
    house.update_sensors()
    ai_parts = load_ai(args)
    if ai_parts:
        try:
            house.start_ai(ai_parts, cloud=bool(args.cloud))
        except Exception as e:  # noqa: BLE001 — the house runs without the AI
            print(f"! AI could not start ({type(e).__name__}: {e}) — running without it")
            house.ai = None
    if not house.ai:
        w.write_ai_insights({"status": "off"})
    house.run_ai()
    try:
        house.run()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
