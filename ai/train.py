"""Nightly training (cron 03:00 on the Pi).

For every AI device (from /config):
  1. last 16 weeks of 15-minute slots (Away / vacation slots removed, AI mistakes counted as OFF)
  2. chronological split: the last 14 days are the test set — never a random split
  3. sample weights = time decay (half-life 14 days) x energy cost (mistakes on power-hungry devices cost more)
  4. metrics: F1, precision, recall, exact change time, change within ±15 min, baseline "same as yesterday",
     calibration (does 80% really mean 80%?), Brier score
  5. explanations (permutation importance per feature group) and drift (routine change)
  6. final model on the whole window -> ai/models/<device>.joblib
Plus: the presence model, power baselines for fault detection, and "what your home has learned".
"""
import json
import math
import time
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import brier_score_loss, f1_score, precision_score, recall_score

from . import anomaly, drift, explain, habits, presence
from . import settings as S
from . import store
from .fit import fit_model
from .features import CORE, build_features, load_slots, local_now, usable
from .spec import ai_devices, energy_devices


def sample_weights(index, y, watts, now):
    age_days = (now - index).total_seconds() / 86400
    w = np.power(0.5, np.asarray(age_days) / S.HALF_LIFE_DAYS)
    cost = 1 + math.log10(1 + max(0.0, watts) / 10)           # 1.0 for small loads, ~3.2 for a 1.5 kW AC
    return w * np.where(np.asarray(y) == 0, cost, 1.0)


def change_accuracy(now_state, yt, p):
    change = now_state != yt
    if not change.any():
        return None, None
    near = change & ((p == yt) | (p == np.r_[yt[1:], yt[-1]]) | (p == np.r_[yt[0], yt[:-1]]))
    return round(float((p[change] == yt[change]).mean()), 3), round(float(near.sum() / change.sum()), 3)


def calibration(yt, proba, bins=5):
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (proba >= a) & ((proba < b) if b < 1 else (proba <= b))
        if m.sum() >= 10:
            out.append([round(float(proba[m].mean()), 3), round(float(yt[m].mean()), 3), int(m.sum())])
    return out


