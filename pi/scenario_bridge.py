"""
The go-between house (stage 1e-3): lets pi/scenarios.py — the digital twin's ten scenarios, unchanged — run on
the REAL Pi services. A ScenarioRunner expects a house `h` and a clock; here they are bridges:

    the world      (Ahmed, temperatures, faults, wall buttons, the clock)  -> hub/test/world  -> fake_nodes --world
    the house      (devices, rooms, what the AI sees)                       <- automation's own picture (MQTT)
    the AI         (its plans, questions, trust ladder, stats)               = automation's AIRuntime (same program)
    your answers   (Yes / No to a question)                                 -> Firebase, exactly like the web app
                                                                               (commands.py executes the Yes)

So a scenario's "the AI switched the light off" really went: sensors -> MQTT -> automation decides -> set ->
node -> state -> MQTT. Only the reading of the AI's thoughts is direct (it is the same process).
Used by automation.py with --sim-clock only (a test helper; never on the real Pi).
"""
import json
import time

WORLD = "hub/test/world"


def wait_until(cond, timeout=3.0, step=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return cond()


class WorldState:
    """The world as fake_nodes publishes it on hub/test/world/state (retained)."""

    def __init__(self):
        self.s = {}

    def attach(self, client):
        client.message_callback_add(WORLD + "/state", self.on_state)
        client.subscribe(WORLD + "/state", qos=1)

    def on_state(self, client, userdata, msg):
        try:
            self.s = json.loads(msg.payload)
        except ValueError:
            pass


class PersonProxy:
    """h.person: Ahmed, moved through the world's test channel."""

    def __init__(self, bridge):
        self.b = bridge

    @property
    def room(self):
        return self.b.world.s.get("room")

    @property
    def activity(self):
        return self.b.world.s.get("activity")

    @property
    def manual(self):
        return self.b.world.s.get("manual")

    @property
    def habits(self):
        return self.b.world.s.get("habits", True)

    @habits.setter
    def habits(self, v):
        self.b.send({"do": "habits", "v": bool(v)}, lambda s: s.get("habits") == bool(v))

    @property
    def force_still(self):
        return self.b.world.s.get("force_still")

    @force_still.setter
    def force_still(self, v):
        self.b.send({"do": "force_still", "v": v}, lambda s: s.get("force_still") == v)

    def take_over(self, room, activity=None):
        self.b.send({"do": "take_over", "room": room, "activity": activity}, lambda s: s.get("manual"))

    def give_back(self):
        self.b.send({"do": "give_back"}, lambda s: not s.get("manual"))


class FaultsProxy(dict):
    """h.faults: a dict whose every change goes to the world (the fake node then draws 2x power, ...)."""

    def __init__(self, bridge):
        super().__init__()
        self.b = bridge

    def _push(self):
        self.b.send({"do": "faults", "v": dict(self)}, lambda s: s.get("faults") == dict(self))

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._push()

    def clear(self):
        super().clear()
        self._push()

    def update(self, *a, **kw):
        super().update(*a, **kw)
        self._push()


class ClockProxy:
    """The scenarios' clock: reads the shared clock, changes it through the world (which owns it)."""

    def __init__(self, bridge):
        self.b = bridge

    @property
    def sim(self):
        return self.b.auto.sim

    def now(self):
        return self.sim.now()

    @property
    def speed(self):
        return self.sim.speed

    @property
    def paused(self):
        return bool((self.sim.state or {}).get("paused"))

    def _op(self, op, arg=None):
        before = json.dumps(self.sim.state, sort_keys=True)
        self.b.publish({"do": "clock", "op": op, "arg": arg})
        wait_until(lambda: json.dumps(self.sim.state, sort_keys=True) != before)

    def set_speed(self, speed):
        if float(speed) != self.speed:
            self._op("speed", speed)

    def resume(self):
        if self.paused:
            self._op("resume")

    def jump_to(self, hhmm):
        self._op("jump", hhmm)
        return self.now()

    def forward(self, seconds):
        self._op("forward", seconds)


class BridgeHouse:
    """The `h` a ScenarioRunner works on, made of the real services."""

    def __init__(self, auto):
        self.auto = auto
        self.world = WorldState()
        self.person = PersonProxy(self)
        self.faults = FaultsProxy(self)
        self.recent = []                         # the twin page's event list: not used by the scenarios here

    def attach(self, client):
        self.world.attach(client)

    # ---- talking to the world
    def publish(self, cmd):
        self.auto.client.publish(WORLD, json.dumps(cmd), qos=1)

    def send(self, cmd, done=None, timeout=3.0):
        """Send a test command to the world and wait until its published state shows it (or the timeout)."""
        self.publish(cmd)
        if done:
            wait_until(lambda: done(self.world.s), timeout)

    # ---- what scenarios.py reads from the house
    @property
    def ai(self):
        return self.auto.ai

    @property
    def config(self):
        return self.auto.config

    @property
    def devices(self):
        return self.auto.house.devices

    @property
    def rooms(self):
        return self.auto.house.rooms

    @property
    def rules_off(self):
        return self.auto.rules_off

    @property
    def override_until(self):
        return self.auto.override_until

    @property
    def counts(self):
        return self.auto.counts

    @property
    def temp_delta(self):
        return float(self.world.s.get("temp_delta", 0.0))

    @temp_delta.setter
    def temp_delta(self, v):
        v = round(float(v), 3)
        self.send({"do": "temp_delta", "v": v}, lambda s: abs(float(s.get("temp_delta", 0)) - v) < 1e-6)

    @property
    def door_entry_at(self):
        return self.auto.door_entry_at

    @door_entry_at.setter
    def door_entry_at(self, v):
        self.auto.door_entry_at = v

    def mid_level(self, dev):
        return self.auto.mid_level(self.auto.house.dev_cfg(dev))

    def house_snapshot(self):
        return self.auto.snapshot()

    def ai_training(self):
        return bool(self.auto.ai and self.auto.ai_safe(self.auto.ai.training))

    # ---- what scenarios.py does to the house
    def set_device(self, dev, v, level=None, **_):
        """The scenario sets a device up (src "system": not a manual action). Waits for the node's answer."""
        self.send({"do": "set_dev", "dev": dev, "v": int(v), "level": level})
        wait_until(lambda: int(self.devices[dev].get("v") or 0) == int(v))

    def press_button(self, dev):
        before = int(self.devices[dev].get("v") or 0)
        self.send({"do": "press", "dev": dev})
        wait_until(lambda: int(self.devices[dev].get("v") or 0) != before)

    def answer_suggestion(self, sid, accept, by):
        """Answer exactly like the web app (web/lib/home.ts answerSuggestion): the response, and on Yes a command
        'via suggestion' — which commands.py executes through MQTT."""
        w = self.auto.w
        s = (w.get_suggestions() or {}).get(sid)
        if not isinstance(s, dict) or s.get("response"):
            return
        w.mark_suggestion(sid, {"response": "accept" if accept else "dismiss", "answered_by": by})
        dev = s.get("device")
        if accept and dev in self.auto.house.dev_room:
            v = 1 if s.get("action") == "on" else 0
            w.db.put(f"commands/{dev}", {"v": v, "via": "suggestion", "confidence": s.get("confidence"),
                                         "by": by, "at": int(time.time() * 1000), "status": "pending"})
            wait_until(lambda: int(self.devices[dev].get("v") or 0) == v, timeout=8.0)

    def handle_suggestion_answers(self):
        self.auto.handle_answers()
