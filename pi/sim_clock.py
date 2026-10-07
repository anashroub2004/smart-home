"""Virtual clock for the simulator / digital twin.

speed 1   = real time (what the Pi does).
speed 60  = one hour per real minute, speed 600 = a whole day in 2.4 minutes.
Pause, change speed or jump to a time of day at any moment. Every part of the simulator (rules, the person,
the AI, the timestamps written to Firebase) reads this clock, so "10 minutes empty" means 10 virtual minutes.
"""
import time
from datetime import datetime, timedelta


class VirtualClock:
    def __init__(self, speed=1.0):
        self.speed = float(speed)
        self._real0 = time.time()
        self._virt0 = self._real0
        self.paused = False

    def now(self):
        if self.paused:
            return self._virt0
        return self._virt0 + (time.time() - self._real0) * self.speed

    def _rebase(self):
        self._virt0 = self.now()
        self._real0 = time.time()

    def set_speed(self, speed):
        self._rebase()
        self.speed = max(0.1, float(speed))

    def pause(self):
        if not self.paused:
            self._rebase()
            self.paused = True

    def resume(self):
        if self.paused:
            self._real0 = time.time()
            self.paused = False

    def jump_to(self, hhmm):
        """Move forward to the next HH:MM (never backwards: the AI's history must stay in order)."""
        h, m = (int(x) for x in hhmm.split(":"))
        now = datetime.fromtimestamp(self.now())
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        self._rebase()
        self._virt0 = target.timestamp()
        return target.timestamp()

    def forward(self, seconds):
        self._rebase()
        self._virt0 += seconds
