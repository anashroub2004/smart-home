"""
Security — the door's "notebook" on the Pi (before the Pi: stage 1, step b). The door node guards itself
(pi/door_guard.py: it counts the wrong PINs and locks its keypad with no Pi and no internet); this service
writes down what happened and tells the owner (docs/contract.md "Door security").

    node:  home/door/event {"type": "keypad", "ok": false, "n": 1}       -> /access_log + History + warning alert
           ... n: 5, then {"type": "lockout", "secs": 60, "round": 1}    -> red critical alert,
                                                                            /home_state/door/lockout_until
           {"type": "keypad", "ok": false, "locked": true}               -> refused during the lockout (logged)
           {"type": "fingerprint", "ok": true, "id": 1}                  -> /access_log + History + last_open
    node:  home/entrance/door_lock/state {"v": 1, "src": "web"}          -> last_open (the app's unlock;
                                                                            commands.py logs that one)

The internet may be down: every write goes to SQLite first (pi/outbox.py, table "access") and is sent to
Firebase when it can be, with the time it happened. MQTT stays inside the house, so nothing here needs the
internet. A persistent MQTT session: the broker keeps the door's messages while this service restarts.

Run (Mosquitto running; Firebase emulators for the app):
    python pi/security.py
    python pi/security.py --cloud KEY.json     # the real Firebase project
    python pi/security.py --memory             # no Firebase at all (prints what it would write)
"""
import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:      # the simulator uses Security without MQTT (standard library only)
    mqtt = None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import house_words  # noqa: E402
from firebase_writer import Writer  # noqa: E402
from outbox import Outbox, read_config  # noqa: E402
from ai import store  # noqa: E402
from ai import settings as AIS  # noqa: E402

FLUSH_S = 1.0             # the outbox is sent every second ...
RETRY_S = 10.0            # ... or every 10 s while Firebase cannot be reached
CONFIG_EVERY_S = 30.0     # /config is re-read (thresholds, fingerprint names)

PRINT_LOCK = threading.Lock()


def say(*parts):
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


def duration(secs):
    return f"{secs} s" if secs < 120 else f"{round(secs / 60)} min"


