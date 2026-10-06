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
    def write_home_state(self, state):
        """state: rooms, devices, power_w, base_w, scene, door — see contract.md"""
        self.db.put("home_state", {**state, "updated_at": now_ms()})

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
    def log_event(self, kind, group, title, source, src_label, result="ok", **fields):
        """
        kind   : device | door | motion | node | ai | rule | scene | alert | config
        group  : manual | ai | rule | door | system   (the History filter + colour)
        title  : "Living room fan turned on"
        fields : short, why, by, by_label, change, device, room, node, confidence,
                 from_, to, latency_ms, saved_wh, tags
        """
        ev = {"at": now_ms(), "kind": kind, "group": group, "title": title,
              "source": source, "src_label": src_label, "result": result}
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
    def get_ai_pause(self):
        return self.db.get("ai_pause") or {}

    def write_ai_schedule(self, decisions):
        self.db.put("ai_schedule", {d["device"]: {**d, "at": now_ms()} for d in decisions})

    def write_ai_insights(self, insights):
        self.db.put("ai_insights", insights)

    def patch_ai_insights(self, partial):
        self.db.patch("ai_insights", partial)

    def push_suggestion(self, device, action, confidence, title, why, expires_at=None):
        data = {"device": device, "action": action, "confidence": round(confidence, 2),
                "title": title, "why": why, "at": now_ms()}
        if expires_at:
            data["expires_at"] = int(expires_at)
        res = self.db.post("suggestions", data)
        return res.get("name") if isinstance(res, dict) else res

    def get_suggestions(self):
        return self.db.get("suggestions") or {}

    def mark_suggestion(self, sid, data):
        self.db.patch(f"suggestions/{sid}", data)

    def delete_suggestion(self, sid):
        """Expired suggestions are removed (the web stops showing them); /events keeps the record."""
        self.db.delete(f"suggestions/{sid}")

    def write_energy_waste(self, day, per_device):
        """/energy_waste/{day}/{device}/{cause} = Wh  (cause: forgotten | rule_delay | ai | standby)"""
        if per_device:
            self.db.patch(f"energy_waste/{day}", per_device)

    # ------------------------------------------------------------ security
    def log_access(self, method, ok, who=None):
        entry = {"at": now_ms(), "method": method, "ok": ok}
        if who:
            entry["who"] = who
        self.db.post("access_log", entry)

    def push_alert(self, level, title, where, go, lines=None):
        """level: critical | warning | info | good. go: the web screen to open (security, energy, settings)."""
        alert = {"at": now_ms(), "level": level, "title": title, "where": where, "go": go}
        if lines:
            alert["lines"] = lines
        return self.db.post("alerts", alert)
