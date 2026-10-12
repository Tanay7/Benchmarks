"""Ground archive — the analogue of a mission's raw-frame and telemetry databases.

  <archive>/YYYYMMDD/cadu.bin   raw CADUs exactly as received (UTC day folders)
                                record: i64 ERT_ns | i16 RSSI dBm (0x7FFF n/a) | u16 len | CADU
  <archive>/YYYYMMDD/events.log human-readable event log
  <archive>/gds.sqlite          frames, packets, decommutated parameters, events, commands and
                                rx_stats (the 1 Hz receiver time series of the data logger,
                                contract 39; one row per second while the station runs)
  <archive>/exports/            CSV exports (dssq.export: CLI and the dashboard FILES tab)

Packets carry their `source`: "RF" (received in a frame) or "CFDP" (decoded from an SSR
file delivered by CFDP, Phase B F-47; those are stored with vcid 2 like an SSR playback).
An archive created by an earlier version gains the column, the rx_stats table and the time
indexes on open (ALTER TABLE / CREATE ... IF NOT EXISTS; the indexes of a large old archive
are built once, at that first open).

The database runs in WAL journal mode (synchronous NORMAL): a reader - the CSV export, a
read-only connection in another thread or process - never blocks the GDS writer, and a
power loss can lose at most the last commits, never corrupt the file. A file system
without WAL support keeps the rollback journal (the PRAGMA answer is checked, not assumed).

The archive is not thread-safe by itself: the GDS calls it under its state lock only.

Writes are best-effort: an I/O error (disk full: OSError ENOSPC, or sqlite3 "database or
disk is full") is counted in `write_errors` with `last_error` and never raised, so a full
disk stops the recording, not the live downlink, FOP-1 or commanding. The GDS reports the
count (rate-limited WARN event, YELLOW alarm, health).

`raw_paused` (set by the data logger's free-space guard, dssq.datalog) skips the two
growing raw products - cadu.bin records and rx_stats rows - and counts what was skipped;
the frame / packet / event / command tables keep recording. The retention primitives
(day folders, logical database size, batched row deletion) live here; the policy (when,
how much) lives in dssq.datalog.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
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
CREATE TABLE IF NOT EXISTS rx_stats (t REAL, los INT, pass_no INT, n_rx INT, rssi_dbm REAL, snr_db REAL,
    snr_est_db REAL, noise_dbm REAL, margin_db REAL, freq_err_hz REAL, fer_10m REAL, rs_symbols INT,
    cb_uncorrectable INT, frames_ok INT, frames_lost INT, afc_hz REAL, doppler_hz REAL, range_rate_m_s REAL);
CREATE INDEX IF NOT EXISTS ix_params ON params(name, ert);
CREATE INDEX IF NOT EXISTS ix_packets ON packets(apid, ert);
CREATE INDEX IF NOT EXISTS ix_rx_stats_t ON rx_stats(t);
CREATE INDEX IF NOT EXISTS ix_frames_ert ON frames(ert);
CREATE INDEX IF NOT EXISTS ix_params_ert ON params(ert);
CREATE INDEX IF NOT EXISTS ix_packets_ert ON packets(ert);
CREATE INDEX IF NOT EXISTS ix_events_t ON events(t);
CREATE INDEX IF NOT EXISTS ix_commands_t ON commands(t);
"""
COMMIT_INTERVAL_S = 2.0
RSSI_NA = 0x7FFF
DB_NAME = "gds.sqlite"
EXPORTS_DIR = "exports"
DAY_RE = re.compile(r"[0-9]{8}")                     # <archive>/YYYYMMDD (UTC day folders)
# rx_stats columns in table order (dssq.rxstats.ReceiverStats.interval_row() produces them;
# dssq.export writes them with their units).
RX_STATS_COLUMNS = ("t", "los", "pass_no", "n_rx", "rssi_dbm", "snr_db", "snr_est_db", "noise_dbm", "margin_db",
                    "freq_err_hz", "fer_10m", "rs_symbols", "cb_uncorrectable", "frames_ok", "frames_lost",
                    "afc_hz", "doppler_hz", "range_rate_m_s")
# Tables the retention prunes by time (contract 39: the high-rate ones), with their time column.
PRUNE_TABLES = (("rx_stats", "t"), ("params", "ert"), ("frames", "ert"))
_RX_INSERT = f"INSERT INTO rx_stats ({', '.join(RX_STATS_COLUMNS)}) VALUES ({', '.join('?' * len(RX_STATS_COLUMNS))})"


