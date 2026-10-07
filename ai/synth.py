"""Simulated household history for EVERY room/device in the config.

Used by `run.py demo` and by the simulator to bootstrap a fresh Pi (no 3 weeks of real data yet).
Numbers that come out of a model trained on this are labelled "simulated" everywhere.

One person with a weekday / weekend routine, small random noise, a short vacation (Away mode),
and optionally a routine change at the end (to test drift detection).
"""
import numpy as np
import pandas as pd

from . import routine, store
from . import settings as S
from .spec import device_kind


def generate(config, days=70, end_ts=None, seed=42, routine_change_days=0, vacation=True):
    """Returns a list of (ts, key, value) every 5 minutes, ending at end_ts (default: now)."""
    rng = np.random.default_rng(seed)
    rooms = config["rooms"]
    devs = {d: (rid, c) for rid, r in rooms.items() for d, c in (r.get("devices") or {}).items()
            if not (c.get("caps") or {}).get("lock")}
    end = pd.Timestamp(end_ts or pd.Timestamp.now().timestamp(), unit="s", tz="UTC").tz_convert(S.TZ)
    end = end.floor("5min")
    start = end - pd.Timedelta(days=days)
    trip = None
    if vacation and days >= 35:
        d0 = int(rng.integers(7, days - 25))
        trip = (start.normalize() + pd.Timedelta(days=d0), start.normalize() + pd.Timedelta(days=d0 + 3))
    th = config.get("thresholds") or {}
    dark_lux = th.get("light_on_lux", 150)
    habit = {dv: 18 + int(rng.integers(0, 4)) + rng.normal(0, .4) for dv in devs}
    state = {dv: 0 for dv in devs}
    level = {dv: None for dv in devs}
    rows = []
    day_plan = {}

    changed_from = (end - pd.Timedelta(days=routine_change_days)).date() if routine_change_days else None
    room_ids = tuple(rooms)

    def plan(day):
        """Where the person is comes from ai/routine.py (the same routine the live simulator uses)."""
        d = day.date()
        p = routine.day_plan(d, seed, bool(changed_from and d >= changed_from))
        return dict(segs=routine.segments(d, seed, changed_from, room_ids), tmax=p["tmax"], sunset=p["sunset"])

    t = start
    prev_home = True
    while t <= end:
        day = t.normalize()
        if day not in day_plan:
            day_plan = {day: plan(day)}
        p = day_plan[day]
        h = t.hour + t.minute / 60
        ts = int(t.timestamp())
        away = bool(trip and trip[0] <= t < trip[1])
        seg = routine.at(p["segs"], h)
        where = routine.OUT if away else seg[2]
        home = where != routine.OUT
        asleep = home and seg[3] == "Sleeping"
        still = seg[4]
        tv_time = home and "TV" in seg[3]
        if home and not prev_home:
            rows.append((ts, "door/entry", 1))
        prev_home = home
        rows.append((ts, "mode/away", int(away)))
        t_out = p["tmax"] - 2.5 * (1 - np.cos((h - 15) / 24 * 2 * np.pi))   # warmest at 15:00
        lux_sun = max(0, 800 * np.sin(np.clip((h - 6) / (p["sunset"] - 6), 0, 1) * np.pi))
        room_temp = {}
        for rid, room in rooms.items():
            sensors = room.get("sensors") or []
            occ = int(where == rid)
            cooling = any(state[d] and devs[d][0] == rid and device_kind(devs[d][1]) == "thermal" for d in devs)
            temp = t_out + (1 if rid == "bedroom" else 0) - (1.2 if cooling else 0) + rng.normal(0, .3)
            room_temp[rid] = temp
            lights_on = any(state[d] and devs[d][0] == rid and device_kind(devs[d][1]) == "light" for d in devs)
            if "temp" in sensors:
                rows.append((ts, f"{rid}/temp", round(temp, 2)))
            if "lux" in sensors:
                rows.append((ts, f"{rid}/lux", round(max(0, lux_sun * 0.75 + (250 if lights_on else 0) + rng.normal(0, 12)), 1)))
            if "occ" in sensors:
                rows.append((ts, f"{rid}/occ", occ))
                hw = " ".join(room.get("hardware") or []).lower()
                if "mmwave" in hw:
                    rows.append((ts, f"{rid}/mmwave", occ))
                if "pir" in hw:   # PIR misses people who sit or sleep still
                    rows.append((ts, f"{rid}/pir", int(occ and rng.random() > still)))
        for dv, (rid, c) in devs.items():
            occ, kind = int(where == rid), device_kind(c)
            caps = c.get("caps") or {}
            temp = room_temp[rid]
            room_lux = lux_sun * 0.75
            st = state[dv]
            if caps.get("power") == "read":            # monitor-only: TV evenings, fridge always
                st = 1 if c.get("icon") == "fridge" else int(occ and tv_time)
            elif kind == "thermal":
                if occ and (temp > 28.3 or (asleep and temp > 27)) and rng.random() < .8:
                    st = 1
                elif not occ and st and rng.random() < .5:
                    st = 0
                elif occ and temp < 26.3:
                    st = 0
            elif kind == "light":
                st = int(occ and not asleep and room_lux < dark_lux and rng.random() < .97)
            elif (c.get("control") or {}).get("ai"):    # generic device: around a usual hour, if home
                st = int(home and not asleep and habit[dv] <= h < habit[dv] + 1.5)
            else:                                      # e.g. washer (no AI): a run on some evenings
                st = int(home and rng.random() < .02) if not st else int(rng.random() < .9)
            steps = (caps.get("level") or {}).get("steps")
            if steps:
                if st and not state[dv]:
                    level[dv] = steps[0] if asleep else (steps[-1] if temp > 31 else steps[len(steps) // 2])
                rows.append((ts, f"level/{dv}", level[dv] or steps[len(steps) // 2]))
            state[dv] = st
            rows.append((ts, f"device/{dv}", st))
            watts = float(c.get("watts") or 0)
            if watts and caps.get("energy", "estimate") != "none":
                factor = (level[dv] or 70) / 100 if steps else 1
                standby = 0.05 if c.get("icon") in ("tv",) else 0.0
                w = watts * factor * rng.uniform(.96, 1.04) if st else standby
                rows.append((ts, f"power/{dv}", round(w, 3)))
        t += pd.Timedelta(minutes=5)
    return rows


def fill(con, config, days=70, end_ts=None, seed=42, routine_change_days=0, replace=True):
    rows = generate(config, days=days, end_ts=end_ts, seed=seed, routine_change_days=routine_change_days)
    if replace:
        con.execute("DELETE FROM readings")
    store.write(con, rows)
    store.set_meta(con, "synth_until", max(r[0] for r in rows))
    return rows
