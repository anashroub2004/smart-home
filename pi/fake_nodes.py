"""
Fake ESP32 nodes — stand in for the real boards until the parts arrive (stage 1 of the plan).

Each node is its own MQTT connection, exactly like a real ESP32, and speaks only the topics in
docs/contract.md ("MQTT" section). The Pi services cannot tell a fake node from a real one, so when a
real board arrives we stop its fake node and nothing on the Pi changes.

Nothing about the house is written in this file: rooms, sensors and devices come from /config
(docs/seed.json now; later the Pi sends each node its part on home/<node>/config). A room belongs to
the node named in its "node" field (kitchen -> living, bathroom -> bedroom).

Run (Mosquitto must be running — on Windows it is a service that starts by itself):
    python pi/fake_nodes.py                       # living + bedroom + door
    python pi/fake_nodes.py --only living         # just one node (the others are real boards)
    python pi/fake_nodes.py --offline bedroom     # this node never comes online
    python pi/fake_nodes.py --broken bathroom/temp --broken living_fan/power   # a sensor that sends nothing

Watch everything the nodes say (second window):
    mosquitto_sub -h localhost -t "home/#" -v

While it runs, type commands (help lists them): press bedroom_light, move kitchen, finger 2, unplug living ...

What each node does (topics from docs/contract.md):
    home/<node>/status               "online" when connected; the broker sends "offline" (last will) if it
                                     disappears without saying goodbye — retained
    home/<room>/temp | hum | lux     plain numbers, sent when the value changes and at least every minute
    home/<room>/mmwave | pir         0 / 1, one topic per presence sensor (from the room's "hardware" list)
    home/<room>/<device>/state       {"v": 1, "level": 70} after a /set, {"v": .., "src": "button"} for a press
    home/<room>/<device>/power       {"w": 2.9} — what the INA226 would measure
    home/<room>/<device>/set         <- from the Pi: switch / level / mode
    home/<node>/config               <- from the Pi (retained): this node's part of /config -> config/ack
    home/door/event                  {"type": "fingerprint" | "keypad" | "exit_button", "ok": .., "id": ..},
                                     {"ok": false, "n": 3}, {"ok": false, "locked": true}, {"type": "lockout", ..}
    home/door/open                   <- from the Pi: open the door (the app's "unlock")

The door node guards itself like the real board (pi/door_guard.py): 5 wrong PINs / unknown fingers lock the
keypad 60 s, then 120, 240 ... 900 s, with no Pi and no internet. Its "flash" is pi/.flash/<node>.json
(survives unplug / restart of this program; delete the file for a factory reset).

By default the readings are deliberately simple (stage 1a): one person who wanders between rooms,
temperatures that drift slowly, daylight from the clock. With --world (stage 1e) the digital twin's person
lives here instead — his routine file, the rooms' temperatures, humidity and daylight, his wall buttons —
from pi/world.py, the same code the simulator runs:
    python pi/fake_nodes.py --world
Needs: pip install "paho-mqtt>=2.0,<3"
"""
import argparse
import json
import math
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from door_guard import DoorGuard  # noqa: E402

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit('paho-mqtt is missing: pip install "paho-mqtt>=2.0,<3"')

SEED = Path(__file__).resolve().parents[1] / "docs" / "seed.json"
FLASH = Path(__file__).resolve().parent / ".flash"      # each fake node's "flash memory" (gitignored)
HEARTBEAT_S = 60          # a reading is sent again after this long even if it did not change
SENSOR_EVERY_S = 5        # how often the sensors are read
KEEPALIVE_S = 10          # the broker declares a node dead (last will) after ~1.5x this without a word
LIGHT_LUX = 150           # what a ceiling light adds to a room's light sensor
USE_S = 180               # "use kettle": a monitor-only device draws power for 3 min

PRINT_LOCK = threading.Lock()


def say(node, arrow, topic, payload):
    text = str(payload)
    if len(text) > 120:                                     # a whole /config: show the start only
        text = text[:117] + "..."
    with PRINT_LOCK:
        print(f"{node:<8} {arrow} {topic} {text}", flush=True)


