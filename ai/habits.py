"""Learned habits: what you usually do at this time of day, learned from the history (nightly, with the models).

Two small tables per 15-minute slot of the day (weekdays and weekends kept apart), time-decay weighted like the
models (a day 2 weeks old counts half), smoothed over the neighbouring slots (45 min for use, 1 h for rest) and with a weak prior so a few days
of data never give 0 % or 100 %:

    use[device]   P(device on | someone in its room)        "you usually have the light on now"
    rest[room]    P(PIR quiet | someone in the room)         "you usually rest / sleep here now"

They answer questions about NOW (the gradient boosting models predict 60 minutes ahead):
    - you walk into a dark room: switch the light on?   -> always, except back within 15 min to where you sleep now
    - the light is on, you lie still: switch it off?    -> use <= 0.50 and rest >= 0.80, after your learned
                                                           "getting ready" time (self_off prep_min, else 10 min)
Saved as ai/models/habits.json. Readable and explainable: "you had it on 9 of the last 10 evenings at this time".
"""
import json

import numpy as np

from . import settings as S
from .features import col, device_state, local_now

SLOTS = S.SLOTS_PER_DAY
PRIOR = 0.5            # weak prior: half a pseudo-observation at 50 % (the decayed weights are small)
SMOOTH_USE = (0.25, 0.5, 0.25)                       # 45 min: keeps "reading -> asleep" sharp
SMOOTH_REST = (1 / 9, 2 / 9, 3 / 9, 2 / 9, 1 / 9)     # 1 h: resting is a slow state, few weekend nights