# ------------------------------------------------------------------ what the door's messages mean
class Security:
    """Turns the door node's messages into /access_log, History, alerts and /home_state/door.
    `out` is an Outbox (the Pi) or a Writer (the simulator, which writes /home_state itself)."""

    def __init__(self, out, config, clock=time.time):
        self.out, self.clock = out, clock
        self.lock = threading.Lock()
        self.door = {"lockout_until": 0}      # /home_state/door
        self.dirty = False                    # self.door changed and is not in Firebase yet
        self.warned = False                   # the yellow alert went out since the last correct open
        self.load_config(config)

    def load_config(self, config):
        self.config, self.th = config, config.get("thresholds") or {}
        index = house_words.device_index(config)
        self.lock_id = next((d for d, (_, cfg) in index.items() if "lock" in (cfg.get("caps") or {})), None)
        self.lock_room = index[self.lock_id][0] if self.lock_id else "entrance"
        self.node = (config["rooms"].get(self.lock_room) or {}).get("node", "door")

    def now_ms(self):
        return int(self.clock() * 1000)

    def finger_name(self, fid):
        """{finger id: name} in /config. Firebase turns objects with keys "1","2",... into arrays: accept both."""
        f = self.config.get("fingerprints") or {}
        if isinstance(f, list):
            f = {str(i): name for i, name in enumerate(f) if name}
        return (f or {"1": "Owner"}).get(str(fid), f"Finger #{fid}")

    def where(self):
        return dict(device=self.lock_id, room=self.lock_room, node=self.node)

    # ---- MQTT
    def on_door_event(self, ev):
        kind, ok = ev.get("type"), bool(ev.get("ok"))
        with self.lock:
            if kind == "lockout":
                self.locked_out(int(ev.get("secs") or self.th.get("door_lockout_s", 60)), int(ev.get("round") or 1))
            elif kind in ("fingerprint", "keypad", "exit_button") and ok:
                fid = ev.get("id")
                self.opened(kind, self.finger_name(fid) if kind == "fingerprint" else None, fid)
            elif kind in ("fingerprint", "keypad"):
                self.refused(kind, ev.get("n"), bool(ev.get("locked")))

    def on_lock_state(self, st):
        """The app's unlock reaches the node through commands.py (which logs it); the node resets its count."""
        if st.get("v") and st.get("src") == "web":
            with self.lock:
                self.opened("web", "Owner", history=False, access=False)

    # ---- what happened
    def opened(self, method, who=None, finger=None, latency=None, history=True, access=True):
        if access:
            self.out.log_access(method, True, who)
        if history:
            words = house_words.door_open_words(method, who, finger)
            self.out.log_event("door", "door", words.pop("title"), method, words.pop("src_label"), **self.where(),
                               change="Locked → Unlocked → Locked", latency_ms=latency,
                               tags=["door"] + (["manual"] if method == "web" else []), **words)
        self.door["last_open"] = {"at": self.now_ms(), "method": method, **({"who": who} if who else {})}
        if method != "exit_button":                       # a correct open ends the node's lockout
            self.door["lockout_until"] = 0
            self.warned = False
        self.dirty = True
        say(f"door opened: {method}" + (f" ({who})" if who else ""))

    def refused(self, method, n, locked):
        what = "Wrong PIN" if method == "keypad" else "Unknown fingerprint"
        label = "Keypad" if method == "keypad" else "Fingerprint"
        self.out.log_access(method, False)
        if locked:
            left = max(0, round((self.door.get("lockout_until", 0) - self.now_ms()) / 1000))
            self.out.log_event("door", "door", f"{label} attempt during the lockout", method, label, result="denied",
                               **self.where(), by_label="Unknown", short="Refused · keypad locked",
                               why=f"The door node is locked out ({left} s left): not counted", change="No change",
                               tags=["door", "security"])
            say(f"door: {method} refused during the lockout")
            return
        limit = int(self.th.get("door_lockout_attempts", 5))
        self.out.log_event("door", "door", f"{what} at the front door", method, label, result="denied",
                           **self.where(), by_label="Unknown", short="Door stayed locked",
                           why=f"{what} (attempt {n or '?'} of {limit})", change="No change", tags=["door"])
        if not self.warned:                               # once; the next rounds get the red alert only
            self.warned = True
            self.out.push_alert("warning", f"{what} at the front door",
                                f"Front door · {label.lower()} · door stayed locked", "security")
        say(f"door: {what.lower()} ({n} of {limit})")

    def locked_out(self, secs, rnd):
        limit = int(self.th.get("door_lockout_attempts", 5))
        self.door["lockout_until"] = self.now_ms() + secs * 1000
        self.dirty = True
        again = f" · lockout {rnd} in a row" if rnd > 1 else ""
        self.out.log_event("door", "door", "Keypad locked", "keypad", "Keypad", result="failed", **self.where(),
                           by_label="Unknown", short=f"{limit} wrong attempts · locked {duration(secs)}",
                           why=f"{limit} wrong attempts in a row{again}", change=f"Keypad blocked for {duration(secs)}",
                           tags=["door", "security"])
        hhmm = datetime.fromtimestamp(self.clock()).strftime("%H:%M")
        self.out.push_alert("critical", "Someone is trying to get in", f"Front door · {limit} wrong attempts",
                            "security", lines=[f"**{limit} wrong attempts** at the front door"
                                               + (f" (lockout {rnd} since the last correct open)" if rnd > 1 else ""),
                                               f"Today {hhmm} · Keypad on the door node",
                                               f"Keypad is blocked for {duration(secs)}. "
                                               "Your fingerprint and the app still open the door."])
        say(f"door: LOCKED OUT {duration(secs)} (round {rnd})")


