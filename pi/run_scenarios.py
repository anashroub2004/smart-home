"""
Run the digital twin's ten scenarios on the REAL Pi services (stage 1e-3) — the gate of stage 1.

Start first (each in its own window), all on the shared fast clock:
    firebase emulators:start --only auth,database
    python pi/fake_nodes.py --world --speed 60
    python pi/mqtt_ingest.py --sim-clock --db ai/data/home_sim.db
    python pi/commands.py   --sim-clock --db ai/data/home_sim.db
    python pi/automation.py --sim-clock --db ai/data/home_sim.db       (wait for "AI ready")
Then:
    python pi/run_scenarios.py                      # all ten
    python pi/run_scenarios.py forgot_light guard_hit

Each scenario is asked for on hub/test/scenario; automation runs it (pi/scenarios.py through
pi/scenario_bridge.py) and answers on hub/test/scenario/result. Results are also appended to
pi/twin/reports/<date>_scenarios.md, like the twin's.
"""
import json
import queue
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit('paho-mqtt is missing: pip install "paho-mqtt>=2.0,<3"')

ALL = ["forgot_light", "still_person", "guard_miss", "guard_hit", "light_waits", "suggestion_expiry",
       "manual_override", "power_fault", "trust_ladder", "full_day"]
TIMEOUT_S = 1800          # one scenario may take this long (real time)


def main():
    ids = sys.argv[1:] or ALL
    results = queue.Queue()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-run-scenarios")
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    import mqtt_auth
    mqtt_auth.login(c)                                      # MQTT_USER / MQTT_PASS if the broker wants a login
    c.on_message = lambda cl, u, m: results.put(json.loads(m.payload))
    c.connect("localhost", 1883, keepalive=30)
    c.subscribe("hub/test/scenario/result", qos=1)
    c.loop_start()
    time.sleep(1)
    passed = []
    for sid in ids:
        t0 = time.time()
        print(f"{sid:<18} ...", end="", flush=True)
        c.publish("hub/test/scenario", json.dumps({"id": sid}), qos=1)
        try:
            while True:
                r = results.get(timeout=TIMEOUT_S)
                if r.get("id") == sid:
                    break
        except queue.Empty:
            print(f"\r{sid:<18} FAIL  no result in {TIMEOUT_S // 60} min (is automation running with --sim-clock?)")
            continue
        passed.append(r["ok"])
        print(f"\r{sid:<18} {'PASS' if r['ok'] else 'FAIL'}  ({time.time() - t0:.0f} s)  {r['detail']}", flush=True)
    c.loop_stop()
    print(f"\n{sum(passed)} / {len(ids)} PASS")
    sys.exit(0 if passed and all(passed) and len(passed) == len(ids) else 1)


if __name__ == "__main__":
    main()
