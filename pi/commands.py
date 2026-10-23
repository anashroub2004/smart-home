"""
Commands — the Pi's "hand" (stage 1c). The app writes /commands/{device}; this service sends it to the node
over MQTT, waits for the node's answer and marks the command done or failed.

    app:      /commands/living_fan = {"v": 1, "level": 70, "by": uid, "at": ms, "status": "pending"}
    here:     checks it  ->  home/living/living_fan/set {"v": 1, "level": 70, "src": "web"}
    node:     home/living/living_fan/state {"v": 1, "level": 70, "src": "web"}     (it echoes src)
    here:     /commands/living_fan status "done"  +  /events "Living room fan turned on · App · 230 ms"
              + SQLite override/living_fan (manual control: the AI keeps its hands off for 2 h)

Decisions (Iyad, 2026-10-22):
    - the node has 5 s to answer (NODE_TIMEOUT_S); the app itself gives up after 10 s
    - no automatic retry: a failed command says so and the user sends it again (a retry could switch twice)
    - a node we know is offline (its last will) fails at once, without waiting
    - /commands is read every second (POLL_S): fine for the emulator and ~3% of the free quota on the real
      Firebase; switch to streaming before the real database (stage 2)

/home_state is NOT written here: mqtt_ingest hears the same state message and updates it.
What this service logs in /events: the app's commands (who, how long). Wall buttons are mqtt_ingest's,
the AI's and the rules' decisions are automation.py's.

Run (Mosquitto + Firebase emulators + mqtt_ingest + fake nodes running):
    python pi/commands.py
    python pi/commands.py --cloud KEY.json     # the real Firebase project
"""
import argparse
import json
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

import house_words  # noqa: E402
from firebase_writer import Writer, now_ms  # noqa: E402
from mqtt_exec import MqttExecutor  # noqa: E402
from ai import store  # noqa: E402
from ai import settings as AIS  # noqa: E402

POLL_S = 1.0              # how often /commands is read
NODE_TIMEOUT_S = 5.0      # the node must answer within 5 s
CONFIG_EVERY_S = 30.0     # /config is re-read (a device added in the app)

PRINT_LOCK = threading.Lock()


