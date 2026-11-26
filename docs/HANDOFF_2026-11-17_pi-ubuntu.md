# HANDOFF — install the smart-home hub on the Raspberry Pi 5 (Ubuntu) — 2026-11-17

> For a new Claude chat that helps Iyad **on the Raspberry Pi itself** (Ubuntu Desktop, monitor + keyboard).
> Goal of that chat: take a freshly installed Ubuntu on the Pi 5 to "the four hub services run by themselves at
> every boot", step by step. Written from the main Claude Code chat that knows the repository.

## 0. How to work with Iyad (important)
- **Reply in Arabic, every message.** Commands, code, file names and commit messages stay in English.
- **One step at a time, with a gate:** give one step → Iyad runs it → he sends the output (often a phone photo of
  the screen) → you check it → only then the next step. Never stack several risky steps in one message.
- Give **exact commands in code blocks** and say **what output to expect**. Explain with short simple stories when
  something is unclear ("مش فاهم" = explain again, simpler, with an example).
- In the Ubuntu Terminal, paste is **Ctrl+Shift+V** and copy is **Ctrl+Shift+C** (remind him once).
- **Never ask him to paste a secret** (Firebase key content, passwords, Wi-Fi password) into the chat. A file's
  *existence* may be checked (`ls -l`), never its content (`cat` of a key is forbidden).
- Be honest about failures; fix the cause, don't loop. If a repo file must change, keep it small (see §6).

## 1. The project in 10 lines
- Graduation project "AI-Driven Adaptive Smart and Secure Home" (Palestine Polytechnic University). Team: Iyad
  Maree, Fares Salameh, Anas Hroub. Repo (public): https://github.com/anashroub2004/smart-home — `main` = `92e8490`.
