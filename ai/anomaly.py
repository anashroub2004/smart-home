"""Energy fault detection from the INA226 readings (approved addition 8.3).

Training (nightly): learn each device's normal power — per level when it has levels — as a robust
median and MAD (median absolute deviation). Robust statistics, because a few bad readings must not
shift the "normal" value; simple enough to run on the Pi and to explain to the committee.

Runtime (every reading): compare live watts with the learned normal:
    high      on, and above median + 4 x MAD and at least 1.5x the usual draw   -> motor stuck / friction
    dead      on, but below 20% of the usual draw                               -> burnt bulb / not responding
    standby   off, but drawing more than 0.3 W (and above its normal standby)   -> stuck relay
A condition must hold for 3 minutes; the same alert repeats at most every 6 hours.
Also nightly: `wear` when the weekly median draw rose 25%+ over 4 weeks (preventive maintenance).
"""
import numpy as np
import pandas as pd

from . import settings as S
from .features import to_local


def _mad(x):
    x = np.asarray(x, float)
    if len(x) == 0:
        return 0.0
    return float(np.median(np.abs(x - np.median(x))))


def learn_baselines(con, devices, since_ts):
    """devices: iterable of device ids. -> {device: {"on": {level|"any": [median, mad, n]}, "off": [...], "wear": ...}}"""
    out = {}
    for dev in devices:
        q = "SELECT ts, key, value FROM readings WHERE ts >= ? AND key IN (?, ?, ?)"
        raw = pd.read_sql(q, con, params=(int(since_ts), f"power/{dev}", f"device/{dev}", f"level/{dev}"))
        if raw.empty or not (raw.key == f"power/{dev}").any():
            continue
        wide = raw.pivot_table(index="ts", columns="key", values="value", aggfunc="last").sort_index().ffill()
        if f"device/{dev}" not in wide:
            continue
        p, on = wide[f"power/{dev}"], wide[f"device/{dev}"] >= 0.5
        lv = wide[f"level/{dev}"] if f"level/{dev}" in wide else pd.Series(np.nan, index=wide.index)
        b = {"on": {}, "off": None}
        on_p = p[on].dropna()
        if len(on_p) >= 20:
            b["on"]["any"] = [float(on_p.median()), _mad(on_p), int(len(on_p))]
            for level, grp in on_p.groupby(lv[on].reindex(on_p.index)):
                if len(grp) >= 20:
                    b["on"][str(int(level))] = [float(grp.median()), _mad(grp), int(len(grp))]
        off_p = p[~on].dropna()
        if len(off_p) >= 20:
            b["off"] = [float(off_p.median()), _mad(off_p), int(len(off_p))]
        # wear: weekly median while on, last 4 weeks
        if len(on_p) >= 100:
            weekly = on_p.groupby(to_local(on_p.index).to_period("W")).median()
            if len(weekly) >= 4:
                first, last = float(weekly.iloc[-4]), float(weekly.iloc[-1])
                b["wear"] = round(last / first - 1, 3) if first > 0 else 0.0
        if b["on"] or b["off"]:
            out[dev] = b
    return out


def classify(baseline, v, watts, level=None):
    """-> (kind, usual_watts) or (None, usual). kind: high | dead | standby."""
    try:
        watts = float(watts)
    except (TypeError, ValueError):
        return None, None
    if not baseline or not np.isfinite(watts):
        return None, None
    try:
        level = int(level) if level is not None and np.isfinite(float(level)) else None
    except (TypeError, ValueError):
        level = None
    if v:
        ref = (baseline.get("on") or {}).get(str(level)) if level is not None else None
        ref = ref or (baseline.get("on") or {}).get("any")
        if not ref:
            return None, None
        med, mad = ref[0], max(ref[1], 0.02 * ref[0], 0.01)
        if watts > med + S.ANOMALY_K * mad and watts >= S.ANOMALY_HIGH_RATIO * med:
            return "high", med
        if med > S.STANDBY_MIN_W and watts < S.ANOMALY_DEAD_RATIO * med:
            return "dead", med
        return None, med
    off = baseline.get("off") or [0.0, 0.0, 0]
    limit = max(S.STANDBY_MIN_W, off[0] + S.ANOMALY_K * max(off[1], 0.01))
    if watts > limit:
        return "standby", off[0]
    return None, off[0]


class Detector:
    """Keeps the 'condition held for N minutes' and 'repeat at most every 6 h' memory."""

    def __init__(self, baselines=None):
        self.baselines = baselines or {}
        self.since = {}          # (device, kind) -> first ts seen
        self.alerted = {}        # (device, kind) -> ts of last alert

    def check(self, now, device, v, watts, level=None, speed=1):
        kind, usual = classify(self.baselines.get(device), v, watts, level)
        for k in ("high", "dead", "standby"):
            if k != kind:
                self.since.pop((device, k), None)
        if not kind:
            return None
        key = (device, kind)
        first = self.since.setdefault(key, now)
        if now - first < S.ANOMALY_PERSIST_MIN * 60 / speed:
            return None
        if now - self.alerted.get(key, -1e18) < S.ANOMALY_REPEAT_H * 3600 / speed:
            return None
        self.alerted[key] = now
        return dict(device=device, kind=kind, watts=round(float(watts), 2),
                    usual=round(float(usual or 0), 2), at=int(now * 1000))


def describe(a, label, level_label=None):
    """Human-readable alert text (English UI)."""
    at = f" at {level_label}" if level_label else ""
    if a["kind"] == "high":
        ratio = a["watts"] / a["usual"] if a["usual"] else 0
        return (f"{label} drawing {ratio:.1f}x its usual power",
                f"Usually {a['usual']} W{at}, now {a['watts']} W — check the motor or for something blocking it")
    if a["kind"] == "dead":
        return (f"{label} is on but draws almost no power",
                f"Usually {a['usual']} W{at}, now {a['watts']} W — the bulb or motor may have failed")
    return (f"{label} draws power while off",
            f"{a['watts']} W while switched off — the relay may be stuck")
