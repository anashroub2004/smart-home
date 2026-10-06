"""Where the AI's outputs go.

FirebaseSink  -> /ai_schedule, /suggestions, /ai_insights, /energy_waste, /events, /alerts
                 through pi/firebase_writer.py (the one file that knows the Firebase paths)
PrintSink     -> stdout (command line / tests)
MqttExecutor  -> executes actions on the ESP32 nodes: home/<room>/<device>/set  (Pi automation service)

Actions are executed by the caller, never by the model directly.
"""
import json


class FirebaseSink:
    def __init__(self, writer, room_of=None, room_name=None):
        self.w = writer
        self.room_of = room_of or (lambda d: None)
        self.room_name = room_name or (lambda r: r)

    def write_schedule(self, decisions):
        self.w.write_ai_schedule(decisions)

    def push_suggestion(self, device, action, confidence, title, why, expires_at_ms):
        res = self.w.push_suggestion(device, action, confidence, title, why, expires_at=expires_at_ms)
        return (res or {}).get("name") if isinstance(res, dict) else res

    def expire_suggestion(self, sid, device, title, reason, quiet=False):
        """Withdraw a suggestion. Returns False if the user already answered it (the answer wins)."""
        current = self.w.db.get(f"suggestions/{sid}")
        if not current or current.get("response"):
            return False
        self.w.delete_suggestion(sid)
        if not quiet:
            self.log("ai", "ai", f"Suggestion expired: {title.rstrip('?')}", device=device, room=self.room_of(device),
                     short=f"Expired · {reason}",
                     why=f"Suggestions are withdrawn after 15 min or when the room changes ({reason})",
                     change="No change", src_label="AI")
        return True

    def log(self, kind, group, title, src_label="AI", **fields):
        fields.setdefault("node", "hub")
        fields.setdefault("by_label", "AI (Gradient Boosting)")
        self.w.log_event(kind, group, title, "ai", src_label, **fields)

    def alert(self, level, title, line, device=None):
        room = self.room_of(device)
        where = f"{self.room_name(room)} · {line}" if room else line
        self.w.push_alert(level, title, where, "energy")
        self.w.log_event("alert", "system", title, "system", "Power sensor", result="failed", device=device, room=room,
                         node="hub", by_label="AI fault detection (INA226)", short=line.split(" — ")[0], why=line,
                         tags=["system", "alert", "energy"])

    def write_waste(self, day, per_device, totals):
        self.w.write_energy_waste(day, per_device)
        self.w.patch_ai_insights({"energy": totals})

    def write_insights(self, doc):
        self.w.write_ai_insights(doc)

    def patch_insights(self, partial):
        self.w.patch_ai_insights(partial)


class PrintSink:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.n = 0

    def _out(self, what, data):
        if not self.quiet:
            print(f"[{what}] {json.dumps(data, default=str)}")

    def write_schedule(self, decisions):
        for d in decisions:
            self._out("plan", d)

    def push_suggestion(self, device, action, confidence, title, why, expires_at_ms):
        self.n += 1
        self._out("suggest", dict(device=device, action=action, confidence=round(confidence, 2), title=title, why=why))
        return f"s{self.n}"

    def expire_suggestion(self, sid, device, title, reason, quiet=False):
        self._out("expire", dict(id=sid, device=device, reason=reason))
        return True

    def log(self, kind, group, title, **fields):
        self._out("event", dict(title=title, **fields))

    def alert(self, level, title, line, device=None):
        self._out("alert", dict(level=level, title=title, line=line, device=device))

    def write_waste(self, day, per_device, totals):
        pass

    def write_insights(self, doc):
        self._out("insights", {k: doc.get(k) for k in ("status", "metrics", "data_source")})

    def patch_insights(self, partial):
        pass


class MqttExecutor:
    """For the Pi automation service (part B): turn an AI action into an MQTT command.

        import paho.mqtt.client as mqtt
        client = mqtt.Client(); client.connect("localhost")
        ex = MqttExecutor(client, config)
        for a in runtime.tick(house): ex.execute(a)   # then log the event like any other change (source "ai")
    """

    def __init__(self, client, config):
        self.client = client
        self.room = {d: rid for rid, r in config["rooms"].items() for d in (r.get("devices") or {})}

    def execute(self, action):
        payload = {"v": int(action["v"])}
        if action.get("level") is not None:
            payload["level"] = action["level"]
        topic = f"home/{self.room[action['device']]}/{action['device']}/set"
        self.client.publish(topic, json.dumps(payload), qos=1)
        return topic, payload
