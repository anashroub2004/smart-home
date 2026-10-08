"""A year of the studio, evaluated honestly — walk-forward (the model never sees the week it is tested on).

    python -m ai.lifesim --days 365            # the data (once, ~40 s)
    python -m ai.experiment_year               # this (~10 min) -> ai/reports/year_evaluation.md / .json / .svg

For every week of the year (after the first 8):
    train on the 16 weeks BEFORE it (time-decay weights, exactly like the nightly training), test on that week.
Compared with:
    "same as yesterday"     the state at this time yesterday             (what a simple timer would do)
    "same as last week"     the state at this time a week ago             (a weekly timer)
    "all history"           the same model on everything so far, equal weights (does forgetting old habits help?)
Metrics per device: F1 (on-predictions), change timing (exact / within ±15 min), Brier (probability quality).
Results are also split by period (semester / exams / summer job / Ramadan ...) to show how it copes with change.
All numbers come from SIMULATED data (ai/personas/student_studio.toml) — the report must say so.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, f1_score, precision_score, recall_score

from . import settings as S
from . import store
from .features import CORE, build_features, load_slots, usable
from .spec import ai_devices
from .fit import fit_model
from .train import change_accuracy

REPORTS = Path(__file__).resolve().parent / "reports"


def walk_forward(con, config, diary=None, train_days=S.WINDOW_DAYS, compare_all=True, devices=None, log=print,
                 half_life=S.HALF_LIFE_DAYS, from_day=None):
    first, last = con.execute("SELECT MIN(ts), MAX(ts) FROM readings").fetchone()
    t0 = time.time()
    slots = load_slots(con, first, last)
    log(f"slots: {len(slots)} x {slots.shape[1]} ({time.time() - t0:.0f} s)")
    kinds = {}
    if diary:
        for r in diary:
            kinds[r["date"]] = ("ramadan" if "ramadan" in r["tags"].split() else r["kind"])
    specs = ai_devices(config)
    if devices:
        specs = {d: s for d, s in specs.items() if d in devices}
    start = slots.index.min().normalize() + pd.Timedelta(days=train_days)
    if from_day is not None:
        start = max(start, pd.Timestamp(from_day))
    end = slots.index.max().normalize()
    weeks = []
    w0 = start
    while w0 + pd.Timedelta(days=7) <= end:
        weeks.append(w0)
        w0 += pd.Timedelta(days=7)
    rows = []
    for dev, spec in specs.items():
        X, y = build_features(slots, spec)
        ok = X[CORE].notna().all(axis=1) & y.notna() & usable(slots)
        X, y = X[ok], y[ok].astype(int)
        t1 = time.time()
        for wk in weeks:
            te = (X.index >= wk) & (X.index < wk + pd.Timedelta(days=7))
            tr = (X.index >= wk - pd.Timedelta(days=train_days)) & (X.index < wk)
            if te.sum() < 300 or tr.sum() < 1000 or y[tr].nunique() < 2:
                continue
            yt = y[te].values
            res = {"device": dev, "kind_dev": spec.kind, "week": wk.strftime("%Y-%m-%d"), "positives": int(yt.sum()),
                   "period": kinds.get(wk.strftime("%Y-%m-%d"), "")}
            ours = fit_predict(X, y, tr, te, spec, wk, half_life=half_life)
            res.update(score("ours", yt, ours, X.now[te].values))
            res.update(score("yesterday", yt, X.yday[te].fillna(0).astype(int).values.astype(float), X.now[te].values))
            res.update(score("last_week", yt, X.lweek[te].fillna(0).astype(int).values.astype(float), X.now[te].values))
            if compare_all:
                tr_all = X.index < wk
                res.update(score("all_history", yt, fit_predict(X, y, tr_all, te, spec, wk, decay=False), X.now[te].values))
            rows.append(res)
        log(f"{dev:16s} {len([r for r in rows if r['device'] == dev])} weeks  ({time.time() - t1:.0f} s)")
    return rows


def fit_predict(X, y, tr, te, spec, now, decay=True, half_life=S.HALF_LIFE_DAYS):
    if decay:
        age = (now - X.index[tr]).total_seconds() / 86400
        cost = 1 + np.log10(1 + max(0.0, spec.watts) / 10)
        w = np.power(0.5, np.asarray(age) / half_life) * np.where(np.asarray(y[tr]) == 0, cost, 1.0)
    else:
        w = np.ones(int(tr.sum()))
    m = fit_model(X[tr], y[tr], sample_weight=w)
    return m.predict_proba(X[te])[:, 1]


def score(name, yt, proba, now_state):
    p = (proba >= 0.5).astype(int)
    exact, near = change_accuracy(now_state, yt, p)
    out = {f"{name}_f1": round(f1_score(yt, p, zero_division=0), 4),
           f"{name}_precision": round(precision_score(yt, p, zero_division=0), 4),
           f"{name}_recall": round(recall_score(yt, p, zero_division=0), 4),
           f"{name}_within15": near, f"{name}_exact": exact}
    if name in ("ours", "all_history"):
        out[f"{name}_brier"] = round(float(brier_score_loss(yt, proba)), 4)
    return out


def summarize(rows):
    df = pd.DataFrame(rows)
    df = df[df.positives >= 8]                         # weeks where the device was actually used
    methods = [m for m in ("ours", "all_history", "last_week", "yesterday") if f"{m}_f1" in df]
    per_dev = df.groupby("device").agg(weeks=("week", "count"), **{
        f"{m}_{k}": (f"{m}_{k}", "mean") for m in methods for k in ("f1", "within15")})
    overall = {m: {"f1": round(float(df[f"{m}_f1"].mean()), 3),
                   "within15": round(float(df[f"{m}_within15"].dropna().mean()), 3),
                   "exact": round(float(df[f"{m}_exact"].dropna().mean()), 3)} for m in methods}
    by_period = df.groupby("period")[[f"{m}_f1" for m in methods]].mean().round(3) if "period" in df else None
    weekly = df.groupby("week")[[f"{m}_f1" for m in methods]].mean()
    return df, per_dev, overall, by_period, weekly, methods


NAMES = {"ours": "Our model (sliding window + time decay)", "all_history": "Same model, all history, equal weights",
         "last_week": "Same as last week", "yesterday": "Same as yesterday"}
COLORS = {"ours": "#2563EB", "all_history": "#A855F7", "last_week": "#F59E0B", "yesterday": "#9CA3AF"}


def svg_weekly(weekly, methods, periods):
    W, H, L, B, T = 900, 330, 50, 46, 30
    n = max(1, len(weekly) - 1)
    X = lambda i: L + (W - L - 20) * i / n
    Y = lambda v: H - B - (H - B - T) * v
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Arial" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="#fff"/>',
           f'<text x="{L}" y="18" font-size="14" font-weight="bold">Weekly F1 on unseen weeks (mean of all devices)</text>']
    shade = {"exams": "#FEE2E2", "ramadan": "#DCFCE7", "summer_job": "#E0F2FE", "break": "#F3F4F6", "free": "#FEF9C3"}
    for i, wk in enumerate(weekly.index):
        k = periods.get(wk, "")
        if k in shade:
            out.append(f'<rect x="{X(i) - (W - L - 20) / n / 2:.1f}" y="{T}" width="{(W - L - 20) / n:.1f}" '
                       f'height="{H - B - T}" fill="{shade[k]}"/>')
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        out.append(f'<line x1="{L}" x2="{W - 20}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#eee"/>'
                   f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#666">{v:.2f}</text>')
    for i, wk in enumerate(weekly.index):
        if i % 6 == 0:
            out.append(f'<text x="{X(i):.1f}" y="{H - B + 16}" text-anchor="middle" fill="#666">{wk[5:]}</text>')
    for m in methods:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(weekly[f"{m}_f1"].values) if not np.isnan(v))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{COLORS[m]}" stroke-width="{2.8 if m == "ours" else 1.8}"/>')
    x = L
    for m in methods:
        out.append(f'<rect x="{x}" y="{H - 16}" width="12" height="4" fill="{COLORS[m]}"/><text x="{x + 16}" y="{H - 11}">{NAMES[m]}</text>')
        x += 16 + 7 * len(NAMES[m]) + 18
    lx = L
    for k, c in shade.items():
        out.append(f'<rect x="{lx}" y="{T - 4}" width="10" height="10" fill="{c}" stroke="#ddd"/>'
                   f'<text x="{lx + 14}" y="{T + 5}" fill="#666">{k}</text>')
        lx += 14 + 8 * len(k) + 14
    out.append("</svg>")
    return "".join(out)


def report(rows, summary_data, out_stem):
    df, per_dev, overall, by_period, weekly, methods = summarize(rows)
    periods = {w: p for w, p in zip(df.week, df.period)}
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    out_stem.with_suffix(".svg").write_text(svg_weekly(weekly, methods, periods), encoding="utf-8")
    pct = lambda v: "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.0f}%"
    md = ["# A year of the studio — walk-forward evaluation", "",
          f"Simulated data: {summary_data.get('days', '?')} days of `{Path(summary_data.get('persona', 'persona')).name}`, "
          f"{summary_data.get('rows', '?'):,} readings, {summary_data.get('distinct_day_types', '?')} different kinds of day, "
          f"{summary_data.get('unusual_days_pct', '?')}% unusual days, {summary_data.get('manual_per_day', '?')} manual presses per day.",
          f"Training window **{summary_data.get('window', S.WINDOW_DAYS)} days**, time-decay half-life "
          f"**{summary_data.get('half_life', S.HALF_LIFE_DAYS)} days**"
          + (f", weeks from **{summary_data['from_day']}** only (held-out: settings were chosen on earlier weeks)"
             if summary_data.get("from_day") else "") + ".",
          "", "Every week is predicted by a model trained only on the weeks before it (the model never sees the test week).",
          "Weeks in which a device was used less than 2 hours are left out of the averages.", "",
          "## Overall", "", "| Method | F1 | Change within ±15 min | Exact change slot |", "|---|---|---|---|"]
    for m in methods:
        o = overall[m]
        md.append(f"| {NAMES[m]} | **{o['f1']:.3f}** | {pct(o['within15'])} | {pct(o['exact'])} |" if m == "ours" else
                  f"| {NAMES[m]} | {o['f1']:.3f} | {pct(o['within15'])} | {pct(o['exact'])} |")
    md += ["", "## Per device (mean over the weeks)", "",
           "| Device | Weeks | " + " | ".join(f"F1 {NAMES[m].split(' (')[0]}" for m in methods) + " |",
           "|---|---|" + "---|" * len(methods)]
    for dev, r in per_dev.iterrows():
        md.append(f"| {dev} | {int(r.weeks)} | " + " | ".join(f"{r[f'{m}_f1']:.3f}" for m in methods) + " |")
    if by_period is not None:
        md += ["", "## By period (does it cope with change?)", "",
               "| Period | " + " | ".join(NAMES[m].split(" (")[0] for m in methods) + " |", "|---|" + "---|" * len(methods)]
        for p, r in by_period.iterrows():
            md.append(f"| {p or '—'} | " + " | ".join(f"{r[f'{m}_f1']:.3f}" for m in methods) + " |")
    md += ["", f"![weekly F1]({out_stem.with_suffix('.svg').name})", ""]
    out_stem.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    out_stem.with_suffix(".json").write_text(json.dumps({"overall": overall, "rows": rows}, indent=1), encoding="utf-8")
    return overall, per_dev, by_period


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(S.DATA_DIR / "persona_year.db"))
    ap.add_argument("--config", default=str(S.CONFIG_PATH))
    ap.add_argument("--no-all-history", action="store_true", help="skip the 'all history' comparison (2x faster)")
    ap.add_argument("--devices", nargs="*", help="only these devices")
    ap.add_argument("--window", type=int, default=S.WINDOW_DAYS, help="training window in days")
    ap.add_argument("--half-life", type=float, default=S.HALF_LIFE_DAYS, help="time-decay half-life in days")
    ap.add_argument("--from", dest="from_day", default=None, help="evaluate only weeks from this date (held-out part)")
    ap.add_argument("--name", default="year_evaluation", help="report file name")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        raise SystemExit(f"{db} not found — first run:  python -m ai.lifesim --days 365")
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    cfg = cfg.get("config", cfg)
    con = store.connect(db)
    diary_path = db.with_suffix(".diary.csv")
    diary = list(csv.DictReader(open(diary_path, encoding="utf-8"))) if diary_path.exists() else None
    summary_path = db.with_suffix(".summary.json")
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    t0 = time.time()
    rows = walk_forward(con, cfg, diary, train_days=a.window, compare_all=not a.no_all_history, devices=a.devices,
                        half_life=a.half_life, from_day=a.from_day)
    summary = dict(summary, window=a.window, half_life=a.half_life, from_day=a.from_day)
    overall, per_dev, by_period = report(rows, summary, REPORTS / a.name)
    print(json.dumps(overall, indent=2))
    print(per_dev.round(3).to_string())
    if by_period is not None:
        print(by_period.to_string())
    print(f"\nreport: {REPORTS / (a.name + '.md')}  ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
