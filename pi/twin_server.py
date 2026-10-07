"""Digital twin web server — a small local page served by the simulator itself (http://localhost:8765).

    GET  /         the page (pi/twin/index.html)
    GET  /state    everything the page draws, as JSON (refreshed by the simulator every tick)
    POST /cmd      a control from the page, e.g. {"action": "speed", "value": 60} -> the state right after it

Only listens on this computer (127.0.0.1). Python standard library only; nothing goes to Firebase.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).resolve().parent / "twin" / "index.html"
ALLOWED = {"speed", "pause", "resume", "jump", "move", "routine", "still", "temp", "fault", "node", "press",
           "scenario", "scenario_stop", "wrong_pin"}


def start_twin(house, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):          # keep the simulator's console readable
            pass

        def _send(self, code, body, ctype, etag=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            if etag:
                self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path.startswith("/state"):
                tag, body = house.twin_pack
                if self.headers.get("If-None-Match") == tag:     # nothing new since the last poll
                    self.send_response(304)
                    self.send_header("ETag", tag)
                    self.end_headers()
                    return
                self._send(200, body, "application/json", etag=tag)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/cmd":
                return self._send(404, b"not found", "text/plain")
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 10_000)
                cmd = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, TypeError):
                return self._send(400, b"bad json", "text/plain")
            if not isinstance(cmd, dict) or cmd.get("action") not in ALLOWED:
                return self._send(400, b"unknown action", "text/plain")
            # wait (briefly) until the house applied it, then answer with the new state: the page updates at once
            done = threading.Event()
            cmd["_done"] = done
            house.commands.put(cmd)
            house.wake.set()
            done.wait(2.0)
            tag, body = house.twin_pack
            self._send(200, body, "application/json", etag=tag)

    server = None
    for p in range(port, port + 10):           # the port may be busy (another simulator)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("! digital twin page could not start (ports busy)")
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://localhost:{server.server_address[1]}"
