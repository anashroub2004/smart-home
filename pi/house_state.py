"""
The picture of the house, built from the nodes' MQTT messages (docs/contract.md) — written once, used by every
Pi service that needs to know "who is where, how warm, what is on": mqtt_ingest (to record and show it) and
automation (to decide). Each service listens to MQTT itself and builds its own copy with this same code, so a
service keeps working when another one stops, and the house keeps working without the internet.

    hs = HouseState(config, listener)
    hs.handle(topic, payload)    # every MQTT message from home/#
    hs.check_holds()             # every few seconds: PIR-only rooms empty after thresholds.pir_hold_s

The listener hears what changed (all methods optional, see Listener).
Standard library only.
"""
import json
import time

SENSORS = ("temp", "hum", "lux")
PRESENCE = ("mmwave", "pir")
PIR_HOLD_S = 300          # default for thresholds.pir_hold_s


def now_ms():
    return int(time.time() * 1000)


def number(payload):
    try:
        return float(payload)
    except ValueError:
        return None


class Listener:
    """What a service wants to hear. Override what you need."""

    def reading(self, key, value):
        """A raw value for SQLite: '<room>/temp', '<room>/occ', 'device/<id>', 'level/<id>', 'power/<id>'."""

    def room_changed(self, rid):
        """Something in /home_state/rooms/<rid> changed."""

    def occ_changed(self, rid, occ):
        """Somebody came into the room (1) or it became empty (0)."""

    def device_changed(self, dev, before, src, changed):
        """A state message from the node: before = {v, level, mode} before it, src as the node sent it."""

    def power_changed(self, dev):
        """New watts for the device (and maybe on/off of a monitor-only device)."""

    def node_status(self, node, online):
        """A node came online or went offline (its last will)."""

    def config_ack(self, node, version):
        """A node runs this /config version."""

    def door_event(self, ev):
        """home/door/event: fingerprint / keypad / exit_button."""


