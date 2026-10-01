"""
Every write to Firebase goes through this file — the Pi services AND the simulator use it,
so the web app cannot tell them apart. Paths and shapes follow docs/contract.md.
"""
import time
from datetime import datetime


def now_ms():
    return int(time.time() * 1000)


def day_key(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d")


class Writer:
    def __init__(self, db):
        self.db = db  # RestDB or any object with get/put/patch/post/delete

    # ------------------------------------------------------------ config
    def get_config(self):
        return self.db.get("config")

    def put_config(self, config):
        self.db.put("config", config)

    # ------------------------------------------------------------ live state
    def write_home_state(self, rooms, devices, power_w):
        self.db.put("home_state", {
            "updated_at": now_ms(),
            "power_w": round(power_w, 2),
            "rooms": rooms,
            "devices": devices,
        })

    def patch_device(self, device, state):
        self.db.patch(f"home_state/devices/{device}", state)

    def set_node(self, node, online, config_version=None, rssi=None):
        data = {"online": bool(online)}
        if online:
            data["last_seen"] = now_ms()
        if config_version is not None:
            data["config_version"] = config_version
        if rssi is not None:
            data["rssi"] = rssi
        self.db.patch(f"nodes/{node}", data)

    # ------------------------------------------------------------ commands
    def get_commands(self):
        return self.db.get("commands") or {}

    def ack_command(self, device, ok, error=None):
        data = {"status": "done" if ok else "failed"}
        if error:
            data["error"] = error
        self.db.patch(f"commands/{device}", data)

    # ------------------------------------------------------------ events log (Pi only)
    def log_event(self, kind, text, source, result="ok", **fields):
        """fields: device, room, by, trigger, confidence, from_, to, latency_ms, tags"""
        ev = {"at": now_ms(), "kind": kind, "text": text, "source": source, "result": result}
        if "from_" in fields:
            fields["from"] = fields.pop("from_")
        ev.update({k: v for k, v in fields.items() if v is not None})
        return self.db.post("events", ev)

    # ------------------------------------------------------------ history / energy
    def write_summary(self, room, ts, data):
        dt = datetime.fromtimestamp(ts)
        self.db.put(f"summaries/{room}/{dt:%Y-%m-%d}/{dt:%H:%M}", data)

    def write_energy_day(self, day, per_device_wh):
        self.db.patch(f"energy_daily/{day}", {k: round(v, 3) for k, v in per_device_wh.items()})

    def get_energy_day(self, day):
        return self.db.get(f"energy_daily/{day}") or {}

    # ------------------------------------------------------------ AI
    def write_ai_schedule(self, decisions):
        self.db.put("ai_schedule", {d["device"]: {**d, "at": now_ms()} for d in decisions})

    def push_suggestion(self, device, action, confidence, text):
        return self.db.post("suggestions", {
            "device": device, "action": action, "confidence": round(confidence, 2),
            "text": text, "at": now_ms(),
        })

    def get_suggestions(self):
        return self.db.get("suggestions") or {}

    # ------------------------------------------------------------ security
    def log_access(self, method, ok, who=None):
        entry = {"at": now_ms(), "method": method, "ok": ok}
        if who:
            entry["who"] = who
        self.db.post("access_log", entry)

    def push_alert(self, text, level="critical"):
        return self.db.post("alerts", {"at": now_ms(), "level": level, "text": text})