- A small **mockup** house. **ESP32 nodes** read sensors / drive LEDs and servos and talk **MQTT** to the **Pi**
  (Mosquitto broker on the Pi). The **Pi services** (Python) record, decide (rules + a Gradient-Boosting AI) and
  talk to **Firebase Realtime Database** (project `ai-home-aef50`). A **Next.js web app** (https://ai-home-aef50.web.app)
  shows the house. ESP32s never talk to Firebase.
- **Local first:** the house keeps working without internet; Firebase writes wait in SQLite and are sent later.
- Read in the repo once cloned: `CLAUDE.md`, `docs/PROJECT_CONTEXT.md`, `pi/README.md` (section "على الـ Raspberry Pi 5"),
  `docs/updates/` (latest: `2026-11-12_01_mockup-hardware.md`).

## 2. State right now
| Item | State |
|---|---|
| Pi | Raspberry Pi 5 8GB, 128GB microSD, **Ubuntu Desktop 24.04.5 LTS (64-bit)**, just installed by Iyad |
| Access | **directly on the Pi** (monitor + keyboard + mouse, micro-HDMI). No SSH needed |
| Network | the team's **own small router** (2.4 GHz). The Pi should be on it with internet |
| ESP32 boards | 2 × classic **ESP32-D0WD-V3**, 4MB flash (PlatformIO `esp32dev`). Not part of this chat's job |
| Firebase key | on Iyad's Windows PC: `C:\dev\secrets\ai-home-key.json` (key id starts `7641bdd917` — an id, not a secret). The old leaked key was deleted. The Pi has **no key yet** (fresh install) |
| Still to buy | Raspberry Pi RTC battery (for the Pi 5 "BAT" connector), official 27W USB-C supply, door parts |
| Repo code for the Pi | done and tested on Windows: `pi/deploy/install.sh` + 4 systemd units + Mosquitto lock. **`install.sh` was written for Raspberry Pi OS and has never run on Ubuntu** — expect possible small fixes |

## 3. What `install.sh` does (read `pi/deploy/install.sh` before step 7)
`sudo bash pi/deploy/install.sh --key <key.json>` (run again later = update; data and passwords are kept):
1 apt: mosquitto, mosquitto-clients, python3-venv, python3-pip, rsync · 2 timezone **Asia/Hebron** · 3 system user
`smarthome` · 4 rsync the repo to **`/opt/smart-home`** (excludes web/, .git, ai/data, ai/models, pi/.flash) ·
5 venv `/opt/smart-home/.venv` + `pip install -r pi/requirements.txt` (AI: scikit-learn, pandas, numpy; google-auth,
requests, paho-mqtt) · 6 key → `/etc/smart-home/ai-home-key.json` (600) + `/etc/smart-home/smart-home.env`
(`FIREBASE_ARGS=--cloud …`) · 7 **Mosquitto lock** (`pi/deploy/mqtt_setup.py`: user + password per node and for the hub,
ACL — only the hub may publish `home/door/open`; files in `/etc/mosquitto/` and `/etc/smart-home/mqtt.env`,
`/etc/smart-home/mqtt-users.txt`) · 8 RTC battery check · 9 systemd: `smart-home-{ingest,commands,automation,security}`
+ `smart-home.target`, enabled at boot, `Restart=always`. At the end it prints each service's state (want: `active`).
On its first start `mqtt_ingest` upgrades the real `/config` from seed 2 to **seed 3** (expected, it is the mockup's parts).

**Ubuntu points to watch (untested):** mosquitto's default `/etc/mosquitto/mosquitto.conf` must include
`include_dir /etc/mosquitto/conf.d` (Debian/Ubuntu default does); Python is 3.12 (the PC's version — good); the RTC
sysfs path may differ (the check only prints a warning, harmless); `pip` inside the venv is fine (PEP 668 only
blocks system-wide pip). If a step fails, read the error, find the cause in the script, propose the smallest fix.

## 4. The steps (one per message, wait for the output each time)
**Step 1 — check (read only).** Terminal (Ctrl+Alt+T):
```bash
echo "== name"; hostname; whoami; echo "== OS"; grep PRETTY /etc/os-release; uname -m; echo "== python"; python3 --version; echo "== git"; git --version 2>/dev/null || echo "git: not installed"; echo "== disk"; df -h / | tail -1; echo "== memory"; free -h | head -2; echo "== network"; hostname -I; ping -c 2 -W 3 google.com >/dev/null 2>&1 && echo "internet: OK" || echo "internet: NO"; echo "== time"; timedatectl | grep -E "Local time|Time zone|synchronized"; echo "== updates waiting"; apt list --upgradable 2>/dev/null | tail -n +2 | wc -l; echo "== RTC"; ls /sys/class/rtc/ 2>/dev/null; cat /sys/class/rtc/rtc0/device/battery_voltage 2>/dev/null || echo "no battery reading"
```
Want: Ubuntu 24.04, aarch64, Python 3.12, `internet: OK`. No internet → fix the network first (Wi-Fi icon top right
→ the router's network, or an Ethernet cable to the router).

**Step 2 — update the system** (can take 10–30 min; reboot after):
```bash
sudo apt update && sudo apt full-upgrade -y && sudo apt install -y git
sudo reboot
```

**Step 3 — the Firebase key, by USB stick (never through the chat).** On the Windows PC copy
`C:\dev\secrets\ai-home-key.json` to a USB stick. On the Pi plug it in (Files app shows it), then:
```bash
cp /media/$USER/*/ai-home-key.json ~/ai-home-key.json && chmod 600 ~/ai-home-key.json && ls -l ~/ai-home-key.json
```
Want: one line `-rw------- … ai-home-key.json`. Then delete the file from the USB stick (on Windows).

**Step 4 — get the code:**
```bash
git clone https://github.com/anashroub2004/smart-home.git ~/smart-home && cd ~/smart-home && git log --oneline -1
```
Want: `92e8490 Merge pull request #14 …` (or newer).

**Step 5 — install** (first time 10–20 min; pip builds/downloads the AI libraries):
```bash
cd ~/smart-home && sudo bash pi/deploy/install.sh --key ~/ai-home-key.json
```
Want at the end: `ingest active`, `commands active`, `automation active`, `security active`, and a line about the
RTC battery (no battery yet → a warning, fine). Then:
```bash
journalctl -u smart-home-ingest -n 30 --no-pager
journalctl -u smart-home-automation -n 30 --no-pager
```
Want: ingest "connected to MQTT", "/config updated … seed 3" the first time; automation "AI starting … simulated
history" then "AI ready" (training a few minutes on the Pi is normal). No `Traceback`.

**Step 6 — try the house with fake nodes** (the real ESP32s come later):
```bash
sudo cp /etc/smart-home/mqtt-users.txt ~/mqtt-users.txt && sudo chown $USER ~/mqtt-users.txt && chmod 600 ~/mqtt-users.txt
/opt/smart-home/.venv/bin/python /opt/smart-home/pi/fake_nodes.py --world --auth ~/mqtt-users.txt --flash ~/fake-flash
```
Want: the three fake nodes say `online`. Iyad opens https://ai-home-aef50.web.app on his phone (he is already logged in)
→ rooms show live temperatures; tapping the living-room light works in < 1 s. In the fake-nodes console:
`pin wrong` ×5 → the red "Someone is trying to get in" alert on the phone. Stop with `quit`.
Do not leave fake nodes running for hours (they write to the real Firebase; free quota).

**Step 7 — reboot test:**
```bash
sudo reboot
```
After it is up (no login needed for the services):
```bash
systemctl is-active smart-home-ingest smart-home-commands smart-home-automation smart-home-security mosquitto
```
Want: five lines `active`. That is the gate: **the hub starts by itself**.

## 5. Later (not now unless Iyad asks)
- RTC battery when it arrives: plug into the Pi 5 "BAT" connector; it charges only with
  `dtparam=rtc_bbat_vchg=3000000` in `/boot/firmware/config.txt` (confirm the file on Ubuntu first), then reboot;
  re-run `install.sh` to see "ok: RTC battery … mV".
- A fixed IP for the Pi on the router (DHCP reservation) so the ESP32 nodes always find it.
- 48 h run; then the ESP32 firmware phase (PlatformIO, `esp32dev`) — that is another chat's job.

## 6. Rules
- **Never** `sim_house.py --reset` against the real database; never `--sim-clock` with `--cloud`; never commit or
  print the key, `mqtt-users.txt` or `mqtt.env`.
- `/etc/smart-home/mqtt-users.txt` holds the nodes' MQTT passwords (the ESP32 firmware needs its line later) — keep it
  on the Pi, never in the chat.
- If `install.sh` (or any repo file) needs a fix: make the smallest change, explain it to Iyad first, then either
  (a) commit on a branch in `~/smart-home` and give Iyad a `git format-patch` file to bring to the main PC, or
  (b) describe the exact change so the main Claude Code chat applies it and opens the PR. Re-run `install.sh` after.
- End of the work session: write the result (what worked, what was fixed, outputs) in Arabic in
  `docs/updates/2026-11-17_01_pi-ubuntu-install.md` (template: `docs/updates/TEMPLATE.md`) — or give the text to Iyad
  for the main chat.

## 7. Report back to the main chat
When done, Iyad brings back: the final `systemctl is-active` output, any error and fix, the RTC line, and the update
file text.