def say(*parts):
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Commands:
    def __init__(self, writer, con, config, args):
        self.w, self.con, self.args = writer, con, args
        self.lock = threading.Lock()
        self.client = None
        self.online = {}        # node -> True / False (from home/<node>/status, retained)
        self.seen = {}          # device -> key of the last command taken, so a command runs once
        self.ex = None          # MqttExecutor: sends the /set and waits for the node's answer
        self.last_state = {}    # device -> the last state its node reported
        self.scene = None
        self.load_config(config)

    def load_config(self, config):
        with self.lock:
            self.config = config
            self.index = house_words.device_index(config)

    def node_of(self, dev):
        return self.config["rooms"][self.index[dev][0]].get("node")

    # ------------------------------------------------------------------ MQTT
    def connect(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-commands")
        c.on_connect = self.on_connect
        c.on_message = self.on_message
        self.client, self.ex = c, MqttExecutor(c, NODE_TIMEOUT_S)    # ready before the first message arrives
        c.connect(self.args.host, self.args.port, keepalive=30)
        c.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            say("!! MQTT connect failed:", reason_code)
            return
        client.subscribe("home/+/status", qos=1)          # which nodes are alive (retained + last will)
        client.subscribe("home/+/+/state", qos=1)         # the nodes' answers
        say("connected to MQTT", f"{self.args.host}:{self.args.port}")

    def on_message(self, client, userdata, msg):
        p = msg.topic.split("/")
        payload = msg.payload.decode("utf-8", "replace")
        if len(p) == 3 and p[2] == "status":
            self.online[p[1]] = payload.strip() == "online"
            return
        if len(p) == 4 and p[3] == "state":
            try:
                st = json.loads(payload)
            except ValueError:
                return
            with self.lock:
                self.last_state[p[2]] = st                # "before" for the next command's History line
            self.ex.answer(p[2], st)

    # ------------------------------------------------------------------ /commands
    def poll(self):
        try:
            cmds = self.w.get_commands()
        except Exception as e:  # Firebase not reachable for a moment: next second
            say("!! cannot read /commands:", e)
            return
        for dev, cmd in cmds.items():
            if not isinstance(cmd, dict) or cmd.get("status") != "pending":
                continue
            key = (cmd.get("at"), cmd.get("v"), cmd.get("level"), cmd.get("mode"))
            if self.seen.get(dev) == key:
                continue
            self.seen[dev] = key
            self.start(dev, cmd)

    def meta(self, cmd):
        """Who asked, in the History's words (the same as the simulator)."""
        scene = cmd.get("scene")
        if cmd.get("via") == "suggestion":
            pct = round((cmd.get("confidence") or 0) * 100)
            return dict(source="ai", src_label=f"AI · {pct}% · approved", group="ai",
                        by_label="AI suggestion, approved by Owner", why="You said yes to an AI suggestion",
                        short="Suggestion approved", confidence=cmd.get("confidence"), tags=["ai", "manual"])
        if scene:
            name = ((self.config.get("scenes") or {}).get(scene) or {}).get("name", scene)
            return dict(source="web", src_label=f"Scene · {name}", group="manual", by_label="Owner (web app)",
                        why=f"{name} scene", short=f"{name} scene", kind="scene", tags=["manual"])
        return dict(source="web", src_label="App", group="manual", by_label="Owner (web app)",
                    why="Tapped in the app", short="Owner · web app", tags=["manual"])

    def start(self, dev, cmd):
        if dev not in self.index:
            self.w.ack_command(dev, False, "unknown device")
            say(f"{dev}: unknown device -> failed")
            return
        rid, cfg = self.index[dev]
        caps, ctrl = cfg.get("caps") or {}, cfg.get("control") or {}
        lock = "lock" in caps
        if not (caps.get("power") == "write" or lock) or not ctrl.get("app", False):
            self.w.ack_command(dev, False, "This device can't be controlled from the app")
            say(f"{dev}: not controllable from the app -> failed")
            return
        self.take_scene(cmd.get("scene"))
        node, meta = self.node_of(dev), self.meta(cmd)
        job = {"cmd": cmd, "meta": meta, "lock": lock, "node": node,
               "before": dict(self.last_state.get(dev) or {"v": 0})}
        if not self.online.get(node):
            self.fail(dev, job, f"No response from the {node} node (offline)")
            return
        self.ex.send(dev, rid, cmd, meta["source"], node=node, lock=lock,
                     done=lambda d, st, _ms: self.finish(d, job, st), failed=lambda d, why: self.fail(d, job, why))
        say(f"{dev}: -> " + ("home/door/open" if lock else f"home/{rid}/{dev}/set"))

    def take_scene(self, scene):
        """A scene button sends one command per device; the house is now in that scene (Away = nobody home)."""
        if not scene or scene == self.scene:
            return
        self.scene = scene
        self.w.patch_home_state({"scene": scene})
        store.write(self.con, [(int(time.time()), "mode/away", 1.0 if scene == "away" else 0.0)])

    # ------------------------------------------------------------------ the end of a command
    def finish(self, dev, job, state):
        cmd, meta = job["cmd"], job["meta"]
        latency = max(0, now_ms() - int(cmd.get("at") or now_ms()))
        self.w.ack_command(dev, True)
        rid, cfg = self.index[dev]
        if job["lock"]:
            self.w.log_access("web", True, "Owner")
            self.w.log_event("door", "door", "Front door unlocked remotely", meta["source"], meta["src_label"],
                             device=dev, room=rid, node=job["node"], by=cmd.get("by"), by_label=meta["by_label"],
                             why="Remote unlock, confirmed in the app", short=meta["short"],
                             change="Locked → Unlocked → Locked", latency_ms=latency, tags=["door", "manual"])
            store.write(self.con, [(int(time.time()), "door/entry", 1.0)])     # someone was let in
        else:
            before = job["before"]
            what = "turned on" if state.get("v") else "turned off"
            if before.get("v") and state.get("v") and before.get("level") != state.get("level") \
                    and (cfg.get("caps") or {}).get("level"):
                what = "speed changed" if cfg.get("icon") == "fan" else "level changed"
            self.w.log_event(meta.pop("kind", "device"), meta["group"], f"{house_words.label(self.config, dev)} {what}",
                             meta["source"], meta["src_label"], device=dev, room=rid, node=job["node"],
                             by=cmd.get("by"), by_label=meta["by_label"], why=meta["why"], short=meta["short"],
                             change=f"{house_words.state_label(cfg, before)} → {house_words.state_label(cfg, state)}",
                             confidence=meta.get("confidence"),
                             from_={k: before[k] for k in ("v", "level", "mode") if before.get(k) is not None},
                             to={k: state[k] for k in ("v", "level", "mode") if state.get(k) is not None},
                             latency_ms=latency, tags=meta["tags"])
            # manual control (app, scene, an approved suggestion): the AI keeps its hands off this device
            store.write(self.con, [(int(time.time()), f"override/{dev}", 1.0)])
        say(f"{dev}: done in {latency} ms")

    def fail(self, dev, job, why):
        cmd, meta = job["cmd"], job["meta"]
        self.w.ack_command(dev, False, why)
        rid, _ = self.index[dev]
        title = ("Remote unlock failed" if job["lock"] else
                 f"{house_words.label(self.config, dev)} {'turned on' if cmd.get('v') else 'turned off'}")
        self.w.log_event("door" if job["lock"] else "device", "door" if job["lock"] else meta["group"], title,
                         meta["source"], meta["src_label"], result="failed", device=dev, room=rid, node=job["node"],
                         by=cmd.get("by"), by_label=meta["by_label"], why=meta["why"], short=why,
                         change="(unchanged)", tags=meta["tags"] + ["failed"])
        say(f"{dev}: FAILED — {why}")

    def check_config(self):
        try:
            cfg = self.w.get_config()
        except Exception as e:
            say("!! cannot read /config:", e)
            return
        if cfg and cfg.get("version") != self.config.get("version"):
            say(f"/config changed: v{self.config.get('version')} -> v{cfg.get('version')}")
            self.load_config(cfg)


def open_firebase(args):
    if args.cloud:
        from cloud_db import cloud_db
        w = Writer(cloud_db(args.cloud))
        return w, f"REAL Firebase · project {w.db.project_id}"
    from rest_db import RestDB, PROJECT_ID
    return Writer(RestDB()), f"emulator · project {PROJECT_ID}"


def main():
    ap = argparse.ArgumentParser(description="/commands -> MQTT set -> done / failed (the Pi's command service)")
    ap.add_argument("--host", default="localhost", help="MQTT broker")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--db", default=str(AIS.DB_PATH), help="SQLite file the AI reads (default ai/data/home.db)")
    ap.add_argument("--cloud", metavar="KEY_JSON", help="the REAL Firebase project with this service-account key")
    args = ap.parse_args()

    w, where = open_firebase(args)
    try:
        config = w.get_config()
    except (RuntimeError, OSError) as e:
        sys.exit(f"Cannot reach Firebase ({where}): {e}\nStart the emulators first:  firebase emulators:start")
    if not config:
        sys.exit("/config is empty: start mqtt_ingest.py first (it seeds an empty emulator)")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    cmds = Commands(w, store.connect(args.db), config, args)
    try:
        cmds.connect()
    except OSError as e:
        sys.exit(f"cannot reach the MQTT broker at {args.host}:{args.port} ({e}) — is Mosquitto running?")
    # commands still "pending" from before we started are old (the app gave up after 10 s): close them as
    # failed instead of switching something minutes later
    for dev, cmd in (w.get_commands() or {}).items():
        if isinstance(cmd, dict):
            cmds.seen[dev] = (cmd.get("at"), cmd.get("v"), cmd.get("level"), cmd.get("mode"))
            if cmd.get("status") == "pending" and now_ms() - int(cmd.get("at") or 0) > 10_000:
                w.ack_command(dev, False, "Expired: the hub was not running")
    say(f"Firebase: {where} · node timeout {NODE_TIMEOUT_S:.0f} s · /commands every {POLL_S:.0f} s")

    last_cfg = time.time()
    try:
        while True:
            cmds.poll()
            cmds.ex.check_timeouts()
            if time.time() - last_cfg >= CONFIG_EVERY_S:
                cmds.check_config()
                last_cfg = time.time()
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    cmds.client.loop_stop()
    cmds.client.disconnect()
    say("commands stopped")


if __name__ == "__main__":
    main()
