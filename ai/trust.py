"""Trust ladder — the AI earns the right to act by itself, per device and per kind of decision.

    level 0  ASK      it asks ("You seem to be asleep — turn off the bedroom lights?")
    level 1  NOTIFY   after 3 "yes" in a row it does it itself and tells you (switch it back = undo)
    level 2  SILENT   after 5 automatic actions nobody undid, it only writes it in the history

A "no" or an undo (you reversed it within 10 min) moves it one level down and resets the count, so it learns
in both directions. History counts too: switching the light off yourself after lying down on >= 5 of the last
28 nights starts the ladder at level 1 (from_history) — until the first "no" / undo. After that the history counts
again only from the nights AFTER that "no": switching it off yourself on 3 of them (HISTORY_BACK_NIGHTS) brings
level 1 back, so one night of reading in bed does not cancel a month of habit for good.
Unanswered questions change nothing. Pure functions on a plain dict (saved in state.json).

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


def answered(state, dev, kind, yes, night=None):
    """-> (old_level, new_level). night: the night of a "no" (ISO date) — history counts again after it."""
    e = entry(state, dev, kind)
    old = e["level"]
    if yes:
        e["yes"] += 1
        if e["level"] == S.TRUST_ASK and e["yes"] >= S.TRUST_PROMOTE_YES:
            e.update(level=S.TRUST_NOTIFY, yes=0, ok=0)
    else:
        e.update(level=max(S.TRUST_ASK, e["level"] - 1), yes=0, ok=0)
        e["history_off"] = True          # you said no: the history before tonight no longer counts
        e["history_off_night"] = str(night) if night else None   # None = never again (answers only)
    return old, e["level"]


def from_history(state, dev, kind, nights, night_list=None):
    """History counts as answers: if you switched it off yourself on enough nights, start at NOTIFY (it does it and
    tells you, undo = no) instead of asking. After a no / undo only the nights after it count, and
    HISTORY_BACK_NIGHTS of them are enough. -> True when it moved up."""
    e = entry(state, dev, kind)
    if e["level"] != S.TRUST_ASK:
        return False
    if e.get("history_off"):
        since = e.get("history_off_night")
        after = sum(1 for n in (night_list or []) if since and n > since)
        if after < S.HISTORY_BACK_NIGHTS:
            return False
        e.pop("history_off")
        e.pop("history_off_night", None)
        e.update(level=S.TRUST_NOTIFY, yes=0, ok=0, source="history", nights=after, back=True)
        return True
    if nights < S.SELF_OFF_MIN_NIGHTS:
        return False
    e.update(level=S.TRUST_NOTIFY, yes=0, ok=0, source="history", nights=int(nights), back=False)
    return True


def acted_ok(state, dev, kind):
    """An automatic action nobody undid."""
    e = entry(state, dev, kind)
    old = e["level"]
    e["ok"] += 1
    if e["level"] == S.TRUST_NOTIFY and e["ok"] >= S.TRUST_SILENT_AFTER:
        e.update(level=S.TRUST_SILENT, ok=0)
    return old, e["level"]


def undone(state, dev, kind, night=None):
    return answered(state, dev, kind, False, night)


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
