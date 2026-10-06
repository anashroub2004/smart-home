"""Smart home AI — command line.

    python -m ai.run demo              # 10 weeks of simulated history -> train -> one plan (no hardware needed)
    python -m ai.run demo --drift 10   # same, with a routine change in the last 10 days (drift detection)
    python -m ai.run train             # nightly training (cron 03:00 on the Pi)
    python -m ai.run plan              # one planning round from the latest readings (prints the decisions)
    python -m ai.run devices           # which devices the AI handles, what each model uses, thresholds
    python -m ai.run report            # last training report (metrics, calibration, importance, drift)
    python -m ai.run evaluate          # experiments for the report (routine change, thresholds) -> evaluation.md

Options: --config PATH (default docs/seed.json) --db PATH (default ai/data/home.db) --models DIR
On the Pi the automation service imports ai.runtime.AIRuntime and calls plan() / tick() (see ai/README_AR.md).
"""
import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):                       # allow `python ai/run.py ...`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "ai"

from ai import energy, settings as S, store, synth, train  # noqa: E402
from ai.dispatch import PrintSink  # noqa: E402
from ai.features import load_slots  # noqa: E402
from ai.runtime import AIRuntime  # noqa: E402
from ai.spec import ai_devices, device_label, load_config  # noqa: E402


def house_from_db(con, config, now):
    """Rebuild a 'house' snapshot from the latest readings (for `plan` from the command line)."""
    slots = load_slots(con, since_ts=now - 86400, until_ts=now)
    last = slots.iloc[-1] if not slots.empty else {}
    rooms = {}
    for rid in config["rooms"]:
        rooms[rid] = {k: float(last[f"{rid}/{k}"]) for k in ("temp", "lux", "occ", "mmwave", "pir")
                      if f"{rid}/{k}" in getattr(last, "index", []) and last[f"{rid}/{k}"] == last[f"{rid}/{k}"]}
    devices = {}
    for r in config["rooms"].values():
        for d in (r.get("devices") or {}):
            key = f"device/{d}"
            if key in getattr(last, "index", []):
                devices[d] = {"v": int(last[key] or 0)}
    return {"rooms": rooms, "devices": devices, "paused": {}}


def cmd_devices(config):
    anchor = (config.get("thresholds") or {}).get("ai_act_at", S.ACT_AT)
    for name, spec in ai_devices(config).items():
        th = energy.thresholds(spec, anchor)
        feats = [k for k in ("occ", "temp", "lux") if getattr(spec, k)] + [c.split("/")[1] for c in spec.context]
        print(f"{name:16s} {spec.kind:8s} room={spec.room:9s} {spec.watts:6.1f} W  act>={th['act']:.2f} "
              f"suggest>={th['suggest']:.2f}  lead={spec.lead_min} min  off after {spec.off_after_min} min empty  "
              f"uses: {', '.join(feats)}")


def cmd_report(model_dir):
    r = train.load_json(model_dir / "report.json", None)
    if not r:
        sys.exit("No report yet — run `train` first.")
    print(json.dumps(r, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["demo", "train", "plan", "devices", "report", "evaluate"])
    ap.add_argument("--config", default=str(S.CONFIG_PATH))
    ap.add_argument("--db", default=str(S.DB_PATH))
    ap.add_argument("--models", default=str(S.MODEL_DIR))
    ap.add_argument("--days", type=int, default=70, help="demo: days of simulated history")
    ap.add_argument("--drift", type=int, default=0, help="demo: routine change in the last N days")
    a = ap.parse_args(argv)
    config = load_config(a.config)
    model_dir = Path(a.models)
    if a.command == "devices":
        return cmd_devices(config)
    if a.command == "report":
        return cmd_report(model_dir)
    if a.command == "evaluate":
        from ai import evaluate
        return evaluate.run(config, model_dir)
    con = store.connect(a.db)
    now = time.time()
    labels = {d: device_label(config, d) for r in config["rooms"].values() for d in (r.get("devices") or {})}
    if a.command == "demo":
        rows = synth.fill(con, config, days=a.days, end_ts=now, routine_change_days=a.drift)
        print(f"wrote {len(rows):,} simulated readings ({a.days} days) to {a.db}")
    if a.command in ("demo", "train"):
        train.train_all(config, con, model_dir, now_ts=now, labels=labels)
    if a.command in ("demo", "plan"):
        rt = AIRuntime(config, con, PrintSink(), model_dir=model_dir)
        rt.plan(house_from_db(con, config, now), now)


if __name__ == "__main__":
    main()
