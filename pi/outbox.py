"""
Local first (the Pi services): the house never waits for the internet.

    Outbox       what a service sends to Firebase waits in SQLite until Firebase takes it, oldest first;
                 History lines, access log and alerts keep the time they happened (`at`)
    read_config  /config from Firebase, kept in SQLite, so a service can start with no internet

Used by security.py (table "access") and mqtt_ingest.py (table "outbox") — docs/contract.md "SQLite".
"""
import json
import threading
import time
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "docs" / "seed.json"
WITH_TIME = {"log_event", "log_access", "push_alert"}     # Writer calls that take the original time
KEEP_SENT_DAYS = 7                                         # sent rows are deleted after a week

PRINT_LOCK = threading.Lock()


def say(*parts):
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Outbox:
    """Writer calls kept in SQLite until Firebase took them: out.log_event(...) is Writer.log_event(...) later."""

    def __init__(self, con, writer, clock=time.time, table="outbox"):
        self.con, self.w, self.clock, self.table = con, writer, clock, table
        self.lock = threading.Lock()
        self.down = False                     # Firebase could not be reached the last time
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER, "
                    "kind TEXT, data TEXT, sent INTEGER DEFAULT 0)")
        con.commit()

    def add(self, kind, *args, **kw):
        if not callable(getattr(self.w, kind, None)):
            raise AttributeError(f"Writer has no {kind}")
        with self.lock:
            self.con.execute(f"INSERT INTO {self.table} (at, kind, data) VALUES (?,?,?)",
                             (int(self.clock() * 1000), kind, json.dumps({"a": args, "k": kw})))
            self.con.commit()

    def __getattr__(self, kind):              # out.write_summary(...), out.log_event(...) ...
        if kind.startswith("_"):
            raise AttributeError(kind)
        return lambda *a, **k: self.add(kind, *a, **k)

    def pending(self):
        return self.con.execute(f"SELECT COUNT(*) FROM {self.table} WHERE sent = 0").fetchone()[0]

    def flush(self):
        """Send what is waiting, oldest first. Stops at the first failure (the internet is down) and keeps the rest."""
        with self.lock:
            rows = self.con.execute(f"SELECT id, at, kind, data FROM {self.table} WHERE sent = 0 ORDER BY id").fetchall()
        sent = 0
        for rid, at, kind, data in rows:
            d = json.loads(data)
            kw = {**d["k"], **({"at": at} if kind in WITH_TIME else {})}
            try:
                getattr(self.w, kind)(*d["a"], **kw)
            except Exception as e:  # noqa: BLE001 — no internet / Firebase down: try again later
                if not self.down:
                    say(f"!! Firebase not reachable ({type(e).__name__}): {len(rows) - sent} kept in SQLite")
                self.down = True
                return False
            with self.lock:
                self.con.execute(f"UPDATE {self.table} SET sent = 1 WHERE id = ?", (rid,))
                self.con.commit()
            sent += 1
        if self.down and sent:
            say(f"Firebase back: sent {sent} kept while it was down")
        if self.down or sent:
            with self.lock:
                old = int((self.clock() - KEEP_SENT_DAYS * 86400) * 1000)
                self.con.execute(f"DELETE FROM {self.table} WHERE sent = 1 AND at < ?", (old,))
                self.con.commit()
        self.down = False
        return True


class Offline(ConnectionError):
    """Firebase is known to be unreachable right now: the call was not even tried (it would hang)."""


class LocalFirstWriter:
    """A Writer for a service whose own work must never wait for the internet (automation, commands):
        - the calls in `queued` (History lines, alerts ...) go to the outbox and reach Firebase later
        - the calls in `fallback` (a command's done / failed) go straight to Firebase when it works (no delay
          for the app) and to the outbox when it does not
        - any other call goes straight to Firebase, but while Firebase is known to be down it fails at once
          (Offline) instead of hanging up to 10 s in the service's loop
        - start_sender(): a thread empties the outbox and notices when Firebase is back"""

    def __init__(self, writer, outbox, queued=("log_event", "log_access", "push_alert"), fallback=(), retry_s=10.0):
        self.w, self.out, self.queued, self.fallback, self.retry_s = writer, outbox, set(queued), set(fallback), retry_s
        self.down = False
        self.db = _GuardedDB(self)

    def guard(self, fn, *a, **kw):
        if self.down:
            raise Offline("Firebase not reachable")
        try:
            return fn(*a, **kw)
        except Exception:
            if not self.down:
                say("!! Firebase not reachable: the house goes on, the app catches up later")
            self.down = True
            raise

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.queued:
            return lambda *a, **k: self.out.add(name, *a, **k)
        fn = getattr(self.w, name)
        if name in self.fallback:
            return lambda *a, **k: self._direct_or_later(name, fn, a, k)
        return (lambda *a, **k: self.guard(fn, *a, **k)) if callable(fn) else fn

    def _direct_or_later(self, name, fn, a, k):
        if self.out.pending() == 0:                         # keep the order: never overtake what waits
            try:
                return self.guard(fn, *a, **k)
            except Exception:  # noqa: BLE001
                pass
        self.out.add(name, *a, **k)

    def sender(self, stop):
        while not stop.is_set():
            ok = self.out.flush()
            if ok and self.down:
                try:
                    self.w.db.get("config/version")              # a tiny read: is Firebase back?
                    self.down = False
                    say("Firebase back")
                except Exception:  # noqa: BLE001
                    ok = False
            elif ok:
                self.down = False
            stop.wait(1.0 if ok else self.retry_s)

    def start_sender(self):
        stop = threading.Event()
        t = threading.Thread(target=self.sender, args=(stop,), daemon=True)
        t.start()
        return stop, t


class _GuardedDB:
    """writer.db (get / put / patch / post / delete) behind the same guard."""

    def __init__(self, lf):
        self.lf = lf

    def __getattr__(self, name):
        fn = getattr(self.lf.w.db, name)
        return (lambda *a, **k: self.lf.guard(fn, *a, **k)) if callable(fn) else fn


def keep_config(con, cfg):
    con.execute("INSERT OR REPLACE INTO meta VALUES ('kept_config', ?)", (json.dumps(cfg),))
    con.commit()


def kept_config(con):
    """The last /config this hub saw, else docs/seed.json (the very first start, without the internet)."""
    row = con.execute("SELECT value FROM meta WHERE key = 'kept_config'").fetchone()
    if row:
        return json.loads(row[0])
    say("!! no /config kept yet: using docs/seed.json")
    return json.loads(SEED.read_text(encoding="utf-8"))["config"]


def read_config(w, con, quiet=False):
    """/config from Firebase (kept in SQLite). Not reachable: the kept one — or None with quiet (a periodic check)."""
    try:
        cfg = w.get_config()
        if cfg:
            keep_config(con, cfg)
            return cfg
    except Exception as e:  # noqa: BLE001
        if not quiet:
            say(f"!! cannot read /config ({type(e).__name__}): using the last one kept in SQLite")
    return None if quiet else kept_config(con)
