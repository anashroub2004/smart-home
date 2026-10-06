"""Sensor checks right before the AI acts. The prediction alone never switches anything.

House state passed in (same shape as /home_state, plus per-sensor presence):
    rooms[rid] = {occ, mmwave?, pir?, temp?, lux?}
    home       = someone anywhere in the house, or a front-door entry in the last 30 min
Missing or NaN readings never count as a "yes".
"""
import math


def value(room, key):
    """A finite number from the room state, else None."""
    v = (room or {}).get(key)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def room_occupied(room):
    """Presence for switching ON: any sensor is enough (C1001 radar also sees people sitting still)."""
    if not room:
        return False
    return bool(value(room, "occ") or value(room, "mmwave") or value(room, "pir"))


def room_vacant(room, presence_keys=None):
    """Vacancy for switching OFF must be confirmed by the radar AND (if present) the PIR.
    A room without a radar is never 'vacant' for the AI — a PIR alone misses people sitting or sleeping still."""
    names = [k.split("/")[-1] for k in (presence_keys or [])]
    if not room or "mmwave" not in names:
        return False
    for s in names + ["occ"]:
        v = value(room, s)
        if v is None and s != "occ":
            return False                       # a sensor we rely on has no reading
        if v:
            return False
    return True


def check_on(spec, room, home, light_on_lux=150, temp=None, lux=None):
    """-> (ok, reason). temp / lux may come from a neighbouring room when this room has no sensor."""
    has_presence = bool(spec.occ or spec.presence_keys)
    here = room_occupied(room)
    temp = temp if temp is not None else value(room, "temp")
    lux = lux if lux is not None else value(room, "lux")
    if spec.kind == "light":
        if has_presence and not here:
            return False, "nobody in the room"
        if not has_presence and not home:
            return False, "nobody home"
        if lux is not None and lux >= light_on_lux:
            return False, f"bright enough ({lux:.0f} lx)"
        return True, "someone in the room and it is dark" if lux is not None else "someone in the room"
    if spec.kind == "thermal":
        if spec.temp_on is not None:
            if temp is None:
                return False, "no temperature reading"
            if spec.heating and temp >= spec.temp_on:
                return False, f"room already {temp:.1f}°C"
            if not spec.heating and temp <= spec.temp_on:
                return False, f"room only {temp:.1f}°C"
        if not home and not here:
            return False, "nobody home"
        return True, (f"room {temp:.1f}°C and someone home" if temp is not None else "someone home")
    # generic device: presence in its room (or someone home if the room has no presence sensor)
    if has_presence:
        return (True, "someone in the room") if here else (False, "nobody in the room")
    return (True, "someone home") if home else (False, "nobody home")
