"""SQLite on the Pi: raw readings stay inside the house.

Table `readings(ts INTEGER unix seconds, key TEXT, value REAL)` — written by the MQTT ingest service on the Pi
(and by the simulator). Keys:

    <room>/temp  <room>/lux  <room>/occ      SHT31, BH1750, fused presence (0/1)
    <room>/mmwave  <room>/pir                 each presence sensor separately (0/1), for vacancy confirmation
    device/<id>                               device state 0/1 (monitor-only devices: from the current they draw)
    level/<id>                                level / speed (e.g. 40, 70, 100)
    power/<id>                                INA226 watts
    override/<id>                             1 at the moment the user controlled it by hand (app / button)
    ai_miss/<id>                              1 from an AI switch-on that nobody needed, 0 when it ended
    mode/away                                 1 while the Away scene / vacation is on, 0 otherwise
    door/entry                                1 when someone came in through the front door

Writing on change + a heartbeat every 15 minutes is enough: the slot builder forward-fills.
Table `meta(key, value)` records e.g. `synth_until` (simulated history ends here).
"""
import sqlite3
import time
from pathlib import Path

from . import settings as S


def connect(db_path=None):
    path = str(db_path or S.DB_PATH)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS readings (ts INTEGER, key TEXT, value REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON readings(ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_key_ts ON readings(key, ts)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return con


def write(con, rows):
    """rows: iterable of (ts, key, value)."""
    con.executemany("INSERT INTO readings VALUES (?,?,?)", rows)
    con.commit()


def last_ts(con, key):
    row = con.execute("SELECT MAX(ts) FROM readings WHERE key = ?", (key,)).fetchone()
    return row[0]


def span_days(con):
    row = con.execute("SELECT MIN(ts), MAX(ts) FROM readings").fetchone()
    return 0 if row[0] is None else (row[1] - row[0]) / 86400


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))
    con.commit()


def prune(con, now_ts=None):
    cut = (now_ts or time.time()) - S.RETENTION_DAYS * 86400
    con.execute("DELETE FROM readings WHERE ts < ?", (int(cut),))
    con.commit()


class Recorder:
    """Writes readings the way the ingest service should: on change, plus a heartbeat every 15 min,
    and analogue values (temp, lux, power) only when they move enough."""

    STEP = {"temp": 0.3, "lux": 25, "power": 0.15}

    def __init__(self, con, heartbeat_s=900):
        self.con, self.heartbeat_s = con, heartbeat_s
        self.last = {}            # key -> (ts, value)

    def _step(self, key):
        tail = key.split("/")[-1] if "/" in key and key.split("/")[0] not in ("power", "level") else key.split("/")[0]
        return self.STEP.get(tail, 0)

    def observe(self, values, ts=None):
        ts = int(ts or time.time())
        rows = []
        for key, value in values.items():
            if value is None:
                continue
            value = float(value)
            prev = self.last.get(key)
            if prev is None or ts - prev[0] >= self.heartbeat_s or abs(value - prev[1]) > self._step(key):
                rows.append((ts, key, value))
                self.last[key] = (ts, value)
        if rows:
            write(self.con, rows)
        return len(rows)

    def event(self, key, value=1, ts=None):
        write(self.con, [(int(ts or time.time()), key, float(value))])
