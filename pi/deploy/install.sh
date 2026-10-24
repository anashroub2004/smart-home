#!/usr/bin/env bash
# Smart home — install (or update) the four Pi services on a Raspberry Pi 5 (Raspberry Pi OS, 64-bit).
#
#   first time:   sudo bash pi/deploy/install.sh --key /path/to/ai-home-key.json
#   update:       git pull   then   sudo bash pi/deploy/install.sh        (the key and the passwords are kept)
#
# What it does (docs: pi/README.md "On the Raspberry Pi"):
#   1 packages (Mosquitto, Python)   2 timezone Asia/Hebron   3 user "smarthome"   4 code -> /opt/smart-home
#   5 Python packages (.venv)        6 the Firebase key -> /etc/smart-home        7 the lock on Mosquitto
#   8 the clock battery check        9 the four services start now and at every boot (systemd)
# The data is never deleted on an update: ai/data (SQLite), ai/models, pi/.flash.
set -euo pipefail

APP=/opt/smart-home
ETC=/etc/smart-home
SVC_USER=smarthome
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KEY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --key) KEY="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "run it with sudo:  sudo bash pi/deploy/install.sh ..."; exit 1; }
step() { echo; echo "== $*"; }

step "1/9 packages: Mosquitto, Python"
apt-get update -qq
apt-get install -y -qq mosquitto mosquitto-clients python3-venv python3-pip rsync >/dev/null

step "2/9 timezone: Asia/Hebron"
timedatectl set-timezone Asia/Hebron

step "3/9 user: $SVC_USER (the services run as this user, not root)"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$SVC_USER"

step "4/9 code: $SRC -> $APP"
mkdir -p "$APP"
if [[ "$SRC" != "$APP" ]]; then
  # --delete removes old code, but never the house's data (excluded paths are left alone)
  rsync -a --delete --exclude .git --exclude web --exclude node_modules --exclude __pycache__ \
        --exclude .venv --exclude ai/data --exclude ai/models --exclude pi/.flash --exclude pi/twin/reports \
        "$SRC/" "$APP/"
fi
mkdir -p "$APP/ai/data" "$APP/ai/models"
chown -R "$SVC_USER:$SVC_USER" "$APP"

step "5/9 Python packages (.venv) — the first time takes a few minutes"
sudo -u "$SVC_USER" python3 -m venv "$APP/.venv"
sudo -u "$SVC_USER" "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u "$SVC_USER" "$APP/.venv/bin/pip" install -q -r "$APP/pi/requirements.txt"

step "6/9 the Firebase key -> $ETC (only root and $SVC_USER can read it)"
mkdir -p "$ETC"
chmod 755 "$ETC"
if [[ -n "$KEY" ]]; then
  install -m 600 -o "$SVC_USER" -g "$SVC_USER" "$KEY" "$ETC/ai-home-key.json"
  echo "FIREBASE_ARGS=--cloud $ETC/ai-home-key.json" > "$ETC/smart-home.env"
  chmod 644 "$ETC/smart-home.env"
elif [[ ! -f "$ETC/smart-home.env" ]]; then
  echo "!! no Firebase key yet: give it once with  --key /path/to/ai-home-key.json"; exit 1
else
  echo "kept: $(cat "$ETC/smart-home.env")"
fi

step "7/9 the lock on Mosquitto: a password for each node and for the hub, each only its own topics"
"$APP/.venv/bin/python" "$APP/pi/deploy/mqtt_setup.py" --db "$APP/ai/data/home.db"
chown mosquitto:mosquitto /etc/mosquitto/smart-home.passwd /etc/mosquitto/smart-home.acl
chmod 600 /etc/mosquitto/smart-home.passwd
chmod 644 /etc/mosquitto/smart-home.acl
chmod 600 "$ETC/mqtt.env" "$ETC/mqtt-users.txt"
systemctl enable -q mosquitto
systemctl restart mosquitto
echo "the nodes' passwords (for the ESP32 firmware): sudo cat $ETC/mqtt-users.txt"

step "8/9 the clock battery (Raspberry Pi 5 RTC)"
BAT=$(cat /sys/class/rtc/rtc0/device/battery_voltage 2>/dev/null \
      || cat /sys/devices/platform/soc/soc:rpi_rtc/rtc/rtc0/battery_voltage 2>/dev/null || echo "")
if [[ -z "$BAT" ]]; then
  echo "?? no Raspberry Pi 5 RTC found: the time comes from the internet only (wrong after a power cut offline)"
elif (( BAT > 2500000 )); then
  echo "ok: RTC battery $((BAT / 1000)) mV — the clock survives a power cut without the internet"
else
  echo "!! RTC battery missing or empty ($((BAT / 1000)) mV): plug the Raspberry Pi RTC Battery into 'BAT'."
  echo "   It charges only with  dtparam=rtc_bbat_vchg=3000000  in /boot/firmware/config.txt (then reboot)."
fi

step "9/9 the four services: start now and at every boot"
install -m 644 "$APP"/pi/deploy/smart-home-*.service "$APP/pi/deploy/smart-home.target" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q smart-home.target smart-home-ingest smart-home-commands smart-home-automation smart-home-security
systemctl restart smart-home.target
sleep 8
echo
for s in ingest commands automation security; do
  printf "  %-12s %s\n" "$s" "$(systemctl is-active smart-home-$s || true)"
done
echo
echo "logs:     journalctl -u smart-home-ingest -f      (or -commands / -automation / -security)"
echo "restart:  sudo systemctl restart smart-home.target"
