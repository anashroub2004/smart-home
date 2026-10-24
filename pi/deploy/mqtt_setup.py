"""
The lock on Mosquitto (docs/contract.md "MQTT login"): nobody connects without a user and password, and each
user may touch only its own topics. Run by install.sh; run it again (sudo) after adding a ROOM in the app.

    hub          the Pi services (mqtt_ingest, commands, automation, security): home/# and hub/#
    <node>       one user per node in /config "nodes": writes only its rooms (home/<room>/#) and its status /
                 config ack, reads only the /set of its rooms' devices and its config.
                 The door node (the one with a lock) also writes home/door/event and reads home/door/open.
                 Nobody but the hub can publish home/door/open or a node's config.

Rooms and nodes come from /config (the copy kept in SQLite by the services, else docs/seed.json) — nothing
about the house is written here. Passwords already given are kept (the boards keep working); new users get a
random one.

Writes (paths are options, install.sh passes the Pi's):
    --conf         Mosquitto listener + allow_anonymous false   (/etc/mosquitto/conf.d/smart-home.conf)
    --passwd       hashed passwords (mosquitto_passwd -U)       (/etc/mosquitto/smart-home.passwd)
    --acl          who may touch what                           (/etc/mosquitto/smart-home.acl)
    --hub-env      MQTT_USER / MQTT_PASS for the Pi services    (/etc/smart-home/mqtt.env)
    --nodes-file   "user:password" for every user, for the ESP32 firmware and fake_nodes.py --auth
                                                                (/etc/smart-home/mqtt-users.txt, root only)
Standard library only (+ the mosquitto_passwd tool).
"""
import argparse
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "docs" / "seed.json"
RESERVED_ROOMS = {"door"}          # home/door/... is the door's event / open, never a room's


def load_config(path=None, db=None):
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data.get("config", data)
    if db and Path(db).exists():
        row = sqlite3.connect(db).execute("SELECT value FROM meta WHERE key = 'kept_config'").fetchone()
        if row:
            return json.loads(row[0])
    return json.loads(SEED.read_text(encoding="utf-8"))["config"]


def acl_text(config):
    lines = ["# smart-home — written by pi/deploy/mqtt_setup.py from /config (run it again after adding a room)", "",
             "# the hub: the Pi services", "user hub", "topic readwrite home/#", "topic readwrite hub/#", ""]
    rooms_of = {}
    for rid, room in config["rooms"].items():
        if rid in RESERVED_ROOMS:
            print(f"!! room id '{rid}' is reserved (home/{rid}/... is the door's): skipped", file=sys.stderr)
            continue
        rooms_of.setdefault(room.get("node"), []).append((rid, room))
    for node in sorted(config.get("nodes") or {}):
        lines += [f"user {node}", f"topic write home/{node}/status", f"topic write home/{node}/config/ack",
                  f"topic read home/{node}/config"]
        for rid, room in sorted(rooms_of.get(node, [])):
            lines += [f"topic write home/{rid}/#", f"topic read home/{rid}/+/set"]
            if any("lock" in (d.get("caps") or {}) for d in (room.get("devices") or {}).values()):
                lines += ["topic write home/door/event", "topic read home/door/open"]
        lines.append("")
    return "\n".join(lines)


def read_users(path):
    users = {}
    if path and Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and ":" in line:
                u, p = line.split(":", 1)
                users[u.strip()] = p.strip()
    return users


def write(path, text, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if mode is not None:
        os.chmod(path, mode)


def main():
    ap = argparse.ArgumentParser(description="Mosquitto users + ACL for the smart home, from /config")
    ap.add_argument("--config", help="a /config JSON (default: the copy kept in --db, else docs/seed.json)")
    ap.add_argument("--db", default=str(ROOT / "ai" / "data" / "home.db"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--conf", default="/etc/mosquitto/conf.d/smart-home.conf")
    ap.add_argument("--passwd", default="/etc/mosquitto/smart-home.passwd")
    ap.add_argument("--acl", default="/etc/mosquitto/smart-home.acl")
    ap.add_argument("--hub-env", default="/etc/smart-home/mqtt.env")
    ap.add_argument("--nodes-file", default="/etc/smart-home/mqtt-users.txt")
    ap.add_argument("--mosquitto-passwd", default="mosquitto_passwd", help="the mosquitto_passwd tool")
    args = ap.parse_args()

    config = load_config(args.config, args.db)
    users = read_users(args.nodes_file)                       # keep the passwords already given
    names = ["hub"] + sorted(config.get("nodes") or {})
    new = [u for u in names if u not in users]
    for u in new:
        users[u] = secrets.token_urlsafe(18)
    users = {u: users[u] for u in names}                      # a node removed from /config loses its login

    write(args.nodes_file, "# MQTT users (pi/deploy/mqtt_setup.py) — keep secret: the ESP32 firmware needs its line\n"
          + "".join(f"{u}:{p}\n" for u, p in users.items()), 0o600)
    write(args.hub_env, f"MQTT_USER=hub\nMQTT_PASS={users['hub']}\n", 0o600)
    write(args.passwd, "".join(f"{u}:{p}\n" for u, p in users.items()), 0o600)
    subprocess.run([args.mosquitto_passwd, "-U", args.passwd], check=True)          # hash them in place
    write(args.acl, acl_text(config))
    write(args.conf, "# smart-home (pi/deploy/mqtt_setup.py): logins required, each user only its topics\n"
          f"listener {args.port}\nallow_anonymous false\n"
          f"password_file {Path(args.passwd).as_posix()}\nacl_file {Path(args.acl).as_posix()}\n")
    print(f"MQTT users: {', '.join(names)}" + (f" (new: {', '.join(new)})" if new else " (passwords kept)"))
    print(f"ACL from /config: {len(config['rooms'])} rooms, {len(names) - 1} nodes -> {args.acl}")


if __name__ == "__main__":
    main()
