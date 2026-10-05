"""
Smart Home AI service - Gradient Boosting (HistGradientBoostingClassifier)
Runs on the Raspberry Pi. Predicts each device's state 60 minutes ahead.

The device list is NOT hard-coded: it is read from the home configuration (docs/seed.json or /config).
Every device with control.ai = true and power = "write" gets its own model automatically.
Monitor-only devices (power = "read", e.g. a TV) are used as extra hints for devices in the same room.

Commands:
    python smart_home_ai.py demo      # fill the database with 10 weeks of simulated data
    python smart_home_ai.py train     # train + evaluate + save one model per AI device (run nightly)
    python smart_home_ai.py predict   # make decisions for the next hour (run every 15 minutes)
    python smart_home_ai.py devices   # show which devices the AI will handle and which features each uses

Options:
    --config PATH   home configuration JSON (default: ../docs/seed.json)
    --db PATH       SQLite database (default: home.db)

Data contract (SQLite table `readings`, written by the MQTT ingest service on the Pi):
    ts    INTEGER  unix seconds
    key   TEXT     '<room>/temp' | '<room>/lux' | '<room>/occ' | 'device/<id>' | 'override/<id>'
    value REAL     temperature in C, lux, occupancy 0/1, device state 0/1
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import f1_score, precision_score, recall_score

# ------------------------------------------------------------------ settings
HERE = Path(__file__).resolve().parent
DB_PATH = Path("home.db")
MODEL_DIR = Path("models")
CONFIG_PATH = HERE.parent / "docs" / "seed.json"
SLOT = "15min"
SLOTS_PER_DAY = 96
HORIZON = 4                 # predict 4 slots = 60 minutes ahead
MIN_DAYS = 21               # a device is "learning" until it has 3 weeks of data
ACT_AT = 0.80               # confidence to act automatically (overridden by config thresholds)
SUGGEST_AT = 0.60           # confidence to send a suggestion to the web app
OVERRIDE_PAUSE_S = 2 * 3600 # after a manual override, AI leaves the device alone for 2 h
WEEKEND_DAYS = (4, 5)       # Friday, Saturday (Python: Monday=0)
TZ = "Asia/Hebron"          # hour-of-day features must use local time, not UTC


# ------------------------------------------------------------------ devices from the configuration
def load_config(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("config", data)          # accepts docs/seed.json or a raw /config export


def ai_devices(config):
    """device id -> which readings feed its model. Built from the config, so new devices just work."""
    rooms = config["rooms"]
    climate_rooms = [r for r, rc in rooms.items() if "temp" in (rc.get("sensors") or [])]
    lux_rooms = [r for r, rc in rooms.items() if "lux" in (rc.get("sensors") or [])]
    out = {}
    for rid, room in rooms.items():
        sensors = room.get("sensors") or []
        devices = room.get("devices") or {}
        context = [f"device/{d}" for d, c in devices.items()
                   if (c.get("caps") or {}).get("power") == "read"]       # monitor-only hints (TV on …)
        for dev, cfg in devices.items():
            caps, ctrl = cfg.get("caps") or {}, cfg.get("control") or {}
            if caps.get("power") != "write" or not ctrl.get("ai"):
                continue
            thermal = cfg.get("icon") in ("fan", "ac") or (cfg.get("rules") or {}).get("follow_temp")
            out[dev] = dict(
                state=f"device/{dev}",
                occ=f"{rid}/occ" if "occ" in sensors else None,
                # a room without its own sensor borrows the nearest one we have
                temp=f"{rid}/temp" if "temp" in sensors else (f"{climate_rooms[0]}/temp" if climate_rooms else None),
                lux=f"{rid}/lux" if "lux" in sensors else (f"{lux_rooms[0]}/lux" if lux_rooms else None),
                context=context,
                lead_min=15 if thermal else 0,       # switch thermal devices on a bit early (pre-cooling)
                room=rid, icon=cfg.get("icon", "generic"), added_at=cfg.get("added_at"),
            )
    return out


# ------------------------------------------------------------------ database
def connect(db_path):
    con = sqlite3.connect(db_path)
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
    state_cols = [c for c in slots if c.endswith("/occ") or c.startswith("device/")]
    slots[state_cols] = (slots[state_cols].ffill() >= 0.5).astype(int)   # on for most of the slot
    return slots.ffill()


# ------------------------------------------------------------------ features
def _col(slots, key):
    return slots[key] if key and key in slots else pd.Series(np.nan, index=slots.index)


def build_features(slots, cfg):
    s = slots[cfg["state"]]
    occ, temp = _col(slots, cfg["occ"]), _col(slots, cfg["temp"])
    idx = slots.index
    minute_of_day = idx.hour * 60 + idx.minute
    feats = {
        "h_sin": np.sin(minute_of_day / 1440 * 2 * np.pi),
        "h_cos": np.cos(minute_of_day / 1440 * 2 * np.pi),
        "dow": idx.dayofweek,
        "weekend": idx.dayofweek.isin(WEEKEND_DAYS).astype(int),
        "temp": temp, "lux": _col(slots, cfg["lux"]), "occ": occ,
        "now": s, "lag1": s.shift(1), "lag2": s.shift(2),
        "yday": s.shift(SLOTS_PER_DAY), "lweek": s.shift(7 * SLOTS_PER_DAY),
        "occ_yday_target": occ.shift(SLOTS_PER_DAY - HORIZON),
        "occ_run": occ.groupby((occ != occ.shift()).cumsum()).cumcount() if occ.notna().any() else occ,
    }
    for c in cfg["context"]:                       # e.g. "TV is on" -> someone is in the living room
        feats["ctx_" + c.split("/", 1)[1]] = _col(slots, c)
    X = pd.DataFrame(feats, index=idx)             # HistGradientBoosting handles missing values (NaN) itself
    y = s.shift(-HORIZON)
    return X, y


CORE = ["now", "lag1", "lag2", "yday", "lweek"]


def new_model():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                          class_weight="balanced", random_state=0)


# ------------------------------------------------------------------ train
def train(config, db_path):
    con = connect(db_path)
    slots = load_slots(con)
    MODEL_DIR.mkdir(exist_ok=True)
    report = {}
    for name, cfg in ai_devices(config).items():
        if cfg["state"] not in slots:
            print(f"{name:16s} learning: no data yet"); report[name] = {"status": "learning", "days": 0}; continue
        X, y = build_features(slots, cfg)
        ok = X[CORE].notna().all(axis=1) & y.notna()
        X, y = X[ok], y[ok].astype(int)
        days = len(X) / SLOTS_PER_DAY
        if y.nunique() < 2 or days < MIN_DAYS:
            print(f"{name:16s} learning: {days:.0f}/{MIN_DAYS} days of data")
            report[name] = {"status": "learning", "days": round(days)}
            continue

        # evaluate on the last 14 days (never seen in training)
        cut = X.index.max() - pd.Timedelta(days=14)
        tr, te = X.index <= cut, X.index > cut
        m = new_model().fit(X[tr], y[tr])
        p = m.predict(X[te]); yt = y[te].values
        change = X.now[te].values != yt
        near = change & ((p == yt) | (p == np.r_[yt[1:], yt[-1]]) | (p == np.r_[yt[0], yt[:-1]]))
        metrics = dict(
            status="ready",
            f1=round(f1_score(yt, p), 3),
            precision=round(precision_score(yt, p, zero_division=0), 3),
            recall=round(recall_score(yt, p), 3),
            change_acc=round(float((p[change] == yt[change]).mean()), 3) if change.any() else None,
            change_acc_15min=round(float(near.sum() / change.sum()), 3) if change.any() else None,
            baseline_yesterday_f1=round(f1_score(yt, X.yday[te].astype(int)), 3),
            features=list(X.columns),
        )
        final = new_model().fit(X, y)                 # final model uses all data
        joblib.dump(dict(model=final, features=list(X.columns), metrics=metrics,
                         sklearn=sklearn.__version__, trained=datetime.now().isoformat()),
                    MODEL_DIR / f"{name}.joblib")
        report[name] = metrics
        print(f"{name:16s} f1={metrics['f1']}  exact={metrics['change_acc']}  ±15min={metrics['change_acc_15min']}")
    (MODEL_DIR / "report.json").write_text(json.dumps(report, indent=2))
    return report


# ------------------------------------------------------------------ predict
def overridden_recently(con, name, now_ts):
    row = con.execute("SELECT MAX(ts) FROM readings WHERE key = ?", (f"override/{name}",)).fetchone()
    return row[0] is not None and now_ts - row[0] < OVERRIDE_PAUSE_S


def predict(config, db_path, now_ts=None):
    con = connect(db_path)
    th = config.get("thresholds", {})
    act_at, suggest_at = th.get("ai_act_at", ACT_AT), th.get("ai_suggest_at", SUGGEST_AT)
    now_ts = now_ts or int(time.time())
    slots = load_slots(con, since_ts=now_ts - 8 * 86400)    # 8 days is enough for all lags
    slots = slots[slots.index <= to_local([now_ts])[0]]
    decisions = []
    for name, cfg in ai_devices(config).items():
        path = MODEL_DIR / f"{name}.joblib"
        if not path.exists() or cfg["state"] not in slots:
            decisions.append(dict(device=name, action="learning")); continue
        bundle = joblib.load(path)
        X, _ = build_features(slots, cfg)
        for f in bundle["features"]:                         # a feature that disappeared -> NaN
            if f not in X:
                X[f] = np.nan
        x = X[bundle["features"]].iloc[[-1]]
        if x[CORE].isna().any(axis=None):
            continue
        p_on = float(bundle["model"].predict_proba(x)[0, 1])
        state_now = int(x["now"].iloc[0])
        target_time = x.index[0] + pd.Timedelta(minutes=15 * HORIZON)
        d = dict(device=name, p_on=round(p_on, 2), state_now=state_now,
                 predicted_for=target_time.isoformat(), action="none")
        if overridden_recently(con, name, now_ts):
            d["action"] = "paused_by_override"
        elif p_on >= act_at and state_now == 1:
            d["action"] = "keep_on"
        elif p_on >= act_at and state_now == 0:
            # AI only switches ON automatically. Switching OFF is done by the rules
            # (device on + empty room), so the AI can never cut something off wrongly.
            d.update(action="schedule_on",
                     execute_at=(target_time - pd.Timedelta(minutes=cfg["lead_min"])).isoformat())
        elif p_on >= suggest_at and state_now == 0:
            d["action"] = "suggest_on"
        elif p_on <= 1 - act_at and state_now == 1:
            d["action"] = "suggest_off"
        decisions.append(d)
    dispatch(decisions)
    return decisions


def dispatch(decisions):
    """Send decisions to the rest of the system.
    - schedule_on        -> MQTT topic home/ai/schedule (the automation service executes it)
    - suggest_on / _off  -> Firebase /suggestions (the web app shows it)
    Replace the print with paho-mqtt / firebase_writer calls on the Pi."""
    for d in decisions:
        print(json.dumps(d))


# ------------------------------------------------------------------ demo data
def demo(config, db_path, days=70, seed=42):
    """Writes simulated readings (every 5 minutes) for EVERY room/device in the config,
    so the pipeline can be tested before the hardware is ready."""
    rng = np.random.default_rng(seed)
    rooms = config["rooms"]
    devs = {d: (rid, c) for rid, r in rooms.items() for d, c in (r.get("devices") or {}).items()
            if not (c.get("caps") or {}).get("lock")}
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
        habit = {dv: 18 + rng.integers(0, 4) + j(.4) for dv in devs}    # a usual "on" hour for other devices
        state = {dv: 0 for dv in devs}
        where = "bedroom"
        for k in range(288):                                  # every 5 minutes
            h, ts = k / 12, day0 + k * 300
            home = not any(a <= h < b for a, b in out)
            asleep = h < wake or h >= sleep
            if not home: where = "out"
            elif asleep: where = "bedroom" if "bedroom" in rooms else next(iter(rooms))
            elif k % 3 == 0:
                awake_rooms = [r for r in rooms if not rooms[r].get("hidden")]
                weights = np.array([6 if r == "living" else 1 for r in awake_rooms], float)
                where = rng.choice(awake_rooms, p=weights / weights.sum())
            t_out = tmax - 7 * np.cos((h - 15) / 24 * 2 * np.pi) - 7
            lux = max(0, 800 * np.sin(np.clip((h - 6) / (sunset - 6), 0, 1) * np.pi)) + j(15)
            for rid, room in rooms.items():
                sensors = room.get("sensors") or []
                occ = int(where == rid)
                t = t_out + (1 if rid == "bedroom" else 0) + j(.4)
                if "temp" in sensors: rows.append((ts, f"{rid}/temp", round(t, 2)))
                if "lux" in sensors: rows.append((ts, f"{rid}/lux", round(max(lux, 0), 1)))
                if "occ" in sensors: rows.append((ts, f"{rid}/occ", occ))
            for dv, (rid, c) in devs.items():
                occ, icon = int(where == rid), c.get("icon")
                t = t_out + (1 if rid == "bedroom" else 0)
                st = state[dv]
                if (c.get("caps") or {}).get("power") == "read":         # monitor-only: TV evenings, fridge always
                    st = 1 if icon == "fridge" else int(occ and 18 <= h < 23 and rng.random() < .9)
                elif icon in ("fan", "ac") or (c.get("rules") or {}).get("follow_temp"):
                    if occ and (t > 28.5 or (asleep and t > 27)) and rng.random() < .8: st = 1
                    elif not occ and st and rng.random() < .8: st = 0
                    elif occ and t < 26.5: st = 0
                elif icon == "light":
                    st = int(occ and not asleep and lux < 150 and rng.random() < .95)
                else:                                                    # habit: around a usual hour, if home
                    st = int(home and not asleep and habit[dv] <= h < habit[dv] + 1.5)
                state[dv] = st
                rows.append((ts, f"device/{dv}", st))
    con = connect(db_path)
    con.execute("DELETE FROM readings")
    con.executemany("INSERT INTO readings VALUES (?,?,?)", rows)
    con.commit()
    print(f"wrote {len(rows):,} simulated readings ({days} days, {len(rooms)} rooms, {len(devs)} devices) to {db_path}")


def show_devices(config):
    for name, cfg in ai_devices(config).items():
        feats = [k for k in ("occ", "temp", "lux") if cfg[k]] + [c.split("/")[1] for c in cfg["context"]]
        print(f"{name:16s} room={cfg['room']:9s} uses: {', '.join(feats)}  lead={cfg['lead_min']} min")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["demo", "train", "predict", "devices"])
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--db", default=str(DB_PATH))
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.command == "demo":
        demo(cfg, a.db)
    elif a.command == "train":
        train(cfg, a.db)
    elif a.command == "predict":
        predict(cfg, a.db)
    elif a.command == "devices":
        show_devices(cfg)
    else:
        print(__doc__)
