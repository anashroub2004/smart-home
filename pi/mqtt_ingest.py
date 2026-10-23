"""
MQTT ingest — the Pi's "ears" (stage 1b). The first real Pi service.

Listens to everything the ESP32 nodes say (home/#, topics in docs/contract.md), keeps a picture of the
house in memory (house_state.py, the same code automation.py uses), and writes it to the two places that
need it:
    SQLite  (ai/data/home.db)     every raw reading, on change + heartbeat — the AI learns from it; it never
                                  leaves the house (ai.store.Recorder, keys from contract.md "SQLite")
    Firebase                      /home_state rooms + devices + power, /nodes, and /events for what only the
                                  nodes know (a wall button, a node that went offline) — the app shows it
It also sends each node its part of /config (home/<node>/config, retained) and records its config/ack.

It only listens, records and shows. It never switches anything: /commands -> MQTT is commands.py (1c),
rules + AI are automation.py (1d). Each service writes only its own part of /home_state (patch_home_state).

Run (Mosquitto running, plus the Firebase emulators: firebase emulators:start):
    python pi/mqtt_ingest.py                    # emulator; seeds /config + the owner login if the db is empty
    python pi/mqtt_ingest.py --memory           # no Firebase at all (prints what it would write)
    python pi/mqtt_ingest.py --cloud KEY.json   # the real Firebase project (keep it short: free quota)
With the fake nodes in another window:  python pi/fake_nodes.py
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

import firebase_writer  # noqa: E402
import house_state  # noqa: E402
import house_words  # noqa: E402
from firebase_writer import Writer  # noqa: E402
from ai import store  # noqa: E402
from ai import settings as AIS  # noqa: E402

FLUSH_S = 2.0             # Firebase gets the changes at most every 2 s (20 MQTT messages/s would waste quota)
HOLD_CHECK_S = 5.0        # how often PIR-only rooms are checked for "no motion for pir_hold_s"
SNAPSHOT_S = 60.0         # every minute all values go to the Recorder too (it writes its 15-min heartbeat)

PRINT_LOCK = threading.Lock()


def say(*parts):
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Ingest(house_state.Listener):
    """Builds the picture of the house (house_state.HouseState) and records / shows what changes."""

    def __init__(self, writer, con, config, args):
        self.w, self.args = writer, args
        self.recorder = store.Recorder(con)
        self.lock = threading.Lock()
        self.client = None
        self.dirty = set()                                      # Firebase paths to write at the next flush
        self.house = house_state.HouseState(config, self)

    @property
    def config(self):
        return self.house.config

    @property
    def th(self):
        return self.house.th

    def load_config(self, config):
        with self.lock:
            self.house.load_config(config)

    def label(self, dev):
        return house_words.label(self.config, dev)

    def node_config(self, node):
        """This node's part of /config (contract.md: home/<node>/config)."""
        rooms = {rid: rc for rid, rc in self.config["rooms"].items() if rc.get("node") == node}
        return {"version": self.config.get("version"), "rooms": rooms}

    def send_configs(self):
        for node in self.house.nodes:
            body = json.dumps(self.node_config(node))
            self.client.publish(f"home/{node}/config", body, qos=1, retain=True)
            say(f"-> home/{node}/config", f"v{self.config.get('version')}")

    # ------------------------------------------------------------------ MQTT
    def connect(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-mqtt-ingest")
        c.on_connect = self.on_connect
        c.on_message = self.on_message
        self.client = c          # before loop_start: on_connect (another thread) publishes through self.client
        c.connect(self.args.host, self.args.port, keepalive=30)
        c.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            say("!! MQTT connect failed:", reason_code)
            return
        client.subscribe("home/#", qos=1)
        say("listening to home/# on", f"{self.args.host}:{self.args.port}")
        self.send_configs()

    def on_message(self, client, userdata, msg):
        topic, payload = msg.topic, msg.payload.decode("utf-8", "replace")
        if self.args.verbose:
            say("<-", topic, payload)
        try:
            with self.lock:
                self.house.handle(topic, payload)
        except (ValueError, TypeError, AttributeError) as e:
            say("!! bad message", topic, payload, f"({e})")

    # ------------------------------------------------------------------ what changed (house_state.Listener)
    def reading(self, key, value):
        self.recorder.observe({key: value})

    def room_changed(self, rid):
        self.dirty.add(f"rooms/{rid}")

    def power_changed(self, dev):
        self.dirty.update((f"devices/{dev}", "power_w"))

    def config_ack(self, node, version):
        self.dirty.add(f"nodes/{node}")
        say(f"node {node}", f"runs config v{version}")

    def node_status(self, node, online):
        self.dirty.add(f"nodes/{node}")
        name = (self.config.get("nodes") or {}).get(node, {}).get("name", node)
        say(f"node {node}", "online" if online else "OFFLINE")
        self.w.log_event("node", "system", f"{name} {'back online' if online else 'offline'}", "system", "System",
                         result="ok" if online else "failed", node=node,
                         short="Connected to the hub" if online else "No contact (power or Wi-Fi)",
                         tags=["system", "node"])

    def door_event(self, ev):
        """A person came in (fingerprint / PIN): the AI's door/entry. Access log + alerts are security.py."""
        if ev.get("ok") and ev.get("type") in ("fingerprint", "keypad"):
            self.recorder.event("door/entry")
            say("door", f"entry ({ev.get('type')})")

    def device_changed(self, dev, before, src, changed):
        self.dirty.add(f"devices/{dev}")
        if src == "button" and changed:
            # only the node knows about a wall button: the AI must hear it (manual control = hands off),
            # and the History must show it. Changes from the app / AI / rules are logged by the service
            # that asked for them (commands.py, automation.py) — not twice.
            st = self.house.devices[dev]
            self.recorder.event(f"override/{dev}")
            rid = self.house.dev_room[dev]
            room = self.config["rooms"][rid]
            what = "turned on" if st["v"] else "turned off"
            self.w.log_event("device", "manual", f"{self.label(dev)} {what}", "button", "Button", device=dev,
                             room=rid, node=room.get("node"), by_label="Someone at home (button)",
                             short=f"Wall button in the {room['name'].lower()}", tags=["manual"],
                             from_={k: v for k, v in before.items() if v is not None},
                             to={k: st.get(k) for k in ("v", "level", "mode") if st.get(k) is not None})
            say(f"button {dev}", what)

    # ------------------------------------------------------------------ periodic work
    def check_holds(self):
        with self.lock:
            self.house.check_holds()

    def snapshot(self):
        """All current values once a minute: the Recorder decides what is new (its 15-min heartbeat)."""
        with self.lock:
            self.recorder.observe(self.house.values())

    def flush(self):
        """Send what changed to Firebase in one patch (rooms + devices + total power + nodes)."""
        h = self.house
        with self.lock:
            dirty, self.dirty = self.dirty, set()
            if not dirty:
                return
            parts = {}
            for path in dirty:
                if path.startswith("rooms/"):
                    parts[path] = h.public_room(path.split("/", 1)[1])
                elif path.startswith("devices/"):
                    parts[path] = dict(h.devices[path.split("/", 1)[1]])
            if "power_w" in dirty or any(p.startswith("devices/") for p in dirty):
                parts["power_w"] = round(sum(st.get("watts") or 0 for st in h.devices.values()), 2)
            nodes = {p.split("/", 1)[1]: dict(h.nodes[p.split("/", 1)[1]]) for p in dirty if p.startswith("nodes/")}
        if parts:
            self.w.patch_home_state(parts)
        for node, n in nodes.items():
            self.w.set_node(node, n["online"], config_version=n["version"])
        if self.args.verbose:
            say("-> firebase", ", ".join(sorted(parts)) or "-", "· nodes:", ", ".join(nodes) or "-")

    def check_config(self):
        """The app (Add device, Settings) changed /config: reload it and send the nodes their new part."""
        try:
            cfg = self.w.get_config()
        except Exception as e:  # Firebase not reachable for a moment: try again next time
            say("!! cannot read /config:", e)
            return
        if cfg and cfg.get("version") != self.config.get("version"):
            say(f"/config changed: v{self.config.get('version')} -> v{cfg.get('version')}")
            self.load_config(cfg)
            self.send_configs()


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


def first_config(w, args):
    """/config from Firebase. An empty emulator gets docs/seed.json + the owner login (like the simulator)."""
    cfg = w.get_config()
    if cfg or args.cloud:
        return cfg
    import sim_house                                  # setup helpers only (it also swaps in a virtual clock)
    firebase_writer.set_clock(time.time)
    cfg = sim_house.seed(w, False)
    if not args.memory:
        sim_house.ensure_owner(w)
    return cfg


def main():
    ap = argparse.ArgumentParser(description="MQTT -> SQLite + Firebase (the Pi's ingest service)")
    ap.add_argument("--host", default="localhost", help="MQTT broker")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--db", default=str(AIS.DB_PATH), help="SQLite file the AI reads (default ai/data/home.db)")
    ap.add_argument("--memory", action="store_true", help="no Firebase: keep everything in memory")
    ap.add_argument("--cloud", metavar="KEY_JSON", help="the REAL Firebase project with this service-account key")
    ap.add_argument("--verbose", action="store_true", help="print every message and every Firebase write")
    args = ap.parse_args()

    w, where = open_firebase(args)
    try:
        config = first_config(w, args)
    except (RuntimeError, OSError) as e:
        sys.exit(f"Cannot reach Firebase ({where}): {e}\nStart the emulators first:  firebase emulators:start")
    if not config:
        sys.exit("/config is empty in the real database: set it up first (see docs/DEPLOY.md)")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = store.connect(args.db)
    ing = Ingest(w, con, config, args)
    say(f"Firebase: {where} · SQLite: {args.db} · config v{config.get('version')} · "
        f"PIR hold {ing.th.get('pir_hold_s', house_state.PIR_HOLD_S)} s")
    try:
        ing.connect()
    except OSError as e:
        sys.exit(f"cannot reach the MQTT broker at {args.host}:{args.port} ({e}) — is Mosquitto running?")

    last = {"flush": 0.0, "hold": 0.0, "snap": time.time(), "config": time.time()}
    try:
        while True:
            now = time.time()
            if now - last["hold"] >= HOLD_CHECK_S:
                ing.check_holds()
                last["hold"] = now
            if now - last["flush"] >= FLUSH_S:
                ing.flush()
                last["flush"] = now
            if now - last["snap"] >= SNAPSHOT_S:
                ing.snapshot()
                last["snap"] = now
            if now - last["config"] >= 30 and not args.memory:
                ing.check_config()
                last["config"] = now
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    ing.flush()
    ing.client.loop_stop()
    ing.client.disconnect()
    say("mqtt_ingest stopped")


if __name__ == "__main__":
    main()
