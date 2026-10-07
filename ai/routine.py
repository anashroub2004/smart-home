"""The household routine — ONE source of truth for the simulated person.

Used by:
    ai/synth.py      simulated history the AI trains on
    pi/sim_house.py  the live person walking through the house (digital twin)
so what the AI learned and what the simulator does are the same routine.

Python standard library only (the simulator must run without numpy).

A day is a list of segments (start_h, end_h, room, activity, still):
    still = chance the person is sitting/lying still (a PIR misses them, the radar does not)
Hours may go past 24 (going to bed at 01:00 = 25.0). Every day is deterministic for a seed + date,
so the twin can show "today's plan" and it does not change when the page reloads.
"""
import random
from datetime import date, timedelta

WEEKEND_DAYS = (4, 5)          # Friday, Saturday (Monday = 0)
OUT = "out"


def _rng(seed, day):
    return random.Random(f"{seed}-{day.isoformat()}")


def day_plan(day, seed=42, changed=False):
    """Key times of a day (hours). changed=True: a different routine (summer holiday: late, out in the evening)."""
    r = _rng(seed, day)
    j = lambda s: r.gauss(0, s)
    weekend = day.weekday() in WEEKEND_DAYS
    if changed:
        p = dict(wake=10 + j(.5), out=[(17 + j(.5), 21 + j(.5))], sleep=25 + j(.4))
    elif weekend:
        p = dict(wake=9.5 + j(.8), out=[(12 + j(1), 17 + j(1))] if r.random() < .5 else [], sleep=23.8 + j(.5))
    else:
        p = dict(wake=7 + j(.3), out=[(8 + j(.3), 16.5 + j(.6))], sleep=23 + j(.4))
    if not changed and r.random() < .07:
        p["out"] = []                                    # stays home (sick day)
    p["cook"] = 19 + j(.3)
    p["sunset"] = 18.3 + j(.15)
    p["tmax"] = 29.5 + j(2)
    p["weekend"] = weekend
    p["visits"] = [r.uniform(0, 1) for _ in range(6)]     # random short bathroom / kitchen trips
    return p


def segments(day, seed=42, changed_from=None, rooms=("living", "kitchen", "bedroom", "bathroom", "entrance")):
    """Segments for one calendar day (0..24 h), including the end of the previous night.
    changed_from: date from which the routine changes (or None)."""
    has = set(rooms)
    R = lambda name, fallback="living": name if name in has else (fallback if fallback in has else sorted(has)[0])
    changed = bool(changed_from and day >= changed_from)
    p = day_plan(day, seed, changed)
    prev = day_plan(day - timedelta(days=1), seed, bool(changed_from and day - timedelta(days=1) >= changed_from))
    segs = []
    t = 0.0
    if prev["sleep"] > 24:                               # still awake after midnight from yesterday
        late = prev["sleep"] - 24
        segs.append((0.0, max(0.0, late - 0.15), R("living"), "Watching TV", .7))
        segs.append((max(0.0, late - 0.15), late, R("bathroom"), "Getting ready for bed", .1))
        t = prev["sleep"] - 24
    segs.append((t, p["wake"], R("bedroom"), "Sleeping", .9))
    t = p["wake"]
    segs.append((t, t + .25, R("bathroom"), "Getting ready", .1))
    t += .25
    segs.append((t, t + .35, R("kitchen"), "Breakfast", .3))
    t += .35
    day_end = min(p["sleep"], 24.0)
    outs = sorted((a, b) for a, b in p["out"] if b > t)
    evening = [(p["cook"], p["cook"] + .6, R("kitchen"), "Cooking", .2)]
    for a, b in outs:
        a = max(a, t)
        if a > t:
            segs += _home_block(t, a, R, p, "Relaxing")
        segs.append((a, a + .03, R("entrance"), "Leaving", 0.0))
        segs.append((a + .03, b, OUT, "Out", 0.0))
        segs.append((b, b + .05, R("entrance"), "Arriving home", 0.0))
        t = b + .05
    for a, b, room, act, still in evening:
        if a > t and a < day_end:
            segs += _home_block(t, a, R, p, "Relaxing")
            segs.append((a, min(b, day_end), room, act, still))
            t = min(b, day_end)
    bed = p["sleep"] - .5
    if bed > t:
        segs += _home_block(t, min(bed, 24.0), R, p, "Dinner & TV", tv=True)
        t = min(bed, 24.0)
    if p["sleep"] <= 24:
        segs.append((t, p["sleep"] - .3, R("bedroom"), "Reading in bed", .8))
        segs.append((p["sleep"] - .3, p["sleep"] - .15, R("bathroom"), "Getting ready for bed", .1))
        segs.append((p["sleep"] - .15, 24.0, R("bedroom"), "Sleeping", .9))
    elif t < 24:
        segs += _home_block(t, 24.0, R, p, "Watching TV", tv=True)
    return [s for s in segs if s[1] - s[0] > 1e-6]


def _home_block(a, b, R, p, activity, tv=False):
    """Time at home in the living room, with short trips to the bathroom / kitchen."""
    out, t = [], a
    trips = [a + v * (b - a) for v in p["visits"][:max(0, int((b - a) / 1.5))]]
    for i, x in enumerate(sorted(trips)):
        if x - t < .2 or b - x < .2:
            continue
        out.append((t, x, R("living"), activity, .6 if tv else .5))
        room = R("bathroom") if i % 2 == 0 else R("kitchen")
        out.append((x, x + .12, room, "Bathroom" if "bath" in room else "Getting a snack", .1))
        t = x + .12
    out.append((t, b, R("living"), activity, .6 if tv else .5))
    return out


def at(segs, hour):
    """The segment running at `hour` (0..24)."""
    for s in segs:
        if s[0] <= hour < s[1]:
            return s
    return segs[-1]


def today(day: date, seed=42, changed_from=None, rooms=None):
    return segments(day, seed, changed_from, rooms or ("living", "kitchen", "bedroom", "bathroom", "entrance"))
