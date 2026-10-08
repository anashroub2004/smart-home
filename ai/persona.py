"""Persona — turns a routine file (ai/personas/*.toml) into concrete days.

    p = Persona.load("ai/personas/student_studio.toml")
    day = p.day(date(2026, 10, 14))
    day.segments   [(start_h, end_h, room, activity, still), ...]   covers 0..24 h of that calendar day
    day.intents    [{"role": "kettle", "on": 7.4, "off": 7.45, "how": "monitor"}, ...]   appliance use
    day.tags       ["semester", "weekend", "guests", ...]           what kind of day it was (for the diary)
    p.weather(date)  -> {"tmax", "tmin", "rh", "sunrise", "sunset", "cloud", "heat_wave"}

The SAME day comes out for the same seed + date, in ai/lifesim.py (the history the AI trains on) and in the
live digital twin (pi/person.py) — so the model is tested on the person it learned.

Hours may go past 24 inside a day's plan (going to bed at 00:40 = 24.67); `segments` already cuts and
stitches calendar days. Python standard library only (the simulator must run without numpy).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

OUT = "out"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_PATH = Path(__file__).resolve().parent / "personas" / "student_studio.toml"


def hm(text):
    h, m = (int(x) for x in str(text).split(":"))
    return h + m / 60


def md(text):
    m, d = (int(x) for x in str(text).split("-"))
    return m, d


@dataclass
class DayPlan:
    date: date
    kind: str
    period: str
    tags: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    wake: float | None = None
    bed: float | None = None
    away: bool = False
    note: list = field(default_factory=list)


class Persona:
    def __init__(self, data, source="(dict)"):
        self.d = data
        self.source = source
        self.seed = int(data.get("person", {}).get("seed", 1))
        self.weekend = {DAYS.index(x) for x in data.get("person", {}).get("weekend", ["Fri", "Sat"])}
        self._active = {}
        self._weather = {}

    @classmethod
    def load(cls, path=None):
        path = Path(path or DEFAULT_PATH)
        if tomllib is None:
            raise RuntimeError("Python 3.11+ is needed to read the routine file (tomllib)")
        with open(path, "rb") as f:
            return cls(tomllib.load(f), source=str(path))

    # ------------------------------------------------------------------ randomness: same seed + date = same day
    def rng(self, day, salt=""):
        return random.Random(f"{self.seed}|{day.isoformat()}|{salt}")

    def j(self, r, spec, scale=1.0):
        """[mean, sigma] in minutes -> a draw (minutes)."""
        mean, sigma = (spec if isinstance(spec, (list, tuple)) else (spec, 0))
        return r.gauss(float(mean), float(sigma)) * scale

    def jt(self, r, spec):
        """["HH:MM", sigma_min] -> hours."""
        t, sigma = spec if isinstance(spec, (list, tuple)) else (spec, 0)
        return hm(t) + r.gauss(0, float(sigma)) / 60

    # ------------------------------------------------------------------ calendar
    def period(self, day):
        mdv = (day.month, day.day)
        for p in self.d.get("period", []):
            a, b = md(p["from"]), md(p["to"])
            inside = a <= mdv <= b if a <= b else (mdv >= a or mdv <= b)
            if inside:
                return p.get("kind", "free"), p.get("name", p.get("kind", "free"))
        return "free", "Free days"

    def period_start(self, day):
        """First day of the period `day` is in (for the bedtime drift)."""
        kind, name = self.period(day)
        d = day
        for _ in range(200):
            if self.period(d - timedelta(days=1))[1] != name:
                return d
            d -= timedelta(days=1)
        return d

    def ramadan(self, day):
        """-> 'ramadan' | 'eid' | None"""
        r = self.d.get("ramadan") or {}
        if not r.get("enabled", False):
            return None
        span = (r.get("dates") or {}).get(str(day.year))
        if not span:
            return None
        a = date(day.year, *md(span[0]))
        b = date(day.year, *md(span[1]))
        if a <= day <= b:
            return "ramadan"
        if b < day <= b + timedelta(days=int(r.get("eid_days", 3))):
            return "eid"
        return None

    def trip(self, day):
        """-> (name, first_day, last_day) if `day` is a day away, else None."""
        for t in self.d.get("trip", []):
            for year in (day.year - 1, day.year):
                m, dd = md(t["from"])
                shift = random.Random(f"{self.seed}|trip|{t['name']}|{year}").randint(-4, 4)
                start = date(year, m, dd) + timedelta(days=shift)
                end = start + timedelta(days=int(t["days"]) - 1)
                if start <= day <= end:
                    return t["name"], start, end
        return None

    # ------------------------------------------------------------------ weather + sun (deterministic per date)
    def weather(self, day):
        if day in self._weather:
            return self._weather[day]
        c = self.d.get("climate", {})
        mx, mn, rh = c.get("max", [25] * 12), c.get("min", [15] * 12), c.get("humidity", [55] * 12)
        doy = day.timetuple().tm_yday
        # climatology: monthly means anchored mid-month, linear in between
        pos = (doy - 15) / 30.44
        i0 = int(math.floor(pos)) % 12
        f = pos - math.floor(pos)
        clim = lambda arr: arr[i0] * (1 - f) + arr[(i0 + 1) % 12] * f
        # smooth weather noise: a weighted mean of the last 4 days' random draws (fronts last a few days)
        noise = 0.0
        for k, wgt in enumerate((0.4, 0.3, 0.2, 0.1)):
            noise += wgt * random.Random(f"{self.seed}|wx|{(day - timedelta(days=k)).isoformat()}").gauss(0, 2.6)
        heat = self.heat_wave(day)
        tmax = clim(mx) + noise + (5.0 if heat else 0.0)
        tmin = clim(mn) + noise * 0.7 + (3.0 if heat else 0.0)
        r = self.rng(day, "cloud")
        winter = day.month in (11, 12, 1, 2, 3)
        cloud = min(1.0, max(0.0, r.random() * (0.9 if winter else 0.3)))
        sunrise, sunset = self.sun(day)
        out = dict(tmax=tmax, tmin=min(tmin, tmax - 4), rh=clim(rh) + noise * -1.5, sunrise=sunrise, sunset=sunset,
                   cloud=cloud, heat_wave=heat)
        self._weather[day] = out
        return out

    # climate at an hour of a day — shared by ai/lifesim.py and the live digital twin
    def outdoor(self, day, h):
        """Outdoor temperature (°C): minimum ~05:30, maximum ~15:00, tomorrow's minimum after 15:00."""
        wx, nx = self.weather(day), self.weather(day + timedelta(days=1))
        tmin = wx["tmin"] if h < 15 else nx["tmin"]
        shape = (1 - math.cos(math.pi * (h - 5.5) / 9.5)) / 2 if 5.5 <= h <= 15 else \
            (1 + math.cos(math.pi * ((h - 15) % 24) / 14.5)) / 2
        return tmin + (wx["tmax"] - tmin) * shape

    def indoor_target(self, day, h, room):
        """Where a room's temperature is heading (before fans / cooking / showers)."""
        wx = self.weather(day)
        off = float(self.d.get("climate", {}).get("indoor_offset", 3))
        return self.outdoor(day, h) * 0.55 + (wx["tmax"] + wx["tmin"]) / 2 * 0.45 + off + \
            {"bedroom": 0.6, "kitchen": 0.4}.get(room, 0)

    def daylight(self, day, h):
        """Sunlight outside (lux) — rooms get a share of it through their windows."""
        wx = self.weather(day)
        if not wx["sunrise"] < h < wx["sunset"]:
            return 0.0
        return 820 * math.sin(math.pi * (h - wx["sunrise"]) / (wx["sunset"] - wx["sunrise"])) * (1 - 0.65 * wx["cloud"])

    def rh_out(self, day, h):
        return max(15.0, min(95.0, self.weather(day)["rh"] + 12 * math.cos(math.pi * (h - 4) / 12)))

    def heat_wave(self, day):
        p = float(self.d.get("unusual", {}).get("heat_wave", 0))
        for k in range(5):
            d = day - timedelta(days=k)
            if d.month not in (4, 5, 6, 7, 8, 9):
                continue
            r = random.Random(f"{self.seed}|heat|{d.isoformat()}")
            if r.random() < p and k < 3 + r.randint(0, 2):
                return True
        return False

    def sun(self, day):
        """Sunrise / sunset in house time (hours) — NOAA approximation + the Palestine DST offset."""
        c = self.d.get("climate", {})
        lat, lon = math.radians(float(c.get("latitude", 31.5))), float(c.get("longitude", 35.1))
        n = day.timetuple().tm_yday
        g = 2 * math.pi / 365 * (n - 1)
        decl = 0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) \
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
        eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                        - 0.040849 * math.sin(2 * g))
        ha = math.degrees(math.acos(math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(decl))
                                    - math.tan(lat) * math.tan(decl)))
        noon_utc = (720 - 4 * lon - eqt) / 60
        off = utc_offset(day)
        return noon_utc - ha * 4 / 60 + off, noon_utc + ha * 4 / 60 + off

    # ------------------------------------------------------------------ one active day (wake .. bed), hours of `day`
    def active(self, day):
        if day in self._active:
            return self._active[day]
        plan = self._build(day)
        self._active[day] = plan
        if len(self._active) > 40:
            self._active.pop(next(iter(self._active)))
        return plan

    def _build(self, day):
        d, r = self.d, self.rng(day)
        kind, period = self.period(day)
        tags = [kind]
        wd = day.weekday()
        weekend = wd in self.weekend
        tomorrow_weekend = (wd + 1) % 7 in self.weekend
        if weekend:
            tags.append("weekend")
        ram = self.ramadan(day)
        if ram:
            tags.append(ram)
        tt = (d.get("timetable", {}).get(kind) or {}).get(DAYS[wd], {}) or {}
        un = d.get("unusual", {})
        hab = d.get("habits", {})
        sl = d.get("sleep", {})
        wx = self.weather(day)
        plan = DayPlan(date=day, kind=kind, period=period, tags=tags)
        if wx["heat_wave"]:
            tags.append("heat_wave")

        trip = self.trip(day)
        prev_trip = self.trip(day - timedelta(days=1))
        if trip:
            name, first, last = trip
            tags.append("trip")
            plan.note.append(name)
            if day != first:                                  # away the whole day
                plan.away = True
                plan.wake, plan.bed = None, None
                plan.segments = [(0.0, 24.0, OUT, "Away", 0.0)]
                return plan

        # ---- unusual days (drawn once per day, in a fixed order so the file is reproducible)
        sick = r.random() < float(un.get("sick", 0)) and not trip
        oversleep = r.random() < float(un.get("oversleep", 0))
        g = un.get("guests", {})
        guests = r.random() < float(g.get("weekend" if (weekend or tomorrow_weekend) else "weekday", 0)) and not sick
        late_night = r.random() < float(un.get("late_night", 0)) and not sick
        all_nighter = kind == "exams" and r.random() < float(un.get("all_nighter", 0)) and not sick
        for t, on in (("sick", sick), ("guests", guests), ("late_night", late_night), ("all_nighter", all_nighter)):
            if on:
                tags.append(t)

        # ---- plans outside (classes / work / gym / out / family / eid)
        outs = []                                          # (start, end, label)

        def opt(v):
            """'HH:MM' (always) or ['HH:MM', p] (on some days) -> hours or None"""
            if v is None:
                return None
            if isinstance(v, list):
                return hm(v[0]) if r.random() < float(v[1]) else None
            return hm(v)

        leave = back = None
        if not sick and not (trip and day == trip[1]):
            leave = opt(tt.get("leave"))
            if leave is not None:
                back = hm(tt.get("back", "14:00"))
                leave += r.gauss(0, 4) / 60
                back += r.gauss(0, 15) / 60
                outs.append((leave, back, "Classes" if kind in ("semester", "exams") else "Work"))
        if trip and day == trip[1]:                        # leaving for a trip this morning
            leave = hm("10:00") + r.gauss(0, 30) / 60
            outs.append((leave, 48.0, "Trip"))
        for key, label in (("gym", "Gym"), ("out", "Out with friends"), ("family", "Family visit")):
            v = tt.get(key)
            if v and not sick and r.random() < float(v[2]):
                a, b = hm(v[0]) + r.gauss(0, 12) / 60, hm(v[1]) + r.gauss(0, 20) / 60
                if b < a:
                    b += 24
                outs.append((a, b, label))
        if ram == "eid" and not sick:
            outs = [o for o in outs if o[2] != "Family visit"] + [(10.5 + r.gauss(0, .5), 20 + r.gauss(0, .7), "Eid, family")]
        if ram == "ramadan" and not sick and r.random() < 0.45:
            a = wx["sunset"] + 1.6 + r.gauss(0, .3)
            outs.append((a, a + 2.2 + r.gauss(0, .5), "Evening out (Ramadan)"))
        if late_night:
            a = 21 + r.gauss(0, .6)
            outs.append((a, 25.2 + r.gauss(0, .6), "Late night out"))
        if prev_trip and prev_trip[2] == day - timedelta(days=1) and not trip:   # coming back from a trip
            outs.append((0.0, 16.5 + r.gauss(0, 1.2), "Coming back"))
            tags.append("back_from_trip")
        outs = merge(outs)

        # ---- wake
        first_out = min((a for a, b, _ in outs if a > 4), default=None)
        wake_home = sl.get("wake_home", {}).get(kind, ["10:00", 60])
        if leave is not None and first_out is not None and abs(first_out - leave) < 0.01:
            wake = leave - self.j(r, sl.get("wake_before_leave", [55, 10])) / 60
        else:
            wake = self.jt(r, wake_home) + (sl.get("weekend_later", 0) / 60 if weekend else 0)
            if first_out is not None and wake > first_out - 0.5:
                wake = first_out - 0.6
        if sick:
            wake = self.jt(r, wake_home) + 0.5
        if oversleep and leave is not None:
            wake = leave - (12 + r.random() * 10) / 60
            tags.append("oversleep")
        if ram == "ramadan":
            wake += 0.4
        if any(a <= 0.01 for a, b, _ in outs):             # back from a trip: wakes up away
            wake = None

        # ---- bed
        bed_spec = sl.get("bedtime", {}).get(kind, ["00:00", 40])
        bed = self.jt(r, bed_spec)
        if bed < 12:
            bed += 24
        if kind == "semester":
            weeks = (day - self.period_start(day)).days / 7
            bed += weeks * float(sl.get("semester_drift_per_week", 0)) / 60
        if tomorrow_weekend:
            bed += sl.get("weekend_later", 0) / 60
        if ram == "ramadan":
            bed += 1.0
        if guests:
            bed += 0.8
        if sick:
            bed -= 1.2
        if all_nighter:
            bed = 28 + r.gauss(0, .4)
        last_back = max((b for a, b, _ in outs if b < 30), default=0)
        if last_back > bed - 0.4:
            bed = last_back + 0.3 + r.random() * 0.3
        plan.wake, plan.bed = wake, bed

        segs, intents = [], []
        home_from = wake if wake is not None else max((b for a, b, lab in outs if a <= 0.01), default=0)

        # ---- morning
        t = home_from
        rushed = oversleep and leave is not None
        if wake is not None:
            sh = d.get("bathroom", {})
            morning_shower = not rushed and not sick and r.random() < float(sh.get("shower_morning_p", .5))
            if morning_shower:
                dur = max(6, self.j(r, sh.get("shower_minutes", [14, 4]))) / 60
                segs.append((t, t + 0.05, "bathroom", "Getting up", 0.2))
                segs.append((t + 0.05, t + 0.05 + dur, "bathroom", "Shower", 0.1))
                self.vent_intent(r, intents, t + 0.07, t + 0.05 + dur, hab)
                t += 0.05 + dur
                tags.append("morning_shower")
            else:
                dur = (4 + r.random() * 5) / 60
                segs.append((t, t + dur, "bathroom", "Getting ready", 0.1))
                t += dur
            meals = d.get("meals", {})
            if not rushed and ram != "ramadan" and r.random() < float(meals.get("breakfast_p", .7)):
                dur = (12 + r.random() * 10) / 60
                segs.append((t, t + dur, "kitchen", "Breakfast", 0.3))
                if r.random() < 0.8:
                    self.kettle(r, intents, t + 0.03, hab)
                t += dur
        else:
            meals = d.get("meals", {})

        # ---- the rest of the day: home time between plans outside
        evening = []                                       # fixed things at home: dinner, guests
        dinner = wx["sunset"] + 0.05 if ram == "ramadan" else self.jt(r, meals.get("dinner", ["19:30", 40]))
        cooks_dinner = r.random() < float(meals.get("cook_dinner_p", .6)) or ram == "ramadan"
        if guests:
            g0 = 19.5 + r.gauss(0, .4)
            evening.append((g0, min(bed - 0.5, g0 + 3.2 + r.gauss(0, .5)), "living", "Friends over", 0.25))
        home = complement(outs, t, bed)
        study = d.get("study", {})
        n_study = max(0, round(self.j(r, study.get("blocks", {}).get(kind, [1, 1]))))
        if sick:
            n_study = 0
        if all_nighter:
            n_study += 2
        nap = (d.get("sleep", {}).get("nap") or {})
        napped = False
        for a, b in home:
            cur = a
            if a > t + 0.01 and a > 0.01 and any(abs(a - ob) < 0.01 for _, ob, _ in outs):
                segs.append((a, a + 0.04, "entrance", "Arriving home", 0.0))
                cur = a + 0.04
                # back from classes: lunch / nap
                if cur < 15.5 and r.random() < float(meals.get("cook_lunch_p", .3)) and ram != "ramadan":
                    cur = self.cook(r, segs, intents, cur, b, meals, hab, "Cooking lunch")
                if not napped and cur < 18 and r.random() < float(nap.get("p", 0)) and not sick:
                    s0 = cur + max(0.1, self.j(r, nap.get("after_back", [30, 20])) / 60)
                    s1 = s0 + max(0.25, self.j(r, nap.get("minutes", [45, 20])) / 60)
                    if s1 < b - 0.3:
                        cur = self.fill(r, segs, intents, cur, s0, hab, study, n_study=0)
                        segs.append((s0, s1, "bedroom", "Nap", 0.9))
                        cur, napped = s1, True
                        tags.append("nap")
            # dinner inside this window?
            if cooks_dinner and a <= dinner - 0.6 < b and dinner + 0.5 < b:
                cur = self.fill(r, segs, intents, cur, dinner - 0.65, hab, study, n_study)
                n_study = max(0, n_study - self._used_study)
                cur = self.cook(r, segs, intents, max(cur, dinner - 0.65), b, meals, hab,
                                "Cooking iftar" if ram == "ramadan" else "Cooking dinner")
                eat = max(0.25, self.j(r, meals.get("eat_minutes", [25, 8])) / 60)
                segs.append((cur, cur + eat, "living", "Dinner & TV", 0.6))
                cur += eat
                cooks_dinner = False
            for ga, gb, room, act, still in evening:
                if a <= ga < b and cur <= ga:
                    cur = self.fill(r, segs, intents, cur, ga, hab, study, n_study)
                    n_study = max(0, n_study - self._used_study)
                    segs.append((ga, gb, room, act, still))
                    if r.random() < 0.6:
                        self.kettle(r, intents, ga + 0.2, hab)
                    cur = gb
            end = b
            if b >= bed - 0.01:
                end = bed - self.prebed_len(r, d, sick)
            cur = self.fill(r, segs, intents, cur, max(cur, end), hab, study, n_study, sick=sick)
            n_study = max(0, n_study - self._used_study)
            if b >= bed - 0.01:
                self.prebed(r, segs, intents, cur, bed, d, hab, sick, morning="morning_shower" in tags)
        # Ramadan: suhoor before dawn (kitchen), then back to sleep — belongs to the NIGHT of `day`
        if ram == "ramadan":
            s0 = wx["sunrise"] - 1.4 + r.gauss(0, .15)
            intents.append({"role": "_suhoor", "on": s0, "off": s0 + 0.45})
            tags.append("suhoor")
        # insomnia: awake in the middle of the night (the night after `day`, hours > 24)
        if r.random() < float(un.get("insomnia", 0)) and not all_nighter and bed < 26:
            i0 = max(bed + 1.5, 26 + r.random() * 2)
            i1 = i0 + (30 + r.random() * 40) / 60
            intents.append({"role": "_insomnia", "on": i0, "off": i1})   # tagged on the calendar day it happens
        # washer: laundry day
        ld = hab.get("laundry") or {}
        if ld and DAYS[wd] == ld.get("day") and r.random() < float(ld.get("p", 0)):
            w0 = self.jt(r, ld.get("at", ["11:00", 60]))
            if any(a <= w0 < b for a, b in home):
                intents.append({"role": "washer", "on": w0, "off": w0 + float(ld.get("minutes", 75)) / 60, "how": "manual"})
                tags.append("laundry")
        # app pre-cool: hot afternoons, he starts the living-room fan from the phone before coming home
        if wx["tmax"] + float(self.d.get("climate", {}).get("indoor_offset", 3)) > 30:
            for a, b, lab in outs:
                if 12 < b < 21 and r.random() < float(hab.get("app_precool_p", 0)):
                    lead = max(5, self.j(r, hab.get("app_precool_min", [15, 5]))) / 60
                    intents.append({"role": "fan", "room": "living", "on": b - lead, "off": None, "how": "app"})
                    tags.append("app_precool")
        # power cut / fridge door left open
        if r.random() < float(un.get("power_cut", 0)):
            p0 = 8 + r.random() * 14
            intents.append({"role": "_power_cut", "on": p0, "off": p0 + 1 + r.random() * 2})
            tags.append("power_cut")
        if r.random() < float(un.get("fridge_door_ajar", 0)):
            f0 = 12 + r.random() * 9
            intents.append({"role": "_fridge_ajar", "on": f0, "off": f0 + 2 + r.random()})
            tags.append("fridge_ajar")
        # bedtime light: most nights he switches it off himself, some nights he falls asleep with it on
        plan.light_off_at_bed = r.random() < float(hab.get("light_off_sleep", .9))
        if not plan.light_off_at_bed:
            tags.append("fell_asleep_light_on")
        # personal "too warm" today
        plan.fan_on_temp = self.j(r, hab.get("fan_on_temp", [28, 0.7]))

        for a, b, lab in outs:
            if b > a:
                segs.append((a, a + 0.03, "entrance", "Leaving", 0.0) if a > (wake or 0) and a > 0.01 else (a, a, OUT, "", 0))
                segs.append((max(a + 0.03, 0.0) if a > 0.01 else 0.0, b, OUT, lab, 0.0))
        segs = [s for s in segs if s[1] - s[0] > 1e-6]
        segs.sort(key=lambda s: s[0])
        plan.segments = segs
        plan.intents = intents
        plan.note += [lab for _, _, lab in outs]
        return plan

    # ------------------------------------------------------------------ building blocks
    _used_study = 0

    def kettle(self, r, intents, at, hab):
        m = max(1.5, self.j(r, hab.get("kettle_minutes", [3, 1])))
        intents.append({"role": "kettle", "on": at, "off": at + m / 60, "how": "monitor"})

    def vent_intent(self, r, intents, on, end, hab):
        if r.random() < float(hab.get("vent_on_shower", .8)):
            off = end + max(3, self.j(r, hab.get("vent_off_after_min", [15, 6]))) / 60
            forgot = r.random() < float(hab.get("vent_forget_p", .2))
            if forgot:
                off += (40 + r.random() * 90) / 60
            intents.append({"role": "vent", "on": on + 0.02, "off": off, "how": "manual", "forgot": forgot})

    def cook(self, r, segs, intents, cur, limit, meals, hab, label):
        dur = max(0.25, self.j(r, meals.get("cook_minutes", [35, 12])) / 60)
        dur = min(dur, max(0.2, limit - cur - 0.1))
        segs.append((cur, cur + dur, "kitchen", label, 0.2))
        if r.random() < float(hab.get("hood_on_cooking", .85)):
            off = cur + dur + max(1, self.j(r, hab.get("hood_off_after_min", [6, 4]))) / 60
            forgot = r.random() < float(hab.get("hood_forget_p", .15))
            if forgot:
                off += (30 + r.random() * 80) / 60
            intents.append({"role": "hood", "on": cur + 0.05, "off": off, "how": "manual", "forgot": forgot})
        if r.random() < 0.35:
            self.kettle(r, intents, cur + dur * 0.3, hab)
        return cur + dur

    def fill(self, r, segs, intents, a, b, hab, study, n_study, sick=False):
        """Free time at home between a and b: study blocks, relaxing, short kitchen / bathroom trips."""
        self._used_study = 0
        t = a
        rate = float(self.d.get("bathroom", {}).get("visits_per_hour_home", .18))
        while b - t > 0.05:
            if sick:
                block = min(b - t, 1.5 + r.random())
                segs.append((t, t + block, "bedroom", "Sick in bed", 0.75))
                t += block
                if b - t > 0.2:
                    segs.append((t, t + 0.12, "bathroom", "Bathroom", 0.1))
                    t += 0.12
                if b - t > 0.3 and r.random() < 0.6:
                    segs.append((t, t + 0.12, "kitchen", "Making tea", 0.3))
                    self.kettle(r, intents, t + 0.02, hab)
                    t += 0.12
                continue
            if n_study > self._used_study and b - t > 0.75:
                if r.random() < float(study.get("tea_p", .5)):
                    segs.append((t, t + 0.08, "kitchen", "Making tea", 0.3))
                    self.kettle(r, intents, t + 0.01, hab)
                    t += 0.08
                dur = min(b - t, max(0.5, self.j(r, study.get("minutes", [90, 30])) / 60))
                segs.append((t, t + dur, "living", "Studying", 0.7))
                t += dur
                self._used_study += 1
                continue
            dur = min(b - t, 0.4 + r.expovariate(1 / 0.9))
            act = "Phone & TV" if t > 18 else "Relaxing"
            segs.append((t, t + dur, "living", act, 0.6))
            t += dur
            if b - t > 0.15 and r.random() < min(0.9, rate * 3):
                room, act = ("bathroom", "Bathroom") if r.random() < 0.55 else ("kitchen", "Getting a snack")
                dur = (3 + r.random() * 5) / 60
                segs.append((t, t + dur, room, act, 0.1 if room == "bathroom" else 0.3))
                t += dur
        return max(t, b)

    def prebed_len(self, r, d, sick):
        return (0.35 + (0 if sick else self.j(r, d.get("sleep", {}).get("read_in_bed", [20, 10])) / 60))

    def prebed(self, r, segs, intents, a, bed, d, hab, sick, morning):
        t = a
        sh = d.get("bathroom", {})
        if not morning and not sick and r.random() < float(sh.get("shower_evening_p", .35)) / max(0.1, 1 - float(sh.get("shower_morning_p", .5))):
            dur = max(6, self.j(r, sh.get("shower_minutes", [14, 4]))) / 60
            segs.append((t, t + dur, "bathroom", "Shower", 0.1))
            self.vent_intent(r, intents, t, t + dur, hab)
            t += dur
        segs.append((t, t + 0.12, "bathroom", "Getting ready for bed", 0.1))
        t += 0.12
        if bed - t > 0.05:
            segs.append((t, bed, "bedroom", "Reading in bed", 0.8))

    # ------------------------------------------------------------------ calendar day = tail of yesterday + today
    def day(self, day):
        prev = self.active(day - timedelta(days=1))
        cur = self.active(day)
        out = DayPlan(date=day, kind=cur.kind, period=cur.period, tags=list(cur.tags), wake=cur.wake, bed=cur.bed,
                      away=cur.away, note=list(cur.note))
        out.light_off_at_bed = getattr(cur, "light_off_at_bed", True)
        out.light_off_prev_night = getattr(prev, "light_off_at_bed", True)
        out.fan_on_temp = getattr(cur, "fan_on_temp", 28.0)
        segs = []
        # 1) yesterday's evening that went past midnight
        for a, b, room, act, still in prev.segments:
            if b > 24:
                segs.append((max(0.0, a - 24), b - 24, room, act, still))
        t = max((s[1] for s in segs), default=0.0)
        # 2) the night: asleep (or away) until today's first segment
        first = min((s[0] for s in cur.segments), default=24.0)
        night_room = OUT if (prev.away or (cur.wake is None and cur.segments and cur.segments[0][2] == OUT)) else "bedroom"
        night_act = "Away" if night_room == OUT else "Sleeping"
        if cur.wake is None and not cur.away:
            first = min((s[0] for s in cur.segments), default=24.0)
        breaks = sorted([i for i in prev.intents + cur.intents if i["role"] in ("_insomnia", "_suhoor")
                         and ((i in prev.intents and i["on"] >= 24) or (i in cur.intents and i["on"] < 24))],
                        key=lambda i: i["on"])
        for i in breaks:
            a, b = (i["on"] - 24, i["off"] - 24) if i in prev.intents else (i["on"], i["off"])
            if t < a < first and night_room != OUT:
                segs.append((t, a, night_room, night_act, 0.9 if night_room != OUT else 0.0))
                if i["role"] == "_suhoor":
                    segs.append((a, min(b, first), "kitchen", "Suhoor", 0.3))
                else:
                    segs.append((a, a + 0.05, "bathroom", "Can't sleep", 0.1))
                    segs.append((a + 0.05, min(b, first), "living", "Can't sleep", 0.5))
                t = min(b, first)
        if first > t:
            segs.append((t, first, night_room, night_act, 0.9 if night_room != OUT else 0.0))
        # 3) today until midnight, then asleep if in bed before midnight
        for a, b, room, act, still in cur.segments:
            if a < 24:
                segs.append((a, min(b, 24.0), room, act, still))
        if cur.bed is not None and cur.bed < 24:
            segs.append((cur.bed, 24.0, "bedroom", "Sleeping", 0.9))
        segs = [s for s in sorted(segs, key=lambda s: s[0]) if s[1] - s[0] > 1e-6]
        # make it contiguous (tiny gaps / overlaps from rounding)
        fixed = []
        for s in segs:
            if fixed and s[0] < fixed[-1][1]:
                s = (fixed[-1][1], s[1], *s[2:])
                if s[1] <= s[0]:
                    continue
            if fixed and s[0] > fixed[-1][1] + 1e-6:
                p = fixed[-1]
                fixed[-1] = (p[0], s[0], *p[2:])
            fixed.append(s)
        if fixed and fixed[0][0] > 0:
            fixed[0] = (0.0, *fixed[0][1:])
        if fixed and fixed[-1][1] < 24:
            fixed[-1] = (*fixed[-1][:1], 24.0, *fixed[-1][2:])
        out.segments = fixed
        # intents that happen on this calendar day
        intents = [dict(i, on=i["on"] - 24, off=(i["off"] - 24 if i.get("off") is not None else None))
                   for i in prev.intents if i["on"] >= 24]
        intents += [i for i in cur.intents if i["on"] < 24]
        out.intents = sorted(intents, key=lambda i: i["on"])
        for i in out.intents:
            if i["role"] in ("_insomnia",) and "insomnia" not in out.tags:
                out.tags.append("insomnia")
        return out

    def at(self, day_plan, hour):
        for s in day_plan.segments:
            if s[0] <= hour < s[1]:
                return s
        return day_plan.segments[-1]


