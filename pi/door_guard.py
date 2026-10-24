"""
The door node's own guard: counts wrong PINs / unknown fingers and locks the keypad by itself
(docs/contract.md "Door security"). No Pi and no internet needed — this is what the real ESP32 firmware does;
pi/fake_nodes.py runs it for the fake door node.

    5 wrong in a row -> keypad locked 60 s, the next round 120 s, 240, 480, then 900 s at most
    while locked:  any PIN (even the right one) and unknown fingers are refused (not counted);
                   a known finger, the exit button and the app still open
    only a correct open resets the count — waiting alone does not
    the count, the round and "locked" survive a power cut (flash); after a restart the lockout starts again

Each call returns (open_the_door, [events to publish on home/door/event]).
"""
import json
import time
from pathlib import Path

DEFAULTS = {"lockout_attempts": 5, "lockout_s": 60, "lockout_max_s": 900}   # factory values in the firmware


class DoorGuard:
    def __init__(self, clock=time.time, path=None):
        self.clock = clock
        self.path = Path(path) if path else None      # the node's flash; None = forgets on restart (tests)
        self.settings = dict(DEFAULTS)
        self.failed = 0          # wrong attempts in this round
        self.round = 0           # lockouts since the last correct open
        self.locked = False
        self.until = 0.0         # clock time the keypad opens again (not saved: the node has no real clock)
        self.load()

    # ------------------------------------------------ flash
    def load(self):
        if not (self.path and self.path.exists()):
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return
        self.settings.update({k: int(v) for k, v in (data.get("settings") or {}).items() if k in DEFAULTS})
        self.failed, self.round = int(data.get("failed", 0)), int(data.get("round", 0))
        self.locked = bool(data.get("locked"))
        if self.locked:                                   # power came back during a lockout: start it again
            self.until = self.clock() + self.lockout_secs()

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"settings": self.settings, "failed": self.failed, "round": self.round, "locked": self.locked}
            self.path.write_text(json.dumps(data), encoding="utf-8", newline="\n")

    def configure(self, door):
        """The "door" part of home/door/config (lockout_attempts, lockout_s, lockout_max_s)."""
        new = {k: int(door[k]) for k in DEFAULTS if isinstance(door.get(k), (int, float)) and door[k] > 0}
        if new and {**self.settings, **new} != self.settings:
            self.settings.update(new)
            self.save()

    # ------------------------------------------------ rules
    def lockout_secs(self):
        s = self.settings
        return min(s["lockout_s"] * 2 ** max(self.round - 1, 0), s["lockout_max_s"])

    def tick(self):
        """The keypad opens again once the lockout is over (the count of this round starts from 0)."""
        if self.locked and self.clock() >= self.until:
            self.locked = False
            self.save()

    def reset(self):
        if self.failed or self.round or self.locked:
            self.failed, self.round, self.locked = 0, 0, False
            self.save()

    def attempt(self, kind, ok, finger_id=None):
        """kind: fingerprint | keypad | exit_button. ok: the sensor matched / the PIN was right."""
        self.tick()
        ev = {"type": kind, "ok": bool(ok)}
        if finger_id is not None:
            ev["id"] = finger_id
        if kind == "exit_button":                         # always: nobody is ever locked inside
            return True, [ev]
        if ok and (kind == "fingerprint" or not self.locked):
            self.reset()
            return True, [ev]
        if self.locked:                                   # refused and not counted
            return False, [{"type": kind, "ok": False, "locked": True}]
        self.failed += 1
        events = [{**ev, "ok": False, "n": self.failed}]
        if self.failed >= self.settings["lockout_attempts"]:
            self.failed, self.round, self.locked = 0, self.round + 1, True
            secs = self.lockout_secs()
            self.until = self.clock() + secs
            events.append({"type": "lockout", "secs": secs, "round": self.round})
        self.save()
        return False, events

    def opened_remotely(self):
        """home/door/open from the Pi (the owner in the app): opens even during a lockout and resets the count."""
        self.reset()

    def status(self):
        left = max(0, round(self.until - self.clock())) if self.locked else 0
        return {"failed": self.failed, "round": self.round, "locked": self.locked, "left_s": left}
