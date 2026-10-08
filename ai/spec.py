"""Which devices the AI handles and what each model reads — built from /config, never hard-coded.

A device takes part when caps.power == "write" and control.ai == true.
Optional per-device overrides in the config:  "ai": {"act": 0.85, "lead_min": 0, "off_after_min": 8,
"grace_min": 10, "temp_on": 27}. Anything missing gets a sensible default from the device's capabilities.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import settings as S


def load_config(path=None):
    data = json.loads(Path(path or S.CONFIG_PATH).read_text(encoding="utf-8"))
    return data.get("config", data)          # accepts docs/seed.json or a raw /config export


def num(v, default):
    """A number from the config, or the default if missing / not a number (the app may write null)."""
    try:
        f = float(v)
        return default if f != f else f        # NaN -> default
    except (TypeError, ValueError):
        return default


def device_kind(cfg):
    """light | thermal | generic — decides which sensors gate it. Derived from icon + rules, so a new
    'heater' or 'lamp' device works without code changes."""
    icon, rules = cfg.get("icon"), cfg.get("rules") or {}
    if icon == "light" or rules.get("on_when_dark"):
        return "light"
    if icon in ("fan", "ac", "heater") or rules.get("follow_temp"):
        return "thermal"
    return "generic"


@dataclass
class DeviceSpec:
    id: str
    room: str
    name: str
    kind: str                       # light | thermal | generic
    watts: float
    state: str                      # SQLite keys
    level_key: str
    power_key: str
    occ: str | None
    temp: str | None
    lux: str | None
    hum: str | None = None              # humidity in its own room (bathroom exhaust fan, kitchen hood)
    presence_keys: list = field(default_factory=list)   # mmwave / pir keys of the room (vacancy confirmation)
    context: list = field(default_factory=list)          # monitor-only devices in the same room
    lead_min: int = 0
    off_after_min: int = 10
    grace_min: int = S.GUARD_GRACE_MIN
    act_override: float | None = None
    suggest_override: float | None = None
    temp_on: float | None = None
    heating: bool = False           # heater: the gate wants the room COLD, not hot
    levels: list | None = None
    energy: str = "estimate"
    rules: dict = field(default_factory=dict)
    control: dict = field(default_factory=dict)


def room_presence_keys(rid, room):
    hw = " ".join(room.get("hardware") or []).lower()
    keys = []
    if "mmwave" in hw:
        keys.append(f"{rid}/mmwave")
    if "pir" in hw:
        keys.append(f"{rid}/pir")
    return keys


def ai_devices(config):
    """device id -> DeviceSpec for every AI-controlled device."""
    rooms = config["rooms"]
    th = config.get("thresholds") or {}
    climate = [r for r, rc in rooms.items() if "temp" in (rc.get("sensors") or [])]
    bright = [r for r, rc in rooms.items() if "lux" in (rc.get("sensors") or [])]
    out = {}
    for rid, room in rooms.items():
        sensors = room.get("sensors") or []
        devices = room.get("devices") or {}
        context = [f"device/{d}" for d, c in devices.items() if (c.get("caps") or {}).get("power") == "read"]
        for dev, cfg in devices.items():
            caps, ctrl = cfg.get("caps") or {}, cfg.get("control") or {}
            if caps.get("power") != "write" or not ctrl.get("ai"):
                continue
            kind = device_kind(cfg)
            ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
            heating = cfg.get("icon") == "heater" or "heat" in str(cfg.get("name", "")).lower()
            out[dev] = DeviceSpec(
                id=dev, room=rid, name=cfg.get("name", dev), kind=kind, watts=num(cfg.get("watts"), 0.0),
                state=f"device/{dev}", level_key=f"level/{dev}", power_key=f"power/{dev}",
                occ=f"{rid}/occ" if "occ" in sensors else None,
                # a room without its own sensor borrows the nearest one we have
                temp=f"{rid}/temp" if "temp" in sensors else (f"{climate[0]}/temp" if climate else None),
                # a windowless room (bathroom) is always dark: no light reading to borrow
                lux=None if room.get("windowless") else (
                    f"{rid}/lux" if "lux" in sensors else (f"{bright[0]}/lux" if bright else None)),
                hum=f"{rid}/hum" if "hum" in sensors else None,
                presence_keys=room_presence_keys(rid, room),
                context=context,
                lead_min=int(num(ai.get("lead_min"), S.LEAD_MIN_THERMAL if kind == "thermal" else 0)),
                off_after_min=int(num(ai.get("off_after_min"), S.OFF_AFTER_MIN[kind])),
                grace_min=int(num(ai.get("grace_min"), S.GUARD_GRACE_MIN)),
                act_override=num(ai.get("act"), None),
                suggest_override=num(ai.get("suggest"), None),
                temp_on=num(ai.get("temp_on"), None) if ai.get("temp_on") is not None else (
                    (num(th.get("heater_on_temp"), 20) if heating else num(th.get("fan_on_temp"), 28))
                    if kind == "thermal" else None),
                heating=heating and kind == "thermal",
                levels=(caps.get("level") or {}).get("steps"),
                energy=caps.get("energy", "estimate"),
                rules=cfg.get("rules") or {}, control=ctrl,
            )
    return out


def monitored_devices(config):
    """Monitor-only devices (power: read) -> (room, cfg). Used for context features, anomalies and waste."""
    out = {}
    for rid, room in config["rooms"].items():
        for dev, cfg in (room.get("devices") or {}).items():
            if (cfg.get("caps") or {}).get("power") == "read":
                out[dev] = (rid, cfg)
    return out


def energy_devices(config):
    """Every device whose power we measure or estimate -> (room, cfg)."""
    out = {}
    for rid, room in config["rooms"].items():
        for dev, cfg in (room.get("devices") or {}).items():
            caps = cfg.get("caps") or {}
            if caps.get("lock") or caps.get("energy", "estimate") == "none" or not num(cfg.get("watts"), 0):
                continue
            out[dev] = (rid, cfg)
    return out


def device_label(config, dev):
    """'Living room fan', 'Bedroom lights', 'Washer' — same wording as the Pi / simulator events."""
    for rid, room in config["rooms"].items():
        cfg = (room.get("devices") or {}).get(dev)
        if cfg is None:
            continue
        rname = room.get("name", rid)
        name = cfg.get("name", dev)
        if cfg.get("icon") in ("washer", "lock") or name.lower().startswith(rname.lower()):
            return name
        return f"{rname} {name if name.isupper() else name.lower()}"
    return dev


def visible_rooms(config):
    return [r for r, rc in config["rooms"].items() if not rc.get("hidden")]
