"""
House simulator — behaves exactly like the Raspberry Pi services, so the web app can be built
before any hardware exists. Writes through firebase_writer.py (the same code the Pi uses).

Run (with the Firebase emulators already running):
    python pi/sim_house.py                 # normal
    python pi/sim_house.py --fast          # rules/summaries ~10x faster (demo mode)
    python pi/sim_house.py --offline bedroom   # bedroom node is offline
    python pi/sim_house.py --fail-rate 0.3     # 30% of commands fail
    python pi/sim_house.py --reset         # wipe the database and re-seed docs/seed.json

What it does:
    - seeds /config from docs/seed.json and creates the owner login (owner@home.test / password123)
    - every tick: executes /commands (done/failed + event), writes /home_state and /nodes
    - applies rules (empty room -> off, dark + occupied -> light on), random button presses,
      AI schedule/suggestions, door access attempts, entrance motion
    - every minute: /summaries and /energy_daily
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
    try:
        try:
            uid = call("accounts:signUp", body)["localId"]
        except urllib.error.HTTPError:  # EMAIL_EXISTS
            uid = call("accounts:signInWithPassword", body)["localId"]
    except Exception as e:  # auth emulator not running
        print(f"! could not reach the Auth emulator ({e}). Login will not work.")
        return None
    w.db.put(f"users/{uid}", {"role": "owner", "name": "Owner"})
    return uid


def seed(w, reset):
    if reset:
        w.db.put("", {})
        print("database wiped")
    config = w.get_config()
    if not config:
        config = json.loads(SEED.read_text(encoding="utf-8"))["config"]
        w.put_config(config)
        print(f"seeded /config from {SEED.relative_to(ROOT)}")
    return config


def backfill_energy(w, config):
    """6 days of fake energy history so the Energy chart is not empty on day one."""
    for d in range(1, 7):
        day = day_key(time.time() - d * 86400)
        if w.get_energy_day(day):
            continue
        per = {}
        for room in config["rooms"].values():
            for dev, cfg in (room.get("devices") or {}).items():
                if cfg["watts"]:
                    per[dev] = cfg["watts"] * random.uniform(1.5, 7)  # 1.5–7 h of use
        w.write_energy_day(day, per)


# ---------------------------------------------------------------- the house
class House:
    def __init__(self, w, config, args):
        self.w, self.args = w, args
        self.speedup = 10 if args.fast else 1
        self.offline = set(args.offline or [])
        self.load_config(config)
        self.override_until = {}         # device -> ts; manual control pauses AI/rules
        self.empty_since = {}            # room -> ts
        self.energy_day = day_key()
        self.energy = dict(w.get_energy_day(self.energy_day))
        self.failed_door = 0
        self.lock_at = None
        self.last = dict(state=0, summary=0, ai=0, random=0, door=0, motion=0)
        self.seen_cmd_at = {}

    def load_config(self, config):
        self.config = config
        self.th = config["thresholds"]
        self.dev_cfg, self.dev_room = {}, {}
        for rid, room in config["rooms"].items():
            for dev, cfg in (room.get("devices") or {}).items():
                self.dev_cfg[dev], self.dev_room[dev] = cfg, rid
        old = getattr(self, "devices", {})
        self.devices = {d: old.get(d, {"v": 0, "src": "system", "at": now_ms(), "watts": 0})
                        for d in self.dev_cfg}
        old_rooms = getattr(self, "rooms", {})
        self.rooms = {r: old_rooms.get(r, {"occ": 0}) for r in config["rooms"]}

    def node_of(self, device):
        return self.config["rooms"][self.dev_room[device]]["node"]

    # ------------------------------------------------ sensors
    def update_sensors(self):
        now = datetime.now()
        h = now.hour + now.minute / 60
        outdoor = 27 + 5 * math.sin((h - 9) / 24 * 2 * math.pi)
        daylight = max(0.0, math.sin((h - 6) / 13 * math.pi)) if 6 <= h <= 19 else 0
        for rid, room in self.config["rooms"].items():
            if room["node"] in self.offline:
                continue
            s = self.rooms[rid]
            sensors = room.get("sensors", [])
            # occupancy: random walk, someone tends to be in living (day) or bedroom (night)
            likely = 0.55 if (rid == "living" and 8 <= h < 23) or (rid == "bedroom" and (h >= 23 or h < 8)) else 0.12
            if random.random() < 0.04 * self.speedup:
                new = int(random.random() < likely)
                if new and not s.get("occ"):
                    s["motion_at"] = now_ms()
                s["occ"] = new
            if "temp" in sensors:
                fan_cool = -1.2 if any(self.devices[d]["v"] and self.dev_cfg[d]["type"] == "fan"
                                       for d in room.get("devices") or {}) else 0
                s["temp"] = round(outdoor + (1 if rid == "bedroom" else 0) + fan_cool + random.gauss(0, 0.15), 1)
            if "hum" in sensors:
                s["hum"] = round(50 - (outdoor - 27) * 1.5 + random.gauss(0, 1), 0)
            if "lux" in sensors:
                lights = 250 if any(self.devices[d]["v"] and self.dev_cfg[d]["type"] == "light"
                                    for d in room.get("devices") or {}) else 0
                s["lux"] = round(max(0, 600 * daylight + lights + random.gauss(0, 8)), 0)

    def update_power(self, dt):
        total = 0.0
        for d, st in self.devices.items():
            cfg = self.dev_cfg[d]
            if st["v"] and cfg["watts"]:
                factor = (st.get("speed", 100) / 100) if cfg["type"] == "fan" else 1
                st["watts"] = round(cfg["watts"] * factor * random.uniform(0.95, 1.05), 2)
            else:
                st["watts"] = 0
            total += st["watts"]
            if cfg["watts"]:
                self.energy[d] = self.energy.get(d, 0) + st["watts"] * dt / 3600
        return total

    # ------------------------------------------------ changing a device (single code path)
    def set_device(self, device, v, speed=None, source="web", trigger="manual", by=None,
                   confidence=None, latency_ms=None, kind="device", tags=None):
        cfg = self.dev_cfg[device]
        before = {k: self.devices[device].get(k) for k in ("v", "speed") if self.devices[device].get(k) is not None}
        st = self.devices[device]
        st.update(v=int(v), src=source, at=now_ms())
        if speed is not None and cfg["type"] == "fan":
            st["speed"] = int(speed)
        after = {k: st.get(k) for k in ("v", "speed") if st.get(k) is not None}
        if source in ("web", "button"):
            self.override_until[device] = time.time() + self.th["override_pause_min"] * 60 / self.speedup
        if cfg["type"] == "lock":
            text = f"{cfg['name']} {'unlocked' if v else 'locked'}"
        else:
            text = f"{cfg['name']} turned {'on' if v else 'off'}" + \
                   (f" ({st['speed']}%)" if v and cfg["type"] == "fan" and st.get("speed") else "")
        room_name = self.config["rooms"][self.dev_room[device]]["name"]
        self.w.log_event(kind, f"{room_name} · {text}", source, device=device, room=self.dev_room[device],
                         by=by, trigger=trigger, confidence=confidence, from_=before, to=after,
                         latency_ms=latency_ms, tags=tags or [source])
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
            latency = max(0, now_ms() - int(cmd.get("at") or now_ms()))
            trigger = f"scene:{cmd['scene']}" if cmd.get("scene") else "manual"
            if device not in self.dev_cfg:
                self.w.ack_command(device, False, "unknown device")
                continue
            if self.node_of(device) in self.offline:
                self.w.ack_command(device, False, "node offline")
                self.w.log_event("device", f"{self.dev_cfg[device]['name']}: command failed (node offline)", "web",
                                 result="failed", device=device, room=self.dev_room[device], by=cmd.get("by"),
                                 trigger=trigger, latency_ms=latency, tags=["web", "failed"])
                continue
            time.sleep(random.uniform(0.15, 0.5))  # network + ESP32 round trip
            if random.random() < self.args.fail_rate:
                self.w.ack_command(device, False, "no ack from node")
                self.w.log_event("device", f"{self.dev_cfg[device]['name']}: no response from node", "web",
                                 result="failed", device=device, room=self.dev_room[device], by=cmd.get("by"),
                                 trigger=trigger, latency_ms=latency, tags=["web", "failed"])
                continue
            latency = max(0, now_ms() - int(cmd.get("at") or now_ms()))
            kind = "scene" if cmd.get("scene") else "device"
            if self.dev_cfg[device]["type"] == "lock":
                self.set_device(device, 1, source="web", trigger=trigger, by=cmd.get("by"),
                                latency_ms=latency, kind="door", tags=["web", "door"])
                self.w.log_access("web", True, "owner")
                self.lock_at = time.time() + 5
            else:
                self.set_device(device, cmd["v"], cmd.get("speed"), source="web", trigger=trigger,
                                by=cmd.get("by"), latency_ms=latency, kind=kind)
            self.w.ack_command(device, True)

    # ------------------------------------------------ automation rules
    def paused(self, device):
        return time.time() < self.override_until.get(device, 0)

    def apply_rules(self):
        t = time.time()
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
                empty_for = t - self.empty_since.get(rid, t)
                if st["v"] and empty_for > self.th["empty_room_off_min"] * 60 / self.speedup:
                    self.set_device(dev, 0, source="rule", trigger="empty_room",
                                    tags=["rule", "energy_saving"])
                elif (cfg["type"] == "light" and not st["v"] and s.get("occ") and not self.paused(dev)
                      and s.get("lux", 999) < self.th["light_on_lux"]):
                    self.set_device(dev, 1, source="rule", trigger="low_lux", tags=["rule"])
        if self.lock_at and t >= self.lock_at and "door_lock" in self.dev_cfg:
            self.lock_at = None
            self.set_device("door_lock", 0, source="system", trigger="auto_relock", kind="door", tags=["door"])

    # ------------------------------------------------ AI (same decisions shape as ai/smart_home_ai.py)
    def run_ai(self):
        decisions = []
        for dev, cfg in self.dev_cfg.items():
            if cfg["type"] not in ("fan", "light"):
                continue
            room = self.rooms[self.dev_room[dev]]
            p = 0.15 + 0.5 * room.get("occ", 0) + (0.25 if cfg["type"] == "fan" and room.get("temp", 0) > 28 else 0)
            p = round(min(0.97, max(0.02, p + random.gauss(0, 0.08))), 2)
            target = datetime.now() + timedelta(minutes=60)
            d = {"device": dev, "p_on": p, "predicted_for": target.isoformat(timespec="minutes"), "action": "none"}
            st = self.devices[dev]
            if self.paused(dev):
                d["action"] = "paused_by_override"
            elif p >= self.th["ai_act_at"] and not st["v"]:
                d["action"] = "schedule_on"
                d["execute_at"] = target.isoformat(timespec="minutes")
                if self.node_of(dev) not in self.offline and random.random() < 0.5:
                    self.set_device(dev, 1, 60 if cfg["type"] == "fan" else None, source="ai",
                                    trigger="ai_schedule", confidence=p, tags=["ai"])
            elif p >= self.th["ai_suggest_at"] and not st["v"]:
                d["action"] = "suggest_on"
                open_for_dev = [s for s in self.w.get_suggestions().values()
                                if s.get("device") == dev and not s.get("response")]
                if not open_for_dev:
                    self.w.push_suggestion(dev, "on", p, f"Turn on {self.config['rooms'][self.dev_room[dev]]['name']} {cfg['name'].lower()}?")
                    self.w.log_event("ai", f"Suggested turning on {cfg['name']}", "ai", device=dev,
                                     room=self.dev_room[dev], trigger="ai_suggestion", confidence=p, tags=["ai"])
            decisions.append(d)
        self.w.write_ai_schedule(decisions)

    # ------------------------------------------------ random life: buttons, door, entrance motion
    def random_life(self):
        r = random.random()
        if r < 0.25:  # someone presses a physical button
            dev = random.choice([d for d, c in self.dev_cfg.items() if c["type"] != "lock"])
            if self.node_of(dev) not in self.offline:
                self.set_device(dev, 0 if self.devices[dev]["v"] else 1, source="button", trigger="manual",
                                tags=["button"])

    def door_activity(self):
        if "door" in self.offline or "door_lock" not in self.dev_cfg:
            return
        if random.random() < 0.7:
            method = random.choice(["fingerprint", "fingerprint", "keypad", "exit_button"])
            self.failed_door = 0
            self.w.log_access(method, True, "owner" if method == "fingerprint" else None)
            self.set_device("door_lock", 1, source=method, trigger=method, kind="door", tags=["door"])
            self.lock_at = time.time() + 5
        else:
            method = random.choice(["fingerprint", "keypad"])
            self.failed_door += 1
            self.w.log_access(method, False)
            self.w.log_event("door", f"Front door · access denied ({method})", method, result="denied",
                             device="door_lock", room="entrance", trigger=method, tags=["door", "denied"])
            if self.failed_door >= self.th["door_lockout_attempts"]:
                self.failed_door = 0
                self.w.push_alert(f"{self.th['door_lockout_attempts']} failed attempts at the front door — keypad locked for 5 min")
                self.w.log_event("alert", "Door lockout after repeated failed attempts", "system",
                                 room="entrance", trigger="lockout", tags=["security"])

    def entrance_motion(self):
        self.w.log_event("motion", "Motion at entrance · camera recording 30 s", "system",
                         room="entrance", trigger="outdoor_pir", tags=["camera"])

    # ------------------------------------------------ periodic writes
    def write_state(self, power):
        rooms = {r: s for r, s in self.rooms.items() if self.config["rooms"][r].get("sensors")}
        self.w.write_home_state(rooms, self.devices, power)
        for node in self.config["nodes"]:
            self.w.set_node(node, node not in self.offline,
                            config_version=self.config["version"], rssi=random.randint(-72, -48))

    def write_summary(self):
        ts = time.time()
        for rid, s in self.rooms.items():
            if not self.config["rooms"][rid].get("sensors"):
                continue
            watts = sum(self.devices[d]["watts"] for d in (self.config["rooms"][rid].get("devices") or {}))
            self.w.write_summary(rid, ts, {**{k: v for k, v in s.items() if k != "motion_at"}, "watts": round(watts, 2)})
        day = day_key()
        if day != self.energy_day:          # midnight rollover
            self.energy_day, self.energy = day, {}
        self.w.write_energy_day(day, self.energy)

    # ------------------------------------------------ main loop
    def run(self):
        print(f"house running  fast={self.args.fast}  offline={sorted(self.offline) or '-'}  "
              f"fail_rate={self.args.fail_rate}   Ctrl+C to stop")
        for node in self.offline:
            self.w.log_event("node", f"{self.config['nodes'][node]['name']} went offline", "system",
                             result="failed", trigger="lwt", tags=["node"])
        last_tick = time.time()
        sp = self.speedup
        while True:
            t = time.time()
            dt, last_tick = t - last_tick, t
            try:
                self.handle_commands()
                cfg = self.w.get_config()
                if cfg and cfg.get("version") != self.config.get("version"):
                    self.load_config(cfg)
                    self.w.log_event("config", f"Config v{cfg['version']} applied", "system", tags=["config"])
                if t - self.last["state"] >= 5 / sp:
                    self.last["state"] = t
                    self.update_sensors()
                    self.apply_rules()
                    self.write_state(self.update_power(dt if dt < 60 else 0))
                else:
                    self.update_power(dt if dt < 60 else 0)
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
                if t - self.last["motion"] >= 240 / sp and random.random() < 0.5:
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
    house = House(w, config, args)
    house.run_ai()
    try:
        house.run()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