class Archive:
    def __init__(self, root: str | Path, enabled: bool = True):
        self.enabled = enabled
        self.root = Path(root)
        self.db: sqlite3.Connection | None = None
        self._last_commit = time.time()
        self.write_errors = 0
        self.last_error: str | None = None
        self.raw_paused = False                          # free-space guard (dssq.datalog)
        self.rx_rows = 0                                 # rx_stats rows inserted
        self.rx_rows_skipped = 0                         # ... not written while raw_paused
        self.cadu_records = 0                            # cadu.bin records appended
        self.cadu_skipped = 0                            # ... not written while raw_paused
        self.journal_mode: str | None = None
        if not enabled:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / DB_NAME, check_same_thread=False)
        row = self.db.execute("PRAGMA journal_mode=WAL").fetchone()
        self.journal_mode = str(row[0]).lower() if row else None
        if self.journal_mode == "wal":
            self.db.execute("PRAGMA synchronous=NORMAL")  # WAL: durable at checkpoints, never corrupt
        self.db.executescript(SCHEMA)
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(packets)")}
        if "source" not in cols:                        # archive written by v2
            self.db.execute("ALTER TABLE packets ADD COLUMN source TEXT DEFAULT 'RF'")
        self.db.commit()

    @property
    def db_path(self) -> Path:
        return self.root / DB_NAME

    @property
    def exports_dir(self) -> Path:
        return self.root / EXPORTS_DIR

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
        if self.raw_paused:
            self.cadu_skipped += 1
            return
        r = RSSI_NA if rssi is None else max(-0x8000, min(0x7FFE, round(rssi)))
        with self._best_effort(), open(self._day() / "cadu.bin", "ab") as f:
            f.write(struct.pack(">qhH", int(ert * 1e9), r, len(cadu)) + cadu)
            self.cadu_records += 1

    def rx_stats(self, row: Mapping[str, Any]) -> bool:
        """One rx_stats row (RX_STATS_COLUMNS; a missing key is NULL). Skipped and counted
        while raw_paused. True when the row was handed to the database."""
        if not self.db:
            return False
        if self.raw_paused:
            self.rx_rows_skipped += 1
            return False
        ok = False
        with self._best_effort():
            self.db.execute(_RX_INSERT, tuple(row.get(c) for c in RX_STATS_COLUMNS))
            self.rx_rows += 1
            ok = True
        self._maybe_commit()
        return ok

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

    def commit(self) -> None:
        """Commits now, outside the cadence (a CSV export that follows sees every row so far)."""
        if self.db:
            self._last_commit = time.time()
            with self._best_effort():
                self.db.commit()

    # ------------------------------------------------------------ retention primitives
    def day_folders(self) -> list[str]:
        """Names of the YYYYMMDD day folders (real directories, never a symbolic link), oldest first."""
        out: list[str] = []
        if not self.enabled:
            return out
        try:
            with os.scandir(self.root) as it:
                for e in it:
                    if DAY_RE.fullmatch(e.name) and e.is_dir(follow_symlinks=False):
                        out.append(e.name)
        except OSError:
            return []
        return sorted(out)

    def db_bytes(self) -> tuple[int, int]:
        """(octets of gds.sqlite + its -wal / -journal files, octets the database pages in
        use hold = (page_count - freelist_count) x page_size). Rows deleted by the retention
        free pages for new rows without shrinking the file (no VACUUM while running), so the
        size budget is checked against the logical size."""
        files = sum(file_bytes(self.root / (DB_NAME + sfx)) for sfx in ("", "-wal", "-journal"))
        if not self.db:
            return files, 0
        try:
            pages = int(self.db.execute("PRAGMA page_count").fetchone()[0])
            free = int(self.db.execute("PRAGMA freelist_count").fetchone()[0])
            size = int(self.db.execute("PRAGMA page_size").fetchone()[0])
        except (sqlite3.Error, TypeError, ValueError):
            return files, files
        return files, max(0, pages - free) * size

    def oldest_t(self) -> float | None:
        """Earliest time stamp of the pruned tables (PRUNE_TABLES; index lookups), None when empty."""
        if not self.db:
            return None
        found: list[float] = []
        with self._best_effort():
            for table, col in PRUNE_TABLES:
                v = self.db.execute(f"SELECT MIN({col}) FROM {table}").fetchone()[0]
                if isinstance(v, (int, float)):
                    found.append(float(v))
        return min(found) if found else None

    def delete_day(self, name: str) -> int:
        """Deletes the day folder `name` (YYYYMMDD directly under the archive, not a link) and
        returns the octets it held. Raises ValueError for any other name, OSError on failure."""
        p = self.root / name
        if not DAY_RE.fullmatch(name) or p.is_symlink() or not p.is_dir():
            raise ValueError(f"not an archive day folder: {name!r}")
        freed = dir_bytes(p)
        shutil.rmtree(p)
        return freed

    def prune_rows(self, cutoff_t: float, batch: int) -> tuple[int, bool]:
        """Deletes at most `batch` rows older than cutoff_t from each PRUNE_TABLES table (oldest
        first, through the time index). Returns (rows deleted, True when a table gave a full
        batch, i.e. more may be left). Best-effort like every write (a failure ends the
        prune: (n, False)); the commit follows the archive cadence."""
        if not self.db:
            return 0, False
        n, more = 0, False
        with self._best_effort():
            for table, col in PRUNE_TABLES:
                cur = self.db.execute(f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                                      f"WHERE {col} < ? ORDER BY {col} LIMIT ?)", (cutoff_t, batch))
                k = max(0, cur.rowcount)
                n += k
                more = more or k >= batch
        self._maybe_commit()
        return n, more

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


def file_bytes(path: Path) -> int:
    """Size of a regular file (not following a link); 0 when absent or unreadable."""
    try:
        st = path.lstat()
    except OSError:
        return 0
    return st.st_size if not path.is_symlink() and path.is_file() else 0


def dir_bytes(path: Path) -> int:
    """Octets of the regular files directly inside `path` (the archive's folders are flat);
    links and subdirectories are not followed."""
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.is_file(follow_symlinks=False):
                    with contextlib.suppress(OSError):
                        total += e.stat(follow_symlinks=False).st_size
    except OSError:
        return total
    return total


def dumps(o: Any) -> str:
    return json.dumps(o, default=str)
