"""
Tiny Realtime Database client over REST (Python standard library only).

Used by the simulator against the local emulator. On the real Pi you can keep using it
against the real database with a service-account token, or swap it for firebase-admin —
anything with the same get/put/patch/post/delete methods works with firebase_writer.Writer.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

EMULATOR_URL = os.environ.get("FIREBASE_DATABASE_EMULATOR_HOST", "127.0.0.1:9000")
PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "demo-smart-home")


class RestDB:
    def __init__(self, base_url=None, namespace=None, token="owner"):
        # "Bearer owner" is the emulator's admin token: it bypasses security rules (like the Admin SDK).
        # token can also be a function that returns a fresh OAuth token (real Firebase, see cloud_db.py).
        self.base = (base_url or f"http://{EMULATOR_URL}").rstrip("/")
        self.ns = namespace if namespace is not None else f"{PROJECT_ID}-default-rtdb"
        self.token = token

    def _req(self, method, path, body=None, **query):
        q = {"ns": self.ns} if self.ns else {}
        # Firebase REST wants JSON values: orderBy='"at"', limitToLast=50
        q.update({k: json.dumps(v) for k, v in query.items()})
        url = f"{self.base}/{path.strip('/')}.json?{urllib.parse.urlencode(q)}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        token = self.token() if callable(self.token) else self.token
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} /{path} -> {e.code}: {e.read().decode(errors='ignore')}") from None

    def get(self, path, **query):
        return self._req("GET", path, **query)

    def put(self, path, value):
        return self._req("PUT", path, value)

    def patch(self, path, value):
        return self._req("PATCH", path, value)

    def post(self, path, value):
        """Push a child with an auto key. Returns the key."""
        res = self._req("POST", path, value)
        return res and res.get("name")

    def delete(self, path):
        return self._req("DELETE", path)

    def stream(self, path, idle_s=60):
        """Listen to a path (REST streaming, server-sent events): yields (event, {"path", "data"}) as it changes.
        First event: "put" at path "/" with the whole value; then "put" / "patch" for each change. Firebase sends
        "keep-alive" about every 30 s, so `idle_s` without a byte means the connection died silently -> OSError.
        Ends (StopIteration) on "auth_revoked" (the 1 h token expired: open it again for a fresh one); raises
        RuntimeError on "cancel" (no permission). The caller reconnects."""
        q = {"ns": self.ns} if self.ns else {}
        url = f"{self.base}/{path.strip('/')}.json?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        token = self.token() if callable(self.token) else self.token
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            r = urllib.request.urlopen(req, timeout=idle_s)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"STREAM /{path} -> {e.code}: {e.read().decode(errors='ignore')}") from None
        with r:
            event, data = None, []
            for raw in r:
                self.stream_bytes = getattr(self, "stream_bytes", 0) + len(raw)
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and event:                        # a blank line ends one event
                    body = "\n".join(data)
                    event_, event, data = event, None, []
                    if event_ == "keep-alive":
                        continue
                    if event_ == "auth_revoked":
                        return
                    if event_ == "cancel":
                        raise RuntimeError(f"STREAM /{path} cancelled by Firebase: {body}")
                    yield event_, (json.loads(body) if body and body != "null" else None)