class HouseState:
    def __init__(self, config, listener=None):
        self.l = listener or Listener()
        self.rooms, self.devices, self.nodes = {}, {}, {}
        self.load_config(config)

    # ------------------------------------------------------------------ /config: what exists, which node
    def load_config(self, config):
        self.config = config
        self.th = config.get("thresholds") or {}
        self.dev_room = {d: rid for rid, rc in config["rooms"].items() for d in (rc.get("devices") or {})}
        for rid, rc in config["rooms"].items():
            if rc.get("sensors"):
                self.rooms.setdefault(rid, {"occ": 0})
        for dev in self.dev_room:
            self.devices.setdefault(dev, {"v": 0})
        for node in config.get("nodes") or {}:
            self.nodes.setdefault(node, {"online": False, "version": None})

    def dev_cfg(self, dev):
        return self.config["rooms"][self.dev_room[dev]]["devices"][dev]

    def node_of(self, dev):
        return self.config["rooms"][self.dev_room[dev]].get("node")

    def room_lux(self, rid):
        """Light in a room: its own BH1750, else dark if windowless, else a room with a light sensor
        (the kitchen is open to the living room) — like the simulator's room_lux."""
        own = (self.rooms.get(rid) or {}).get("lux")
        if own is not None:
            return own
        if (self.config["rooms"].get(rid) or {}).get("windowless"):
            return 0
        for r, rc in self.config["rooms"].items():
            if "lux" in (rc.get("sensors") or []) and (self.rooms.get(r) or {}).get("lux") is not None:
                return self.rooms[r]["lux"]
        return None

    # ------------------------------------------------------------------ one MQTT message
    def handle(self, topic, payload):
        """-> True when the message was about the house. Raises ValueError on a broken payload."""
        p = topic.split("/")
        if len(p) == 3 and p[1] in self.nodes and p[2] == "status":
            self.on_status(p[1], payload)
        elif len(p) == 4 and p[1] in self.nodes and p[2:] == ["config", "ack"]:
            self.nodes[p[1]]["version"] = json.loads(payload).get("version")
            self.l.config_ack(p[1], self.nodes[p[1]]["version"])
        elif len(p) == 3 and p[1] == "door" and p[2] == "event":
            self.l.door_event(json.loads(payload))
        elif len(p) == 3 and p[1] in self.rooms and p[2] in SENSORS:
            self.on_sensor(p[1], p[2], number(payload))
        elif len(p) == 3 and p[1] in self.rooms and p[2] in PRESENCE:
            self.on_presence(p[1], p[2], number(payload))
        elif len(p) == 4 and p[2] in self.devices and p[3] == "state":
            self.on_state(p[2], json.loads(payload))
        elif len(p) == 4 and p[2] in self.devices and p[3] == "power":
            self.on_power(p[2], json.loads(payload))
        else:
            return False            # config we sent, /set and door/open from other services
        return True

    def on_status(self, node, payload):
        online = payload.strip() == "online"
        if self.nodes[node]["online"] != online:
            self.nodes[node]["online"] = online
            self.l.node_status(node, online)

    def on_sensor(self, rid, kind, value):
        if value is None:
            return
        value = round(value, 1) if kind == "temp" else round(value)
        room = self.rooms[rid]
        if room.get(kind) != value:
            room[kind] = value
            self.l.room_changed(rid)
        self.l.reading(f"{rid}/{kind}", value)

    def on_presence(self, rid, kind, value):
        if value is None:
            return
        room, v = self.rooms[rid], int(value >= 0.5)
        if kind == "pir" and v:
            room["_motion"] = time.time()
        if room.get(kind) != v:
            room[kind] = v
            self.l.room_changed(rid)
        self.l.reading(f"{rid}/{kind}", v)
        self.update_occ(rid)

    def update_occ(self, rid):
        """Is someone in the room? Radar room: the radar (it sees you sitting still) or the PIR.
        PIR-only room (kitchen, bathroom): motion in the last pir_hold_s seconds."""
        room, rc = self.rooms[rid], self.config["rooms"][rid]
        if "occ" not in (rc.get("sensors") or []):
            return
        if room.get("mmwave") is not None:
            occ, by = int(bool(room.get("mmwave") or room.get("pir"))), "mmwave" if room.get("mmwave") else "pir"
        else:
            hold = float(self.th.get("pir_hold_s", PIR_HOLD_S))
            occ, by = int(time.time() - room.get("_motion", 0) < hold), "pir"
        if occ != room.get("occ"):
            room["occ"], room["occ_since"] = occ, now_ms()
            if occ:
                room["occ_by"] = by
            self.l.room_changed(rid)
            self.l.reading(f"{rid}/occ", occ)
            self.l.occ_changed(rid, occ)

    def check_holds(self):
        for rid, room in self.rooms.items():
            if room.get("mmwave") is None and "_motion" in room:
                self.update_occ(rid)

    def on_state(self, dev, body):
        st = self.devices[dev]
        before = {k: st.get(k) for k in ("v", "level", "mode")}
        st["v"] = int(bool(body.get("v")))
        for k in ("level", "mode"):
            if body.get(k) is not None:
                st[k] = body[k]
        src = body.get("src")
        if src:
            st["src"] = src
        changed = before != {k: st.get(k) for k in ("v", "level", "mode")}
        if changed:
            st["at"] = now_ms()
        self.l.reading(f"device/{dev}", st["v"])
        if st.get("level") is not None:
            self.l.reading(f"level/{dev}", st["level"])
        self.l.device_changed(dev, before, src, changed)

    def on_power(self, dev, body):
        w = body.get("w")
        if w is None:
            return
        st, cfg = self.devices[dev], self.dev_cfg(dev)
        st["watts"] = round(float(w), 2)
        caps = cfg.get("caps") or {}
        if (caps.get("energy") or "none") != "none" or caps.get("power") == "read":
            self.l.reading(f"power/{dev}", st["watts"])
        if caps.get("power") == "read":
            # monitor-only device (fridge, kettle): on/off comes from the current it draws
            on = int(st["watts"] > float((cfg.get("hw") or {}).get("on_above_w", 0.5)))
            if on != st.get("v"):
                st.update(v=on, src="system", at=now_ms())
                self.l.reading(f"device/{dev}", on)
        self.l.power_changed(dev)

    # ------------------------------------------------------------------ for the AI / snapshots
    def values(self):
        """Every current value under its SQLite key (the Recorder's heartbeat)."""
        vals = {}
        for rid, room in self.rooms.items():
            for k in SENSORS + PRESENCE + ("occ",):
                if room.get(k) is not None:
                    vals[f"{rid}/{k}"] = room[k]
        for dev, st in self.devices.items():
            vals[f"device/{dev}"] = st.get("v", 0)
        return vals

    def public_room(self, rid):
        """The room as /home_state shows it (no internal fields)."""
        return {k: v for k, v in self.rooms[rid].items() if not k.startswith("_")}
