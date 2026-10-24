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

Local first: the internet may be down (or slow). The house's own work (MQTT, SQLite, the energy counter) runs
in the main loop and never waits for Firebase; a sender thread does all the Firebase traffic. While Firebase
cannot be reached, /home_state keeps its changes for the next try, History lines and minute summaries wait in
SQLite (pi/outbox.py, table "outbox"), and a start without the internet uses the last /config kept in SQLite.

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
import mqtt_auth  # noqa: E402
from firebase_writer import Writer  # noqa: E402
from outbox import Outbox, keep_config, kept_config, read_config  # noqa: E402
from ai import store  # noqa: E402
from ai import settings as AIS  # noqa: E402

FLUSH_S = 2.0             # Firebase gets the changes at most every 2 s (20 MQTT messages/s would waste quota)
RETRY_S = 10.0            # while Firebase cannot be reached: try again every 10 s
CONFIG_EVERY_S = 30.0     # /config is re-read (a device added in the app)
HOLD_CHECK_S = 5.0        # how often PIR-only rooms are checked for "no motion for pir_hold_s"
SNAPSHOT_S = 60.0         # every minute all values go to the Recorder too (it writes its 15-min heartbeat)
ENERGY_S = 2.0            # the energy counter adds watts x time this often (real seconds)
BASE_W = 6.8              # the Pi + the 3 nodes + their sensors (no INA226 on them; the simulator's figure)

PRINT_LOCK = threading.Lock()


