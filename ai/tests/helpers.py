"""Shared test helpers: a fixed-probability model, a recording sink, a small house."""
import copy

import numpy as np

from ai import store
from ai.spec import load_config


class ConstModel:
    def __init__(self, p):
        self.p = p

    def predict_proba(self, X):
        return np.array([[1 - self.p, self.p]] * len(X))


class RecSink:
    def __init__(self):
        self.schedule, self.suggestions, self.expired, self.events, self.alerts = [], {}, [], [], []
        self.waste, self.insights, self.n = None, {}, 0

    def write_schedule(self, decisions):
        self.schedule = decisions

    def push_suggestion(self, device, action, confidence, title, why, expires_at_ms):
        self.n += 1
        sid = f"s{self.n}"
        self.suggestions[sid] = dict(device=device, action=action, confidence=confidence, title=title, why=why)
        return sid

    def expire_suggestion(self, sid, device, title, reason, quiet=False):
        self.expired.append((sid, device, reason))
        return True

    def log(self, kind, group, title, **fields):
        self.events.append(dict(title=title, **fields))

    def alert(self, level, title, line, device=None):
        self.alerts.append(dict(level=level, title=title, line=line, device=device))

    def write_waste(self, day, per_device, totals):
        self.waste = (day, per_device, totals)

    def write_insights(self, doc):
        self.insights = doc

    def patch_insights(self, partial):
        self.insights.update(partial)


def config():
    return copy.deepcopy(load_config())


def memory_db():
    return store.connect(":memory:")


def house(occ=None, temp=29.5, lux=40, devices=None, mmwave=None, pir=None, door_entry_at=None, paused=None):
    """occ: {room: 0/1}. mmwave/pir default to occ."""
    occ = occ or {}
    rooms = {}
    for r in ("living", "bedroom", "kitchen", "bathroom"):
        o = occ.get(r, 0)
        room = {"occ": o}
        if r in ("living", "bedroom"):
            room.update(temp=temp, lux=lux, mmwave=(mmwave or {}).get(r, o), pir=(pir or {}).get(r, o))
        else:
            room.update(pir=o)
        rooms[r] = room
    rooms["entrance"] = {}
    return {"rooms": rooms, "devices": devices or {}, "paused": paused or {}, "door_entry_at": door_entry_at}
