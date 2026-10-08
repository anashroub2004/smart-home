"""Trust ladder — the AI earns the right to act by itself, per device and per kind of decision.

    level 0  ASK      it asks ("You seem to be asleep — turn off the bedroom lights?")
    level 1  NOTIFY   after 3 "yes" in a row it does it itself and tells you (switch it back = undo)
    level 2  SILENT   after 5 automatic actions nobody undid, it only writes it in the history

A "no" or an undo (you reversed it within 10 min) moves it one level down and resets the count, so it learns
in both directions. History counts too: switching the light off yourself after lying down on >= 5 of the last
28 nights starts the ladder at level 1 (from_history) — until the first "no" / undo. Unanswered questions change nothing. Pure functions on a plain dict (saved in state.json).

Kinds of decision:
    off_still   switch a light off while you rest (radar sees you, PIR quiet, light usually off now)
    suggest_on  switch on at a time the model is only fairly sure about (between "suggest" and "act")
"""
from . import settings as S

LEVEL_NAMES = {S.TRUST_ASK: "asks first", S.TRUST_NOTIFY: "does it and tells you", S.TRUST_SILENT: "does it by itself"}
KIND_TEXT = {"off_still": "switching the light off while you rest", "suggest_on": "switching on at your usual time"}


def entry(state, dev, kind):
    return state.setdefault("trust", {}).setdefault(dev, {}).setdefault(kind, {"level": S.TRUST_ASK, "yes": 0, "ok": 0})


def level(state, dev, kind):
    return ((state.get("trust") or {}).get(dev) or {}).get(kind, {}).get("level", S.TRUST_ASK)


def answered(state, dev, kind, yes):
    """-> (old_level, new_level)."""
    e = entry(state, dev, kind)
    old = e["level"]
    if yes:
        e["yes"] += 1
        if e["level"] == S.TRUST_ASK and e["yes"] >= S.TRUST_PROMOTE_YES:
            e.update(level=S.TRUST_NOTIFY, yes=0, ok=0)
    else:
        e.update(level=max(S.TRUST_ASK, e["level"] - 1), yes=0, ok=0)
        e["history_off"] = True          # you said no: from now on only your answers count, not the history
    return old, e["level"]


def from_history(state, dev, kind, nights):
    """History counts as answers: if you switched it off yourself on enough nights, start at NOTIFY (it does it and
    tells you, undo = no) instead of asking. Never after you said no / undid it. -> True when it moved up."""
    e = entry(state, dev, kind)
    if e["level"] != S.TRUST_ASK or e.get("history_off") or nights < S.SELF_OFF_MIN_NIGHTS:
        return False
    e.update(level=S.TRUST_NOTIFY, yes=0, ok=0, source="history", nights=int(nights))
    return True


def acted_ok(state, dev, kind):
    """An automatic action nobody undid."""
    e = entry(state, dev, kind)
    old = e["level"]
    e["ok"] += 1
    if e["level"] == S.TRUST_NOTIFY and e["ok"] >= S.TRUST_SILENT_AFTER:
        e.update(level=S.TRUST_SILENT, ok=0)
    return old, e["level"]


def undone(state, dev, kind):
    return answered(state, dev, kind, False)


def summary(state, labels=None):
    """For /ai_insights and the twin page: [{device, kind, level, text}]."""
    out = []
    for dev, kinds in (state.get("trust") or {}).items():
        for kind, e in kinds.items():
            out.append({"device": dev, "label": (labels or {}).get(dev, dev), "kind": kind, "level": e["level"],
                        "text": f"{KIND_TEXT.get(kind, kind)}: {LEVEL_NAMES[e['level']]}"
                                + (f" (you did it yourself on {e.get('nights')} nights)" if e.get("source") == "history"
                                   and e["level"] == S.TRUST_NOTIFY else ""),
                        "yes": e.get("yes", 0), "ok": e.get("ok", 0)})
    return out
