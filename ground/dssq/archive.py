"""Ground archive — the analogue of a mission's raw-frame and telemetry databases.

  <archive>/YYYYMMDD/cadu.bin   raw CADUs exactly as received
                                record: u64 ERT_ns | i16 RSSI dBm (0x7FFF n/a) | u16 len | CADU
  <archive>/gds.sqlite          frames, packets, decommutated parameters, events, commands
  <archive>/YYYYMMDD/events.log human-readable event log
"""
from __future__ import annotations

import json
import sqlite3
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (ert REAL, mcfc INT, vcid INT, vcfc INT, fhp INT,
    rs_corrected INT, rssi INT, snr REAL, ok INT, reason TEXT);
CREATE TABLE IF NOT EXISTS packets (ert REAL, scet REAL, apid INT, name TEXT, seq INT,
    sclk REAL, partition INT, vcid INT, raw BLOB);
CREATE TABLE IF NOT EXISTS params (ert REAL, scet REAL, packet TEXT, name TEXT, value REAL,
    limit_state TEXT);
CREATE TABLE IF NOT EXISTS events (t REAL, level TEXT, source TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS commands (t REAL, id INT, text TEXT, state TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_params ON params(name, ert);
CREATE INDEX IF NOT EXISTS ix_packets ON packets(apid, ert);
"""


class Archive:
    def __init__(self, root: str | Path, enabled: bool = True):
        self.enabled = enabled
        self.root = Path(root)
        self.db = None
        if not enabled:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "gds.sqlite", check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._last_commit = time.time()

    def _day(self) -> Path:
        d = self.root / datetime.now(timezone.utc).strftime("%Y%m%d")
        d.mkdir(exist_ok=True)
        return d

    def raw_cadu(self, ert: float, rssi: int | None, cadu: bytes):
        if not self.enabled:
            return
        with open(self._day() / "cadu.bin", "ab") as f:
            f.write(struct.pack(">qhH", int(ert * 1e9), 0x7FFF if rssi is None else rssi, len(cadu)))
            f.write(cadu)

    def frame(self, **kw):
        if self.db:
            self.db.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (kw.get("ert"), kw.get("mcfc"), kw.get("vcid"), kw.get("vcfc"),
                             kw.get("fhp"), kw.get("rs"), kw.get("rssi"), kw.get("snr"),
                             int(kw.get("ok", 1)), kw.get("reason", "")))
            self._maybe_commit()

    def packet(self, ert, scet, apid, name, seq, sclk, partition, vcid, raw, values, limits):
        if not self.db:
            return
        self.db.execute("INSERT INTO packets VALUES (?,?,?,?,?,?,?,?,?)",
                        (ert, scet, apid, name, seq, sclk, partition, vcid, raw))
        rows = [(ert, scet, name, k, float(v), limits.get(k, "OK")) for k, v in values.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        self.db.executemany("INSERT INTO params VALUES (?,?,?,?,?,?)", rows)
        self._maybe_commit()

    def event(self, t, level, source, text):
        line = f"{datetime.fromtimestamp(t, timezone.utc).isoformat(timespec='seconds')} " \
               f"{level:5s} {source:8s} {text}"
        if self.enabled:
            with open(self._day() / "events.log", "a") as f:
                f.write(line + "\n")
        if self.db:
            self.db.execute("INSERT INTO events VALUES (?,?,?,?)", (t, level, source, text))
            self._maybe_commit()
        return line

    def command(self, rec: dict):
        if self.db:
            self.db.execute("INSERT INTO commands VALUES (?,?,?,?,?)",
                            (time.time(), rec["id"], rec["text"], rec["state"], rec["detail"]))
            self._maybe_commit()

    def _maybe_commit(self):
        if time.time() - self._last_commit > 2:
            self.db.commit()
            self._last_commit = time.time()

    def close(self):
        if self.db:
            self.db.commit()
            self.db.close()


def read_cadu_file(path: str | Path):
    """Yields (ert, rssi, cadu) from a ground archive OR (ert, None, cadu) from the
    spacecraft EGSE format ([u64 ns][u16 len][CADU])."""
    data = Path(path).read_bytes()
    i = 0
    egse = path and "cadu_" in Path(path).name
    while i < len(data):
        if egse:
            ns, n = struct.unpack_from(">QH", data, i)
            i += 10
            rssi = None
        else:
            ns, rssi, n = struct.unpack_from(">qhH", data, i)
            i += 12
            rssi = None if rssi == 0x7FFF else rssi
        yield ns / 1e9, rssi, data[i:i + n]
        i += n


def dumps(o) -> str:
    return json.dumps(o, default=str)
