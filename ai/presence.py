"""Presence prediction (approved addition 8.4): "will someone be home in 60 minutes?"

Same model family as the devices (Gradient Boosting), trained on 'anyone home' = any room with presence.
Used for: Insights ("usually home around 16:30"), the Away scene, and the plan shown to the user.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from . import settings as S
from .features import home_occupancy, run_length, time_features, usable

FEATURES = ["h_sin", "h_cos", "dow", "weekend", "home", "lag1", "lag2", "yday", "lweek", "run", "door_recent"]


def build(slots, door_slots=None):
    home = home_occupancy(slots)
    idx = slots.index
    f = time_features(idx)
    f.update({"home": home, "lag1": home.shift(1), "lag2": home.shift(2),
              "yday": home.shift(S.SLOTS_PER_DAY - S.HORIZON),     # was someone home at the target time yesterday
              "lweek": home.shift(7 * S.SLOTS_PER_DAY - S.HORIZON),
              "run": run_length(home),
              "door_recent": door_slots.reindex(idx).fillna(0) if door_slots is not None else 0.0})
    X = pd.DataFrame(f, index=idx)
    return X[FEATURES], home.shift(-S.HORIZON)


def train(slots, door_slots=None):
    X, y = build(slots, door_slots)
    ok = X[["home", "lag1", "yday"]].notna().all(axis=1) & y.notna() & usable(slots)
    X, y = X[ok], y[ok].astype(int)
    if y.nunique() < 2 or len(X) < S.MIN_DAYS * S.SLOTS_PER_DAY:
        return None, {"status": "learning", "days": round(len(X) / S.SLOTS_PER_DAY)}
    cut = X.index.max() - pd.Timedelta(days=S.TEST_DAYS)
    tr = X.index <= cut
    m = HistGradientBoostingClassifier(**S.MODEL_PARAMS).fit(X[tr], y[tr])
    acc = float((m.predict(X[~tr]) == y[~tr].values).mean())
    final = HistGradientBoostingClassifier(**S.MODEL_PARAMS).fit(X, y)
    return final, {"status": "ready", "accuracy": round(acc, 3)}


def arrival_time(slots, weekend=False):
    """Typical time someone gets home (after being out 2 h+), as 'HH:MM', or None."""
    home = home_occupancy(slots).fillna(0)
    out_run = run_length(home)
    arrivals = home[(home == 1) & (home.shift(1) == 0) & (out_run.shift(1) >= 8)]
    if arrivals.empty:
        return None
    idx = arrivals.index
    sel = idx[idx.dayofweek.isin(S.WEEKEND_DAYS) == weekend]
    sel = sel[(sel.hour >= 12)]
    days = len(set(home.index.normalize()))
    if len(sel) < max(3, days * 0.2):
        return None
    minutes = np.median(sel.hour * 60 + sel.minute)
    minutes = int(round(minutes / 15) * 15)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
