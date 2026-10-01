"""
Smart Home AI service - Gradient Boosting (HistGradientBoostingClassifier)
Runs on the Raspberry Pi. Predicts each device's state 60 minutes ahead.

Commands:
    python smart_home_ai.py demo      # fill the database with 10 weeks of simulated data
    python smart_home_ai.py train     # train + evaluate + save one model per device (run nightly)
    python smart_home_ai.py predict   # make decisions for the next hour (run every 15 minutes)

Data contract (SQLite table `readings`, written by the MQTT ingest service):
    ts    INTEGER  unix seconds
    key   TEXT     e.g. 'bedroom/temp', 'bedroom/occ', 'bedroom/fan', 'override/bedroom_fan'
    value REAL     temperature in C, lux, occupancy 0/1, device state 0/1
"""
import json, sqlite3, sys, time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import f1_score, precision_score, recall_score

# ------------------------------------------------------------------ config
DB_PATH = Path("home.db")
MODEL_DIR = Path("models")
SLOT = "15min"
SLOTS_PER_DAY = 96
HORIZON = 4                 # predict 4 slots = 60 minutes ahead
ACT_AT = 0.80               # confidence to act automatically
SUGGEST_AT = 0.60           # confidence to send a suggestion to the web app
OVERRIDE_PAUSE_S = 2 * 3600 # after a manual override, AI leaves the device alone for 2 h

# lead_min: how many minutes before the predicted time the device is switched on
DEVICES = {
    "bedroom_fan":  dict(state="bedroom/fan",  occ="bedroom/occ", temp="bedroom/temp", lux="bedroom/lux", lead_min=15),
    "living_fan":   dict(state="living/fan",   occ="living/occ",  temp="living/temp",  lux="living/lux",  lead_min=15),
    "living_light": dict(state="living/light", occ="living/occ",  temp="living/temp",  lux="living/lux",  lead_min=0),
    "bedroom_light":dict(state="bedroom/light",occ="bedroom/occ", temp="bedroom/temp", lux="bedroom/lux", lead_min=0),
}
WEEKEND_DAYS = (4, 5)       # Friday, Saturday (Python: Monday=0)
TZ = "Asia/Hebron"          # hour-of-day features must use local time, not UTC


# ------------------------------------------------------------------ database
def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS readings (ts INTEGER, key TEXT, value REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON readings(ts)")
    return con


def to_local(ts):
    """unix seconds -> local wall-clock time (naive), so 07:00 means 07:00 at home."""
    return pd.DatetimeIndex(pd.to_datetime(ts, unit="s", utc=True)).tz_convert(TZ).tz_localize(None)


def load_slots(con, since_ts=0):
    """Raw readings -> one row per 15-minute slot, one column per key."""
    raw = pd.read_sql("SELECT ts, key, value FROM readings WHERE ts >= ? AND key NOT LIKE 'override/%'",
                      con, params=(since_ts,))
    if raw.empty:
        raise SystemExit("No readings in the database yet.")
    raw["ts"] = to_local(raw.ts)
    wide = raw.pivot_table(index="ts", columns="key", values="value", aggfunc="mean")
    slots = wide.resample(SLOT).mean()
    state_cols = [c for c in slots if c.endswith(("/occ", "/fan", "/light"))]
    slots[state_cols] = (slots[state_cols].ffill() >= 0.5).astype(int)   # on for most of the slot
    return slots.ffill()


# ------------------------------------------------------------------ features
def build_features(slots, cfg):
    s, occ, temp = slots[cfg["state"]], slots[cfg["occ"]], slots[cfg["temp"]]
    idx = slots.index
    minute_of_day = idx.hour * 60 + idx.minute
    X = pd.DataFrame({
        "h_sin": np.sin(minute_of_day / 1440 * 2 * np.pi),
        "h_cos": np.cos(minute_of_day / 1440 * 2 * np.pi),
        "dow": idx.dayofweek,
        "weekend": idx.dayofweek.isin(WEEKEND_DAYS).astype(int),
        "temp": temp, "lux": slots[cfg["lux"]], "occ": occ,
        "now": s, "lag1": s.shift(1), "lag2": s.shift(2),
        "yday": s.shift(SLOTS_PER_DAY), "lweek": s.shift(7 * SLOTS_PER_DAY),
        "occ_yday_target": occ.shift(SLOTS_PER_DAY - HORIZON),
        "occ_run": occ.groupby((occ != occ.shift()).cumsum()).cumcount(),
    }, index=idx)
    y = s.shift(-HORIZON)
    return X, y


def new_model():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                          class_weight="balanced", random_state=0)