# ------------------------------------------------------------------ the service
class Service:
    def __init__(self, writer, con, config, args, clock=None):
        self.w, self.con, self.args = writer, con, args
        self.sim = clock                                        # hub_clock.SharedClock with --sim-clock
        self.clock = clock.now if clock else time.time
        self.out = Outbox(con, writer, self.clock, table="access")
        self.sec = Security(self.out, config, self.clock)
        self.client = None

    def connect(self):
        # persistent session: messages for us wait in the broker while this service restarts (QoS 1)
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-security", clean_session=False)
        c.on_connect = self.on_connect
        c.on_message = self.on_message
        self.client = c
        c.connect(self.args.host, self.args.port, keepalive=30)
        c.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            say("!! MQTT connect failed:", reason_code)
            return
        if self.sim:
            self.sim.attach(client)
        client.subscribe("home/door/event", qos=1)
        if self.sec.lock_id:
            client.subscribe(f"home/{self.sec.lock_room}/{self.sec.lock_id}/state", qos=1)
        say("connected to MQTT", f"{self.args.host}:{self.args.port}")

    def on_message(self, client, userdata, msg):
        try:
            body = json.loads(msg.payload.decode("utf-8", "replace") or "{}")
        except ValueError:
            say("!! not JSON:", msg.topic)
            return
        if msg.topic == "home/door/event":
            self.sec.on_door_event(body)
        elif msg.topic.endswith("/state"):
            self.sec.on_lock_state(body)

    def flush(self):
        """-> True when Firebase took everything."""
        ok = self.out.flush()
        if ok and self.sec.dirty:
            with self.sec.lock:
                door, self.sec.dirty = dict(self.sec.door), False
            try:
                self.w.patch_home_state({"door": door})
            except Exception:  # noqa: BLE001 — next time
                self.sec.dirty, ok = True, False
        return ok

    def check_config(self):
        cfg = read_config(self.w, self.con, quiet=True)
        if cfg and cfg.get("version") != self.sec.config.get("version"):
            say(f"/config changed: v{self.sec.config.get('version')} -> v{cfg.get('version')}")
            with self.sec.lock:
                self.sec.load_config(cfg)


def open_firebase(args):
    if args.memory:
        from memory_db import MemoryDB
        return Writer(MemoryDB()), "memory (no Firebase)"
    if args.cloud:
        from cloud_db import cloud_db
        w = Writer(cloud_db(args.cloud))
        return w, f"REAL Firebase · project {w.db.project_id}"
    from rest_db import RestDB, PROJECT_ID
    return Writer(RestDB()), f"emulator · project {PROJECT_ID}"


def main():
    ap = argparse.ArgumentParser(description="Door security on the Pi: access log, alerts, lockout state")
    ap.add_argument("--host", default="localhost", help="MQTT broker")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--db", default=str(AIS.DB_PATH), help="SQLite file (default ai/data/home.db)")
    ap.add_argument("--cloud", metavar="KEY_JSON", help="the REAL Firebase project with this service-account key")
    ap.add_argument("--memory", action="store_true", help="no Firebase: keep everything in memory")
    ap.add_argument("--sim-clock", action="store_true",
                    help="test only: use the world's fast clock (fake_nodes.py --world --speed N, hub/clock)")
    args = ap.parse_args()
    if mqtt is None:
        sys.exit('paho-mqtt is missing: pip install "paho-mqtt>=2.0,<3"')

    sim = None
    if args.sim_clock:
        import firebase_writer
        from hub_clock import SharedClock
        sim = SharedClock()
        firebase_writer.set_clock(sim.now)
    w, where = open_firebase(args)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = store.connect(args.db)
    svc = Service(w, con, read_config(w, con), args, clock=sim)
    try:
        door = w.db.get("home_state/door")                    # keep last_open across a restart
        if isinstance(door, dict):
            svc.sec.door.update(door)
    except Exception:  # noqa: BLE001 — no internet at start: fine
        pass
    try:
        svc.connect()
    except OSError as e:
        sys.exit(f"cannot reach the MQTT broker at {args.host}:{args.port} ({e}) — is Mosquitto running?")
    say(f"Firebase: {where} · SQLite: {args.db} · {svc.out.pending()} waiting to be sent · "
        f"lockout after {svc.sec.th.get('door_lockout_attempts', 5)} wrong (enforced on the door node)")

    last = {"flush": 0.0, "config": time.time()}
    try:
        while True:
            now = time.time()
            if now - last["flush"] >= (RETRY_S if svc.out.down else FLUSH_S):
                svc.flush()
                last["flush"] = now
            if now - last["config"] >= CONFIG_EVERY_S and not args.memory:
                svc.check_config()
                last["config"] = now
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    svc.client.loop_stop()
    svc.client.disconnect()
    svc.flush()
    say("security stopped")


if __name__ == "__main__":
    main()
