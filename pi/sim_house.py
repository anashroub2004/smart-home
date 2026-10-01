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

What it does:
    - seeds /config from docs/seed.json and creates the owner login (owner@home.test / password123)
    - every tick: executes /commands (done/failed + event), writes /home_state and /nodes
    - rules: empty room -> off, dark + occupied -> lights on, hot + occupied -> fan on, cool -> fan off
    - AI: /ai_schedule plan + /suggestions (same decision rules as ai/smart_home_ai.py)
    - door: fingerprint / keypad / exit button / wrong PINs / lockout, entrance motion + camera clips
    - /alerts for the Activity screen, /summaries + /energy_daily every minute
Python standard library only.
"""
import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from firebase_writer import Writer, day_key, now_ms  # noqa: E402
from rest_db import PROJECT_ID, RestDB  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "docs" / "seed.json"
AUTH_URL = "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1"
OWNER_EMAIL, OWNER_PASSWORD = "owner@home.test", "password123"
TICK_S = 0.5
BASE_W = 6.8            # Raspberry Pi + 3 ESP32 nodes + sensors
SPEEDS = {"Low": 40, "Medium": 70, "High": 100}


def speed_name(v):
    v = v or 70
    return "High" if v >= 100 else "Medium" if v >= 70 else "Low"


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


def seed(w, reset):
    seed_cfg = json.loads(SEED.read_text(encoding="utf-8"))["config"]
    config = None if reset else w.get_config()
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


def write_ai_insights(w):
    """What the AI service publishes after its nightly training (see ai/smart_home_ai.py train)."""
    retrained = datetime.now().replace(hour=3, minute=0, second=0, microsecond=0)
    if retrained > datetime.now():
        retrained -= timedelta(days=1)
    w.write_ai_insights({
        "metrics": {"within_15": 0.93, "exact": 0.75, "retrained_at": int(retrained.timestamp() * 1000),
                    "model": "Gradient Boosting", "devices": 5},
        "learned": [
            "You usually get home around **16:30** on weekdays.",
            "You turn the bedroom fan on most nights when the room is above **27°C**.",
            "Living room lights stay on after sunset while someone is there.",
        ],
    })


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
        self.node_version = {}           # node -> config version it confirmed
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
        self.devices = {d: old.get(d, {"v": 0, "src": "system", "at": now_ms(), "watts": 0,
                                       **({"speed": 70} if c["type"] == "fan" else {})})
                        for d, c in self.dev_cfg.items()}
        old_rooms = getattr(self, "rooms", {})
        self.rooms = {r: old_rooms.get(r, {"occ": 0}) for r in config["rooms"]}

    # ------------------------------------------------ names
    def node_of(self, device):
        return self.config["rooms"][self.dev_room[device]]["node"]

    def room_name(self, rid):
        return self.config["rooms"][rid]["name"]

    def label(self, device):
        """'Living room fan', 'Bedroom lights', 'Washer', 'Front door'"""
        cfg = self.dev_cfg[device]
        if cfg["type"] in ("washer", "lock"):
            return cfg["name"]
        return f"{self.room_name(self.dev_room[device])} {cfg['name'].lower()}"

    def state_label(self, device, st):
        cfg = self.dev_cfg[device]
        if not st.get("v"):
            return "Off"
        return f"On · {speed_name(st.get('speed'))}" if cfg["type"] == "fan" else "On"

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
            devs = room.get("devices") or {}
            if "temp" in sensors:
                fan_cool = -1.2 if any(self.devices[d]["v"] and self.dev_cfg[d]["type"] == "fan" for d in devs) else 0
                s["temp"] = round(outdoor + (0.8 if rid == "bedroom" else 0) + fan_cool + random.gauss(0, 0.12), 1)
            if "hum" in sensors:
                s["hum"] = round(50 - (outdoor - 27) * 1.5 + random.gauss(0, 1))
            if "lux" in sensors:
                lights = 250 if any(self.devices[d]["v"] and self.dev_cfg[d]["type"] == "light" for d in devs) else 0
                s["lux"] = round(max(0, 600 * daylight + lights + random.gauss(0, 6)))

    def update_power(self, dt):
        total = BASE_W
        self.energy["_base"] = self.energy.get("_base", 0) + BASE_W * dt / 3600
        for d, st in self.devices.items():
            cfg = self.dev_cfg[d]
            if st["v"] and cfg.get("watts"):
                factor = (st.get("speed", 70) / 100) if cfg["type"] == "fan" else 1
                st["watts"] = round(cfg["watts"] * factor * random.uniform(0.97, 1.03), 2)
            else:
                st["watts"] = 0
            total += st["watts"]
            if cfg.get("watts"):
                self.energy[d] = self.energy.get(d, 0) + st["watts"] * dt / 3600
        return total

    # ------------------------------------------------ changing a device (single code path)
    def set_device(self, device, v, speed=None, *, source, src_label, group, why, by=None, by_label=None,
                   short=None, confidence=None, latency_ms=None, kind="device", tags=None, saved_wh=None,
                   title=None, change=None):
        cfg = self.dev_cfg[device]
        st = self.devices[device]
        before = {k: st.get(k) for k in ("v", "speed") if st.get(k) is not None}
        before_label = self.state_label(device, st)
        speed_only = cfg["type"] == "fan" and st["v"] and v and speed and speed != st.get("speed")
        st.update(v=int(v), src=source, at=now_ms())
        if speed is not None and cfg["type"] == "fan":
            st["speed"] = int(speed)
        after = {k: st.get(k) for k in ("v", "speed") if st.get(k) is not None}
        after_label = self.state_label(device, st)
        if source in ("web", "button"):
            self.override_until[device] = time.time() + self.th["override_pause_min"] * 60 / self.speedup
        if title is None:
            title = f"{self.label(device)} {'speed changed' if speed_only else 'turned on' if v else 'turned off'}"
        if speed_only and short is None:
            short = f"{speed_name(before.get('speed'))} → {speed_name(speed)}"
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
            key = (cmd.get("at"), cmd.get("v"), cmd.get("speed"))
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
            node = self.node_of(device)
            fail = None
            if node in self.offline:
                fail = f"No response from the {node} node"
            elif random.random() < self.args.fail_rate:
                fail = f"No response from the {node} node"
            if fail:
                time.sleep(0.3)
                self.w.ack_command(device, False, fail)
                st = self.devices[device]
                title = (f"Remote unlock failed" if cfg["type"] == "lock" else
                         f"{self.label(device)} {'turned on' if cmd.get('v') else 'turned off'}")
                self.w.log_event("door" if cfg["type"] == "lock" else "device",
                                 "door" if cfg["type"] == "lock" else meta["group"], title, meta["source"],
                                 meta["src_label"], result="failed", device=device, room=self.dev_room[device],
                                 node=node, by=cmd.get("by"), by_label=meta["by_label"], why=meta["why"],
                                 short=fail, change=f"{self.state_label(device, st)} (unchanged)",
                                 latency_ms=10000, tags=meta["tags"] + ["failed"])
                continue
            time.sleep(random.uniform(0.2, 0.6))  # network + ESP32 round trip
            latency = max(0, now_ms() - int(cmd.get("at") or now_ms()))
            if cfg["type"] == "lock":
                self.open_door("web", "Owner", latency)
            else:
                self.set_device(device, cmd["v"], cmd.get("speed"), by=cmd.get("by"), latency_ms=latency, **meta)
            self.w.ack_command(device, True)

    # ------------------------------------------------ door
    def open_door(self, method, who=None, latency=None, finger=None):
        if "door_lock" not in self.dev_cfg:
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
        self.set_device("door_lock", 1, source=method, src_label=labels[method], group="door", kind="door",
                        title=titles.get(method, "Front door unlocked"), short=shorts[method], why=whys[method],
                        by_label=by_labels[method], change="Locked → Unlocked → Locked (5 s)",
                        latency_ms=latency or random.randint(150, 700), tags=["door"] + (["manual"] if method == "web" else []))
        self.door["last_open"] = {"at": now_ms(), "method": method, **({"who": who} if who else {})}
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
                         device="door_lock", room="entrance", node="door", by_label="Unknown",
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
                         device="door_lock", room="entrance", node="door", by_label="Unknown",
                         short=f"{limit} wrong PINs · locked {secs} s", why=f"{limit} wrong attempts in a row",
                         change=f"Keypad blocked for {secs} s", tags=["door", "security"])
        self.w.push_alert("critical", "Someone is trying to get in", f"Front door · keypad · {limit} wrong PINs",
                          "security", lines=[f"**{limit} wrong PIN attempts** at the front door",
                                             f"Today {hhmm()} · Keypad on the door node",
                                             f"Keypad is blocked for {secs} s. Motion clip was saved."])
        self.entrance_motion()

    def door_activity(self):
        if "door" in self.offline or "door_lock" not in self.dev_cfg:
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
                st = self.devices[dev]
                if cfg["type"] in ("lock", "washer"):
                    continue
                empty_min = th["empty_room_off_min"]
                empty_for = t - self.empty_since.get(rid, t)
                rule = dict(source="rule", src_label="Rule", group="rule", by_label="Automation", tags=["rule"])
                if st["v"] and not self.user_paused(dev) and empty_for > empty_min * 60 / self.speedup:
                    saved = round(cfg.get("watts", 0) * empty_min / 60 * 2, 1)
                    self.set_device(dev, 0, short=f"Empty for {empty_min} min · saved {saved} Wh",
                                    why=f'Rule "Turn off after empty for {empty_min} min"', saved_wh=saved, **rule)
                    self.w.push_alert("good", f"Waste stopped: {self.label(dev).lower()} off",
                                      f"{room['name']} · empty for {empty_min} min · saved {saved} Wh", "energy")
                elif self.paused(dev) or not s.get("occ"):
                    continue
                elif cfg["type"] == "light" and not st["v"] and s.get("lux", 999) < th["light_on_lux"]:
                    self.set_device(dev, 1, short=f"Light below {th['light_on_lux']} lx",
                                    why=f'Rule "Lights on below {th["light_on_lux"]} lx" · light {s.get("lux")} lx · someone in the room', **rule)
                elif cfg["type"] == "fan" and not st["v"] and s.get("temp", 0) > th["fan_on_temp"]:
                    self.set_device(dev, 1, 70, short=f"Above {th['fan_on_temp']}°C",
                                    why=f'Rule "Fan on above {th["fan_on_temp"]}°C" · room {s.get("temp")}°C · someone in the room', **rule)
                elif cfg["type"] == "fan" and st["v"] and s.get("temp", 99) < th["fan_off_temp"]:
                    self.set_device(dev, 0, short=f"Below {th['fan_off_temp']}°C",
                                    why=f'Rule "Fan off below {th["fan_off_temp"]}°C" · room {s.get("temp")}°C', **rule)
        if self.lock_at and t >= self.lock_at and "door_lock" in self.dev_cfg:
            self.lock_at = None
            st = self.devices["door_lock"]
            st.update(v=0, at=now_ms())
            self.w.patch_device("door_lock", st)

    # ------------------------------------------------ AI (same decision rules as ai/smart_home_ai.py)
    def run_ai(self):
        decisions = []
        act, suggest = self.th["ai_act_at"], self.th["ai_suggest_at"]
        open_sugg = {s.get("device") for s in self.w.get_suggestions().values() if not s.get("response")}
        for dev, cfg in self.dev_cfg.items():
            if cfg["type"] not in ("fan", "light"):
                continue
            rid = self.dev_room[dev]
            room = self.rooms[rid]
            temp = room.get("temp", 26)
            p = 0.15 + 0.5 * room.get("occ", 0) + (0.25 if cfg["type"] == "fan" and temp > 27.5 else 0) \
                + (0.2 if cfg["type"] == "light" and room.get("lux", 999) < 200 else 0)
            p = round(min(0.97, max(0.03, p + random.gauss(0, 0.08))), 2)
            pct = round(p * 100)
            when = datetime.now() + timedelta(minutes=random.choice([15, 30, 45, 60]))
            d = {"device": dev, "p_on": p, "predicted_for": when.isoformat(timespec="minutes"),
                 "time": hhmm(when), "action": "none"}
            st = self.devices[dev]
            label = self.label(dev)
            if self.paused(dev):
                d["action"] = "paused_by_override"
            elif st["v"] and p >= act:
                d.update(action="keep_on", title=f"{label} stays on",
                         why=(f"After sunset you usually stay in the {self.room_name(rid).lower()} until 22:30."
                              if cfg["type"] == "light" else
                              f"The room is {temp}°C and you are usually here now."))
            elif not st["v"] and p >= act:
                d.update(action="schedule_on", execute_at=when.isoformat(timespec="minutes"),
                         title=f"{label} turns on",
                         why=(f"Pre-cooling: the {self.room_name(rid).lower()} is {temp}°C and you usually move there by "
                              f"{hhmm(when + timedelta(minutes=15))}." if cfg["type"] == "fan" else
                              f"It gets dark around then and you are usually in the {self.room_name(rid).lower()}."))
                if self.node_of(dev) not in self.offline and random.random() < 0.5:
                    self.set_device(dev, 1, 70 if cfg["type"] == "fan" else None, source="ai",
                                    src_label=f"AI · {pct}%", group="ai", by_label="AI (Gradient Boosting)",
                                    short="Pre-cooling before you got here" if cfg["type"] == "fan" else "Getting dark",
                                    why=f"{d['why']} · {pct}% sure", confidence=p, tags=["ai"])
            elif not st["v"] and p >= suggest and dev not in open_sugg:
                d["action"] = "suggest_on"
                self.w.push_suggestion(dev, "on", p, f"Turn on the {label.lower()}?",
                                       "You usually use it around this time")
            elif st["v"] and p <= 1 - act and dev not in open_sugg:
                d["action"] = "suggest_off"
                self.w.push_suggestion(dev, "off", 1 - p, f"Turn off the {label.lower()}?",
                                       "The room is cooling down and it is usually off by now" if cfg["type"] == "fan"
                                       else f"Nobody has been in the {self.room_name(rid).lower()} for a while")
            decisions.append(d)
        self.w.write_ai_schedule(decisions)

    def handle_suggestion_answers(self):
        for sid, s in self.w.get_suggestions().items():
            if not isinstance(s, dict) or not s.get("response") or s.get("handled"):
                continue
            pct = round(s.get("confidence", 0) * 100)
            accepted = s["response"] == "accept"
            if not accepted:
                self.w.log_event("ai", "ai", f"Suggestion dismissed: {s.get('title', '').rstrip('?')}", "web",
                                 f"AI · {pct}%", device=s.get("device"), node="hub", by_label="Owner (web app)",
                                 short="The AI will learn from this", why=f"{s.get('why')} · {pct}% sure",
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
        for dev in set(self.dev_cfg) - old_devs:
            c, node = self.dev_cfg[dev], self.node_of(dev)
            ok = node not in self.offline
            self.w.log_event("config", "system", f"Device added: {c['name']}" if ok else f"Adding {c['name']} failed",
                             "web", "App", result="ok" if ok else "failed", device=dev, room=self.dev_room[dev],
                             node=node, by_label="Owner (web app)",
                             why="Settings · add device · " + (f"INA226 {c['ina']}" if c.get("ina") else "no power sensor"),
                             short=f"{node} node · GPIO {c['pin']}" if ok else f"The {node} node didn't confirm",
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
            dev = random.choice([d for d, c in self.dev_cfg.items() if c["type"] != "lock"])
            node = self.node_of(dev)
            if node not in self.offline:
                self.set_device(dev, 0 if self.devices[dev]["v"] else 1, source="button", src_label="Button",
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
            if devs:
                data["dev"] = {d: self.devices[d]["v"] for d in devs}
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
                self.handle_commands()
                cfg = self.w.get_config()
                if cfg and json.dumps(cfg, sort_keys=True) != json.dumps(self.config, sort_keys=True):
                    self.apply_config(cfg)
                if t - self.last["state"] >= 5 / sp:
                    self.last["state"] = t
                    self.sync_ai_pause()
                    self.update_sensors()
                    self.apply_rules()
                    self.write_state(self.update_power(dt if dt < 60 else 0))
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
                print(f"! {e}  (are the emulators running? `firebase emulators:start`)")
                time.sleep(3)
            time.sleep(TICK_S)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true", help="10x faster rules, summaries, AI and random events")
    ap.add_argument("--offline", action="append", metavar="NODE", help="simulate a node offline (door/living/bedroom)")
    ap.add_argument("--fail-rate", type=float, default=0.0, help="fraction of commands that fail (0..1)")
    ap.add_argument("--lockout", action="store_true", help="5 wrong PINs at the door right away")
    ap.add_argument("--reset", action="store_true", help="wipe the database and re-seed")
    args = ap.parse_args()

    w = Writer(RestDB())
    print(f"project {PROJECT_ID} · database {w.db.base} · ns {w.db.ns}")
    try:
        config = seed(w, args.reset)
    except (RuntimeError, OSError) as e:
        sys.exit(f"Cannot reach the Database emulator: {e}\nStart it first:  firebase emulators:start")
    uid = ensure_owner(w)
    if uid:
        print(f"owner login: {OWNER_EMAIL} / {OWNER_PASSWORD}")
    backfill_energy(w, config)
    write_ai_insights(w)
    house = House(w, config, args)
    house.update_sensors()
    house.run_ai()
    try:
        house.run()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
