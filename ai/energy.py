"""Energy in the AI's decisions.

1. Energy-scaled thresholds — the decision rule "act if p x benefit > (1 - p) x waste cost" means
   p > cost / (benefit + cost): the more a mistake costs, the surer the AI must be. We use a log scale of the
   device's power so the threshold grows smoothly and stays explainable:

       act(w) = ai_act_at - 0.15 + 0.085 x log10(1 + watts)        clamped to [0.60, 0.97]

   With ai_act_at = 0.80: 1.2 W light ~0.68, 2.4 W fan ~0.70, 100 W heater ~0.82, 1.5 kW AC ~0.92.
   A device can override it with config  "ai": {"act": 0.85}.

2. Adaptive offsets — every rejected suggestion raises that device's threshold for that 2-hour block
   by 0.03, every accepted one lowers it by 0.02 (kept within -0.10 .. +0.15).

3. Waste ledger — energy used while a room is confirmed empty, with the reason:
       forgotten   left on, nothing was going to switch it off
       rule_delay  waiting for the "empty room" rule / smart off timer
       ai          switched on by the AI and nobody came (yet)
       standby     "off" but still drawing power (stuck relay, standby)
"""
import math

from . import settings as S


def energy_threshold(watts, act_anchor=S.ACT_AT):
    t = act_anchor - S.ENERGY_BASE_DROP + S.ENERGY_SLOPE * math.log10(1 + max(0.0, watts or 0))
    return round(min(S.ACT_MAX, max(S.ACT_MIN, t)), 3)


def bucket(hour):
    return int(hour) // S.ADAPT_BUCKET_H


def thresholds(spec, act_anchor, offsets=None, hour=0, drift=False, gap=S.SUGGEST_GAP):
    """-> dict(act, suggest, exit, off, base, offset). `offsets`: {"<device>:<bucket>": float}.
    gap = ai_act_at - ai_suggest_at from the config (default 0.20)."""
    base = spec.act_override if spec.act_override is not None else energy_threshold(spec.watts, act_anchor)
    off = (offsets or {}).get(f"{spec.id}:{bucket(hour)}", 0.0)
    act = min(S.ACT_MAX, max(S.ACT_MIN, base + off + (S.DRIFT_BUMP if drift else 0)))
    if getattr(spec, "suggest_override", None) is not None:
        suggest = min(act - 0.05, spec.suggest_override + off)
    else:
        suggest = max(0.5, min(act - 0.05, act - gap))
    return dict(act=round(act, 3), suggest=round(suggest, 3), exit=round(act - S.HYSTERESIS, 3),
                off=S.SUGGEST_OFF_AT, base=base, offset=round(off, 3))


def adapt(offsets, device, hour, accepted):
    key = f"{device}:{bucket(hour)}"
    v = offsets.get(key, 0.0) + (-S.ADAPT_ACCEPT if accepted else S.ADAPT_REJECT)
    offsets[key] = round(min(S.ADAPT_MAX, max(S.ADAPT_MIN, v)), 3)
    return offsets[key]


class WasteLedger:
    """Accumulates wasted Wh per day / device / cause. Call `step` every tick with the live house state."""

    CAUSES = ("forgotten", "rule_delay", "ai", "standby")

    def __init__(self):
        self.day = None
        self.wh = {}                       # device -> cause -> Wh
        self.saved = {"ai": 0.0, "rule": 0.0}
        self.ai_wasted = 0.0

    def reset_if_new_day(self, day):
        if day != self.day:
            self.day, self.wh, self.saved, self.ai_wasted = day, {}, {"ai": 0.0, "rule": 0.0}, 0.0

    def add(self, device, cause, wh):
        if wh <= 0:
            return
        d = self.wh.setdefault(device, {})
        d[cause] = d.get(cause, 0.0) + wh
        if cause == "ai":
            self.ai_wasted += wh

    def step(self, day, dt_s, devices, classify):
        """devices: {id: dict(v, watts, room_empty, must_run)}; classify(id) -> cause when on in an empty room."""
        self.reset_if_new_day(day)
        for dev, d in devices.items():
            try:
                w = float(d.get("watts") or 0)
            except (TypeError, ValueError):
                w = 0.0
            if not math.isfinite(w) or w <= 0 or d.get("must_run"):
                continue
            wh = w * dt_s / 3600
            if not d.get("v"):
                if w > S.STANDBY_MIN_W:
                    self.add(dev, "standby", wh)
            elif d.get("room_empty"):
                self.add(dev, classify(dev), wh)

    def record_saved(self, by, wh):
        self.saved[by] = self.saved.get(by, 0.0) + max(0.0, wh)

    def snapshot(self):
        return {dev: {c: round(v, 3) for c, v in causes.items()} for dev, causes in self.wh.items()}

    def totals(self):
        total = sum(v for causes in self.wh.values() for v in causes.values())
        return dict(wasted_wh=round(total, 2), saved_by_ai_wh=round(self.saved.get("ai", 0), 2),
                    saved_by_rules_wh=round(self.saved.get("rule", 0), 2),
                    wasted_by_ai_wh=round(self.ai_wasted, 2),
                    ai_net_wh=round(self.saved.get("ai", 0) - self.ai_wasted, 2))


def saved_estimate(watts, horizon_min=S.SAVED_HORIZON_MIN):
    """Energy saved by switching off now, estimated over the next hour (shown as an estimate)."""
    return round(max(0.0, watts) * horizon_min / 60, 2)
