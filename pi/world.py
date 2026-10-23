"""
The world around the house: the simulated person (pi/person.py, living the routine file ai/personas/*.toml)
and what the sensors see — presence (radar / PIR), temperature and humidity with the room's inertia, daylight
— plus what he does by hand: wall buttons (light on in the dark, off when leaving or lying down, sometimes
forgetting), appliances from today's plan (hood, exhaust fan, laundry, pre-cooling from the app).

One source for two users (stage 1e, 2026-10-23):
    sim_house.py     the simulator / digital twin (its House is the `h` below)
    fake_nodes.py    --world: the fake ESP32 nodes publish what Ahmed's world shows (a WorldHouse is the `h`)

Every function takes `h`, a house-like object that provides:
    now(), vnow()                         the clock (virtual in the twin)
    config, th, persona, person, speedup  the house, its thresholds, the routine file, the person, legacy --fast
    rooms                                 room -> dict of sensor values (written here), with hidden "_t" / "_h"
    devices                               device -> {"v", "level", "src", ...} (read here)
    offline                               nodes that are off: their rooms are not sensed, their buttons not pressed
    temp_delta, sensor_t, dark_since, bed_since, bed_forgets, intents_done, override_until   world state
    icon(d), writable(d), ctrl(d), node_of(d), dev_room, dev_cfg, room_name(r), mid_level(d)
    person_switch(dev, v, level, **why)   the person pressed a button (or used the app)
    end_hands_off(rid), kpi_add(key)      house bookkeeping (no-ops where they don't apply)
    person_moved(old, new), person_left_home(), person_came_home()

The code was moved here from sim_house.py unchanged (the twin's behaviour and random sequence stay the same).
Standard library only.
"""
import math
import random
from datetime import timedelta

from firebase_writer import now_ms
from person import OUT

DARK_PATIENCE_S = 180            # the person switches the light on himself after 3 min in the dark
BED_FORGET = 0.3                 # some nights he falls asleep with the light on (without a routine file)
PIR_RATE = 12.0                  # PIR movements per minute = 12 x (1 - still)^2
WINDOW = {"living": 0.75, "kitchen": 0.5, "bedroom": 0.6, "bathroom": 0.0}   # share of daylight per room


def sense(h):
    """What every sensor sees now: presence from where the person is and how still, temperature and humidity
    with the room's inertia (and the fans / exhaust fan / shower / cooking), daylight + lights."""
    now = h.vnow()
    t = h.now()
    sens_dt = min(60.0, max(1.0, t - (h.sensor_t or t - 5)))   # seconds since the last reading
    h.sensor_t = t
    hh = now.hour + now.minute / 60
    today = now.date()
    if h.persona is not None:          # seasons, sunset and weather of the routine file (= the AI's history)
        outdoor = h.persona.outdoor(today, hh) + h.temp_delta
        sun_lux = h.persona.daylight(today, hh)
        rh_out = h.persona.rh_out(today, hh)
    else:
        outdoor = 27 + 4 * math.sin((hh - 9) / 24 * 2 * math.pi) + h.temp_delta
        sun_lux = 600 * (max(0.0, math.sin((hh - 6) / 13 * math.pi)) if 6 <= hh <= 19 else 0)
        rh_out = None
    act, here = h.person.activity, h.person.room
    for rid, room in h.config["rooms"].items():
        if room["node"] in h.offline:
            continue
        s = h.rooms[rid]
        sensors = room.get("sensors", [])
        if "occ" in sensors:
            new = int(h.person.room == rid)            # the simulated person is in this room
            if new and not s.get("occ"):
                s["motion_at"] = now_ms()
                s["entry_pending"] = True                 # "on when dark" looks at every entry once
            if new != s.get("occ"):
                s["occ_since"] = now_ms()
            if s.get("occ") and not new:
                h.end_hands_off(rid)                     # you left: the 2 h hands-off ends with your visit
            s["occ"] = new
            s["occ_by"] = ("mmwave" if "C1001 mmWave" in room.get("hardware", []) else "pir")
            hw = room.get("hardware", [])
            if "C1001 mmWave" in hw:
                s["mmwave"] = new                            # the radar also sees people sitting still
            if "PIR" in hw:                                  # PIR only sees movement: it misses still people
                # movements per minute: walking ~10/min, TV ~2/min, reading ~0.5/min, asleep ~0.1/min
                rate = PIR_RATE * (1 - h.person.still) ** 2
                s["pir"] = int(bool(new) and random.random() < 1 - math.exp(-rate * sens_dt / 60))
        devs = room.get("devices") or {}
        fan_on = any(h.devices[d]["v"] and h.icon(d) in ("fan", "ac") for d in devs)
        if h.persona is not None:      # inertia like the real room (and like ai/lifesim.py)
            target = h.persona.indoor_target(today, hh, rid) - (1.4 if fan_on else 0)
            if rid == "kitchen" and here == rid and act.startswith("Cooking"):
                target += 2.5
            if rid == "bathroom" and here == rid and act == "Shower":
                target += 3.0
            k = 1 - math.exp(-sens_dt / 7200)
            s["_t"] = s.get("_t", target) + (target - s.get("_t", target)) * k
            base_h = rh_out * 0.75 + 8
            vent = rid == "bathroom" and any(h.devices[d]["v"] and h.icon(d) == "vent" for d in devs)
            goal, rate = ((93, 0.18) if rid == "bathroom" and here == rid and act == "Shower"
                          else (base_h, 0.09 if vent else 0.025))
            s["_h"] = s.get("_h", base_h) + (goal - s.get("_h", base_h)) * (1 - (1 - rate) ** (sens_dt / 60))
        if "temp" in sensors:
            if h.persona is not None:
                # the twin's "hotter / cooler" control (and the scenarios) act at once, outside the room's inertia
                s["temp"] = round(s["_t"] + h.temp_delta + random.gauss(0, 0.08), 1)
            else:
                s["temp"] = round(outdoor + (0.8 if rid == "bedroom" else 0) + (-1.2 if fan_on else 0)
                                  + random.gauss(0, 0.12), 1)
        if "hum" in sensors:
            s["hum"] = round(s["_h"] + random.gauss(0, 0.6)) if h.persona is not None else \
                round(50 - (outdoor - 27) * 1.5 + random.gauss(0, 1))
        if "lux" in sensors:
            lights = 250 if any(h.devices[d]["v"] and h.icon(d) == "light" for d in devs) else 0
            share = 0.0 if room.get("windowless") else WINDOW.get(rid, 0.6)
            s["lux"] = round(max(0, sun_lux * share + lights + random.gauss(0, 6)))


