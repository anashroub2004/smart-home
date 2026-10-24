"""
Words for the History screen, from /config — shared by the Pi services so they all say it the same way
(and the same way the simulator does): "Living room fan", "On · Medium", "Low → High".
Standard library only.
"""


def device_index(config):
    """device id -> (room id, device cfg) for every device in /config."""
    return {dev: (rid, cfg) for rid, rc in config["rooms"].items() for dev, cfg in (rc.get("devices") or {}).items()}


def label(config, dev):
    """'Living room fan', 'Bedroom lights', 'Washer', 'Front door'."""
    rid, cfg = device_index(config)[dev]
    room = config["rooms"][rid]["name"]
    if cfg.get("icon") in ("washer", "lock") or cfg["name"].lower().startswith(room.lower()):
        return cfg["name"]
    return f"{room} {cfg['name'] if cfg['name'].isupper() else cfg['name'].lower()}"


def level_label(cfg, value):
    """The nearest named level ('Low' / 'Medium' / 'High'), or None for a device without levels."""
    lv = (cfg.get("caps") or {}).get("level")
    if not lv:
        return None
    steps, labels = lv["steps"], lv.get("labels") or []
    i = min(range(len(steps)), key=lambda k: abs(steps[k] - (value or steps[len(steps) // 2])))
    return labels[i] if i < len(labels) else f"{steps[i]}%"


def state_label(cfg, st):
    """'Off', 'On', 'On · Medium', 'On · Cool'."""
    if not st.get("v"):
        return "Off"
    caps = cfg.get("caps") or {}
    if caps.get("level"):
        return f"On · {level_label(cfg, st.get('level'))}"
    if caps.get("mode") and st.get("mode"):
        return f"On · {st['mode']}"
    return "On"


def door_open_words(method, who=None, finger=None):
    """The History line of a door opening (security.py and the simulator): fields for log_event."""
    titles = {"web": "Front door unlocked remotely", "exit_button": "Front door opened from inside"}
    labels = {"web": "App", "fingerprint": "Fingerprint", "keypad": "Keypad", "exit_button": "Exit button"}
    shorts = {"web": "Owner · web app", "fingerprint": f"{who} · finger #{finger}",
              "keypad": "Correct PIN", "exit_button": "Opened from inside"}
    whys = {"web": "Remote unlock, confirmed in the app", "fingerprint": "Fingerprint matched on the R503 sensor",
            "keypad": "Correct PIN on the keypad", "exit_button": "Exit button next to the door"}
    by_labels = {"web": "Owner (web app)", "fingerprint": f"{who} (fingerprint #{finger})",
                 "keypad": "Someone with the PIN", "exit_button": "Someone at home"}
    return dict(title=titles.get(method, "Front door unlocked"), src_label=labels[method], short=shorts[method],
                why=whys[method], by_label=by_labels[method])
