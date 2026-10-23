"""
A shared fast clock for testing the real Pi services (stage 1e-2) — never used on the real Pi.

The digital twin runs ×60 inside one program with one VirtualClock (pi/sim_clock.py). The real services are
separate programs, so the world (fake_nodes.py --world --speed 60) owns the clock and publishes it on MQTT;
each service started with --sim-clock reads it and agrees on "now":

    hub/clock                 retained {"virt0": ts, "real0": ts, "speed": 60, "paused": false}
                              now = virt0 (paused) or virt0 + (time.time() - real0) * speed
    hub/clock/hold/<who>      "1" / "0" — a service asks the world to stop the clock (the AI training, like the
                              twin pauses its day while the models are rebuilt) and to let it go again

The services and the world run on the same computer, so their real clocks agree. Without --sim-clock every
service uses time.time() as always.
"""
import json
import threading
import time

from sim_clock import VirtualClock

TOPIC = "hub/clock"
HOLD = "hub/clock/hold/"


class ClockPublisher:
    """The world's clock (a VirtualClock) told to every service. Holds pause it until released."""

    def __init__(self, speed=60.0):
        self.clock = VirtualClock(speed)
        self.client = None
        self.holders, self.user_paused = set(), False
        self.lock = threading.Lock()

    def now(self):
        return self.clock.now()

    def attach(self, client):
        self.client = client
        client.subscribe(HOLD + "+", qos=1)
        client.message_callback_add(HOLD + "+", self.on_hold)
        self.publish()

    def publish(self):
        if self.client:
            c = self.clock
            body = {"virt0": c._virt0, "real0": c._real0, "speed": c.speed, "paused": c.paused}
            self.client.publish(TOPIC, json.dumps(body), qos=1, retain=True)

    def on_hold(self, client, userdata, msg):
        who, on = msg.topic[len(HOLD):], msg.payload.decode().strip() == "1"
        with self.lock:
            (self.holders.add if on else self.holders.discard)(who)
            self.apply()

    def apply(self):
        if self.holders or self.user_paused:
            self.clock.pause()
        else:
            self.clock.resume()
        self.publish()

    # ---- the world's controls (console / scenarios)
    def set_speed(self, speed):
        self.clock.set_speed(speed)
        self.publish()

    def pause(self, on=True):
        with self.lock:
            self.user_paused = on
            self.apply()

    def jump_to(self, hhmm):
        t = self.clock.jump_to(hhmm)
        self.publish()
        return t

    def forward(self, seconds):
        self.clock.forward(seconds)
        self.publish()


class SharedClock:
    """A service's view of the world's clock (call attach(client) in on_connect). now() is time.time() until
    the first hub/clock message arrives — wait_ready() blocks until then."""

    def __init__(self):
        self.state = None
        self.ready = threading.Event()
        self.client = None
        self.then = None            # called once the first time is known (a service subscribes to home/# then)

    def attach(self, client, then=None):
        """Listen to hub/clock; `then()` runs as soon as the time is known (at once if it already is)."""
        self.client, self.then = client, then
        client.message_callback_add(TOPIC, self.on_clock)
        client.subscribe(TOPIC, qos=1)
        if self.ready.is_set() and then:
            then()

    def on_clock(self, client, userdata, msg):
        try:
            self.state = json.loads(msg.payload)
        except ValueError:
            return
        if not self.ready.is_set():
            self.ready.set()
            if self.then:
                self.then()

    def wait_ready(self, timeout=30):
        return self.ready.wait(timeout)

    @property
    def speed(self):
        return float((self.state or {}).get("speed", 1.0))

    def now(self):
        s = self.state
        if not s:
            return time.time()
        if s.get("paused"):
            return s["virt0"]
        return s["virt0"] + (time.time() - s["real0"]) * s["speed"]

    def hold(self, who, on=True):
        """Ask the world to stop (or restart) the clock — e.g. while the AI trains."""
        if self.client:
            self.client.publish(HOLD + who, "1" if on else "0", qos=1)