def room_lux(h, rid):
    """Light level for the rules / the person: own BH1750, else dark if windowless, else a neighbour's."""
    room = h.config["rooms"].get(rid) or {}
    own = (h.rooms.get(rid) or {}).get("lux")
    if own is not None:
        return own
    if room.get("windowless"):
        return 0
    for r, rc in h.config["rooms"].items():
        if "lux" in (rc.get("sensors") or []) and (h.rooms.get(r) or {}).get("lux") is not None:
            return h.rooms[r]["lux"] - (250 if any(h.devices[d]["v"] and h.icon(d) == "light"
                                                   for d in (rc.get("devices") or {})) else 0)
    return None


def person_tick(h):
    """Move the person along today's routine; doors, light switches and appliances follow what he does."""
    moved = h.person.step(h.now())
    dark_habit(h)
    bed_habit(h)
    appliance_tick(h)
    if not moved:
        return
    old, new = moved
    h.person_moved(old, new)
    if old and old != OUT and h.person.habits:
        leave_room_habits(h, old)
    if new == OUT and old is not None:                     # leaving: exit button, camera sees him
        h.person_left_home()
    elif old == OUT:                                       # coming home: fingerprint
        h.person_came_home()


def leave_room_habits(h, rid, always=False, lights_only=False):
    """Like a real person: usually switches the light off when leaving, sometimes forgets (the AI's job)."""
    for dev in (h.config["rooms"].get(rid, {}).get("devices") or {}):
        if lights_only and h.icon(dev) != "light":
            continue
        st = h.devices.get(dev) or {}
        if not st.get("v") or not h.writable(dev) or not h.ctrl(dev).get("button"):
            continue
        if h.node_of(dev) in h.offline:
            continue
        chance = 1.0 if always else 0.6 if h.icon(dev) == "light" else 0.3
        if random.random() < chance:
            h.person_switch(dev, 0, source="button", src_label="Button", group="manual",
                            by_label="Someone at home (button)",
                            short=f"Wall button in the {h.room_name(rid).lower()}",
                            why="Switched off before sleeping" if always else "Switched off when leaving the room",
                            tags=["manual"])


def bed_habit(h):
    """Lying down to sleep with the light on: most nights he switches it off himself after a minute or two,
    some nights he falls asleep with it on (decided once per night) — the case the AI must learn to handle."""
    p = h.person
    if p.activity != "Sleeping" or p.manual or not p.habits:
        h.bed_since = None
        return
    now = h.now()
    vnow = h.vnow()
    night = vnow.date() if vnow.hour >= 12 else (vnow - timedelta(days=1)).date()
    lights = [d for d in (h.config["rooms"].get(p.room, {}).get("devices") or {})
              if h.icon(d) == "light" and h.devices[d]["v"] and h.writable(d) and h.ctrl(d).get("button")]
    if not lights:
        h.bed_since = None
        return
    h.bed_since = h.bed_since or now
    if h.bed_forgets.get(night) is None:
        plan = h.person.plan(now) if h.persona is not None else None
        if plan is not None:      # the routine file decides (the same nights the AI's history has)
            off = plan.light_off_at_bed if vnow.hour >= 12 else plan.light_off_prev_night
            h.bed_forgets = {night: not off}
        else:
            h.bed_forgets = {night: random.random() < BED_FORGET}
    if not h.bed_forgets[night] and now - h.bed_since >= 90 / h.speedup:
        for d in lights:
            h.person_switch(d, 0, source="button", src_label="Button", group="manual",
                            by_label="Someone at home (button)", short=f"Wall button in the {h.room_name(p.room).lower()}",
                            why="Switched off after lying down to sleep", tags=["manual"])
            h.kpi_add("manual_fix")
        h.bed_since = None


