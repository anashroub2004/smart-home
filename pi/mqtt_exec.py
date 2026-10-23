"""
MqttExecutor — "send an order to a node and wait for its answer", shared by commands.py (the app's orders)
and automation.py (the rules' and the AI's decisions).

    ex = MqttExecutor(client, timeout_s=5)
    ex.send(dev, room, {"v": 1, "level": 70}, src="ai", done=..., failed=...)   # -> home/<room>/<dev>/set
    ex.send(dev, room, {"v": 1}, src="web", lock=True, ...)                     # -> home/door/open
    ex.answer(dev, state)     # every home/+/+/state message
    ex.check_timeouts()       # every second: no answer in timeout_s -> failed(dev, why)

A state message is the answer when it has the same v (and the same level / mode when they were asked for);
a wall button pressed meanwhile is not. No automatic retry (a retry could switch the device twice).
Standard library + an already connected paho client.
"""
import json
import threading
import time


class MqttExecutor:
    def __init__(self, client, timeout_s=5.0):
        self.client, self.timeout_s = client, timeout_s
        self.lock = threading.Lock()
        self.waiting = {}         # device -> job waiting for the node's answer

    def busy(self, dev):
        with self.lock:
            return dev in self.waiting

    def send(self, dev, room, body, src, done, failed, node=None, lock=False):
        """done(dev, state, latency_ms) / failed(dev, why) are called once, outside any lock."""
        want = {"v": 1} if lock else {"v": int(bool(body.get("v"))), "level": body.get("level"),
                                      "mode": body.get("mode")}
        with self.lock:
            self.waiting[dev] = {"want": want, "sent": time.time(), "done": done, "failed": failed, "node": node}
        if lock:
            self.client.publish("home/door/open", json.dumps({"src": src}), qos=1)
        else:
            msg = {k: body[k] for k in ("v", "level", "mode") if body.get(k) is not None}
            msg["src"] = src
            self.client.publish(f"home/{room}/{dev}/set", json.dumps(msg), qos=1)

    @staticmethod
    def matches(want, st):
        if int(bool(st.get("v"))) != want["v"]:
            return False
        return all(st.get(k) == want[k] for k in ("level", "mode") if want.get(k) is not None)

    def answer(self, dev, st):
        with self.lock:
            job = self.waiting.get(dev)
            if not job or not self.matches(job["want"], st):
                return
            self.waiting.pop(dev)
        job["done"](dev, st, int((time.time() - job["sent"]) * 1000))

    def check_timeouts(self):
        now = time.time()
        with self.lock:
            late = [(d, j) for d, j in self.waiting.items() if now - j["sent"] > self.timeout_s]
            for d, _ in late:
                self.waiting.pop(d)
        for dev, job in late:
            job["failed"](dev, f"No response from the {job['node']} node ({self.timeout_s:.0f} s)")
