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
