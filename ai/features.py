"""Raw readings -> 15-minute slots -> feature table for one device.

Every feature looks only at the past (shift >= 0). The label is the device state HORIZON slots later.
"""
import numpy as np
import pandas as pd

from . import settings as S

EVENT_PREFIXES = ("override/", "door/")          # moments, not states: never forward-filled into slots
BINARY_PREFIXES = ("device/", "ai_miss/", "mode/")
BINARY_SUFFIXES = ("/occ", "/mmwave", "/pir")

# feature groups — used for explanations ("what drives this device")
GROUPS = {
    "time": ["h_sin", "h_cos", "dow", "weekend"],
    "habit": ["now", "lag1", "lag2", "yday", "lweek"],
    "presence": ["occ", "occ_run", "occ_yday_target", "home_occ"],
    "temperature": ["temp"],
    "light": ["lux"],
}
CORE = ["now", "lag1", "lag2", "yday", "lweek"]


def to_local(ts):
    """unix seconds -> local wall-clock time (naive), so 07:00 means 07:00 at home."""
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(ts), unit="s", utc=True)).tz_convert(S.TZ).tz_localize(None)


def local_now(now_ts):
    return to_local([now_ts])[0]


def load_slots(con, since_ts=0, until_ts=None):
    """One row per 15-minute slot, one column per key. Binary keys are 1 if on for most of the slot."""
    q = "SELECT ts, key, value FROM readings WHERE ts >= ?" + (" AND ts <= ?" if until_ts else "")
    params = (int(since_ts),) + ((int(until_ts),) if until_ts else ())
    raw = pd.read_sql(q, con, params=params)
    raw = raw[~raw.key.str.startswith(EVENT_PREFIXES)]
    if raw.empty:
        return pd.DataFrame()
    raw["ts"] = to_local(raw.ts)
    wide = raw.pivot_table(index="ts", columns="key", values="value", aggfunc="mean")
    binary = [c for c in wide if c.startswith(BINARY_PREFIXES) or c.endswith(BINARY_SUFFIXES)]
    # binary: time-weighted share of the slot (forward-fill at 1-minute resolution first)
    fine = wide[binary].resample("1min").last().ffill() if binary else pd.DataFrame(index=wide.index)
    slots = wide.drop(columns=binary).resample(S.SLOT).mean()
    if binary:
        share = fine.resample(S.SLOT).mean()
        slots = slots.join((share >= 0.5).astype(float).where(share.notna()), how="outer")
    return slots.ffill()


def col(slots, key):
    return slots[key] if key and key in slots else pd.Series(np.nan, index=slots.index)


def home_occupancy(slots):
    occ_cols = [c for c in slots if c.endswith("/occ")]
    return slots[occ_cols].max(axis=1) if occ_cols else pd.Series(np.nan, index=slots.index)


def run_length(s):
    """How many slots the current value has lasted (presence run length)."""
    if not s.notna().any():
        return s
    return s.groupby((s != s.shift()).cumsum()).cumcount().astype(float)


def time_features(idx):
    minute = idx.hour * 60 + idx.minute
    return {
        "h_sin": np.sin(minute / 1440 * 2 * np.pi),
        "h_cos": np.cos(minute / 1440 * 2 * np.pi),
        "dow": idx.dayofweek,
        "weekend": idx.dayofweek.isin(S.WEEKEND_DAYS).astype(int),
    }


def device_state(slots, spec):
    """Device state with AI mistakes removed: while `ai_miss/<id>` is 1 the device was on only because the AI
    guessed wrong, so for learning it counts as OFF (otherwise the model would teach itself its own mistakes)."""
    s = col(slots, spec.state).copy()
    miss = col(slots, f"ai_miss/{spec.id}")
    return s.where(~(miss == 1), 0.0)


def build_features(slots, spec):
    s = device_state(slots, spec)
    occ, temp = col(slots, spec.occ), col(slots, spec.temp)
    idx = slots.index
    feats = time_features(idx)
    feats.update({
        "temp": temp, "lux": col(slots, spec.lux), "occ": occ,
        "now": s, "lag1": s.shift(1), "lag2": s.shift(2),
        "yday": s.shift(S.SLOTS_PER_DAY), "lweek": s.shift(7 * S.SLOTS_PER_DAY),
        "occ_yday_target": occ.shift(S.SLOTS_PER_DAY - S.HORIZON),
        "occ_run": run_length(occ),
        "home_occ": home_occupancy(slots),
    })
    for c in spec.context:                         # e.g. "TV is on" -> someone is in the living room
        feats["ctx_" + c.split("/", 1)[1]] = col(slots, c)
    X = pd.DataFrame(feats, index=idx)             # HistGradientBoosting handles missing values (NaN) itself
    y = s.shift(-S.HORIZON)
    return X, y


def usable(slots):
    """Slots that may be used for training: not in Away / vacation mode."""
    away = col(slots, "mode/away")
    return ~(away == 1)


def feature_group(name):
    if name.startswith("ctx_"):
        return "other devices"
    for g, members in GROUPS.items():
        if name in members:
            return g
    return "other"