# ------------------------------------------------------------------ train
def train():
    con = connect()
    slots = load_slots(con)
    MODEL_DIR.mkdir(exist_ok=True)
    report = {}
    for name, cfg in DEVICES.items():
        if cfg["state"] not in slots:
            print(f"skip {name}: no data"); continue
        X, y = build_features(slots, cfg)
        ok = X.notna().all(axis=1) & y.notna()
        X, y = X[ok], y[ok].astype(int)
        if y.nunique() < 2 or len(X) < 14 * SLOTS_PER_DAY:
            print(f"skip {name}: need at least 3 weeks of data with both ON and OFF"); continue

        # evaluate on the last 14 days (never seen in training)
        cut = X.index.max() - pd.Timedelta(days=14)
        tr, te = X.index <= cut, X.index > cut
        m = new_model().fit(X[tr], y[tr])
        p = m.predict(X[te]); yt = y[te].values
        change = X.now[te].values != yt
        near = change & ((p == yt) | (p == np.r_[yt[1:], yt[-1]]) | (p == np.r_[yt[0], yt[:-1]]))
        metrics = dict(
            f1=round(f1_score(yt, p), 3),
            precision=round(precision_score(yt, p, zero_division=0), 3),
            recall=round(recall_score(yt, p), 3),
            change_acc=round(float((p[change] == yt[change]).mean()), 3),
            change_acc_15min=round(float(near.sum() / change.sum()), 3),
            baseline_yesterday_f1=round(f1_score(yt, X.yday[te].astype(int)), 3),
        )
        # final model uses all data
        final = new_model().fit(X, y)
        joblib.dump(dict(model=final, features=list(X.columns), metrics=metrics,
                         sklearn=sklearn.__version__, trained=datetime.now().isoformat()),
                    MODEL_DIR / f"{name}.joblib")
        report[name] = metrics
        print(f"{name:14s} {metrics}")
    (MODEL_DIR / "report.json").write_text(json.dumps(report, indent=2))


# ------------------------------------------------------------------ predict
def overridden_recently(con, name, now_ts):
    row = con.execute("SELECT MAX(ts) FROM readings WHERE key = ?", (f"override/{name}",)).fetchone()
    return row[0] is not None and now_ts - row[0] < OVERRIDE_PAUSE_S


def predict(now_ts=None):
    con = connect()
    now_ts = now_ts or int(time.time())
    slots = load_slots(con, since_ts=now_ts - 8 * 86400)    # 8 days is enough for all lags
    slots = slots[slots.index <= to_local([now_ts])[0]]
    decisions = []
    for name, cfg in DEVICES.items():
        path = MODEL_DIR / f"{name}.joblib"
        if not path.exists() or cfg["state"] not in slots:
            continue
        bundle = joblib.load(path)
        X, _ = build_features(slots, cfg)
        x = X[bundle["features"]].iloc[[-1]]
        if x.isna().any(axis=None):
            continue
        p_on = float(bundle["model"].predict_proba(x)[0, 1])
        state_now = int(x["now"].iloc[0])
        target_time = x.index[0] + pd.Timedelta(minutes=15 * HORIZON)
        d = dict(device=name, p_on=round(p_on, 2), state_now=state_now,
                 predicted_for=target_time.isoformat(), action="none")
        if overridden_recently(con, name, now_ts):
            d["action"] = "paused_by_override"
        elif p_on >= ACT_AT and state_now == 0:
            # AI only switches ON automatically (pre-cooling / arrival). Switching OFF is done
            # by the waste rules (device on + empty room), so the AI can never cut something off wrongly.
            d.update(action="schedule_on",
                     execute_at=(target_time - pd.Timedelta(minutes=cfg["lead_min"])).isoformat())
        elif p_on >= SUGGEST_AT and state_now == 0:
            d["action"] = "suggest_on"
        elif p_on <= 1 - ACT_AT and state_now == 1:
            d["action"] = "suggest_off"
        decisions.append(d)
    dispatch(decisions)
    return decisions