def appliance_tick(h):
    """Appliance use from today's plan in the routine file: hood while cooking, exhaust fan after a shower,
    laundry, the app pre-cooling the living room on hot afternoons. Same plan the AI's history came from."""
    plan = h.person.plan(h.now()) if h.persona is not None else None
    if plan is None or h.person.manual or not h.person.habits:
        return
    now = h.vnow()
    hh = now.hour + now.minute / 60 + now.second / 3600
    for n, i in enumerate(plan.intents):
        role = i["role"]
        if role.startswith("_") or role == "kettle":
            continue
        devs = [d for d in h.dev_cfg if h.icon(d) == role and h.writable(d)] if role != "fan" else \
            [d for d in (h.config["rooms"].get(i.get("room", "living"), {}).get("devices") or {})
             if h.icon(d) == "fan" and h.writable(d)]
        for edge, at, v in (("on", i["on"], 1), ("off", i.get("off"), 0)):
            key = (plan.date, n, edge)
            if at is None or key in h.intents_done or hh < at or hh > at + 0.5:
                continue
            h.intents_done.add(key)
            for dev in devs:
                if h.node_of(dev) in h.offline or h.devices[dev]["v"] == v:
                    continue
                app = i.get("how") == "app"
                h.person_switch(dev, v, h.mid_level(dev) if v else None, source="web" if app else "button",
                                src_label="App" if app else "Button", group="manual",
                                by_label="Owner (app)" if app else "Someone at home (button)",
                                short=("Started from the app before coming home" if app else
                                       f"Wall button in the {h.room_name(h.dev_room[dev]).lower()}"),
                                why={"hood": "Cooking", "vent": "After a shower", "washer": "Laundry",
                                     "fan": "Hot afternoon: pre-cooling"}.get(role, "Daily routine")
                                + (" (forgot to switch it off)" if (edge == "off" and i.get("forgot")) else ""),
                                tags=["manual"])
    if len(h.intents_done) > 400:
        h.intents_done = {k for k in h.intents_done if k[0] == plan.date}


def dark_habit(h):
    """Like a real person: awake in a dark room with the light off, he presses the wall switch himself
    after a few minutes (only if the rules / AI did not). Not if he switched it off himself in this room."""
    rid, now = h.person.room, h.now()
    room = h.config["rooms"].get(rid) or {}
    lux = room_lux(h, rid)
    if not h.person.habits or h.person.activity == "Sleeping" or lux is None or lux >= h.th["light_on_lux"] \
            or room.get("node") in h.offline:
        h.dark_since.pop(rid, None)
        return
    lights = [d for d in (room.get("devices") or {}) if h.icon(d) == "light" and h.writable(d)
              and h.ctrl(d).get("button") and h.node_of(d) not in h.offline]
    off = [d for d in lights if not h.devices[d]["v"]
           and not (h.devices[d].get("src") == "button" and now < h.override_until.get(d, 0))]
    if not off or len(off) < len(lights):                # a light is on, or he wants it dark
        h.dark_since.pop(rid, None)
        return
    since = h.dark_since.setdefault(rid, now)
    if now - since >= DARK_PATIENCE_S / h.speedup:
        h.dark_since.pop(rid, None)
        h.kpi_add("manual_fix")
        h.person_switch(off[0], 1, h.mid_level(off[0]), source="button", src_label="Button", group="manual",
                        by_label="Someone at home (button)",
                        short=f"Wall button in the {h.room_name(rid).lower()}",
                        why=f"Too dark ({lux} lx) and nothing switched the light on", tags=["manual"])


def wants(h, dev, action):
    """What the simulated person would answer: does he want this device on / off right now?"""
    p, rid = h.person, h.dev_room.get(dev)
    here = p.room == rid
    if action == "off":
        return not here or p.activity == "Sleeping"
    if not here or p.activity == "Sleeping":
        return False
    s = h.rooms.get(rid) or {}
    if h.icon(dev) == "light":
        return s.get("lux", 0) < h.th["light_on_lux"]
    if h.icon(dev) in ("fan", "ac"):
        return s.get("temp", 0) > h.th["fan_on_temp"]
    return True
