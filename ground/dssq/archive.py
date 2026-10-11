"""Ground archive — the analogue of a mission's raw-frame and telemetry databases.

  <archive>/YYYYMMDD/cadu.bin   raw CADUs exactly as received
                                record: i64 ERT_ns | i16 RSSI dBm (0x7FFF n/a) | u16 len | CADU
  <archive>/gds.sqlite          frames, packets, decommutated parameters, events, commands
  <archive>/YYYYMMDD/events.log human-readable event log

Packets carry their `source`: "RF" (received in a frame) or "CFDP" (decoded from an SSR
file delivered by CFDP, Phase B F-47; those are stored with vcid 2 like an SSR playback).
An archive created by an earlier version gains the column on open (ALTER TABLE).

The archive is not thread-safe by itself: the GDS calls it under its state lock only.

Writes are best-effort: an I/O error (disk full: OSError ENOSPC, or sqlite3 "database or
disk is full") is counted in `write_errors` with `last_error` and never raised, so a full
disk stops the recording, not the live downlink, FOP-1 or commanding. The GDS reports the
count (rate-limited WARN event, YELLOW alarm, health).
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import struct
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (ert REAL, mcfc INT, vcid INT, vcfc INT, fhp INT,
    rs_corrected INT, rssi INT, snr REAL, ok INT, reason TEXT);
CREATE TABLE IF NOT EXISTS packets (ert REAL, scet REAL, apid INT, name TEXT, seq INT,
    sclk REAL, partition INT, vcid INT, raw BLOB, source TEXT DEFAULT 'RF');
CREATE TABLE IF NOT EXISTS params (ert REAL, scet REAL, packet TEXT, name TEXT, value REAL,
    limit_state TEXT);
CREATE TABLE IF NOT EXISTS events (t REAL, level TEXT, source TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS commands (t REAL, id INT, text TEXT, state TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_params ON params(name, ert);
CREATE INDEX IF NOT EXISTS ix_packets ON packets(apid, ert);
"""
COMMIT_INTERVAL_S = 2.0
RSSI_NA = 0x7FFF


class Archive:
    def __init__(self, root: str | Path, enabled: bool = True):
        self.enabled = enabled
        self.root = Path(root)
        self.db: sqlite3.Connection | None = None
        self._last_commit = time.time()
        self.write_errors = 0
        self.last_error: str | None = None
        if not enabled:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "gds.sqlite", check_same_thread=False)
        self.db.executescript(SCHEMA)
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(packets)")}
        if "source" not in cols:                        # archive written by v2
            self.db.execute("ALTER TABLE packets ADD COLUMN source TEXT DEFAULT 'RF'")
        self.db.commit()

    @contextlib.contextmanager
    def _best_effort(self) -> Iterator[None]:
        try:
            yield
        except (OSError, sqlite3.Error) as e:
            self.write_errors += 1
            self.last_error = f"{type(e).__name__}: {e}"[:300]

    def _day(self) -> Path:
        d = self.root / datetime.now(UTC).strftime("%Y%m%d")
        d.mkdir(exist_ok=True)
        return d

    def raw_cadu(self, ert: float, rssi: float | None, cadu: bytes) -> None:
        if not self.enabled:
            return
        r = RSSI_NA if rssi is None else max(-0x8000, min(0x7FFE, round(rssi)))
        with self._best_effort(), open(self._day() / "cadu.bin", "ab") as f:
            f.write(struct.pack(">qhH", int(ert * 1e9), r, len(cadu)) + cadu)

    def frame(self, **kw: Any) -> None:
        if self.db:
            with self._best_effort():
                self.db.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (kw.get("ert"), kw.get("mcfc"), kw.get("vcid"), kw.get("vcfc"),
                                 kw.get("fhp"), kw.get("rs"), kw.get("rssi"), kw.get("snr"),
                                 int(kw.get("ok", 1)), kw.get("reason", "")))
            self._maybe_commit()

    def packet(self, ert: float, scet: float | None, apid: int, name: str, seq: int, sclk: float | None,
               partition: int, vcid: int, raw: bytes, values: Mapping[str, Any], limits: Mapping[str, str],
               source: str = "RF") -> None:
        if not self.db:
            return
        rows = [(ert, scet, name, k, float(v), limits.get(k, "OK")) for k, v in values.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        with self._best_effort():
            self.db.execute("INSERT INTO packets (ert, scet, apid, name, seq, sclk, partition, vcid, raw, source) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (ert, scet, apid, name, seq, sclk, partition, vcid, raw, source))
            self.db.executemany("INSERT INTO params VALUES (?,?,?,?,?,?)", rows)
        self._maybe_commit()

    def event(self, t: float, level: str, source: str, text: str) -> str:
        line = f"{datetime.fromtimestamp(t, UTC).isoformat(timespec='seconds')} " \
               f"{level:5s} {source:8s} {text}"
        if self.enabled:
            with self._best_effort(), open(self._day() / "events.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.db:
            with self._best_effort():
                self.db.execute("INSERT INTO events VALUES (?,?,?,?)", (t, level, source, text))
            self._maybe_commit()
        return line

    def command(self, rec: Mapping[str, Any]) -> None:
        if self.db:
            with self._best_effort():
                self.db.execute("INSERT INTO commands VALUES (?,?,?,?,?)",
                                (time.time(), rec["id"], rec["text"], rec["state"], rec["detail"]))
            self._maybe_commit()

    def _maybe_commit(self) -> None:
        if self.db and time.time() - self._last_commit > COMMIT_INTERVAL_S:
            self._last_commit = time.time()             # a failing commit is retried a period later
            with self._best_effort():
                self.db.commit()

    def flush(self) -> None:
        """Commits the database and forces today's raw files to disk (shutdown path)."""
        if self.db:
            with self._best_effort():
                self.db.commit()
        if not self.enabled:
            return
        day = self.root / datetime.now(UTC).strftime("%Y%m%d")
        for name in ("cadu.bin", "events.log"):
            p = day / name
            if p.exists():
                with contextlib.suppress(OSError):
                    fd = os.open(p, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)

    def close(self) -> None:
        """Commit and close; idempotent."""
        db, self.db = self.db, None
        if db is not None:
            with self._best_effort():
                db.commit()
            db.close()


def read_cadu_file(path: str | Path) -> Iterator[tuple[float, int | None, bytes]]:
    """Yields (ert, rssi, cadu) from a ground archive OR (ert, None, cadu) from the
    spacecraft EGSE format ([u64 ns][u16 len][CADU], file name cadu_*)."""
    data = Path(path).read_bytes()
    i = 0
    egse = "cadu_" in Path(path).name
    while i < len(data):
        if egse:
            if i + 10 > len(data):
                break
            ns, n = struct.unpack_from(">QH", data, i)
            i += 10
            rssi = None
        else:
            if i + 12 > len(data):
                break
            ns, rssi, n = struct.unpack_from(">qhH", data, i)
            i += 12
            rssi = None if rssi == RSSI_NA else rssi
        if i + n > len(data):
            break                                      # truncated last record (power loss)
        yield ns / 1e9, rssi, data[i:i + n]
        i += n


def dumps(o: Any) -> str:
    return json.dumps(o, default=str)
