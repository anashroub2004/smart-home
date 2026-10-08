"""Life simulator — a year (or any span) of the studio, minute by minute, from a routine file.

    python -m ai.lifesim --days 365                       -> ai/data/persona_year.db + diary + summary
    python -m ai.lifesim --days 365 --persona my.toml --out ai/data/other.db

What it produces is what the Pi's SQLite would contain after living there: the same keys, written the same way
(store.Recorder: on change, deadbands, 15-minute heartbeat), so the AI trains on it exactly like on a real hub.

Per minute:
  person    where he is and what he does (ai/persona.py — the same days the digital twin lives)
  sensors   radar (sees him still), PIR (only movement: rate = 12 x (1 - still)^2 per minute), temperature
            (outside -> inside with inertia, cooking / showers / fans), humidity (bathroom showers, exhaust fan),
            light (sun by date and clouds, windows per room, lamps)
  devices   only what HE does by hand (the house before automation): light on in the dark (usually), off when
            leaving (often forgotten), fan when too warm (his own threshold, a bit different every day), hood when
            cooking, exhaust fan after a shower, laundry, the app to pre-cool on hot afternoons. Every press is
            recorded as `override/<device>` (manual control), like the Pi does.
  monitored kettle (when he makes tea), fridge (compressor cycles, more in summer, door left open sometimes)
  unusual   power cuts (no data, everything off), trips (Away mode), sick days, insomnia, guests ... (persona)

Also writes a diary (one line per day) so the report can show that no two days are alike.
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import settings as S
from . import store
from .persona import OUT, Persona, utc_offset

WINDOW = {"living": 0.75, "kitchen": 0.5, "bedroom": 0.6, "bathroom": 0.0}   # share of daylight reaching the room
PIR_RATE = 12.0


class BufferedRecorder(store.Recorder):
    """Recorder that writes in big batches (a year is ~2 million rows)."""

    STEP = dict(store.Recorder.STEP, hum=2.0)

    def __init__(self, con, heartbeat_s=900):
        super().__init__(con, heartbeat_s)
        self.buf = []
        self.rows = 0

    def observe(self, values, ts=None):
        before = len(self.buf)
        for key, value in values.items():
            if value is None:
                continue
            value = float(value)
            prev = self.last.get(key)
            if prev is None or ts - prev[0] >= self.heartbeat_s or abs(value - prev[1]) > self._step(key):
                self.buf.append((int(ts), key, value))
                self.last[key] = (ts, value)
        if len(self.buf) > 200_000:
            self.flush()
        return len(self.buf) - before

    def event(self, key, value=1, ts=None):
        self.buf.append((int(ts), key, float(value)))

    def flush(self):
        if self.buf:
            store.write(self.con, self.buf)
            self.con.commit()
            self.rows += len(self.buf)
            self.buf = []


def roles(config):
    """Devices by what they are (from the config — nothing hard-coded): lights / fans per room, hood, vent ..."""
    out = {"light": {}, "fan": {}, "hood": [], "vent": [], "kettle": [], "fridge": [], "washer": [], "watts": {},
           "levels": {}, "room": {}, "monitor": set(), "measured": set()}
    for rid, room in config["rooms"].items():
        for dev, c in (room.get("devices") or {}).items():
            icon = c.get("icon")
            caps = c.get("caps") or {}
            out["room"][dev] = rid
            out["watts"][dev] = float(c.get("watts") or 0)
            if caps.get("energy", "estimate") != "none":
                out["measured"].add(dev)
            if (caps.get("level") or {}).get("steps"):
                out["levels"][dev] = caps["level"]["steps"]
            if caps.get("power") == "read":
                out["monitor"].add(dev)
            if icon == "light":
                out["light"].setdefault(rid, []).append(dev)
            elif icon in ("fan", "ac"):
                out["fan"].setdefault(rid, []).append(dev)
            elif icon in ("hood", "vent", "kettle", "fridge", "washer"):
                out[icon].append(dev)
    return out


def day_start_ts(d):
    """Unix time of local midnight (Asia/Hebron)."""
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds() - utc_offset(d) * 3600)


def simulate(config, persona=None, days=365, end_date=None, con=None, seed=7, progress=None, diary_path=None,
             start_date=None):
    """Simulate `days` days ending yesterday (or end_date) and write the readings to `con`. -> summary dict."""
    persona = persona or Persona.load()
    end_date = end_date or (date.today() - timedelta(days=1))
    start = start_date or (end_date - timedelta(days=days - 1))
    rnd = random.Random(f"life|{persona.seed}|{seed}|{start.isoformat()}")
    R = roles(config)
    rooms = {rid: r for rid, r in config["rooms"].items()}
    radar = {rid for rid, r in rooms.items() if "mmwave" in " ".join(r.get("hardware") or []).lower()}
    pir_rooms = {rid for rid, r in rooms.items() if "pir" in " ".join(r.get("hardware") or []).lower()
                 and "occ" in (r.get("sensors") or [])}
    th = config.get("thresholds") or {}
    dark = float(th.get("light_on_lux", 150))
    hab = persona.d.get("habits", {})

    rec = BufferedRecorder(con) if con is not None else None
    devs = {d: {"v": 0, "level": None} for d in R["room"] if not ((rooms[R["room"][d]].get("devices") or {})[d].get("caps") or {}).get("lock")}
    temp = {rid: 22.0 for rid in rooms}
    hum = {rid: 50.0 for rid in rooms}
    forgot = {}                # device -> minute it gets noticed and switched off
    pending_on = {}            # room -> minute the light goes on
    dark_since = {}            # room -> minute he has been sitting in the dark
    fridge = {"on": False, "until": 0}
    prev_room, prev_act = "bedroom", "Sleeping"
    diary = []
    t0 = time.time()

    def press(dev, v, level=None, ts=0, by="manual"):
        st = devs[dev]
        if st["v"] == v and (level is None or st["level"] == level):
            return False
        st["v"] = v
        if v and dev in R["levels"]:
            st["level"] = level or R["levels"][dev][len(R["levels"][dev]) // 2]
        if rec is not None and by == "manual":
            rec.event(f"override/{dev}", 1, ts)
        day_stats["manual"] += by == "manual"
        day_stats["presses"][dev] = day_stats["presses"].get(dev, 0) + (by == "manual")
        forgot.pop(dev, None)
        return True

    for k in range((end_date - start).days + 1):
        d = start + timedelta(days=k)
        dp = persona.day(d)
        wx = persona.weather(d)
        base = day_start_ts(d)
        segs = dp.segments
        si = 0
        ints = dp.intents
        cut = [(i["on"], i["off"]) for i in ints if i["role"] == "_power_cut"]
        ajar = [(i["on"], i["off"]) for i in ints if i["role"] == "_fridge_ajar"]
        day_stats = {"manual": 0, "presses": {}, "asleep_light_min": 0, "forgotten_light_min": 0, "wh": {},
                     "kettle": 0, "showers": sum(1 for s in segs if s[3] == "Shower")}
        done = set()
        for m in range(1440):
            h = m / 60
            ts = base + m * 60
            while si + 1 < len(segs) and h >= segs[si][1]:
                si += 1
            _, _, room, act, still = segs[si]
            away = room == OUT
            powered = not any(a <= h < b for a, b in cut)

            # ---- weather + room climate (the same formulas the live digital twin uses)
            sun = persona.daylight(d, h)
            rh_out = persona.rh_out(d, h)
            for rid in rooms:
                target = persona.indoor_target(d, h, rid)
                if rid == "kitchen" and act.startswith("Cooking") and room == "kitchen":
                    target += 2.5
                if rid == "bathroom" and act == "Shower" and room == "bathroom":
                    target += 3.0
                if any(devs[f]["v"] for f in R["fan"].get(rid, [])):
                    target -= 1.4
                temp[rid] += (target - temp[rid]) / 120
                base_h = rh_out * 0.75 + 8
                if rid == "bathroom" and act == "Shower" and room == "bathroom":
                    hum[rid] += (93 - hum[rid]) * 0.18
                else:
                    vent_on = rid == "bathroom" and any(devs[v]["v"] for v in R["vent"])
                    hum[rid] += (base_h - hum[rid]) * (0.09 if vent_on else 0.025)
                if rid == "kitchen" and act.startswith("Cooking") and room == "kitchen":
                    hum[rid] += (base_h + 15 - hum[rid]) * 0.05

            # ---- the person moves: leaving / entering rooms
            if room != prev_room:
                for dev in R["light"].get(prev_room, []):
                    if devs[dev]["v"]:
                        if rnd.random() < float(hab.get("light_off_leave", .6)):
                            press(dev, 0, ts=ts)
                        else:
                            forgot[dev] = m + max(5, persona.j(rnd, hab.get("forgot_light_notice_min", [45, 30])))
                for dev in R["fan"].get(prev_room, []):
                    if devs[dev]["v"] and rnd.random() < float(hab.get("fan_off_leave", .5)):
                        press(dev, 0, ts=ts)
                pending_on.pop(prev_room, None)
                dark_since.pop(prev_room, None)
                if room != OUT and prev_room == OUT and rec is not None:
                    rec.event("door/entry", 1, ts)
                if room in R["light"] and act != "Sleeping":
                    lux_room = sun * WINDOW.get(room, 0.5)
                    if lux_room < dark and rnd.random() < float(hab.get("light_on_dark", .95)):
                        pending_on[room] = m + max(0, persona.j(rnd, hab.get("light_on_delay_min", [0.5, 0.4])))
                for dev in R["light"].get(room, []):
                    forgot.pop(dev, None)       # he is back: a forgotten light is "his" again
            # going to sleep / waking up
            if act == "Sleeping" and prev_act != "Sleeping" and room == "bedroom":
                for dev in R["light"].get(room, []):
                    if devs[dev]["v"]:
                        if dp.light_off_at_bed if h > 12 else dp.light_off_prev_night:
                            forgot[dev] = m + 1.5 + rnd.random()          # switches it off after lying down
                        else:
                            day_stats["fell_asleep"] = True
            if prev_act == "Sleeping" and act != "Sleeping":
                for dev in R["light"].get(prev_room, []):
                    if devs[dev]["v"] and prev_room == room:
                        press(dev, 0, ts=ts)                               # wakes up, light was on all night
            prev_room, prev_act = room, act

            # ---- lights: pending switch-on, sitting in the dark, forgotten ones noticed, bright daylight
            if room in pending_on and m >= pending_on[room]:
                pending_on.pop(room)
                for dev in R["light"].get(room, []):
                    press(dev, 1, ts=ts)
            if room in R["light"] and act != "Sleeping" and not away:
                lux_room = sun * WINDOW.get(room, 0.5)
                lit = any(devs[x]["v"] for x in R["light"][room])
                if lux_room < dark and not lit and room not in pending_on:
                    dark_since.setdefault(room, m)
                    if m - dark_since[room] >= 3:
                        for dev in R["light"][room]:
                            press(dev, 1, ts=ts)
                        dark_since.pop(room, None)
                else:
                    dark_since.pop(room, None)
                if lit and lux_room > dark * 2.5 and rnd.random() < 0.004:
                    for dev in R["light"][room]:
                        press(dev, 0, ts=ts)
            for dev, at in list(forgot.items()):
                if m >= at and (R["room"][dev] != room or act == "Sleeping"):
                    press(dev, 0, ts=ts)

            # ---- fans: his own "too warm" today
            for rid, fans in R["fan"].items():
                for dev in fans:
                    st = devs[dev]
                    if room == rid and not st["v"] and temp[rid] > dp.fan_on_temp \
                            and rnd.random() < float(hab.get("fan_on_p", .85)) / 8:
                        over = temp[rid] - dp.fan_on_temp
                        steps = R["levels"].get(dev, [70])
                        lvl = steps[0] if act == "Sleeping" else (steps[0] if over < 1 else steps[min(len(steps) - 1, 1 if over < 2.5 else 2)])
                        press(dev, 1, lvl, ts=ts)
                    elif st["v"] and temp[rid] < dp.fan_on_temp - 1.6 and rnd.random() < 0.05:
                        press(dev, 0, ts=ts)

            # ---- appliances from the day's plan (hood, vent, washer, kettle, the app)
            for n, i in enumerate(ints):
                role = i["role"]
                if role.startswith("_"):
                    continue
                key = (n, "on")
                if key not in done and h >= i["on"]:
                    done.add(key)
                    if role == "kettle":
                        day_stats["kettle"] += 1
                    elif role == "fan":
                        for dev in R["fan"].get(i.get("room", "living"), []):
                            press(dev, 1, R["levels"].get(dev, [70])[0], ts=ts)
                    else:
                        for dev in R.get(role, []):
                            press(dev, 1, ts=ts)
                if i.get("off") is not None and (n, "off") not in done and h >= i["off"]:
                    done.add((n, "off"))
                    if role not in ("kettle", "fan"):
                        for dev in R.get(role, []):
                            press(dev, 0, ts=ts)
            for dev in R["kettle"]:
                devs[dev]["v"] = int(any(i["role"] == "kettle" and i["on"] <= h < i["off"] for i in ints))
            for dev in R["fridge"]:
                if any(a <= h < b for a, b in ajar):
                    devs[dev]["v"] = 1
                elif m >= fridge["until"]:
                    fridge["on"] = not fridge["on"]
                    kt = temp.get("kitchen", 22)
                    fridge["until"] = m + (14 + max(0, kt - 22) * 0.8 if fridge["on"] else max(10, 30 - max(0, kt - 22) * 1.5)) \
                        + rnd.random() * 4
                    devs[dev]["v"] = int(fridge["on"])

            # ---- power cut: everything off, the nodes are silent (no readings)
            if not powered:
                for dev in devs:
                    devs[dev]["v"] = 0
                forgot.clear()
                pending_on.clear()
                continue

            # ---- diary counters
            if act == "Sleeping" and any(devs[x]["v"] for x in R["light"].get(room, [])):
                day_stats["asleep_light_min"] += 1
            for rid, lights in R["light"].items():
                if rid != room and any(devs[x]["v"] for x in lights):
                    day_stats["forgotten_light_min"] += 1

            # ---- readings (what the nodes report)
            if rec is None:
                continue
            vals = {"mode/away": int(dp.away or (away and act in ("Trip", "Away", "Coming back")))}
            for rid, r in rooms.items():
                sensors = r.get("sensors") or []
                occ = int(room == rid)
                if "temp" in sensors:
                    vals[f"{rid}/temp"] = round(temp[rid] + rnd.gauss(0, 0.08), 2)
                if "hum" in sensors:
                    vals[f"{rid}/hum"] = round(hum[rid] + rnd.gauss(0, 0.6), 1)
                if "lux" in sensors:
                    lamps = 250 if any(devs[x]["v"] for x in R["light"].get(rid, [])) else 0
                    vals[f"{rid}/lux"] = round(max(0.0, sun * WINDOW.get(rid, 0.5) + lamps + rnd.gauss(0, 6)), 1)
                if "occ" in sensors:
                    vals[f"{rid}/occ"] = occ
                    if rid in radar:
                        vals[f"{rid}/mmwave"] = occ
                    if rid in pir_rooms:
                        rate = PIR_RATE * (1 - still) ** 2
                        vals[f"{rid}/pir"] = int(occ and rnd.random() < 1 - math.exp(-rate))
            for dev, st in devs.items():
                vals[f"device/{dev}"] = st["v"]
                if st["level"] is not None:
                    vals[f"level/{dev}"] = st["level"]
                if dev in R["measured"]:
                    w = R["watts"][dev]
                    if st["v"] and dev in R["levels"]:
                        w *= (st["level"] or 70) / 100
                    watt = round(w * rnd.uniform(0.97, 1.03), 3) if st["v"] else 0.0
                    vals[f"power/{dev}"] = watt
                    day_stats["wh"][dev] = day_stats["wh"].get(dev, 0) + watt / 60
            rec.observe(vals, ts)

        # timers that run past midnight continue tomorrow (minutes are counted per day)
        fridge["until"] -= 1440
        forgot = {dv: at - 1440 for dv, at in forgot.items()}
        pending_on = {rm: at - 1440 for rm, at in pending_on.items()}
        diary.append(dict(date=d.isoformat(), weekday=d.strftime("%a"), period=dp.period, kind=dp.kind,
                          tags=" ".join(t for t in dp.tags if t not in (dp.kind,)),
                          wake=fmt(dp.wake), bed=fmt(dp.bed), plans=" · ".join(dp.note),
                          manual=day_stats["manual"], showers=day_stats["showers"], kettle=day_stats["kettle"],
                          asleep_light_min=day_stats["asleep_light_min"],
                          forgotten_light_min=day_stats["forgotten_light_min"],
                          wh=round(sum(day_stats["wh"].values()), 1),
                          tmax=round(wx["tmax"], 1), sunset=fmt(wx["sunset"])))
        if progress and (k % 30 == 29 or k == (end_date - start).days):
            progress(k + 1, (end_date - start).days + 1, time.time() - t0)
    if rec is not None:
        rec.flush()
        store.set_meta(con, "synth_until", day_start_ts(end_date) + 86400)
        store.set_meta(con, "persona", persona.source)
    if diary_path:
        write_diary(diary, diary_path)
    return {"days": len(diary), "rows": rec.rows if rec else 0, "seconds": round(time.time() - t0, 1), "diary": diary}


def fill(con, config, persona=None, days=70, end_ts=None, seed=7):
    """Bootstrap a new hub (and the digital twin) with `days` of this person's life, ending now."""
    end = date.fromtimestamp(end_ts) if end_ts else date.today()
    return simulate(config, persona, days=days, end_date=end - timedelta(days=1), con=con, seed=seed)