def dispatch(decisions):
    """Send decisions to the rest of the system.
    - schedule_on        -> MQTT topic home/ai/schedule (the automation service executes it)
    - suggest_on / _off  -> Firebase /suggestions (the web app shows it)
    Replace the print with paho-mqtt / firebase-admin calls on the Pi."""
    for d in decisions:
        print(json.dumps(d))


# ------------------------------------------------------------------ demo data
def demo(days=70, seed=42):
    """Writes simulated readings (every 5 minutes) so the pipeline can be tested before the hardware is ready."""
    rng = np.random.default_rng(seed)
    midnight = pd.Timestamp.now(tz=TZ).normalize()
    rows = []
    for d in range(days):
        day = midnight - pd.Timedelta(days=days - d)
        day0 = int(day.timestamp())                           # local midnight in unix seconds
        weekend = day.weekday() in WEEKEND_DAYS
        j = lambda s: rng.normal(0, s)
        if not weekend:
            wake, sleep, out = 7 + j(.3), 23 + j(.5), [(8 + j(.3), 16.5 + j(.7))]
        else:
            wake, sleep = 9.5 + j(1), 23.8 + j(.6)
            out = [(12 + j(1), 17 + j(1))] if rng.random() < .5 else []
        if rng.random() < .07: out = []
        tmax, sunset = 31 + j(2.5), 18.3 + j(.15)
        st = dict(bedroom_fan=0, living_fan=0)
        room = "bedroom"
        for k in range(288):                                  # every 5 minutes
            h, ts = k / 12, day0 + k * 300
            home = not any(a <= h < b for a, b in out)
            asleep = h < wake or h >= sleep
            if not home: room = "out"
            elif asleep: room = "bedroom"
            elif k % 3 == 0: room = "bedroom" if rng.random() < .15 else "living"
            t_out = tmax - 7 * np.cos((h - 15) / 24 * 2 * np.pi) - 7
            lux = max(0, 800 * np.sin(np.clip((h - 6) / (sunset - 6), 0, 1) * np.pi)) + j(15)
            for r, t in (("bedroom", t_out + 1 + j(.4)), ("living", t_out + j(.4))):
                occ = int(room == r)
                f = f"{r}_fan"
                if occ and (t > 28.5 or (asleep and t > 27)) and rng.random() < .8: st[f] = 1
                elif not occ and st[f] and rng.random() < .8: st[f] = 0
                elif occ and t < 26.5: st[f] = 0
                light = int(occ and not asleep and lux < 150 and rng.random() < .95)
                rows += [(ts, f"{r}/temp", round(t, 2)), (ts, f"{r}/lux", round(max(lux, 0), 1)),
                         (ts, f"{r}/occ", occ), (ts, f"{r}/fan", st[f]), (ts, f"{r}/light", light)]
    con = connect()
    con.execute("DELETE FROM readings")
    con.executemany("INSERT INTO readings VALUES (?,?,?)", rows)
    con.commit()
    print(f"wrote {len(rows):,} simulated readings ({days} days) to {DB_PATH}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"demo": demo, "train": train, "predict": predict}.get(
        cmd, lambda: print(__doc__))()
