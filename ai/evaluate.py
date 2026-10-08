"""Experiments for the graduation report — run:  python -m ai.run evaluate

1. Routine change (summer holiday in the last 14 days): does the sliding window + time-decay weighting
   adapt faster than training on everything with equal weights?
2. Energy-scaled thresholds for typical devices.
Writes ai/models/evaluation.md (all numbers come from SIMULATED data — say so in the report).
"""
import time

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score

from . import energy, store, synth
from . import settings as S
from .features import CORE, build_features, load_slots, local_now, usable
from .spec import ai_devices
from .fit import fit_model
from .train import change_accuracy, sample_weights


def drift_experiment(config, seeds=(1, 2, 3), change_days=14, test_days=7, total_days=98):
    variants = {"all history, equal weights": (None, False),
                f"{S.WINDOW_DAYS // 7}-week window, equal weights": (S.WINDOW_DAYS, False),
                f"{S.WINDOW_DAYS // 7}-week window + time decay (ours)": (S.WINDOW_DAYS, True)}
    scores = {v: [] for v in variants}
    near = {v: [] for v in variants}
    for seed in seeds:
        con = store.connect(":memory:")
        now = int(time.time()) // 900 * 900
        synth.fill(con, config, days=total_days, end_ts=now, seed=seed, routine_change_days=change_days)
        slots = load_slots(con, now - (total_days + 1) * 86400, now)
        nowl = local_now(now)
        for dev, spec in ai_devices(config).items():
            X, y = build_features(slots, spec)
            ok = X[CORE].notna().all(axis=1) & y.notna() & usable(slots)
            X, y = X[ok], y[ok].astype(int)
            cut = X.index.max() - pd.Timedelta(days=test_days)
            te = X.index > cut
            if y[te].sum() < 5:
                continue
            for name, (window, decay) in variants.items():
                tr = X.index <= cut
                if window:
                    tr &= X.index > cut - pd.Timedelta(days=window - S.TEST_DAYS)
                w = sample_weights(X.index[tr], y[tr], spec.watts, nowl) if decay else None
                m = fit_model(X[tr], y[tr], sample_weight=w)
                p = m.predict(X[te])
                scores[name].append(f1_score(y[te], p, zero_division=0))
                near[name].append(change_accuracy(X.now[te].values, y[te].values, p)[1] or 0)
    return {k: (round(float(np.mean(v)), 3), round(float(np.mean(near[k])), 3)) for k, v in scores.items() if v}


def run(config, model_dir):
    lines = ["# AI evaluation (simulated data)", "",
             "## 1. Routine change: last 14 days follow a new routine (e.g. summer holiday)", "",
             "| training | F1 on the last 7 days | changes caught within ±15 min |", "|---|---|---|"]
    res = drift_experiment(config)
    for k, (f1, nr) in res.items():
        lines.append(f"| {k} | {f1} | {nr:.0%} |")
    lines += ["", "Read honestly: when the differences are within ~0.02 they are noise. The features "
              "'yesterday' and 'last week' already follow a new routine within days, so the window and the "
              "time decay mostly matter for longer changes; drift detection (status 'relearning', higher "
              "thresholds) is what keeps the AI careful meanwhile."]
    lines += ["", "## 2. Energy-scaled thresholds (ai_act_at = 0.80)", "", "| device | watts | acts at | suggests at |",
              "|---|---|---|---|"]
    for name, w in [("LED light", 1.2), ("DC fan (model)", 2.4), ("TV", 100), ("Heater", 800), ("Air conditioner", 1500)]:
        a = energy.energy_threshold(w)
        lines.append(f"| {name} | {w} | {a:.2f} | {max(0.5, a - S.SUGGEST_GAP):.2f} |")
    text = "\n".join(lines) + "\n"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "evaluation.md").write_text(text, encoding="utf-8")
    print(text)
    return res