def say(*parts):
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Ingest(house_state.Listener):
    """Builds the picture of the house (house_state.HouseState) and records / shows what changes."""

    def __init__(self, writer, con, config, args, clock=None):
        self.w, self.args, self.con = writer, args, con
        self.recorder = store.Recorder(con)
        self.lock = threading.Lock()
        self.client = None
        self.dirty = set()                                      # Firebase paths to write at the next flush
        self.sim = clock                                        # hub_clock.SharedClock with --sim-clock
        self.clock = clock.now if clock else time.time
        self.house = house_state.HouseState(config, self, self.clock)
        self.out = Outbox(con, writer, self.clock)              # History lines + summaries wait here offline
        self.down = False                                       # /home_state could not be written last time
        # energy today, Wh per device + "_base". Counting starts at once (with --sim-clock once hub/clock has
        # arrived); what /energy_daily already has for today is added when Firebase can be read ("synced"),
        # so a restart without the internet never writes a smaller total over the real one
        self.energy_day, self.energy, self.energy_t, self.energy_synced = None, {}, None, False
        self.energy_out = {}                                    # day -> (Wh per device, synced): to write
        self.minute = None                                      # the house minute last summarised

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
        body = {"version": self.config.get("version"), "rooms": rooms}
        if any("lock" in (d.get("caps") or {}) for rc in rooms.values() for d in (rc.get("devices") or {}).values()):
            th = self.config.get("thresholds") or {}         # the door node enforces the lockout itself (kept in flash)
            body["door"] = {"lockout_attempts": int(th.get("door_lockout_attempts", 5)),
                            "lockout_s": int(th.get("door_lockout_s", 60)),
                            "lockout_max_s": int(th.get("door_lockout_max_s", 900))}
        return body

    def send_configs(self):
        for node in self.house.nodes:
            body = json.dumps(self.node_config(node))
            self.client.publish(f"home/{node}/config", body, qos=1, retain=True)
            say(f"-> home/{node}/config", f"v{self.config.get('version')}")

    # ------------------------------------------------------------------ MQTT
    def connect(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-mqtt-ingest")
        mqtt_auth.login(c)                 # the Pi: MQTT_USER / MQTT_PASS
        c.on_connect = self.on_connect
        c.on_message = self.on_message
        self.client = c          # before loop_start: on_connect (another thread) publishes through self.client
        c.connect(self.args.host, self.args.port, keepalive=30)
        c.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            say("!! MQTT connect failed:", reason_code)
            return
        if self.sim:                                            # the time first: readings are stamped with it
            self.sim.attach(client, then=lambda: self.listen(client))
        else:
            self.listen(client)

    def listen(self, client):
        client.subscribe("home/#", qos=1)
        say("listening to home/# on", f"{self.args.host}:{self.args.port}"
            + (f" · shared clock x{self.sim.speed:g}" if self.sim else ""))
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
        except Exception as e:  # noqa: BLE001 — never let one message stop the MQTT thread (the ears go deaf)
            say("!! error on", topic, f"({type(e).__name__}: {e})")

    # ------------------------------------------------------------------ what changed (house_state.Listener)
    def reading(self, key, value):
        self.recorder.observe({key: value}, ts=self.clock())

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
        self.out.log_event("node", "system", f"{name} {'back online' if online else 'offline'}", "system", "System",
                         result="ok" if online else "failed", node=node,
                         short="Connected to the hub" if online else "No contact (power or Wi-Fi)",
                         tags=["system", "node"])

    def door_event(self, ev):
        """A person came in (fingerprint / PIN): the AI's door/entry. Access log + alerts are security.py."""
        if ev.get("ok") and ev.get("type") in ("fingerprint", "keypad"):
            self.recorder.event("door/entry", ts=self.clock())
            say("door", f"entry ({ev.get('type')})")

    def device_changed(self, dev, before, src, changed):
        self.dirty.add(f"devices/{dev}")
        if src == "button" and changed:
            # only the node knows about a wall button: the AI must hear it (manual control = hands off),
            # and the History must show it. Changes from the app / AI / rules are logged by the service
            # that asked for them (commands.py, automation.py) — not twice.
            st = self.house.devices[dev]
            self.recorder.event(f"override/{dev}", ts=self.clock())
            rid = self.house.dev_room[dev]
            room = self.config["rooms"][rid]
            what = "turned on" if st["v"] else "turned off"
            self.out.log_event("device", "manual", f"{self.label(dev)} {what}", "button", "Button", device=dev,
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
            self.recorder.observe(self.house.values(), ts=self.clock())

    def flush(self):
        """Send what changed to /home_state + /nodes in one go. -> False when Firebase cannot be reached: the
        changes are kept and the next flush sends the values of that moment."""
        h = self.house
        with self.lock:
            dirty, self.dirty = self.dirty, set()
            if not dirty:
                return True
            parts = {}
            for path in dirty:
                if path.startswith("rooms/"):
                    parts[path] = h.public_room(path.split("/", 1)[1])
                elif path.startswith("devices/"):
                    parts[path] = dict(h.devices[path.split("/", 1)[1]])
            if "power_w" in dirty or any(p.startswith("devices/") for p in dirty):
                parts["power_w"] = round(BASE_W + sum(st.get("watts") or 0 for st in h.devices.values()), 2)
                parts["base_w"] = BASE_W
            nodes = {p.split("/", 1)[1]: dict(h.nodes[p.split("/", 1)[1]]) for p in dirty if p.startswith("nodes/")}
        try:
            if parts:
                self.w.patch_home_state(parts)
            for node, n in nodes.items():
                self.w.set_node(node, n["online"], config_version=n["version"])
        except Exception as e:  # noqa: BLE001 — no internet: keep the changes for the next try
            with self.lock:
                self.dirty |= dirty
            if not self.down:
                say(f"!! Firebase not reachable ({type(e).__name__}): the house goes on, the app catches up later")
            self.down = True
            return False
        if self.down:
            say("Firebase back: /home_state is up to date")
        self.down = False
        if self.args.verbose:
            say("-> firebase", ", ".join(sorted(parts)) or "-", "· nodes:", ", ".join(nodes) or "-")
        return True

    # ------------------------------------------------------------------ energy + summaries (the app's history)
    def count_energy(self):
        """Wh = watts x time, per device (what its INA226 measures) and for the hub itself (_base)."""
        now = self.clock()
        if self.energy_t is None:
            if self.sim and not self.sim.ready.is_set():
                return
            self.energy_day, self.energy_t = firebase_writer.day_key(now), now
            return
        with self.lock:
            # at most a minute per step: a clock jump (tests) or a pause must not add hours at once
            dt = min(60.0, max(0.0, now - self.energy_t))
            self.energy_t = now
            for dev, st in self.house.devices.items():
                if st.get("watts"):
                    self.energy[dev] = self.energy.get(dev, 0.0) + st["watts"] * dt / 3600
            self.energy["_base"] = self.energy.get("_base", 0.0) + BASE_W * dt / 3600

    def each_minute(self):
        """Once per house minute: /summaries/{room}/{day}/{HH:MM} (the room's day for the app's charts, through
        the outbox) and /energy_daily/{day} (the Energy screen, by the sender). A new day starts the counter
        again at midnight."""
        now = self.clock()
        minute = int(now // 60)
        if minute == self.minute or self.energy_t is None:
            return
        self.minute = minute
        h, rows = self.house, []
        with self.lock:
            for rid, room in self.config["rooms"].items():
                s = h.rooms.get(rid)
                if not room.get("sensors") or s is None or not h.nodes.get(room.get("node"), {}).get("online"):
                    continue
                devs = [d for d in (room.get("devices") or {}) if d in h.devices]
                data = {k: s[k] for k in ("temp", "hum", "lux", "occ") if s.get(k) is not None}
                data["watts"] = round(sum(h.devices[d].get("watts") or 0 for d in devs), 2)
                dv = {d: int(h.devices[d].get("v") or 0) for d in devs
                      if "lock" not in (h.dev_cfg(d).get("caps") or {})}
                if dv:
                    data["dev"] = dv
                rows.append((rid, data))
            day = firebase_writer.day_key(now)
            # this day's total so far (at midnight: yesterday's last minute)
            self.energy_out[self.energy_day] = (dict(self.energy), self.energy_synced)
            if day != self.energy_day:                          # midnight: yesterday is complete, today from 0
                say("new day:", day, "— the energy counter starts again")
                self.energy_day, self.energy, self.energy_synced = day, {}, True
        for rid, data in rows:
            self.out.write_summary(rid, now, data)

    def send_energy(self):
        """The sender's part of the energy counter. -> False when Firebase cannot be reached."""
        try:
            if not self.energy_synced and self.energy_day:
                day = self.energy_day
                stored = self.w.get_energy_day(day) or {}       # what today already had before we started
                with self.lock:
                    if day == self.energy_day and not self.energy_synced:
                        for k, v in stored.items():
                            self.energy[k] = self.energy.get(k, 0.0) + float(v or 0)
                        self.energy_synced = True
                        self.energy_out.pop(day, None)          # the next minute writes the full total
            with self.lock:
                todo = list(self.energy_out.items())
            for day, entry in todo:
                vals, synced = entry
                if not synced:
                    if day == self.energy_day:
                        continue                                # today: written once synced (above)
                    stored = self.w.get_energy_day(day) or {}    # a past day counted before it was synced
                    vals = {k: vals.get(k, 0.0) + float(stored.get(k) or 0) for k in set(vals) | set(stored)}
                self.w.write_energy_day(day, vals)
                with self.lock:
                    if self.energy_out.get(day) is entry:
                        self.energy_out.pop(day)
        except Exception:  # noqa: BLE001 — no internet: the counter goes on, the next try catches up
            return False
        return True

    def check_config(self):
        """The app (Add device, Settings) changed /config: reload it and send the nodes their new part."""
        cfg = read_config(self.w, self.con, quiet=True)          # kept in SQLite for a start without internet
        if cfg and cfg.get("version") != self.config.get("version"):
            say(f"/config changed: v{self.config.get('version')} -> v{cfg.get('version')}")
            self.load_config(cfg)
            self.send_configs()

    def sender(self, stop):
        """All Firebase traffic, away from the house's own work: a slow or missing internet never holds it up."""
        last_cfg = time.time()
        while not stop.is_set():
            ok = self.flush()
            ok = self.out.flush() and ok
            ok = self.send_energy() and ok
            if time.time() - last_cfg >= CONFIG_EVERY_S and not self.args.memory:
                self.check_config()
                last_cfg = time.time()
            stop.wait(FLUSH_S if ok else RETRY_S)


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
    keep = firebase_writer._clock
    import sim_house                                  # setup helpers only (it also swaps in a virtual clock)
    firebase_writer.set_clock(keep)
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
    ap.add_argument("--sim-clock", action="store_true",
                    help="test only: use the world's fast clock (fake_nodes.py --world --speed N, hub/clock)")
    args = ap.parse_args()

    sim = None
    if args.sim_clock:
        from hub_clock import SharedClock
        sim = SharedClock()
        firebase_writer.set_clock(sim.now)                     # Firebase timestamps follow the world's time too
    w, where = open_firebase(args)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = store.connect(args.db)
    try:
        config = first_config(w, args)
        if not config:
            sys.exit("/config is empty in the real database: set it up first (see docs/DEPLOY.md)")
        keep_config(con, config)
    except (RuntimeError, OSError) as e:                       # no internet at start: the house still starts
        say(f"!! cannot reach Firebase ({where}): {e} — using the last /config kept in SQLite")
        config = kept_config(con)
    ing = Ingest(w, con, config, args, clock=sim)
    say(f"Firebase: {where} · SQLite: {args.db} · config v{config.get('version')} · "
        f"PIR hold {ing.th.get('pir_hold_s', house_state.PIR_HOLD_S)} s")
    try:
        ing.connect()
    except OSError as e:
        sys.exit(f"cannot reach the MQTT broker at {args.host}:{args.port} ({e}) — is Mosquitto running?")

    stop = threading.Event()
    sender = threading.Thread(target=ing.sender, args=(stop,), daemon=True)
    sender.start()
    last = {"hold": 0.0, "snap": time.time()}
    try:
        while True:
            now = time.time()
            if now - last["hold"] >= (0.25 if sim else HOLD_CHECK_S):     # a fast clock: PIR holds end in time
                ing.check_holds()
                last["hold"] = now
            if now - last.get("energy", 0) >= (0.25 if sim else ENERGY_S):
                ing.count_energy()
                ing.each_minute()
                last["energy"] = now
            if now - last["snap"] >= SNAPSHOT_S:
                ing.snapshot()
                last["snap"] = now
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    stop.set()
    sender.join(timeout=15)
    ing.flush()
    ing.out.flush()
    ing.client.loop_stop()
    ing.client.disconnect()
    say("mqtt_ingest stopped")


if __name__ == "__main__":
    main()