def load_config(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("config", data)          # docs/seed.json or a raw /config export


def presence_sensors(room):
    """mmwave / pir topics this room has, read from its hardware list (like ai.spec.room_presence_keys).
    Only rooms with "occ" in their sensors: the entrance's outdoor PIR sits on the Pi (GPIO17), not on a node."""
    if "occ" not in (room.get("sensors") or []):
        return []
    hw = " ".join(room.get("hardware") or []).lower()
    return [s for s, word in (("mmwave", "mmwave"), ("pir", "pir")) if word in hw]


# ------------------------------------------------------------------ the (very simple) world
class World:
    """What the sensors would see: one person moving between rooms, slow temperatures, daylight.
    Shared by all nodes, like the real house is."""

    def __init__(self, config):
        self.lock = threading.Lock()
        self.rooms = [r for r, rc in config["rooms"].items() if not rc.get("hidden")]
        self.where = random.choice(self.rooms)
        self.next_move = time.time() + random.uniform(120, 480)
        self.temp = {r: random.uniform(25.5, 28.0) for r in config["rooms"]}
        self.hum = {r: random.uniform(45, 55) for r in config["rooms"]}

    def tick(self):
        with self.lock:
            now = time.time()
            if now >= self.next_move:                       # every 2..8 min he goes somewhere else (or out)
                self.where = random.choice(self.rooms + ["out"])
                self.next_move = now + random.uniform(120, 480)
            for r in self.temp:                             # slow drift, kept in a sensible band
                self.temp[r] = min(32.0, max(22.0, self.temp[r] + random.gauss(0, 0.03)))
                self.hum[r] = min(80.0, max(35.0, self.hum[r] + random.gauss(0, 0.2)))

    def move(self, room):
        with self.lock:
            self.where = room
            self.next_move = time.time() + 600              # stay there 10 min when you put him somewhere

    def set_temp(self, rid, value):
        self.temp[rid] = value

    @staticmethod
    def daylight():
        """Lux through the window from the local clock: 0 at night, up to ~400 at noon."""
        t = time.localtime()
        h = t.tm_hour + t.tm_min / 60
        return 0.0 if not 6 <= h <= 18 else 400 * math.sin(math.pi * (h - 6) / 12)

    def reading(self, node, rid, kind):
        """What the sensor `kind` in room `rid` reads now (node: the FakeNode the room belongs to)."""
        rc = node.rooms[rid]
        here = self.where == rid
        if kind == "temp":
            return self.temp[rid]
        if kind == "hum":
            return self.hum[rid]
        if kind == "lux":
            lights_on = any(d["v"] for d in node.devices.values() if d["room"] == rid and d["cfg"].get("icon") == "light")
            return (0 if rc.get("windowless") else self.daylight()) + (LIGHT_LUX if lights_on else 0)
        if kind == "mmwave":
            return 1 if here else 0                                    # the radar also sees you sitting still
        return 1 if here and random.random() < 0.6 else 0              # the PIR only sees movement


class AhmedWorld:
    """--world: the simulated person of the digital twin lives here (pi/world.py, the routine file), and the
    fake nodes publish what his world shows. Real time, or with --speed N a fast clock the world owns and
    publishes on hub/clock (pi/hub_clock.py) for the services started with --sim-clock (stage 1e-2).
    This object is the `h` that pi/world.py works on."""

    def __init__(self, config, nodes, speed=None):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        global world, OUT
        import house_words
        import hub_clock
        import world
        self.clock = hub_clock.ClockPublisher(speed) if speed else None
        from ai.persona import Persona
        from person import OUT, Person
        self.words, self.nodes = house_words, nodes
        self.lock = threading.Lock()
        self.config, self.th = config, config.get("thresholds") or {}
        index = house_words.device_index(config)
        self.dev_room = {d: rid for d, (rid, _) in index.items()}
        self.dev_cfg = {d: cfg for d, (_, cfg) in index.items()}
        self.persona = Persona.load(None)
        self.person = Person(config, persona=self.persona)
        self.speedup = 1
        self.rooms = {rid: {} for rid in config["rooms"]}
        self.temp_delta, self.sensor_t, self.bed_since = 0.0, None, None
        self.dark_since, self.bed_forgets, self.intents_done, self.override_until = {}, {}, set(), {}
        self.faults = {}            # device -> "high" (2x power, e.g. a blocked motor) | "dead" | "standby" — tests
        self.test_client, self.shown = None, None
        say("world", "**", "Ahmed lives here:", f"{self.persona.d['person']['name']} · routine {self.persona.source}")

    # ---- test channel (with --speed): the scenarios move the world through MQTT (pi/scenario_bridge.py)
    def attach_tests(self, client):
        """hub/test/world {"do": ...} — take_over, give_back, force_still, habits, temp_delta, fault, set_dev, press,
        clock. The world's state goes back on hub/test/world/state (retained) whenever it changes."""
        self.test_client = client
        client.message_callback_add("hub/test/world", self.on_test)
        client.subscribe("hub/test/world", qos=1)
        self.shown = None

    def on_test(self, client, userdata, msg):
        try:
            c = json.loads(msg.payload)
            with self.lock:
                self.test(c)
        except Exception as e:  # noqa: BLE001 — a bad test message must not stop the world
            say("world", "!!", "test command failed:", f"{msg.payload[:80]} ({e})")
        self.show()

    def test(self, c):
        do, p = c.get("do"), self.person
        if do == "take_over":
            p.take_over(OUT if c["room"] in ("out", OUT) else c["room"], c.get("activity"))
        elif do == "give_back":
            p.give_back()
        elif do == "force_still":
            p.force_still = c.get("v")
        elif do == "habits":
            p.habits = bool(c.get("v"))
        elif do == "temp_delta":
            self.temp_delta = float(c.get("v", 0))
        elif do == "faults":
            self.faults = dict(c.get("v") or {})
        elif do in ("set_dev", "press"):
            node = self.nodes.get(self.node_of(c["dev"]))
            if node and node.client:
                if do == "press":
                    node.press(c["dev"])
                else:                                   # the scenario sets it up: not a manual action
                    node.switch(c["dev"], {"v": c["v"], "level": c.get("level")}, src=c.get("src", "system"))
        elif do == "clock" and self.clock:
            op, arg = c.get("op"), c.get("arg")
            {"jump": lambda: self.clock.jump_to(arg), "forward": lambda: self.clock.forward(float(arg)),
             "speed": lambda: self.clock.set_speed(float(arg)), "pause": lambda: self.clock.pause(True),
             "resume": lambda: self.clock.pause(False)}[op]()
        # a person taken over moves on the world's next tick (a quarter second), like in the twin

    def show(self):
        """Publish what the scenarios need to know about the world, when it changed."""
        if not self.test_client:
            return
        p = self.person
        state = {"room": p.room, "activity": p.activity, "still": p.still, "manual": bool(p.manual),
                 "force_still": p.force_still, "habits": p.habits, "temp_delta": self.temp_delta, "faults": self.faults}
        body = json.dumps(state, sort_keys=True)
        if body != self.shown:
            self.shown = body
            self.test_client.publish("hub/test/world/state", body, qos=1, retain=True)

    # ---- what pi/world.py reads
    def now(self):
        return self.clock.now() if self.clock else time.time()

    def vnow(self):
        from datetime import datetime
        return datetime.fromtimestamp(self.now())

    @property
    def devices(self):
        """Every device's state, from the node that drives it ({"v": 0} for a node that is not running)."""
        out = {d: {"v": 0} for d in self.dev_cfg}
        for n in self.nodes.values():
            out.update(n.devices)
        return out

    @property
    def offline(self):
        return {n for n in (self.config.get("nodes") or {}) if n not in self.nodes or not self.nodes[n].client}

    def icon(self, d):
        return self.dev_cfg[d].get("icon")

    def writable(self, d):
        return (self.dev_cfg[d].get("caps") or {}).get("power") == "write"

    def ctrl(self, d):
        return self.dev_cfg[d].get("control") or {}

    def node_of(self, d):
        return self.config["rooms"][self.dev_room[d]].get("node")

    def room_name(self, rid):
        return self.config["rooms"][rid]["name"]

    def mid_level(self, d):
        lv = (self.dev_cfg[d].get("caps") or {}).get("level")
        return lv["steps"][len(lv["steps"]) // 2] if lv else None

    # ---- what Ahmed does (pi/world.py calls these)
    def person_switch(self, dev, v, level=None, source="button", why="", **_):
        node = self.nodes.get(self.node_of(dev))
        if not node or not node.client:
            return
        say("world", "**", f"Ahmed: {dev} {'on' if v else 'off'}", f"({source}: {why})")
        if source in ("button", "web"):
            self.override_until[dev] = self.now() + float(self.th.get("override_pause_min", 120)) * 60
        node.switch(dev, {"v": v, "level": level}, src=source)

    def person_moved(self, old, new):
        say("world", "**", f"Ahmed: {old or '-'} -> {new}", f"({self.person.activity})")

    def door(self):
        return next((n for n in self.nodes.values() if n.lock_dev() and n.client), None)

    def person_left_home(self):
        if self.door():
            self.door().door_event("exit_button", True)

    def person_came_home(self):
        if self.door():
            self.door().door_event("fingerprint", True, 1)

    def end_hands_off(self, rid):
        for dev in (self.config["rooms"].get(rid, {}).get("devices") or {}):
            self.override_until.pop(dev, None)

    def kpi_add(self, key, n=1):
        pass

    # ---- what the fake nodes read
    def tick(self):
        with self.lock:
            world.person_tick(self)
            world.sense(self)
        self.show()

    def reading(self, node, rid, kind):
        return self.rooms.get(rid, {}).get(kind)

    def move(self, room):
        self.person.take_over(OUT if room == "out" else room)

    def set_temp(self, rid, value):
        raise ValueError("with --world the rooms follow the weather: use 'hotter <°C>' (0 = normal)")


# ------------------------------------------------------------------ one fake ESP32
class FakeNode:
    def __init__(self, name, config, world, args):
        self.name, self.world, self.args = name, world, args
        self.lock = threading.Lock()
        self.version = None                 # config version this node runs (acked to the Pi)
        self.rooms, self.devices = {}, {}   # room id -> room cfg; device id -> {"room", "cfg", "v", "level", ...}
        self.apply_config(config, announce=False)
        self.sent = {}                      # topic -> (payload, ts) last published, for "on change + heartbeat"
        self.lock_open_until = 0
        self.muted = False                  # "mute": still online and sending readings, but ignores every order
        self.client = None
        self.guard = None                   # the door's own lockout (only the node with a lock)
        if self.lock_dev():
            self.guard = DoorGuard(clock=getattr(world, "now", time.time), path=Path(args.flash) / f"{name}.json")

    # ------------------------------------------------ config: which rooms / sensors / devices are mine
    def apply_config(self, config, announce=True):
        with self.lock:
            old = self.devices
            self.rooms = {r: rc for r, rc in config["rooms"].items() if rc.get("node") == self.name}
            self.devices = {}
            for rid, rc in self.rooms.items():
                for dev, cfg in (rc.get("devices") or {}).items():
                    keep = old.get(dev, {})
                    self.devices[dev] = {"room": rid, "cfg": cfg, "v": keep.get("v", 0), "level": keep.get("level"),
                                         "mode": keep.get("mode"), "busy_until": keep.get("busy_until", 0)}
            self.version = config.get("version")
        if announce:
            with PRINT_LOCK:
                print(f"{self.name:<8} ** config v{self.version}: rooms {', '.join(self.rooms) or '-'}; "
                      f"{len(self.devices)} devices", flush=True)

    def caps(self, dev):
        return self.devices[dev]["cfg"].get("caps") or {}

    def writable(self, dev):
        return self.caps(dev).get("power") == "write"

    def is_lock(self, dev):
        return "lock" in self.caps(dev)

    # ------------------------------------------------ MQTT connection (one per node, like a real board)
    def connect(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fake-{self.name}")
        c.will_set(f"home/{self.name}/status", "offline", qos=1, retain=True)    # sent by the broker if we vanish
        c.on_connect = self.on_connect
        c.on_message = self.on_message
        self.client = c          # before loop_start: on_connect (another thread) publishes through self.client
        c.connect(self.args.host, self.args.port, keepalive=KEEPALIVE_S)
        c.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            say(self.name, "!!", "connect failed:", reason_code)
            return
        self.sent.clear()                                   # send everything again after a reconnect
        self.publish(f"home/{self.name}/status", "online", retain=True, force=True)
        client.subscribe(f"home/{self.name}/config", qos=1)  # once: re-subscribing would get the retained copy again
        self.subscribe_devices(client)
        for dev in self.devices:                           # tell the Pi what everything is doing right now
            self.publish_state(dev)

    def subscribe_devices(self, client):
        """Listen to the /set of each device this node drives (and the door's open)."""
        for t in getattr(self, "subs", []):
            client.unsubscribe(t)
        self.subs = [f"home/{d['room']}/{dev}/set" for dev, d in self.devices.items() if self.writable(dev)]
        if self.lock_dev():
            self.subs.append("home/door/open")
        for t in self.subs:
            client.subscribe(t, qos=1)

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace")
        say(self.name, "<-", msg.topic, payload)
        try:
            body = json.loads(payload) if payload else {}
        except ValueError:
            say(self.name, "!!", msg.topic, "is not JSON — ignored")
            return
        if msg.topic == f"home/{self.name}/config":
            cfg = body.get("config", body)
            if "rooms" not in cfg:
                say(self.name, "!!", msg.topic, "has no rooms — ignored")
                return
            if self.guard and isinstance(cfg.get("door"), dict):
                self.guard.configure(cfg["door"])           # lockout settings, kept in flash
            if cfg.get("version") != self.version:         # the same version again (Pi restarted): only ack
                self.apply_config(cfg)
                self.subscribe_devices(client)             # a device may have been added or removed
                for dev in self.devices:
                    self.publish_state(dev)
            self.publish(f"home/{self.name}/config/ack", json.dumps({"version": self.version}), force=True)
        elif self.muted:
            say(self.name, "!!", msg.topic, "ignored (muted: the node hangs)")
        elif msg.topic == "home/door/open":
            if self.guard:
                self.guard.opened_remotely()                # the owner in the app: opens even during a lockout
            self.open_lock(src=body.get("src") or "web")
        elif msg.topic.endswith("/set"):
            dev = msg.topic.split("/")[2]
            if dev in self.devices and self.writable(dev):
                self.switch(dev, body, src=body.get("src"))     # the node does not interpret src: it echoes it

    # ------------------------------------------------ publishing
    def publish(self, topic, payload, retain=False, force=False):
        """Send if the value changed or the heartbeat is due (force = always). Broken sensors send nothing."""
        if any(topic.endswith("/" + b) for b in self.args.broken):
            return
        now = time.time()
        last = self.sent.get(topic)
        if not force and last and last[0] == payload and now - last[1] < HEARTBEAT_S:
            return
        quiet = not force and last and last[0] == payload        # heartbeat: same value again
        quiet = quiet or (self.args.speed and retain)            # fast clock: readings fly by, don't print them
        self.sent[topic] = (payload, now)
        self.client.publish(topic, payload, qos=1, retain=retain)
        if not quiet or self.args.verbose:
            say(self.name, "->", topic, payload)

    def publish_state(self, dev, src=None):
        d = self.devices[dev]
        room = d["room"]
        if self.writable(dev) or self.is_lock(dev):
            st = {"v": d["v"]}
            if d["level"] is not None:
                st["level"] = d["level"]
            if d["mode"] is not None:
                st["mode"] = d["mode"]
            if src:
                st["src"] = src
            self.publish(f"home/{room}/{dev}/state", json.dumps(st), force=True)
        self.publish_power(dev, force=True)

    def watts(self, dev):
        """What the INA226 would measure: the device's rated watts, scaled by its level (PWM motor)."""
        d = self.devices[dev]
        cfg = d["cfg"]
        rated = float(cfg.get("watts") or 0)
        if self.writable(dev):
            fault = (getattr(self.world, "faults", None) or {}).get(dev)     # --world tests: a broken device
            if not d["v"]:
                return 1.1 if fault == "standby" else 0.0
            w = rated * (d["level"] or 100) / 100 if d["level"] is not None else rated
            return {"high": w * 2.1, "dead": 0.02}.get(fault, w)          # a blocked motor / a burnt bulb
        if self.caps(dev).get("power") == "read":               # monitor only (fridge, kettle)
            on_above = float((cfg.get("hw") or {}).get("on_above_w", 0.5))
            if time.time() < d["busy_until"]:
                return max(rated, on_above * 4)
            if cfg.get("icon") == "fridge":                     # always on: the compressor makes it go up and down
                return max(rated, on_above * 2) * (1.5 if (time.time() // 600) % 3 == 0 else 1.0)
            return 0.0
        return 0.0

    def publish_power(self, dev, force=False):
        if (self.caps(dev).get("energy") or "none") == "none" and not self.caps(dev).get("power") == "read":
            return                                              # no INA226 on this device (the door servo)
        self.publish(f"home/{self.devices[dev]['room']}/{dev}/power",
                     json.dumps({"w": round(self.watts(dev), 2)}), force=force)

    # ------------------------------------------------ actions
    def switch(self, dev, body, src=None):
        """A /set from the Pi, or a wall button press (src="button")."""
        with self.lock:
            d = self.devices[dev]
            if "v" in body:
                d["v"] = 1 if body["v"] else 0
            steps = ((self.caps(dev).get("level") or {}).get("steps")) or []
            if body.get("level") is not None and steps:
                d["level"] = int(body["level"])
            elif d["v"] and steps and d["level"] is None:
                d["level"] = steps[len(steps) // 2]             # switched on without a level: the middle one
            if body.get("mode") is not None:
                d["mode"] = body["mode"]
        self.publish_state(dev, src)

    def press(self, dev):
        """The wall button next to the device: toggles it and works without any network (on the real board)."""
        if dev not in self.devices or not self.writable(dev):
            return False
        if not (self.devices[dev]["cfg"].get("control") or {}).get("button"):
            say(self.name, "!!", dev, "has no wall button")
            return True
        self.switch(dev, {"v": 0 if self.devices[dev]["v"] else 1}, src="button")
        return True

    def use(self, dev):
        if dev not in self.devices or self.caps(dev).get("power") != "read":
            return False
        self.devices[dev]["busy_until"] = time.time() + USE_S
        self.publish_power(dev, force=True)
        return True

    def lock_dev(self):
        return next((d for d in self.devices if self.is_lock(d)), None)

    def open_lock(self, src=None):
        dev = self.lock_dev()
        if not dev:
            return
        open_s = float((self.caps(dev).get("lock") or {}).get("open_s", 5))
        self.devices[dev]["v"] = 1
        self.lock_open_until = time.time() + open_s
        self.publish_state(dev, src)

    def door_event(self, kind, ok, finger_id=None):
        """Fingerprint / keypad / exit button at the door. The node decides by itself (works offline):
        door_guard counts the wrong ones and refuses the keypad during a lockout."""
        opens, events = self.guard.attempt(kind, ok, finger_id)
        for ev in events:
            self.publish("home/door/event", json.dumps(ev), force=True)
        if opens:
            self.open_lock(src=kind)
        elif events[-1].get("locked"):
            say(self.name, "**", "keypad locked", f"{self.guard.status()['left_s']} s left - attempt refused")

    # ------------------------------------------------ sensors (called every few seconds)
    def tick(self):
        w = self.world
        for rid, rc in self.rooms.items():
            sensors = rc.get("sensors") or []
            fmt = {"temp": lambda v: f"{v:.1f}", "hum": lambda v: f"{v:.0f}",
                   "lux": lambda v: f"{round(v / 10) * 10:.0f}"}                    # BH1750, 10 lx steps
            # retained: a service that starts later knows every reading at once (no minute of "getting to know")
            for kind in ("temp", "hum", "lux"):
                if kind in sensors:
                    v = w.reading(self, rid, kind)
                    if v is not None:
                        self.publish(f"home/{rid}/{kind}", fmt[kind](v), retain=True)
            for s in presence_sensors(rc):
                v = w.reading(self, rid, s)
                if v is not None:
                    self.publish(f"home/{rid}/{s}", str(int(v)), retain=True)
        if self.guard:
            was = self.guard.locked
            self.guard.tick()
            if was and not self.guard.locked:
                say(self.name, "**", "keypad unlocked", "(lockout over)")
        for dev in self.devices:
            if self.is_lock(dev) and self.devices[dev]["v"] and time.time() >= self.lock_open_until:
                self.devices[dev]["v"] = 0                                  # the door locks itself again
                self.publish_state(dev, "system")
            else:
                self.publish_power(dev)

    def unplug(self):
        """Power cut: no goodbye, the socket just dies -> the broker publishes our last will ("offline")."""
        self.client.loop_stop()
        sock = self.client.socket()
        if sock:
            sock.close()
        self.client = None

    def stop(self):
        """Ctrl+C / quit: unlike a power cut, say goodbye (like a node that is switched off on purpose)."""
        if self.client:
            self.client.publish(f"home/{self.name}/status", "offline", qos=1, retain=True).wait_for_publish(2)
            self.client.disconnect()
            self.client.loop_stop()


# ------------------------------------------------------------------ typed commands
HELP = """commands:
  press <device>        wall button next to a device (toggle), e.g. press bedroom_light
  move <room|out>       put the person in a room (presence sensors follow), e.g. move kitchen
  temp <room> <value>   set a room's temperature, e.g. temp living 30
  use <device>          a monitor-only device draws power for 3 min, e.g. use kettle
  finger <id|wrong>     fingerprint at the door, e.g. finger 2
  pin <ok|wrong>        PIN on the door keypad
  exit                  exit button inside the door
  door                  the door's guard: wrong attempts, round, locked and seconds left
  unplug <node>         power cut: the broker announces "offline" by itself (last will)
  plug <node>           power back: the node connects again
  mute <node>           the node hangs: still online, but answers no order (unmute <node> to fix)
  with --world:  move <room|out> takes Ahmed out of his routine, routine gives it back, hotter <°C> warms every room
  with --speed:  time, jump <HH:MM>, speed <N>, pause, resume (the shared clock on hub/clock)
  quit"""


def console(nodes, world, config, stop):
    def node_of_device(dev):
        return next((n for n in nodes.values() if dev in n.devices and n.client), None)

    def door():
        return next((n for n in nodes.values() if n.lock_dev() and n.client), None)

    print(HELP, flush=True)
    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        cmd, rest = parts[0].lower(), parts[1:]
        try:
            if cmd == "quit":
                break
            elif cmd == "help":
                print(HELP, flush=True)
            elif cmd == "press" and rest:
                n = node_of_device(rest[0])
                if not (n and n.press(rest[0])):
                    print(f"no switchable device '{rest[0]}' on a running node", flush=True)
            elif cmd == "use" and rest:
                n = node_of_device(rest[0])
                if not (n and n.use(rest[0])):
                    print(f"no monitor-only device '{rest[0]}' on a running node", flush=True)
            elif cmd == "move" and rest:
                if rest[0] != "out" and rest[0] not in config["rooms"]:
                    print(f"unknown room '{rest[0]}'", flush=True)
                else:
                    world.move(rest[0])
            elif cmd == "temp" and len(rest) == 2:
                world.set_temp(rest[0], float(rest[1]))
            elif cmd == "hotter" and rest and isinstance(world, AhmedWorld):
                world.temp_delta = float(rest[0])
            elif cmd == "routine" and isinstance(world, AhmedWorld):
                world.person.give_back()
                print("Ahmed follows his routine again", flush=True)
            elif cmd in ("jump", "speed", "pause", "resume", "time") and getattr(world, "clock", None):
                c = world.clock
                if cmd == "jump" and rest:
                    c.jump_to(rest[0])
                elif cmd == "speed" and rest:
                    c.set_speed(float(rest[0]))
                elif cmd in ("pause", "resume"):
                    c.pause(cmd == "pause")
                print(f"world time {world.vnow():%a %H:%M:%S} · x{c.clock.speed:g}"
                      + (" · paused" if c.clock.paused else "")
                      + (f" · held by {', '.join(sorted(c.holders))}" if c.holders else ""), flush=True)
            elif cmd == "door":
                n = door()
                print(f"door guard: {n.guard.status()}, settings {n.guard.settings}" if n
                      else "the door node is not running", flush=True)
            elif cmd in ("finger", "pin", "exit"):
                n = door()
                if not n:
                    print("the door node is not running", flush=True)
                elif cmd == "finger":
                    ok = rest and rest[0] != "wrong"
                    n.door_event("fingerprint", ok, int(rest[0]) if ok and rest[0].isdigit() else None)
                elif cmd == "pin":
                    n.door_event("keypad", bool(rest) and rest[0] == "ok")
                else:
                    n.door_event("exit_button", True)
            elif cmd in ("mute", "unmute") and rest and rest[0] in nodes:
                nodes[rest[0]].muted = cmd == "mute"
                print(f"{rest[0]} {'muted: orders are ignored' if cmd == 'mute' else 'answers orders again'}",
                      flush=True)
            elif cmd == "unplug" and rest and rest[0] in nodes and nodes[rest[0]].client:
                nodes[rest[0]].unplug()
                print(f"{rest[0]} unplugged — the broker will say 'offline' within ~{int(KEEPALIVE_S * 1.5)} s",
                      flush=True)
            elif cmd == "plug" and rest and rest[0] in nodes and not nodes[rest[0]].client:
                nodes[rest[0]].connect()
            else:
                print("? (type help)", flush=True)
        except (ValueError, KeyError) as e:
            print(f"? {e}", flush=True)
    stop.set()


def main():
    ap = argparse.ArgumentParser(description="Fake ESP32 nodes on MQTT (stand-ins for the real boards)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--config", default=str(SEED), help="docs/seed.json or a /config export")
    ap.add_argument("--only", action="append", default=[], help="run only this node (repeatable)")
    ap.add_argument("--offline", action="append", default=[], help="this node never comes online")
    ap.add_argument("--broken", action="append", default=[],
                    help="a reading that never arrives, e.g. bathroom/temp or living_fan/power")
    ap.add_argument("--verbose", action="store_true", help="also print heartbeats (unchanged values)")
    ap.add_argument("--flash", default=str(FLASH), help="folder of the nodes' flash memory (door lockout state)")
    ap.add_argument("--world", action="store_true",
                    help="Ahmed (the digital twin's person, his routine file) lives in the house: pi/world.py")
    ap.add_argument("--speed", type=float, default=0,
                    help="with --world: a fast clock (e.g. 60) published on hub/clock for services run with --sim-clock")
    args = ap.parse_args()
    if args.speed and not args.world:
        sys.exit("--speed needs --world (the world owns the clock)")

    config = load_config(args.config)
    names = [n for n in (config.get("nodes") or {}) if (not args.only or n in args.only) and n not in args.offline]
    if not names:
        sys.exit("no node to run (check --only / --offline)")
    nodes = {}
    world = AhmedWorld(config, nodes, speed=args.speed or None) if args.world else World(config)
    nodes.update({n: FakeNode(n, config, world, args) for n in names})
    if args.speed:
        clock_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fake-world-clock")
        clock_client.on_connect = lambda c, *a: (world.clock.attach(c), world.attach_tests(c))
        clock_client.connect(args.host, args.port, keepalive=30)
        clock_client.loop_start()
        say("world", "**", f"clock x{args.speed:g} on hub/clock", "(services: --sim-clock)")
    for n in nodes.values():
        rooms = ", ".join(f"{r} ({', '.join(rc.get('sensors') or []) or 'no sensors'})" for r, rc in n.rooms.items())
        with PRINT_LOCK:
            print(f"{n.name:<8} ** {rooms}; devices: {', '.join(n.devices) or '-'}", flush=True)
        try:
            n.connect()
        except OSError as e:
            sys.exit(f"cannot reach the MQTT broker at {args.host}:{args.port} ({e}) — is Mosquitto running?")

    stop = threading.Event()
    threading.Thread(target=console, args=(nodes, world, config, stop), daemon=True).start()
    try:
        while not stop.is_set():
            world.tick()
            for n in nodes.values():
                if n.client:
                    n.tick()
            # a fast clock moves the world every quarter second (15 virtual s at x60, like the twin's tick)
            stop.wait(0.25 if args.speed else SENSOR_EVERY_S)
    except KeyboardInterrupt:
        pass
    for n in nodes.values():
        n.stop()
    print("fake nodes stopped (each said 'offline')", flush=True)


if __name__ == "__main__":
    main()