# ---------------------------------------------------------------------- helpers
def merge(outs):
    """Overlapping plans outside -> one span (keeps the first label)."""
    outs = sorted(outs)
    res = []
    for a, b, lab in outs:
        if res and a <= res[-1][1] + 0.25:
            pa, pb, pl = res[-1]
            res[-1] = (pa, max(pb, b), pl)
        else:
            res.append((a, b, lab))
    return res


def complement(outs, a, b):
    """Home windows inside [a, b] around the plans outside."""
    res, t = [], a
    for oa, ob, _ in sorted(outs):
        if ob <= t:
            continue
        if oa > t:
            res.append((t, min(oa, b)))
        t = max(t, ob)
        if t >= b:
            break
    if t < b:
        res.append((t, b))
    return [(x, y) for x, y in res if y - x > 1e-3]


def utc_offset(day):
    """Asia/Hebron offset in hours (DST included), without needing a time-zone database."""
    try:
        from zoneinfo import ZoneInfo
        return datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo("Asia/Hebron")).utcoffset().total_seconds() / 3600
    except Exception:  # noqa: BLE001 — no tz database (e.g. Windows without tzdata)
        # Palestine: summer time roughly from the last Saturday of March to the last Saturday of October
        def last_sat(m):
            d = date(day.year, m, 31)
            return d - timedelta(days=(d.weekday() - 5) % 7)
        return 3.0 if last_sat(3) <= day < last_sat(10) else 2.0