def preferred_levels(slots, spec):
    """Most used level per 2-hour block while the device is on -> {"9": 70, ...}."""
    if not spec.levels or spec.level_key not in slots or spec.state not in slots:
        return {}
    on = slots[spec.state] == 1
    lv = slots.loc[on, spec.level_key].dropna()
    if lv.empty:
        return {}
    snapped = lv.apply(lambda v: min(spec.levels, key=lambda s: abs(s - v)))
    blocks = (lv.index.hour // S.ADAPT_BUCKET_H)
    return {str(int(b)): int(g.mode().iloc[0]) for b, g in snapped.groupby(blocks)}


def data_source(con, window_start_ts, now_ts):
    if store.get_meta(con, "all_simulated") == "1":       # the simulator records its own (simulated) readings
        return "simulated"
    synth_until = store.get_meta(con, "synth_until")
    if synth_until is None or float(synth_until) < window_start_ts:
        return "real"
    return "simulated" if float(synth_until) >= now_ts - 86400 else "mixed"


def train_device(slots, spec, now):
    X, y = build_features(slots, spec)
    ok = X[CORE].notna().all(axis=1) & y.notna() & usable(slots)
    X, y = X[ok], y[ok].astype(int)
    days = len(X) / S.SLOTS_PER_DAY
    if y.nunique() < 2 or days < S.MIN_DAYS:
        return None, {"status": "learning", "days": int(days)}
    cut = X.index.max() - pd.Timedelta(days=S.TEST_DAYS)
    tr, te = X.index <= cut, X.index > cut
    w = sample_weights(X.index, y, spec.watts, now)
    m = fit_model(X[tr], y[tr], sample_weight=w[tr])
    proba = m.predict_proba(X[te])[:, 1]
    p = (proba >= 0.5).astype(int)
    yt = y[te].values
    exact, near = change_accuracy(X.now[te].values, yt, p)
    d = drift.detect(X.index[te].values, yt, p)
    metrics = dict(
        status="relearning" if d["drift"] else "ready", days=int(days),
        f1=round(f1_score(yt, p, zero_division=0), 3),
        precision=round(precision_score(yt, p, zero_division=0), 3),
        recall=round(recall_score(yt, p, zero_division=0), 3),
        exact=exact, within_15=near,
        baseline_f1=round(f1_score(yt, X.yday[te].astype(int), zero_division=0), 3),
        brier=round(float(brier_score_loss(yt, proba)), 4),
        calibration=calibration(yt, proba),
        importance=explain.group_importance(m, X[te], y[te]),
        drift=d,
        features=list(X.columns),
    )
    final = fit_model(X, y, sample_weight=w)
    bundle = dict(model=final, features=list(X.columns), typical=explain.typical_values(X[X.index > cut]),
                  metrics=metrics, drift=d["drift"], levels=preferred_levels(slots, spec),
                  sklearn=sklearn.__version__, trained=datetime.now().isoformat(timespec="seconds"))
    return bundle, metrics


def door_slots(con, since_ts):
    raw = pd.read_sql("SELECT ts FROM readings WHERE key = 'door/entry' AND ts >= ?", con, params=(int(since_ts),))
    if raw.empty:
        return None
    from .features import to_local
    s = pd.Series(1.0, index=to_local(raw.ts)).resample(S.SLOT).max()
    return s.rolling(2, min_periods=1).max()                 # an entry counts for 30 minutes


def train_all(config, con, model_dir=None, now_ts=None, labels=None, quiet=False):
    model_dir = model_dir or S.MODEL_DIR
    model_dir.mkdir(parents=True, exist_ok=True)
    now_ts = int(now_ts or time.time())
    since = now_ts - S.WINDOW_DAYS * 86400
    store.prune(con, now_ts)
    slots = load_slots(con, since_ts=since - 8 * 86400, until_ts=now_ts)   # +8 days so lags exist at the start
    now = local_now(now_ts)
    specs = ai_devices(config)
    labels = labels or {d: s.name for d, s in specs.items()}
    report = {"trained_at": int(time.time() * 1000), "model": "Gradient Boosting",
              "data_source": data_source(con, since, now_ts), "devices": {}}
    if slots.empty:
        report["status"] = "learning"
        (model_dir / "report.json").write_text(json.dumps(report, indent=2))
        return report
    slots = slots[slots.index >= local_now(since - 8 * 86400)]
    for dev, spec in specs.items():
        if spec.state not in slots:
            report["devices"][dev] = {"status": "learning", "days": 0}
            continue
        bundle, metrics = train_device(slots, spec, now)
        report["devices"][dev] = {k: v for k, v in metrics.items() if k != "features"}
        if bundle:
            joblib.dump(bundle, model_dir / f"{dev}.joblib")
        if not quiet:
            if bundle:
                print(f"{dev:16s} {metrics['status']:10s} f1={metrics['f1']} (yesterday baseline {metrics['baseline_f1']})  "
                      f"exact={metrics['exact']}  ±15min={metrics['within_15']}  brier={metrics['brier']}")
            else:
                print(f"{dev:16s} learning: {metrics['days']}/{S.MIN_DAYS} days of data")
    # learned habits (questions about NOW: light on when you walk in? light off while you rest?)
    use = slots[usable(slots) & (slots.index >= local_now(since))]
    learned = habits.learn(use, specs, now)
    learned["self_off"] = habits.self_off(con, use, specs, now_ts)
    habits.save(model_dir / "habits.json", learned)
    # presence
    doors = door_slots(con, since)
    pm, pmeta = presence.train(slots, doors)
    if pm is not None:
        joblib.dump(dict(model=pm, sklearn=sklearn.__version__), model_dir / "presence.joblib")
    arrival = presence.arrival_time(slots)
    pmeta["arrival_weekday"] = arrival
    pmeta["arrival_weekend"] = presence.arrival_time(slots, weekend=True)
    report["presence"] = pmeta
    # power baselines (fault detection)
    baselines = anomaly.learn_baselines(con, list(energy_devices(config)), since)
    (model_dir / "baselines.json").write_text(json.dumps(baselines, indent=2))
    report["wear"] = {d: b["wear"] for d, b in baselines.items() if b.get("wear", 0) >= S.WEAR_RISE}
    report["learned"] = explain.learned_sentences(slots[slots.index >= local_now(since)], specs, labels, arrival)
    ready = [m for m in report["devices"].values() if m.get("status") in ("ready", "relearning") and m.get("within_15") is not None]
    report["status"] = "ready" if ready else "learning"
    if ready:
        report["summary"] = dict(
            within_15=round(float(np.mean([m["within_15"] for m in ready])), 3),
            exact=round(float(np.mean([m["exact"] for m in ready if m["exact"] is not None])), 3),
            f1=round(float(np.mean([m["f1"] for m in ready])), 3),
            baseline_f1=round(float(np.mean([m["baseline_f1"] for m in ready])), 3),
        )
    (model_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    if not quiet and ready:
        s = report["summary"]
        print(f"overall: within ±15 min {s['within_15']:.0%}, exact {s['exact']:.0%}, "
              f"F1 {s['f1']} vs yesterday-baseline {s['baseline_f1']}  ({report['data_source']} data)")
    return report


def load_bundle(model_dir, dev):
    path = model_dir / f"{dev}.joblib"
    return joblib.load(path) if path.exists() else None


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default
