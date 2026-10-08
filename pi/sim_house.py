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

Digital twin (open http://localhost:8765 while it runs — the house from above, the person, the AI's mind):
    python pi/sim_house.py --twin-only --speed 60    # no Firebase at all: any speed, scenarios, no quota
    python pi/sim_house.py --cloud KEY --speed 1     # the twin next to the real web app (keep speed 1 there)

What it does:
    - seeds /config from docs/seed.json and creates the owner login (owner@home.test / password123)
    - every tick: executes /commands (done/failed + event), writes /home_state and /nodes
    - rules: empty room -> off, dark + occupied -> lights on, hot + occupied -> fan on, cool -> fan off
    - AI: the REAL model from ai/ (ai.runtime.AIRuntime) — plan every 15 min, sensor gate, waste guard,
      smart off, suggestion expiry, energy faults, waste ledger. A fresh install gets simulated history
      (labelled "simulated") so the models are ready on day one. Needs: pip install -r ai/requirements.txt
    - a person (pi/person.py) follows the routine from ai/routine.py: presence, doors, TV, light switches
    - door: fingerprint / keypad / exit button / wrong PINs / lockout, entrance motion + camera clips
    - /alerts for the Activity screen, /summaries + /energy_daily every minute
The simulator itself uses the Python standard library; the AI needs numpy/pandas/scikit-learn.
"""
import argparse
import json
import math
import queue
import random
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import firebase_writer  # noqa: E402
from firebase_writer import Writer, day_key, now_ms  # noqa: E402
from person import OUT, Person  # noqa: E402
from rest_db import PROJECT_ID, RestDB  # noqa: E402
from sim_clock import VirtualClock  # noqa: E402

CLOCK = VirtualClock()                       # every part of the simulator reads this clock
firebase_writer.set_clock(CLOCK.now)         # timestamps written to Firebase follow it too
SEED = ROOT / "docs" / "seed.json"
AUTH_URL = "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1"
OWNER_EMAIL, OWNER_PASSWORD = "owner@home.test", "password123"
TICK_S = 0.25
DARK_PATIENCE_S = 180            # the person switches the light on himself after 3 min in the dark
BED_FORGET = 0.3                 # some nights he falls asleep with the light on
PIR_RATE = 12.0                  # PIR movements per minute = 12 x (1 - still)^2
BASE_W = 6.8            # Raspberry Pi + 3 ESP32 nodes + sensors


def vnow():
    return datetime.fromtimestamp(CLOCK.now())


def hhmm(dt=None):
    return (dt or vnow()).strftime("%H:%M")


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
    upgraded = upgrade_config(config, seed_cfg)
    if upgraded:
        config, added, skipped = upgraded
        w.put_config(config)
        print(f"/config updated from {SEED.relative_to(ROOT)} (seed {config['seed']}, config v{config['version']}) — "
              f"added: {', '.join(added) or 'nothing'}" + (f" · skipped (pin taken): {', '.join(skipped)}" if skipped else ""))
    return config


def _taken(config, node):
    """Pins and INA addresses already used on a node (so an added device never collides with yours)."""
    pins, inas = set(), set()
    for room in config.get("rooms", {}).values():
        if room.get("node") != node:
            continue
        for d in (room.get("devices") or {}).values():
            hw = d.get("hw") or {}
            pins |= {hw[k] for k in ("pin", "button") if hw.get(k) is not None}
            if hw.get("ina"):
                inas.add(hw["ina"])
    return pins, inas


def upgrade_config(config, seed_cfg):
    """A newer docs/seed.json (its `seed` number went up) ADDS what is new — rooms, devices, sensors, nodes —
    to the existing /config without wiping anything and without touching what you changed in the app.
    Returns (new config, added, skipped) or None when there is nothing to do."""
    if (config.get("seed") or 1) >= (seed_cfg.get("seed") or 1):
        return None
    new, added, skipped = json.loads(json.dumps(config)), [], []
    nodes = new.setdefault("nodes", {})
    for nid, n in (seed_cfg.get("nodes") or {}).items():
        if nid not in nodes:
            nodes[nid] = n
            added.append(f"node {nid}")
        else:
            have = nodes[nid].get("i2c") or []
            nodes[nid]["i2c"] = have + [a for a in n.get("i2c") or [] if a not in have]
    rooms = new.setdefault("rooms", {})
    known = {d for r in rooms.values() for d in (r.get("devices") or {})}
    for rid, r in seed_cfg.get("rooms", {}).items():
        if rid not in rooms:
            rooms[rid] = r
            added.append(f"room {rid}")
            known |= set(r.get("devices") or {})
            continue
        cur = rooms[rid]
        for k in ("sensors", "hardware"):
            have = cur.get(k) or []
            cur[k] = have + [x for x in r.get(k) or [] if x not in have]
        if r.get("windowless") and "windowless" not in cur:
            cur["windowless"] = True
        devs = cur.get("devices") or {}
        for did, d in (r.get("devices") or {}).items():
            if did in known:
                continue                                   # already there (maybe renamed or moved by you)
            pins, inas = _taken(new, cur.get("node"))
            hw = d.get("hw") or {}
            if {hw.get("pin"), hw.get("button")} & pins or (hw.get("ina") and hw["ina"] in inas):
                skipped.append(did)
                continue
            devs[did] = d
            known.add(did)
            added.append(did)
        cur["devices"] = devs
    new["seed"] = seed_cfg.get("seed")
    new["version"] = (config.get("version") or 0) + 1         # the nodes download the new config
    return new, added, skipped


def backfill_energy(w, config):
    """6 days of fake energy history so the Energy chart is not empty on day one."""
    for d in range(1, 7):
        day = day_key(CLOCK.now() - d * 86400)
        if w.get_energy_day(day):
            continue
        per = {"_base": BASE_W * 24 * random.uniform(0.97, 1.03)}
        for room in config["rooms"].values():
            for dev, cfg in (room.get("devices") or {}).items():
                if cfg.get("watts"):
                    per[dev] = cfg["watts"] * random.uniform(1, 7)  # 1–7 h of use
        w.write_energy_day(day, per)


WINDOW = {"living": 0.75, "kitchen": 0.5, "bedroom": 0.6, "bathroom": 0.0}   # share of daylight per room


def load_persona(args):
    """The routine file the simulated person lives (ai/personas/*.toml) — standard library only."""
    choice = getattr(args, "persona", "default") or "default"
    if str(choice).lower() == "none":
        return None
    try:
        from ai.persona import Persona
        return Persona.load(None if choice == "default" else choice)
    except Exception as e:  # noqa: BLE001 — fall back to the older fixed routine
        print(f"! routine file not loaded ({type(e).__name__}: {e}) — using the old fixed routine")
        return None


def ai_error_log(e):
    """Write the full traceback to ai/data/ai_errors.log (send it to the team) -> 'file.py:line in function'."""
    import platform
    import traceback
    frames = traceback.extract_tb(e.__traceback__)
    ours = [f for f in frames if str(ROOT) in f.filename]              # the last line of OUR code
    f = (ours or frames)[-1] if frames else None
    try:
        import numpy
        import pandas
        import sklearn
        versions = f"numpy {numpy.__version__} · pandas {pandas.__version__} · scikit-learn {sklearn.__version__}"
    except Exception:  # noqa: BLE001
        versions = "?"
    try:
        log = ROOT / "ai" / "data" / "ai_errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} · Python {platform.python_version()} · {versions}\n")
            fh.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    except OSError:
        pass
    return f"{Path(f.filename).name}:{f.lineno} in {f.name}" if f else "?"


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
        # --fast (old mode): real clock, rule timers 10x shorter. --speed N: a virtual clock N times faster.
        self.speedup = 10 if args.fast and not args.speed else 1
        self.offline = set(args.offline or [])
        self.override_until = {}         # device -> ts; manual control pauses AI/rules
        self.empty_since = {}            # room -> ts
        self.dark_since = {}             # room -> ts the person has been sitting in the dark
        self.manual_at = {}              # device -> ts of the last app / wall-button control
        self.bed_since, self.bed_forgets = None, {}
        self.sensor_t = None
        self.kpi = {}                    # day -> counts (learning curve: manual fixes, questions, automatic actions)
        self.kpi_ai0 = None              # AI stats at the start of the day
        self.sim_answers = bool(getattr(args, "twin_only", False) or getattr(args, "auto_answer", False))
        self.answer_at = {}              # suggestion id -> virtual ts the simulated person answers
        self.sync_retrain = False        # headless runs (learning curve) retrain in place
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
        self.persona = load_persona(args)  # the routine file the person lives (and the AI's history comes from)
        self.person = Person(config, persona=self.persona)
        self.intents_done = set()        # (date, n, "on"/"off") appliance uses from today's plan already done
        self.temp_delta = 0.0            # twin control: hotter / cooler day
        self.recent = deque(maxlen=80)   # latest events for the twin page
        self.changes = deque(maxlen=4000)  # (ts, kind, id, value) today: device on/off + person room, for the timeline
        self.commands = queue.Queue()    # from the twin page
        self.wake = threading.Event()    # set by the twin page: handle its command now, not at the next tick
        self.resume_after_train = True
        self.twin_seq = 0
        self.twin_pack = ('"0"', b"{}")
        self.counts = {}                 # "rule_on", "ai_off", "manual_off", ... (who switched what, for the twin)
        self.twin_json = b"{}"
        self.scenarios = None            # pi/scenarios.py runner (set in main)
        self._wrap_writer()
        # the real Firebase free plan has a download quota, so poll it less often than the local emulator
        self.poll_s = 1.0 if args.cloud else TICK_S
        self.config_poll_s = 10.0 if args.cloud else TICK_S
        self.load_config(config)
        for n in config["nodes"]:
            self.node_version[n] = config["version"]

    def _wrap_writer(self):
        """Keep a copy of every event / alert for the twin page (the Firebase writes stay unchanged)."""
        log, alert = self.w.log_event, self.w.push_alert

        def log_event(kind, group, title, source, src_label, result="ok", **fields):
            self.recent.appendleft({"at": now_ms(), "kind": kind, "group": group, "title": title, "src": src_label,
                                    "result": result, "short": fields.get("short"), "why": fields.get("why"),
                                    "device": fields.get("device")})
            return log(kind, group, title, source, src_label, result, **fields)

        def push_alert(level, title, where, go, lines=None):
            self.recent.appendleft({"at": now_ms(), "kind": "alert", "group": "alert", "title": title, "src": level,
                                    "result": "ok", "short": where})
            return alert(level, title, where, go, lines)

        self.w.log_event, self.w.push_alert = log_event, push_alert

    def load_config(self, config):
        self.config = config
        if hasattr(self, "person"):
            self.person.set_config(config)
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
        now = vnow()
        t = CLOCK.now()
        sens_dt = min(60.0, max(1.0, t - (self.sensor_t or t - 5)))   # seconds since the last reading
        self.sensor_t = t
        h = now.hour + now.minute / 60
        today = now.date()
        if self.persona is not None:          # seasons, sunset and weather of the routine file (= the AI's history)
            outdoor = self.persona.outdoor(today, h) + self.temp_delta
            sun_lux = self.persona.daylight(today, h)
            rh_out = self.persona.rh_out(today, h)
        else:
            outdoor = 27 + 4 * math.sin((h - 9) / 24 * 2 * math.pi) + self.temp_delta
            sun_lux = 600 * (max(0.0, math.sin((h - 6) / 13 * math.pi)) if 6 <= h <= 19 else 0)
            rh_out = None
        act, here = self.person.activity, self.person.room
        for rid, room in self.config["rooms"].items():
            if room["node"] in self.offline:
                continue
            s = self.rooms[rid]
            sensors = room.get("sensors", [])
            if "occ" in sensors:
                new = int(self.person.room == rid)            # the simulated person is in this room
                if new and not s.get("occ"):
                    s["motion_at"] = now_ms()
                    s["entry_pending"] = True                 # "on when dark" looks at every entry once
                if new != s.get("occ"):
                    s["occ_since"] = now_ms()
                if s.get("occ") and not new:
                    self.end_hands_off(rid)                  # you left: the 2 h hands-off ends with your visit
                s["occ"] = new
                s["occ_by"] = ("mmwave" if "C1001 mmWave" in room.get("hardware", []) else "pir")
                hw = room.get("hardware", [])
                if "C1001 mmWave" in hw:
                    s["mmwave"] = new                            # the radar also sees people sitting still
                if "PIR" in hw:                                  # PIR only sees movement: it misses still people
                    # movements per minute: walking ~10/min, TV ~2/min, reading ~0.5/min, asleep ~0.1/min
                    rate = PIR_RATE * (1 - self.person.still) ** 2
                    s["pir"] = int(bool(new) and random.random() < 1 - math.exp(-rate * sens_dt / 60))
            devs = room.get("devices") or {}
            fan_on = any(self.devices[d]["v"] and self.icon(d) in ("fan", "ac") for d in devs)
            if self.persona is not None:      # inertia like the real room (and like ai/lifesim.py)
                target = self.persona.indoor_target(today, h, rid) - (1.4 if fan_on else 0)
                if rid == "kitchen" and here == rid and act.startswith("Cooking"):
                    target += 2.5
                if rid == "bathroom" and here == rid and act == "Shower":
                    target += 3.0
                k = 1 - math.exp(-sens_dt / 7200)
                s["_t"] = s.get("_t", target) + (target - s.get("_t", target)) * k
                base_h = rh_out * 0.75 + 8
                vent = rid == "bathroom" and any(self.devices[d]["v"] and self.icon(d) == "vent" for d in devs)
                goal, rate = ((93, 0.18) if rid == "bathroom" and here == rid and act == "Shower"
                              else (base_h, 0.09 if vent else 0.025))
                s["_h"] = s.get("_h", base_h) + (goal - s.get("_h", base_h)) * (1 - (1 - rate) ** (sens_dt / 60))
            if "temp" in sensors:
                if self.persona is not None:
                    # the twin's "hotter / cooler" control (and the scenarios) act at once, outside the room's inertia
                    s["temp"] = round(s["_t"] + self.temp_delta + random.gauss(0, 0.08), 1)
                else:
                    s["temp"] = round(outdoor + (0.8 if rid == "bedroom" else 0) + (-1.2 if fan_on else 0)
                                      + random.gauss(0, 0.12), 1)
            if "hum" in sensors:
                s["hum"] = round(s["_h"] + random.gauss(0, 0.6)) if self.persona is not None else \
                    round(50 - (outdoor - 27) * 1.5 + random.gauss(0, 1))
            if "lux" in sensors:
                lights = 250 if any(self.devices[d]["v"] and self.icon(d) == "light" for d in devs) else 0
                share = 0.0 if room.get("windowless") else WINDOW.get(rid, 0.6)
                s["lux"] = round(max(0, sun_lux * share + lights + random.gauss(0, 6)))

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
        if int(v) != st.get("v"):
            self.changes.append((CLOCK.now(), "dev", device, int(v)))
            key = f"{group}_{'on' if v else 'off'}"
            self.counts[key] = self.counts.get(key, 0) + 1
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
            self.manual_at[device] = CLOCK.now()
            self.override_until[device] = CLOCK.now() + self.th["override_pause_min"] * 60 / self.speedup
            if self.ai:
                self.ai_safe(self.ai.on_manual, device, v=int(v))
            self.kpi_add("manual")
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
            self.dev_cfg[device]
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
                title = ("Remote unlock failed" if lock else
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
            self.door_entry_at = CLOCK.now()
            if self.ai and self.ai.recorder:
                self.ai_safe(self.ai.recorder.event, "door/entry")
        self.lock_at = CLOCK.now() + 5

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

    def entrance_motion(self):
        now = vnow()
        self.w.log_event("motion", "system", "Motion at the front door", "system", "Camera",
                         room="entrance", node="hub", by_label="Outdoor PIR on the hub",
                         short="15 s clip saved", why="Motion detected by the outdoor PIR",
                         change=f"Clip {now:%Y-%m-%d}/{now:%H%M%S}.mp4", tags=["door", "system"])
        self.w.push_alert("info", "Motion at the front door", "Entrance · camera · clip saved", "security")

    # ------------------------------------------------ automation rules
    def paused(self, device):
        """AI + comfort rules leave the device alone after manual control, or while the user paused it."""
        return CLOCK.now() < self.override_until.get(device, 0) or self.user_paused(device)

    def end_hands_off(self, rid):
        """Manual control pauses the AI and the rules for 2 h, or until you leave that room."""
        for dev in (self.config["rooms"].get(rid, {}).get("devices") or {}):
            self.override_until.pop(dev, None)

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
        t = CLOCK.now()
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
                # counted from the later of "room empty" and your last manual control: switching a light on in an
                # empty room (wall button, app) is not undone a second later
                empty_for = t - max(self.empty_since.get(rid, t), self.manual_at.get(dev, 0))
                rule = dict(source="rule", src_label="Rule", group="rule", by_label="Automation", tags=["rule"])
                # AI pre-cooling (its waste guard decides) or the AI's smart-off timer is running: the rule waits
                guarded = bool(self.ai and (self.ai_safe(self.ai.guarding, dev) or self.ai_safe(self.ai.counting_off, dev)))
                if r.get("off_when_empty") and st["v"] and not self.user_paused(dev) and not guarded \
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
                elif r.get("on_when_dark") and not st["v"] and (self.room_lux(rid) if self.room_lux(rid) is not None
                                                                 else 999) < th["light_on_lux"] \
                        and (s.get("entry_pending") or t - s.get("occ_since", 0) / 1000 < 90) \
                        and not (self.ai and self.ai_safe(self.ai.handles_entry, dev)):
                    # fallback only: when the AI is ready for this light it decides from your habits
                    # only when someone has just come in: a person sleeping or who switched it off stays in the dark
                    self.set_device(dev, 1, self.mid_level(dev), short=f"Light below {th['light_on_lux']} lx",
                                    why=f'Rule "On below {th["light_on_lux"]} lx" · light {s.get("lux")} lx · someone in the room', **rule)
                elif r.get("follow_temp") and not st["v"] and s.get("temp", 0) > th["fan_on_temp"]:
                    self.set_device(dev, 1, self.mid_level(dev), short=f"Above {th['fan_on_temp']}°C",
                                    why=f'Rule "On above {th["fan_on_temp"]}°C" · room {s.get("temp")}°C · someone in the room', **rule)
                elif r.get("follow_temp") and st["v"] and s.get("temp", 99) < th["fan_off_temp"]:
                    self.set_device(dev, 0, short=f"Below {th['fan_off_temp']}°C",
                                    why=f'Rule "Off below {th["fan_off_temp"]}°C" · room {s.get("temp")}°C', **rule)
            if s.get("occ"):
                s["entry_pending"] = False                   # seen once (works at any --speed, not only within 90 s)
        if self.lock_at and t >= self.lock_at and self.lock_id:
            self.lock_at = None
            st = self.devices[self.lock_id]
            st.update(v=0, at=now_ms())
            self.w.patch_device(self.lock_id, st)

    # ------------------------------------------------ monitor-only devices (power: "read")
    def simulate_monitored(self):
        """Devices we only READ (TV, fridge, ...): their on/off comes from the current they draw.
        Here we fake that current; on the Pi it comes from the INA226 (on when watts > hw.on_above_w)."""
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
            elif icon == "tv":       # on while the person is watching TV in this room
                want = 1 if occ and "TV" in self.person.activity else 0
            elif icon == "kettle":   # boiling when today's plan says tea / coffee
                plan = self.person.plan(CLOCK.now()) if self.persona is not None else None
                hh = vnow().hour + vnow().minute / 60
                want = int(bool(plan) and any(i["role"] == "kettle" and i["on"] <= hh < max(i["off"], i["on"] + 0.05)
                                              for i in plan.intents))
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

    def history_of(self):
        """What the simulated history was made from: the routine file + the AI devices of /config."""
        from ai.spec import ai_devices
        who = getattr(self.args, "persona", "default") if self.persona is not None else "none"
        return f"{who}|" + ",".join(sorted(ai_devices(self.config)))

    def renew_simulated_history(self, con, ai_store, name):
        """The simulator's history is 100% simulated: when the routine file or the devices changed, an old
        history would teach the AI the wrong house (e.g. no kitchen hood). Rebuild it. Real readings are never touched."""
        sig = self.history_of()
        has = con.execute("SELECT 1 FROM readings LIMIT 1").fetchone()
        if has and ai_store.get_meta(con, "all_simulated") == "1" and ai_store.get_meta(con, "history_of") != sig:
            print("AI: the simulated history is from another routine / device list — rebuilding it")
            con.execute("DELETE FROM readings")
            con.commit()
            shutil.rmtree(ROOT / "ai" / "models" / name, ignore_errors=True)
        ai_store.set_meta(con, "history_of", sig)

    def start_ai(self, ai_parts, cloud=False, name=None):
        """Create the AI runtime, bootstrap history if the hub is new, train if needed."""
        AIRuntime, FirebaseSink, ai_store = ai_parts
        name = name or ("sim_cloud" if cloud else ("twin" if self.args.twin_only else "sim"))
        con = ai_store.connect(ROOT / "ai" / "data" / f"{name}.db")
        self.renew_simulated_history(con, ai_store, name)
        last = con.execute("SELECT MAX(ts) FROM readings").fetchone()[0]
        if last and last > CLOCK.now():
            # the twin ran ahead of real time last session: continue from there so the history stays in order
            CLOCK.forward(last - CLOCK.now() + 60)
            print(f"virtual clock continues from the last session: {hhmm()} on {vnow():%a %d %b}")
        sink = FirebaseSink(self.w, room_of=lambda d: self.dev_room.get(d),
                            room_name=lambda r: self.config["rooms"].get(r, {}).get("name", r))
        self.ai = AIRuntime(self.config, con, sink, model_dir=ROOT / "ai" / "models" / name,
                            speed=self.speedup, record=True, clock=CLOCK.now, persona=self.persona)
        ai_store.set_meta(con, "all_simulated", "1")    # every reading here is simulated — say so in the metrics
        self.clean_suggestions(keep_open=False)        # open ones from an earlier run can't be tracked
        t0 = time.time()
        CLOCK.pause()                                  # training takes seconds: the virtual day waits
        self.ai_safe(self.ai.bootstrap_if_needed)
        CLOCK.resume()
        self.next_train = self.next_3am()
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
            where = ai_error_log(e)
            print(f"! AI error ({type(e).__name__}: {e}) at {where}" + ("" if self.ai_errors < 5 else " — AI switched off")
                  + "\n  full details: ai/data/ai_errors.log")
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

    def next_3am(self):
        if self.speedup > 1:                          # old --fast mode: every 2.4 h
            return CLOCK.now() + 86400 / self.speedup
        t = vnow().replace(hour=3, minute=0, second=0, microsecond=0)
        return (t if t > vnow() else t + timedelta(days=1)).timestamp()

    def run_ai(self):
        if not self.ai or self.ai_training():
            return
        if CLOCK.now() >= self.next_train:            # nightly retraining at 03:00 (virtual clock)
            self.next_train = self.next_3am()
            self.resume_after_train = not CLOCK.paused
            CLOCK.pause()                             # the virtual day waits for the new models
            if not self.sync_retrain and self.ai_safe(self.ai.start_retrain):
                return                                # in the background: the house and the twin page stay live
            self.ai_safe(self.ai.retrain, quiet=True)  # in-memory DB: no second connection possible
            self.after_retrain()
            return
        self.ai_safe(self.ai.plan, self.house_snapshot())

    def ai_training(self):
        return bool(self.ai and self.ai_safe(self.ai.training))

    def check_retrain(self):
        """Background retraining finished? Install the models, continue the day, plan."""
        if not self.ai_training():
            return
        try:
            if self.ai.finish_retrain() is None:
                return
        except Exception as e:  # noqa: BLE001 — keep the old models
            print(f"! AI retraining failed ({type(e).__name__}: {e}) — keeping the previous models")
        self.after_retrain()
        self.ai_safe(self.ai.plan, self.house_snapshot())

    def after_retrain(self):
        if self.resume_after_train:
            CLOCK.resume()
        self.clean_suggestions()

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

    # ------------------------------------------------ the person (digital twin)
    def person_tick(self):
        """Move the person along today's routine; doors, light switches and the TV follow what he does."""
        moved = self.person.step(CLOCK.now())
        self.dark_habit()
        self.bed_habit()
        self.appliance_tick()
        if not moved:
            return
        old, new = moved
        self.changes.append((CLOCK.now(), "room", "person", new))
        if old and old != OUT and self.person.habits:
            self.leave_room_habits(old)
        if new == OUT and old is not None:                     # leaving: exit button, camera sees him
            if self.lock_id and "door" not in self.offline:
                self.open_door("exit_button")
            self.entrance_motion()
        elif old == OUT:                                       # coming home: fingerprint
            if self.lock_id and "door" not in self.offline:
                fingers = self.fingerprints()
                finger = next(iter(fingers))
                self.open_door("fingerprint", fingers[finger], finger=finger)
            else:
                self.door_entry_at = CLOCK.now()
            self.entrance_motion()

    def leave_room_habits(self, rid, always=False, lights_only=False):
        """Like a real person: usually switches the light off when leaving, sometimes forgets (the AI's job)."""
        for dev in (self.config["rooms"].get(rid, {}).get("devices") or {}):
            if lights_only and self.icon(dev) != "light":
                continue
            st = self.devices.get(dev) or {}
            if not st.get("v") or not self.writable(dev) or not self.ctrl(dev).get("button"):
                continue
            if self.node_of(dev) in self.offline:
                continue
            chance = 1.0 if always else 0.6 if self.icon(dev) == "light" else 0.3
            if random.random() < chance:
                self.set_device(dev, 0, source="button", src_label="Button", group="manual",
                                by_label="Someone at home (button)",
                                short=f"Wall button in the {self.room_name(rid).lower()}",
                                why="Switched off before sleeping" if always else "Switched off when leaving the room",
                                tags=["manual"])

    def bed_habit(self):
        """Lying down to sleep with the light on: most nights he switches it off himself after a minute or two,
        some nights he falls asleep with it on (decided once per night) — the case the AI must learn to handle."""
        p = self.person
        if p.activity != "Sleeping" or p.manual or not p.habits:
            self.bed_since = None
            return
        now = CLOCK.now()
        night = vnow().date() if vnow().hour >= 12 else (vnow() - timedelta(days=1)).date()
        lights = [d for d in (self.config["rooms"].get(p.room, {}).get("devices") or {})
                  if self.icon(d) == "light" and self.devices[d]["v"] and self.writable(d) and self.ctrl(d).get("button")]
        if not lights:
            self.bed_since = None
            return
        self.bed_since = self.bed_since or now
        if self.bed_forgets.get(night) is None:
            plan = self.person.plan(now) if self.persona is not None else None
            if plan is not None:      # the routine file decides (the same nights the AI's history has)
                off = plan.light_off_at_bed if vnow().hour >= 12 else plan.light_off_prev_night
                self.bed_forgets = {night: not off}
            else:
                self.bed_forgets = {night: random.random() < BED_FORGET}
        if not self.bed_forgets[night] and now - self.bed_since >= 90 / self.speedup:
            for d in lights:
                self.set_device(d, 0, source="button", src_label="Button", group="manual",
                                by_label="Someone at home (button)", short=f"Wall button in the {self.room_name(p.room).lower()}",
                                why="Switched off after lying down to sleep", tags=["manual"])
                self.kpi_add("manual_fix")
            self.bed_since = None

    # ------------------------------------------------ learning curve (per day) + simulated answers
    KPI_AI = ("suggested", "accepted", "dismissed", "expired", "ai_on", "ai_entry_on", "ai_entry_skip", "smart_off",
              "ai_auto_off", "ai_trusted", "ai_undone", "trust_up", "trust_down")

    def today_view(self):
        plan = self.person.plan(CLOCK.now()) if self.persona is not None else None
        if plan is None:
            return None
        return {"period": plan.period, "kind": plan.kind, "tags": [t for t in plan.tags if t != plan.kind],
                "plans": plan.note, "persona": self.persona.d.get("person", {}).get("name", "")}

    def kpi_day(self):
        day = vnow().strftime("%Y-%m-%d")
        if day not in self.kpi:
            stats = dict((self.ai.state.get("stats") or {}) if self.ai else {})
            self.kpi[day] = {"manual": 0, "manual_fix": 0, "asleep_light_min": 0.0, "ai0": stats}
            for old in sorted(self.kpi)[:-60]:
                self.kpi.pop(old)
        return self.kpi[day]

    def kpi_add(self, key, n=1):
        k = self.kpi_day()
        k[key] = k.get(key, 0) + n

    def kpi_tick(self, dt):
        """Minutes a light burned while the person slept in that room (the 'fell asleep with the light on' cost)."""
        p = self.person
        if p.activity == "Sleeping" and p.room in self.config["rooms"]:
            if any(self.devices[d]["v"] and self.icon(d) == "light" for d in (self.config["rooms"][p.room].get("devices") or {})):
                self.kpi_add("asleep_light_min", dt / 60)

    def kpi_view(self):
        """[{day, manual, manual_fix, asleep_light_min, asked, yes, no, ai_auto, ...}] oldest first."""
        days = sorted(self.kpi)
        now_stats = dict((self.ai.state.get("stats") or {}) if self.ai else {})
        out = []
        for i, d in enumerate(days):
            k = self.kpi[d]
            end = self.kpi[days[i + 1]]["ai0"] if i + 1 < len(days) else now_stats
            delta = {key: round(end.get(key, 0) - k["ai0"].get(key, 0), 2) for key in self.KPI_AI}
            out.append({"day": d, "manual": k["manual"], "manual_fix": k["manual_fix"],
                        "asleep_light_min": round(k["asleep_light_min"]), "asked": delta["suggested"],
                        "yes": delta["accepted"], "no": delta["dismissed"], "unanswered": delta["expired"],
                        "ai_auto": delta["ai_on"] + delta["ai_entry_on"] + delta["smart_off"] + delta["ai_auto_off"],
                        "ai_learned": delta["ai_trusted"], "undone": delta["ai_undone"], **delta})
        return out

    def wants(self, dev, action):
        """What the simulated person would answer: does he want this device on / off right now?"""
        p, rid = self.person, self.dev_room.get(dev)
        here = p.room == rid
        if action == "off":
            return not here or p.activity == "Sleeping"
        if not here or p.activity == "Sleeping":
            return False
        s = self.rooms.get(rid) or {}
        if self.icon(dev) == "light":
            return s.get("lux", 0) < self.th["light_on_lux"]
        if self.icon(dev) in ("fan", "ac"):
            return s.get("temp", 0) > self.th["fan_on_temp"]
        return True

    def simulated_answers(self):
        """In the twin (and with --auto-answer) the simulated person answers the AI's questions after 1-4 min,
        the way he would want it. With the real app (--cloud) you answer yourself."""
        if not (self.sim_answers and self.ai and self.ai.suggestions) or (self.scenarios and self.scenarios.cur):
            return                                    # test scenarios control the answers themselves
        now = CLOCK.now()
        for sid, info in list(self.ai.suggestions.items()):
            due = self.answer_at.setdefault(sid, now + random.uniform(60, 240) / self.speedup)
            if now >= due:
                self.answer_at.pop(sid, None)
                self.answer_suggestion(sid, self.wants(info["device"], info["action"]), "the simulated person")
        for sid in [k for k in self.answer_at if k not in self.ai.suggestions]:
            self.answer_at.pop(sid)

    def answer_suggestion(self, sid, accept, by):
        """Answer exactly like the web app does: write the response; on Yes run the command 'via suggestion'."""
        s = (self.w.get_suggestions() or {}).get(sid)
        if not isinstance(s, dict) or s.get("response"):
            return
        self.w.mark_suggestion(sid, {"response": "accept" if accept else "dismiss", "answered_by": by})
        dev = s.get("device")
        if accept and dev in self.dev_cfg:
            v = 1 if s.get("action") == "on" else 0
            pct = round((s.get("confidence") or 0) * 100)
            self.set_device(dev, v, self.mid_level(dev) if v else None, source="ai", src_label=f"AI · {pct}% · approved",
                            group="ai", by_label=f"AI suggestion, approved by {by}", why="Yes to an AI suggestion",
                            short="Suggestion approved", confidence=s.get("confidence"), tags=["ai", "manual"])

    def room_lux(self, rid):
        """Light level for the rules / the person: own BH1750, else dark if windowless, else a neighbour's."""
        room = self.config["rooms"].get(rid) or {}
        own = (self.rooms.get(rid) or {}).get("lux")
        if own is not None:
            return own
        if room.get("windowless"):
            return 0
        for r, rc in self.config["rooms"].items():
            if "lux" in (rc.get("sensors") or []) and (self.rooms.get(r) or {}).get("lux") is not None:
                return self.rooms[r]["lux"] - (250 if any(self.devices[d]["v"] and self.icon(d) == "light"
                                                          for d in (rc.get("devices") or {})) else 0)
        return None

    def appliance_tick(self):
        """Appliance use from today's plan in the routine file: hood while cooking, exhaust fan after a shower,
        laundry, the app pre-cooling the living room on hot afternoons. Same plan the AI's history came from."""
        plan = self.person.plan(CLOCK.now()) if self.persona is not None else None
        if plan is None or self.person.manual or not self.person.habits:
            return
        now = vnow()
        h = now.hour + now.minute / 60 + now.second / 3600
        for n, i in enumerate(plan.intents):
            role = i["role"]
            if role.startswith("_") or role == "kettle":
                continue
            devs = [d for d in self.dev_cfg if self.icon(d) == role and self.writable(d)] if role != "fan" else \
                [d for d in (self.config["rooms"].get(i.get("room", "living"), {}).get("devices") or {})
                 if self.icon(d) == "fan" and self.writable(d)]
            for edge, at, v in (("on", i["on"], 1), ("off", i.get("off"), 0)):
                key = (plan.date, n, edge)
                if at is None or key in self.intents_done or h < at or h > at + 0.5:
                    continue
                self.intents_done.add(key)
                for dev in devs:
                    if self.node_of(dev) in self.offline or self.devices[dev]["v"] == v:
                        continue
                    app = i.get("how") == "app"
                    self.set_device(dev, v, self.mid_level(dev) if v else None, source="web" if app else "button",
                                    src_label="App" if app else "Button", group="manual",
                                    by_label="Owner (app)" if app else "Someone at home (button)",
                                    short=("Started from the app before coming home" if app else
                                           f"Wall button in the {self.room_name(self.dev_room[dev]).lower()}"),
                                    why={"hood": "Cooking", "vent": "After a shower", "washer": "Laundry",
                                         "fan": "Hot afternoon: pre-cooling"}.get(role, "Daily routine")
                                    + (" (forgot to switch it off)" if (edge == "off" and i.get("forgot")) else ""),
                                    tags=["manual"])
        if len(self.intents_done) > 400:
            self.intents_done = {k for k in self.intents_done if k[0] == plan.date}

    def dark_habit(self):
        """Like a real person: awake in a dark room with the light off, he presses the wall switch himself
        after a few minutes (only if the rules / AI did not). Not if he switched it off himself in this room."""
        rid, now = self.person.room, CLOCK.now()
        room = self.config["rooms"].get(rid) or {}
        lux = self.room_lux(rid)
        if not self.person.habits or self.person.activity == "Sleeping" or lux is None or lux >= self.th["light_on_lux"] \
                or room.get("node") in self.offline:
            self.dark_since.pop(rid, None)
            return
        lights = [d for d in (room.get("devices") or {}) if self.icon(d) == "light" and self.writable(d)
                  and self.ctrl(d).get("button") and self.node_of(d) not in self.offline]
        off = [d for d in lights if not self.devices[d]["v"]
               and not (self.devices[d].get("src") == "button" and now < self.override_until.get(d, 0))]
        if not off or len(off) < len(lights):                # a light is on, or he wants it dark
            self.dark_since.pop(rid, None)
            return
        since = self.dark_since.setdefault(rid, now)
        if now - since >= DARK_PATIENCE_S / self.speedup:
            self.dark_since.pop(rid, None)
            self.kpi_add("manual_fix")
            self.set_device(off[0], 1, self.mid_level(off[0]), source="button", src_label="Button", group="manual",
                            by_label="Someone at home (button)",
                            short=f"Wall button in the {self.room_name(rid).lower()}",
                            why=f"Too dark ({lux} lx) and nothing switched the light on", tags=["manual"])

    def press_button(self, dev):
        """Twin control: the person presses a wall button."""
        if dev not in self.dev_cfg or not self.writable(dev):
            return
        on = not self.devices[dev]["v"]
        self.set_device(dev, int(on), self.mid_level(dev) if on else None, source="button", src_label="Button",
                        group="manual", by_label="Someone at home (button)",
                        short=f"Wall button in the {self.room_name(self.dev_room[dev]).lower()}",
                        why="Pressed from the digital twin", tags=["manual"])

    # ------------------------------------------------ digital twin: commands + state for the page
    def handle_twin_commands(self):
        """Commands from the twin page. The page waits for the new state: rebuild it at once, then answer."""
        done = []
        while True:
            try:
                c = self.commands.get_nowait()
            except queue.Empty:
                break
            ev = c.pop("_done", None)
            if ev:
                done.append(ev)
            try:
                self.twin_command(c)
            except Exception as e:  # noqa: BLE001 — a bad command must never stop the house
                print(f"! twin command {c}: {e}")
        if done:
            self.person_tick()
            self.update_sensors()                     # a moved person / pressed button shows on the sensors now
            self.twin_state()
            for ev in done:
                ev.set()

    def twin_command(self, c):
        a = c.get("action")
        if a == "speed":
            CLOCK.set_speed(float(c["value"]))
        elif a == "pause":
            CLOCK.pause()
            self.resume_after_train = False
        elif a == "resume":
            CLOCK.resume()
            self.resume_after_train = True
        elif a == "jump":
            CLOCK.jump_to(c["time"])
        elif a == "move":
            self.person.take_over(c["room"])
        elif a == "routine":
            self.person.give_back()
        elif a == "still":
            self.person.force_still = bool(c["on"]) if c.get("on") is not None else None
        elif a == "temp":
            self.temp_delta = float(c["delta"])
        elif a == "fault":
            if c.get("kind"):
                self.faults[c["device"]] = c["kind"]
            else:
                self.faults.pop(c["device"], None)
        elif a == "node":
            (self.offline.add if c.get("offline") else self.offline.discard)(c["node"])
        elif a == "press":
            self.press_button(c["device"])
        elif a == "scenario" and self.scenarios:
            self.scenarios.start(c["id"])
        elif a == "scenario_stop" and self.scenarios:
            self.scenarios.stop()
        elif a == "wrong_pin":
            self.wrong_attempt("keypad")
        elif a == "answer":
            self.answer_suggestion(c["sid"], bool(c.get("yes")), "you (twin page)")
            self.handle_suggestion_answers()

    def twin_state(self):
        now = CLOCK.now()
        house = self.house_snapshot()
        seg = self.person.plan_now(now)
        day0 = vnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rooms = {}
        for rid, rc in self.config["rooms"].items():
            rooms[rid] = {"name": rc.get("name", rid), "hidden": bool(rc.get("hidden")), "node": rc.get("node"),
                          "offline": rc.get("node") in self.offline, "sensors": rc.get("sensors") or [],
                          "hardware": rc.get("hardware") or [], **{k: v for k, v in self.rooms.get(rid, {}).items()
                                                                   if k in ("occ", "mmwave", "pir", "temp", "lux", "hum")}}
        devices = {}
        for dev, cfg in self.dev_cfg.items():
            st = self.devices[dev]
            devices[dev] = {"room": self.dev_room[dev], "label": self.label(dev), "icon": self.icon(dev),
                            "v": st.get("v"), "level": st.get("level"), "watts": st.get("watts"), "src": st.get("src"),
                            "write": self.writable(dev), "ai": bool(self.ctrl(dev).get("ai")),
                            "button": bool(self.ctrl(dev).get("button")), "fault": self.faults.get(dev),
                            "paused": self.paused(dev), "state": self.state_label(dev, st)}
        state = {
            "ai_busy": self.ai_training(),
            "now": now, "day0": day0, "speed": CLOCK.speed, "paused": CLOCK.paused, "legacy_fast": self.speedup > 1,
            "mode": "twin-only" if self.args.twin_only else ("cloud" if self.args.cloud else "emulator"),
            "person": {"room": self.person.room, "activity": self.person.activity, "still": self.person.still,
                       "manual": bool(self.person.manual), "force_still": self.person.force_still,
                       "moved_at": self.person.moved_at, "path": self.person.path,
                       "next": {"start": seg[0], "end": seg[1]}},
            "routine": [list(x) for x in self.person.segments(now)],
            "rooms": rooms, "devices": devices, "temp_delta": self.temp_delta,
            "door": {"entry_at": self.door_entry_at, "lockout_until": self.door.get("lockout_until", 0)},
            "events": list(self.recent)[:40],
            "changes": [c for c in self.changes if c[0] >= day0 - 3600],
            "ai": self.ai_safe(self.ai.twin_view, house, now) if self.ai else None,
            "scenarios": self.scenarios.view() if self.scenarios else None,
            "kpi": self.kpi_view()[-28:],
            "today": self.today_view(),
            "sim_answers": self.sim_answers,
        }
        body = json.dumps(state, default=str).encode()
        if body != self.twin_json:
            self.twin_seq += 1
            self.twin_pack = (f'"{self.twin_seq}"', body)   # one tuple: the page never gets a body with the wrong tag
            self.twin_json = body

    # ------------------------------------------------ periodic writes
    def write_state(self, power):
        rooms = {r: s for r, s in self.rooms.items() if self.config["rooms"][r].get("sensors")}
        self.w.write_home_state({"rooms": rooms, "devices": self.devices, "power_w": round(power, 2),
                                 "base_w": BASE_W, "door": self.door, **({"scene": self.scene} if self.scene else {})})
        for node in self.config["nodes"]:
            self.w.set_node(node, node not in self.offline, config_version=self.node_version.get(node),
                            rssi=random.randint(-72, -48))

    def write_summary(self):
        ts = CLOCK.now()
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
        print(f"house running  speed={CLOCK.speed:g}x{'  (old --fast timers)' if self.speedup > 1 else ''}  "
              f"offline={sorted(self.offline) or '-'}  fail_rate={self.args.fail_rate}   Ctrl+C to stop")
        for node in self.offline:
            rooms = " & ".join(r["name"] for r in self.config["rooms"].values() if r["node"] == node and not r.get("hidden"))
            self.w.log_event("node", "system", f"{self.node_name(node)} went offline", "system", "System",
                             result="failed", node=node, by_label="Hub watchdog", short="No data for 2 min",
                             why="No MQTT heartbeat for 120 s", change="Online → Offline", tags=["system"])
            self.w.push_alert("warning", f"{self.node_name(node)} is offline", f"{rooms or node} · ESP32 node · now", "settings")
        if self.args.lockout:
            for _ in range(self.th["door_lockout_attempts"]):
                self.wrong_attempt("keypad")
        self.loop_state = dict(last_tick=CLOCK.now(), real={})
        while True:
            try:
                self.step()
            except (RuntimeError, OSError) as e:
                hint = "check your internet / key" if self.args.cloud else "are the emulators running? `firebase emulators:start`"
                print(f"! {e}  ({hint})")
                time.sleep(3)
            self.wake.wait(TICK_S)                    # a twin command wakes the loop at once
            self.wake.clear()

    def step(self, headless=False):
        """One pass of the main loop. headless=True (learning-curve runs): no Firebase / twin page work,
        the caller moves the virtual clock forward between steps."""
        sp = self.speedup
        ls = self.loop_state
        real = ls["real"]
        r = time.time()
        state_real_s = 1.0 if self.args.cloud else 0.5
        summary_real_s = 5.0 if self.args.cloud else 0.5
        if not headless:
            self.handle_twin_commands()
        t = CLOCK.now()
        dt, ls["last_tick"] = max(0.0, t - ls["last_tick"]), t
        if not headless and r - real.get("cmd", 0) >= self.poll_s:
            real["cmd"] = r
            self.handle_commands()
        if not headless and r - real.get("cfg", 0) >= self.config_poll_s:
            real["cfg"] = r
            cfg = self.w.get_config()
            if cfg and json.dumps(cfg, sort_keys=True) != json.dumps(self.config, sort_keys=True):
                self.apply_config(cfg)
        if t - self.last["state"] >= 5 / sp:
            state_dt = t - self.last["state"] if self.last["state"] else 0
            self.last["state"] = t
            if not headless and r - real.get("pause", 0) >= self.poll_s:
                real["pause"] = r
                self.sync_ai_pause()
            self.person_tick()
            self.update_sensors()
            self.simulate_monitored()
            self.apply_rules()
            power = self.update_power(dt if dt < 3600 else 0)
            self.kpi_tick(state_dt if state_dt < 3600 else 0)
            self.simulated_answers()
            if self.ai and self.ai.suggestions:   # read answers first, so "Yes" is never seen as expired
                self.handle_suggestion_answers()
            if not self.ai_training():
                self.ai_tick(t - self.last.get("ai_tick", t))
                self.last["ai_tick"] = t
            if self.scenarios:
                self.scenarios.tick()
            if not headless and r - real.get("state", 0) >= state_real_s:
                real["state"] = r
                self.write_state(power)
        else:
            self.update_power(dt if dt < 3600 else 0)
        if not headless and r - real.get("sugg", 0) >= 2:
            real["sugg"] = r
            self.handle_suggestion_answers()
        if not headless and t - self.last["summary"] >= 60 / sp and r - real.get("summary", 0) >= summary_real_s:
            self.last["summary"] = t
            real["summary"] = r
            self.write_summary()
        self.check_retrain()
        if t - self.last["ai"] >= 900 / sp:
            self.last["ai"] = t
            self.run_ai()
        if not headless:
            self.twin_state()

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
    ap.add_argument("--speed", type=float, default=0, metavar="N",
                    help="virtual clock N times faster than real time (60 = one hour per minute)")
    ap.add_argument("--twin-only", action="store_true",
                    help="digital twin without Firebase (in memory): any speed, scenarios, no quota")
    ap.add_argument("--twin-port", type=int, default=8765, help="port of the digital twin page (default 8765)")
    ap.add_argument("--no-twin", action="store_true", help="don't start the digital twin page")
    ap.add_argument("--persona", default="default", metavar="FILE",
                    help="routine file the person lives (default ai/personas/student_studio.toml; 'none' = old routine)")
    ap.add_argument("--auto-answer", action="store_true",
                    help="the simulated person answers the AI's questions (default in --twin-only; with --cloud you answer in the app)")
    args = ap.parse_args()
    if args.speed:
        CLOCK.set_speed(args.speed)
    if args.cloud and args.twin_only:
        sys.exit("--twin-only runs without Firebase; drop --cloud")
    if args.cloud and CLOCK.speed > 10:
        print("! with the real Firebase keep --speed at 10 or less (free quota). For fast tests use --twin-only.")

    if args.twin_only:
        from memory_db import MemoryDB
        w = Writer(MemoryDB())
        print("digital twin only · no Firebase (everything stays in memory)")
    elif args.cloud:
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
    if args.twin_only:
        pass
    elif args.cloud:
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
    if not args.no_twin:
        from scenarios import ScenarioRunner
        from twin_server import start_twin
        house.scenarios = ScenarioRunner(house, CLOCK)
        house.twin_state()
        url = start_twin(house, args.twin_port)
        print(f"digital twin: {url}")
    try:
        house.run()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