def fmt(h):
    if h is None:
        return ""
    h = h % 24
    return f"{int(h):02d}:{int(round(h % 1 * 60)) % 60:02d}"


def write_diary(diary, path):
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(diary[0].keys()))
        w.writeheader()
        w.writerows(diary)


def variety(diary):
    """How different the days are — for the report."""
    def mins(t):
        if not t:
            return None
        h, m = (int(x) for x in t.split(":"))
        v = h * 60 + m
        return v + 1440 if v < 12 * 60 else v                 # bedtime after midnight counts as late
    wakes = [mins(r["wake"]) - (1440 if mins(r["wake"]) and mins(r["wake"]) >= 1440 else 0) for r in diary if r["wake"]]
    beds = [mins(r["bed"]) for r in diary if r["bed"]]
    sd = lambda xs: (sum((x - sum(xs) / len(xs)) ** 2 for x in xs) / len(xs)) ** 0.5 if xs else 0
    unusual = [r for r in diary if any(t in r["tags"].split() for t in
                                       ("sick", "insomnia", "guests", "late_night", "all_nighter", "oversleep", "power_cut",
                                        "fridge_ajar", "trip", "fell_asleep_light_on"))]
    signatures = {(r["kind"], r["weekday"], r["plans"], r["tags"]) for r in diary}
    return {"days": len(diary), "wake_sd_min": round(sd(wakes)), "bed_sd_min": round(sd(beds)),
            "unusual_days_pct": round(100 * len(unusual) / max(1, len(diary)), 1),
            "distinct_day_types": len(signatures), "manual_per_day": round(sum(r["manual"] for r in diary) / max(1, len(diary)), 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--persona", default=None, help="routine file (default ai/personas/student_studio.toml)")
    ap.add_argument("--config", default=str(S.CONFIG_PATH), help="house config (default docs/seed.json)")
    ap.add_argument("--out", default=str(S.DATA_DIR / "persona_year.db"))
    ap.add_argument("--end", default=None, help="last day YYYY-MM-DD (default yesterday)")
    ap.add_argument("--seed", type=int, default=7, help="behaviour randomness (the days themselves come from the file)")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    cfg = cfg.get("config", cfg)
    persona = Persona.load(a.persona)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("", "-wal", "-shm"):
        Path(str(out) + ext).unlink(missing_ok=True)
    con = store.connect(out)
    end = date.fromisoformat(a.end) if a.end else None
    print(f"simulating {a.days} days of '{persona.d['person']['name']}' -> {out}")
    res = simulate(cfg, persona, days=a.days, end_date=end, con=con, seed=a.seed,
                   diary_path=out.with_suffix(".diary.csv"),
                   progress=lambda k, n, s: print(f"  {k}/{n} days  ({s:.0f} s)", flush=True))
    store.set_meta(con, "all_simulated", "1")
    con.commit()
    v = variety(res["diary"])
    summary = {"persona": persona.source, "rows": res["rows"], "seconds": res["seconds"], **v}
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"diary: {out.with_suffix('.diary.csv')}")


if __name__ == "__main__":
    main()
