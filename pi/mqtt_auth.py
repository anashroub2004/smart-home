"""
MQTT login (docs/contract.md "MQTT login"). On the Pi, Mosquitto refuses anyone without a user and password
(pi/deploy/mqtt_setup.py), and each user may touch only its own topics: the hub (the Pi services) everything,
a node only its rooms — so a phone on the Wi-Fi cannot send home/door/open.

    the Pi services   MQTT_USER / MQTT_PASS from the environment (systemd: /etc/smart-home/mqtt.env)
    fake_nodes.py     --auth FILE: "user:password" lines, one per node (the file mqtt_setup.py writes)
    nothing set       no login: the PC's local Mosquitto (anonymous, tests)
Standard library only.
"""
import os


def login(client, user=None, password=None):
    """Give a paho client its MQTT user before connect()."""
    user = user or os.environ.get("MQTT_USER")
    if user:
        client.username_pw_set(user, password if password is not None else os.environ.get("MQTT_PASS", ""))


def read_auth_file(path):
    """{user: password} from "user:password" lines (# comments allowed)."""
    users = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            user, password = line.split(":", 1)
            users[user.strip()] = password.strip()
    return users
