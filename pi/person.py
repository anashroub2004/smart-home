"""The simulated person who lives in the house (digital twin).

Lives the days of a routine file (ai/personas/*.toml, via ai/persona.py) — the same days the AI's simulated
history was generated from (ai/lifesim.py) — so the model and the live simulator agree. Without a routine file
he follows the older fixed routine (ai/routine.py). You can take over from the twin page (move him, keep him
still, send him out) and give the routine back later.

Python standard library only.
"""
import random
from datetime import datetime

from ai import routine

OUT = routine.OUT


class Person:
    def __init__(self, config, seed=42, persona=None):
        self.seed = seed
        self.persona = persona        # ai.persona.Persona, or None for the old fixed routine
        self.rng = random.Random(seed)
        self.rooms = tuple(r for r in config["rooms"])
        self.room = None
        self.activity = "Sleeping"
        self.still = 0.9
        self.manual = None            # {"room", "activity", "still"} — you took over from the twin page
        self.force_still = None       # True/False overrides the routine's "sitting still"
        self.changed_from = None      # date the routine changed (scenario: summer holiday)
        self.moved_at = 0
        self.path = []                # rooms passed on the last move (for the walking animation)
        self.habits = True            # switches lights off when leaving (sometimes forgets)
        self._segs_day = None
        self._segs = []
        self.day_plan = None

    def set_config(self, config):
        self.rooms = tuple(r for r in config["rooms"])
        self._segs_day = None

    def segments(self, ts):
        day = datetime.fromtimestamp(ts).date()
        if day != self._segs_day:
            if self.persona is not None:
                self.day_plan = self.persona.day(day)
                has = set(self.rooms)
                fix = lambda r: r if r in has or r == OUT else ("living" if "living" in has else sorted(has)[0])
                self._segs = [(a, b, fix(room), act, still) for a, b, room, act, still in self.day_plan.segments]
            else:
                self.day_plan = None
                self._segs = routine.segments(day, self.seed, self.changed_from, self.rooms)
            self._segs_day = day
        return self._segs

    def plan(self, ts):
        """Today's plan from the routine file (appliance use, unusual-day tags), or None."""
        self.segments(ts)
        return self.day_plan

    def plan_now(self, ts):
        dt = datetime.fromtimestamp(ts)
        return routine.at(self.segments(ts), dt.hour + dt.minute / 60 + dt.second / 3600)

    def step(self, ts):
        """-> (old_room, new_room) when he moved, else None."""
        if self.manual:
            room, activity, still = self.manual["room"], self.manual["activity"], self.manual["still"]
        else:
            _, _, room, activity, still = self.plan_now(ts)
        if self.force_still is not None:
            still = 0.95 if self.force_still else 0.05
        self.activity, self.still = activity, still
        if room != self.room:
            old, self.room = self.room, room
            self.moved_at = ts
            self.path = [r for r in (old, room) if r]
            return old, room
        return None

    def take_over(self, room, activity=None):
        self.manual = {"room": room, "activity": activity or ("Out" if room == OUT else "Moved by you"),
                       "still": 0.0 if room == OUT else 0.5}

    def give_back(self):
        self.manual = None
        self.force_still = None

    def is_home(self):
        return self.room not in (None, OUT)