def _table(values, cond, index, now, smooth=SMOOTH_USE):
    """Weighted share of values==1 where cond, per (weekend, slot) -> {"weekday": [96], "weekend": [96], "days": n}."""
    m = cond.fillna(False).astype(bool) & values.notna()
    if not m.any():
        return None
    idx = index[m]
    v = values[m].to_numpy(dtype=float)
    age = (now - idx).total_seconds() / 86400
    w = np.power(0.5, np.asarray(age) / S.HALF_LIFE_DAYS)
    slot = (idx.hour * 60 + idx.minute) // S.SLOT_MIN
    weekend = idx.dayofweek.isin(S.WEEKEND_DAYS)
    out = {"days": int(len(set(idx.date)))}
    for name, sel in (("weekday", ~weekend), ("weekend", weekend)):
        on = np.bincount(slot[sel], weights=(w * v)[sel], minlength=SLOTS)
        tot = np.bincount(slot[sel], weights=w[sel], minlength=SLOTS)
        shifts = range(len(smooth) // 2, -(len(smooth) // 2) - 1, -1)
        on = sum(k * np.roll(on, s) for k, s in zip(smooth, shifts))
        tot = sum(k * np.roll(tot, s) for k, s in zip(smooth, shifts))
        out[name] = [round(float(x), 3) for x in (on + PRIOR * 0.5) / (tot + PRIOR)]
    return out


def learn(slots, specs, now):
    """-> {"use": {dev: table}, "rest": {room: table}}."""
    use, rest = {}, {}
    for dev, spec in specs.items():
        if spec.state not in slots or not spec.occ or spec.occ not in slots:
            continue
        t = _table(device_state(slots, spec), col(slots, spec.occ) == 1, slots.index, now)
        if t:
            use[dev] = t
        room = spec.room
        pir = next((k for k in spec.presence_keys if k.endswith("/pir")), None)
        if room not in rest and pir and pir in slots:
            quiet = (col(slots, pir) == 0).astype(float).where(col(slots, pir).notna())
            r = _table(quiet, col(slots, spec.occ) == 1, slots.index, now, SMOOTH_REST)
            if r:
                rest[room] = r
    return {"use": use, "rest": rest}


def self_off(con, slots, specs, now_ts, days=None):
    """History counts as answers: nights in the last `days` on which you switched a light off BY HAND and stayed
    in the room (lying down to sleep / rest). -> {dev: {"nights": n, "days": d, "prep_min": m, "prep_nights": k}}.
    The habit says "the light off while I rest here is what I want" — the same thing 3 "yes" answers say.
    prep_min = your "getting ready" time: how long you lay still (no PIR movement) before you switched it off,
    median of those nights + a margin, within limits; None with fewer than SELF_OFF_MIN_NIGHTS nights."""
    import pandas as pd
    from .features import to_local
    days = days or S.SELF_OFF_DAYS
    since = int(now_ts - days * 86400)
    out = {}
    if slots is None or slots.empty:
        return out
    for dev, spec in specs.items():
        if spec.kind != "light" or not spec.occ or spec.occ not in slots or spec.state not in slots:
            continue
        raw = pd.read_sql("SELECT ts FROM readings WHERE key = ? AND ts >= ? AND ts <= ?", con,
                          params=(f"override/{dev}", since, int(now_ts)))
        pir = next((k for k in spec.presence_keys if k.endswith("/pir")), None)
        nights, prep = set(), {}
        for ts, t in zip(raw.ts, to_local(raw.ts)):
            if not (t.hour >= 20 or t.hour < 5):
                continue
            slot = t.floor(S.SLOT)
            after = slot + pd.Timedelta(minutes=S.SLOT_MIN)
            if after not in slots.index or slot not in slots.index:
                continue
            off = (slots.at[after, spec.state] or 0) <= 0.5               # the press switched it OFF ...
            stayed = all((slots.at[x, spec.occ] or 0) >= 0.5 for x in (slot, after))   # ... and you stayed there
            if off and stayed:
                night = (t - pd.Timedelta(hours=12)).date()
                nights.add(night)
                if pir and night not in prep:
                    prep[night] = still_before(con, pir, int(ts))
        m = prep_minutes(list(prep.values()))
        out[dev] = {"nights": len(nights), "days": days, "prep_min": m, "prep_nights": len(prep) if m else 0}
    return out


def still_before(con, pir, ts):
    """Minutes you lay still before `ts`: since the PIR last saw movement (the press itself, the last
    PREP_IGNORE_S seconds, does not count). No movement in the last PREP_LOOK_MIN -> PREP_LOOK_MIN."""
    end = ts - S.PREP_IGNORE_S
    moved = con.execute("SELECT MAX(ts) FROM readings WHERE key = ? AND value >= 0.5 AND ts >= ? AND ts <= ?",
                        (pir, ts - S.PREP_LOOK_MIN * 60, end)).fetchone()[0]
    if moved is None:
        return float(S.PREP_LOOK_MIN)
    # readings are written on change: stillness starts at the first "0" after that movement (like quiet_since)
    still = con.execute("SELECT MIN(ts) FROM readings WHERE key = ? AND value < 0.5 AND ts > ? AND ts <= ?",
                        (pir, moved, end)).fetchone()[0]
    return (ts - still) / 60 if still is not None else 0.0


def prep_minutes(values):
    """Median + margin, within PREP_LIMITS_MIN; None with too few nights (the fixed STILL_OFF_MIN is used then)."""
    if len(values) < S.SELF_OFF_MIN_NIGHTS:
        return None
    lo, hi = S.PREP_LIMITS_MIN
    return round(min(hi, max(lo, float(np.median(values)) + S.PREP_MARGIN_MIN)), 1)


def save(path, habits):
    path.write_text(json.dumps(habits))


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"use": {}, "rest": {}}


def _at(table, ts, ahead_min=0):
    """Mean of the table over [ts, ts + ahead_min] (one slot if ahead_min == 0)."""
    if not table or table.get("days", 0) < S.HABIT_MIN_DAYS:
        return None
    t = local_now(ts)
    vals = []
    for k in range(max(1, ahead_min // S.SLOT_MIN)):
        tt = t + np.timedelta64(k * S.SLOT_MIN, "m")
        row = table["weekend" if tt.dayofweek in S.WEEKEND_DAYS else "weekday"]
        vals.append(row[(tt.hour * 60 + tt.minute) // S.SLOT_MIN])
    return float(np.mean(vals))


def use_now(habits, dev, ts, ahead_min=0):
    return _at((habits.get("use") or {}).get(dev), ts, ahead_min)


def rest_now(habits, room, ts):
    return _at((habits.get("rest") or {}).get(room), ts)
