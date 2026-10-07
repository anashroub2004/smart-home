"""Explanations (approved addition 8.1).

- Global, per device (nightly): permutation importance on the test period, summed per feature group
  -> "time 41%, habit 33%, presence 18%, temperature 8%". Shown in Insights.
- Local, per decision (every plan): replace one feature group at a time by its typical value and see how
  much the probability moves; the biggest movers become the "why" sentence.
- "What your home has learned": plain sentences mined from the data (usual times, temperatures).
"""
import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from . import settings as S
from .features import feature_group


def group_importance(model, X, y, seed=0):
    if len(X) < 50 or y.nunique() < 2:
        return {}
    r = permutation_importance(model, X, y, n_repeats=5, random_state=seed, scoring="roc_auc")
    groups = {}
    for name, v in zip(X.columns, r.importances_mean):
        g = feature_group(name)
        groups[g] = groups.get(g, 0.0) + max(0.0, float(v))
    total = sum(groups.values())
    if total <= 0:
        return {}
    return {g: round(v / total, 3) for g, v in sorted(groups.items(), key=lambda kv: -kv[1]) if v / total >= 0.01}


def typical_values(X):
    """Median of each feature (for numeric) — the 'neutral' value used in local explanations."""
    return {c: float(np.nanmedian(X[c].values)) if X[c].notna().any() else np.nan for c in X.columns}


def local_contributions(model, x, typical):
    """x: one-row DataFrame. -> {group: change in p_on when that group is set to typical values}."""
    base = float(model.predict_proba(x)[0, 1])
    by_group = {}
    for c in x.columns:
        by_group.setdefault(feature_group(c), []).append(c)
    out = {}
    for g, cols in by_group.items():
        z = x.copy()
        for c in cols:
            z[c] = typical.get(c, np.nan)
        out[g] = round(base - float(model.predict_proba(z)[0, 1]), 3)
    return out


def why_text(spec, x, contrib, p, action, label_ctx=None):
    """Short English sentence for the web app, built from the real values that pushed the decision.
    No percentage in it: the app and the event log show the confidence next to it."""
    row = x.iloc[0]
    parts = []
    want_on = action in ("schedule_on", "keep_on", "suggest_on")
    ranked = sorted(contrib.items(), key=lambda kv: -(kv[1] if want_on else -kv[1]))
    skip = {"light": {"temperature"}, "thermal": {"light"}}.get(spec.kind, set())
    temp, lux = row.get("temp"), row.get("lux")
    for g, v in ranked:
        if g in skip or (want_on and v <= 0.02) or (not want_on and v >= -0.02):
            continue
        # only mention a value when it really points the same way as the decision
        if g == "habit":
            if want_on:
                if row.get("yday") == 1 and row.get("lweek") == 1:
                    parts.append("on at this time yesterday and last week")
                elif row.get("yday") == 1:
                    parts.append("on at this time yesterday")
                elif row.get("lweek") == 1:
                    parts.append("on at this time last week")
                elif row.get("now") == 1:
                    parts.append("it is on now")
                else:
                    parts.append("your usual routine")
            elif row.get("yday") == 0 and row.get("lweek") == 0:
                parts.append("usually off at this time")
        elif g == "time":
            parts.append("your usual time" if want_on else "not your usual time")
        elif g == "presence":
            if want_on:
                parts.append("someone is in the room" if row.get("occ") == 1 else
                             "someone is home" if row.get("home_occ") == 1 else "you are usually here by then")
            elif row.get("occ") != 1:
                parts.append("nobody is in the room")
        elif g == "temperature" and pd.notna(temp):
            limit = spec.temp_on
            if want_on and (limit is None or temp > limit - 1):
                parts.append(f"room {temp:.1f}°C")
            elif not want_on and limit is not None and temp <= limit:
                parts.append(f"room cooled to {temp:.1f}°C")
        elif g == "light" and pd.notna(lux):
            if want_on and lux < 150:
                parts.append(f"dark ({lux:.0f} lx)")
            elif not want_on and lux >= 150:
                parts.append(f"bright ({lux:.0f} lx)")
        elif g == "other devices" and want_on:
            on = [c[4:] for c in x.columns if c.startswith("ctx_") and row.get(c) == 1]
            if on:
                names = [(label_ctx or {}).get(d, d) for d in on]
                parts.append(f"{', '.join(names)} on")
        if len(parts) == 3:
            break
    text = " · ".join(dict.fromkeys(parts)) or ("your usual routine" if want_on else "usually off by now")
    return f"{text[0].upper()}{text[1:]}"          # the confidence is shown separately (bar / "84% sure")


# ---------------------------------------------------------------- learned patterns
def _fmt(minutes):
    minutes = int(round(minutes / 15) * 15) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def usual_on_time(state, weekend=False, after_hour=12):
    """Median time a device is switched on in the afternoon/evening, if it happens on most days."""
    s = state.fillna(0)
    ons = s[(s == 1) & (s.shift(1) == 0)]
    idx = ons.index[(ons.index.hour >= after_hour) & (ons.index.dayofweek.isin(S.WEEKEND_DAYS) == weekend)]
    if len(idx) == 0:
        return None
    first = pd.Series(idx.hour * 60 + idx.minute, index=idx).groupby(idx.normalize()).min()
    n_days = (s.index.normalize().unique().dayofweek.isin(S.WEEKEND_DAYS) == weekend).sum()
    if len(first) < max(3, 0.5 * n_days):
        return None
    return _fmt(float(first.median()))


def usual_switch_temp(state, temp):
    s, t = state.fillna(0), temp
    ons = (s == 1) & (s.shift(1) == 0)
    vals = t[ons].dropna()
    return round(float(vals.median()), 1) if len(vals) >= 5 else None


def learned_sentences(slots, specs, labels, arrival=None):
    out = []
    if arrival:
        out.append(f"You usually get home around **{arrival}** on weekdays.")
    for dev, spec in specs.items():
        if spec.state not in slots:
            continue
        s = slots[spec.state]
        label = labels.get(dev, spec.name).lower()
        if spec.kind == "thermal" and spec.temp and spec.temp in slots:
            t = usual_switch_temp(s, slots[spec.temp])
            if t:
                out.append(f"You turn the {label} on when the room is above **{t}°C**.")
                continue
        when = usual_on_time(s)
        if when:
            out.append(f"You usually switch the {label} on around **{when}** on weekdays.")
    return out[:6]
