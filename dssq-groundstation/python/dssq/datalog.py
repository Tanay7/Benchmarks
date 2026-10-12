"""Receiver data logger: the 1 Hz rx_stats series, archive retention and the free-space guard
(contract 39).

The GDS owns one DataLogger next to its Archive and calls tick() from the 1 Hz housekeeping,
under the station lock (the same thread that writes every other archive row):

* rx_stats: one row per call (dssq.rxstats.ReceiverStats.interval_row(), at most one per
  second), inserted through the archive's best-effort path and committed with its 2 s cadence.
* Free-space guard ([gds] archive_min_free_mb, 0 = off): the archive file system's free octets
  (shutil.disk_usage: the space an unprivileged writer may use) are read every FREE_CHECK_S.
  Below the floor, Archive.raw_paused stops the two growing raw products (cadu.bin records
  and rx_stats rows) with a WARN event and a YELLOW alarm while it lasts; the logging resumes
  by itself (INFO event) once the free space is back above floor x RESUME_FACTOR (hysteresis:
  a disk at the floor does not flap). Frames, packets, parameters, events and commands keep
  recording (they are what the operator needs to understand the pass) - a really full disk
  still never stops the downlink or commanding (the archive's best-effort rule).
* Retention ([gds] archive_keep_days and archive_max_gb, 0 = that limit off), checked every
  PRUNE_CHECK_S and once shortly after start:
    - age: a YYYYMMDD day folder whose whole UTC day ended more than keep_days ago is deleted,
      and the rx_stats / params / frames rows older than now - keep_days;
    - size: while the day folders plus the database pages in use exceed max_gb, the oldest
      data day (the oldest day folder or the oldest row, whichever is older) is pruned - one
      day per check, the check repeating at once after a prune that freed something while
      the archive is still over the cap;
    - never the current UTC day (folder or rows), never a folder dated in the future (a
      station clock that jumped back), never a symbolic link;
    - rows go in PRUNE_BATCH-row batches per table per tick (bounded time under the lock),
      day folders in one rmtree each; the database file does not shrink (no VACUUM while
      running: the freed pages take the new rows), so the size cap counts pages in use;
    - exports/ CSV files older than keep_days, and beyond EXPORT_KEEP_MAX files, are deleted.
  Each completed prune is reported as one INFO event (what went, why).

health() is the dashboard's "logger" snapshot section (FILES tab). Sizes are octets; the
configuration's GB / MB are SI (1e9 / 1e6 octets).
"""
from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from .archive import Archive, dir_bytes

FREE_CHECK_S = 5.0                  # free-space reading period
PRUNE_CHECK_S = 60.0                # retention check period
FIRST_PRUNE_CHECK_S = 5.0           # first check this long after start (not inside the start-up burst)
PRUNE_BATCH = 2000                  # rows per table per tick while a prune runs
RESUME_FACTOR = 1.05                # logging resumes above floor x this (hysteresis)
EXPORT_KEEP_MAX = 200               # newest export files kept in <archive>/exports
DAY_S = 86_400.0
GB = 1e9
MB = 1e6


class DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


def _utc_day(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y%m%d")


def _day_start(name: str) -> float:
    return datetime.strptime(name, "%Y%m%d").replace(tzinfo=UTC).timestamp()


def _iso(t: float | None) -> str:
    return "-" if t is None else datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class DataLogger:
    """Policy half of the receiver data logger (module docstring); the archive does the I/O."""

    def __init__(self, archive: Archive, gds_cfg: Mapping[str, Any], *,
                 disk_usage: Callable[[Path], DiskUsage] | None = None) -> None:
        self.archive = archive
        self.enabled = archive.enabled
        self.keep_days = int(gds_cfg["archive_keep_days"])
        self.max_bytes = int(float(gds_cfg["archive_max_gb"]) * GB)
        self.min_free_bytes = int(float(gds_cfg["archive_min_free_mb"]) * MB)
        self._disk_usage: Callable[[Path], Any] = disk_usage or shutil.disk_usage
        self.t_start = time.time()
        self.last_row_t: float | None = None
        # free-space guard
        self.free_bytes: int | None = None
        self.total_bytes: int | None = None
        self.free_t: float | None = None
        self.free_error: str | None = None
        self.paused_since: float | None = None
        self.pauses = 0
        self._free_next = -1e18
        # usage and retention
        self.usage: dict[str, Any] = {"t": None, "raw_bytes": 0, "db_file_bytes": 0, "db_used_bytes": 0,
                                      "exports_bytes": 0, "exports": 0, "days": 0, "oldest_day": None}
        self._prune_next = self.t_start + FIRST_PRUNE_CHECK_S
        self._prune_cutoff: float | None = None          # rows older than this are being deleted
        self._prune_reason = ""
        self._prune_days: list[str] = []
        self._prune_rows = 0
        self._prune_started: float | None = None
        self.last_prune: dict[str, Any] | None = None
        self.prune_errors = 0
        self.totals = {"days_deleted": 0, "rows_deleted": 0, "exports_deleted": 0, "bytes_freed": 0}

    # ------------------------------------------------------------------ state
    @property
    def paused(self) -> bool:
        return self.archive.raw_paused

    @property
    def used_bytes(self) -> int:
        """What the size cap counts: day folders + database pages in use."""
        return int(self.usage["raw_bytes"]) + int(self.usage["db_used_bytes"])

    # ------------------------------------------------------------------ 1 Hz
    def tick(self, now: float, row: Callable[[], Mapping[str, Any]]) -> list[tuple[str, str]]:
        """One logger step (housekeeping, station lock held): free-space guard, the rx_stats
        row (row() builds it), retention. Returns (level, text) events for the GDS log."""
        if not self.enabled:
            return []
        events: list[tuple[str, str]] = []
        if now >= self._free_next or now < self._free_next - 2 * FREE_CHECK_S:
            self._free_next = now + FREE_CHECK_S
            events += self._check_free(now)
        r = row()
        if self.archive.rx_stats(r):
            self.last_row_t = now
        events += self._retention(now)
        return events

    def _check_free(self, now: float) -> list[tuple[str, str]]:
        try:
            du = self._disk_usage(self.archive.root)
            self.free_bytes, self.total_bytes = int(du.free), int(du.total)
            self.free_error = None
        except OSError as e:                             # unmounted external drive, permissions
            self.free_error = f"{type(e).__name__}: {e}"[:200]
            return []
        self.free_t = now
        floor = self.min_free_bytes
        a = self.archive
        if floor <= 0:
            if a.raw_paused:                             # cannot happen with a fixed configuration
                a.raw_paused, self.paused_since = False, None
            return []
        if not a.raw_paused and self.free_bytes < floor:
            a.raw_paused, self.paused_since = True, now
            self.pauses += 1
            return [("WARN", f"free space {self.free_bytes / MB:.0f} MB < [gds] archive_min_free_mb "
                             f"{floor / MB:.0f} MB on {a.root}: raw CADU and rx_stats logging PAUSED (frames, "
                             "packets and events still recorded; it resumes by itself)")]
        if a.raw_paused and self.free_bytes >= floor * RESUME_FACTOR:
            dur = now - (self.paused_since or now)
            a.raw_paused, self.paused_since = False, None
            return [("INFO", f"free space {self.free_bytes / MB:.0f} MB: raw CADU and rx_stats logging resumed "
                             f"after {dur:.0f} s ({a.cadu_skipped} CADU records and {a.rx_rows_skipped} rx_stats "
                             "rows skipped so far)")]
        return []

    # ------------------------------------------------------------------ retention
    def scan(self, now: float) -> None:
        """Re-reads the archive usage (one directory level per folder: the folders are flat)."""
        a = self.archive
        days = a.day_folders()
        raw = sum(dir_bytes(a.root / d) for d in days)
        db_file, db_used = a.db_bytes()
        exports = self._export_files()
        self.usage = {"t": now, "raw_bytes": raw, "db_file_bytes": db_file, "db_used_bytes": db_used,
                      "exports_bytes": sum(st.st_size for _, st in exports), "exports": len(exports),
                      "days": len(days), "oldest_day": days[0] if days else None}

    def _export_files(self) -> list[tuple[Path, os.stat_result]]:
        out: list[tuple[Path, os.stat_result]] = []
        try:
            with os.scandir(self.archive.exports_dir) as it:
                for e in it:
                    if e.is_file(follow_symlinks=False) and e.name.endswith(".csv"):
                        out.append((Path(e.path), e.stat(follow_symlinks=False)))
        except OSError:
            return []
        out.sort(key=lambda x: x[1].st_mtime)
        return out

    def _retention(self, now: float) -> list[tuple[str, str]]:
        if self._prune_cutoff is not None:               # a prune is running: one batch per tick
            n, more = self.archive.prune_rows(self._prune_cutoff, PRUNE_BATCH)
            self._prune_rows += n
            return [] if more else self._prune_done(now)
        if now < self._prune_next and now >= self._prune_next - 2 * PRUNE_CHECK_S:
            return []
        self._prune_next = now + PRUNE_CHECK_S
        self.scan(now)
        self._prune_exports(now)
        return self._plan(now)

    def _plan(self, now: float) -> list[tuple[str, str]]:
        """Decides what to prune now (module docstring) and starts it."""
        today = _utc_day(now)
        today_start = _day_start(today)
        days = [d for d in self.archive.day_folders() if d < today]     # never today, never the future
        cutoff: float | None = None
        reasons: list[str] = []
        if self.keep_days > 0:
            t_keep = min(now - self.keep_days * DAY_S, today_start)
            old = [d for d in days if _day_start(d) + DAY_S <= t_keep]
            oldest = self.archive.oldest_t()
            if old or (oldest is not None and oldest < t_keep):
                cutoff, reasons = t_keep, [f"older than archive_keep_days {self.keep_days}"]
        if self.max_bytes > 0 and self.used_bytes > self.max_bytes:
            starts = [_day_start(d) for d in days]
            oldest = self.archive.oldest_t()
            if oldest is not None:
                starts.append(oldest)
            if starts:
                first = min(starts)
                t_size = min(_day_start(_utc_day(first)) + DAY_S, today_start)
                if t_size > first and (cutoff is None or t_size > cutoff):
                    cutoff = t_size
                    reasons.append(f"archive {self.used_bytes / GB:.3f} GB > archive_max_gb {self.max_bytes / GB:g}")
        if cutoff is None:
            return []
        self._prune_started, self._prune_cutoff, self._prune_reason = now, cutoff, "; ".join(reasons)
        self._prune_days, self._prune_rows = [], 0
        freed = 0
        for d in days:
            if _day_start(d) + DAY_S <= cutoff:
                try:
                    freed += self.archive.delete_day(d)
                    self._prune_days.append(d)
                except (OSError, ValueError) as e:
                    self.prune_errors += 1
                    self.archive.last_error = f"retention: day {d}: {type(e).__name__}: {e}"[:300]
        self.totals["bytes_freed"] += freed
        return []

    def _prune_done(self, now: float) -> list[tuple[str, str]]:
        cutoff = self._prune_cutoff
        days = self._prune_days
        self.archive.commit()
        self.totals["days_deleted"] += len(days)
        self.totals["rows_deleted"] += self._prune_rows
        self.last_prune = {"t": now, "started_t": self._prune_started, "cutoff_t": cutoff, "reason": self._prune_reason,
                           "days_deleted": list(days), "rows_deleted": self._prune_rows}
        self._prune_cutoff = None
        self.scan(now)
        progressed = bool(days) or self._prune_rows > 0
        if progressed and self.max_bytes > 0 and self.used_bytes > self.max_bytes:
            self._prune_next = now                       # still over the cap: next day at the next tick
        span = f"{days[0]}..{days[-1]}" if len(days) > 1 else (days[0] if days else "none")
        return [("INFO", f"retention: {len(days)} day folder(s) deleted ({span}) and {self.last_prune['rows_deleted']} "
                         f"rx_stats / params / frames rows older than {_iso(cutoff)} - {self._prune_reason}")]

    def _prune_exports(self, now: float) -> None:
        files = self._export_files()
        keep_after = now - self.keep_days * DAY_S if self.keep_days > 0 else -1e18
        excess = max(0, len(files) - EXPORT_KEEP_MAX)
        for i, (p, st) in enumerate(files):                # oldest first
            if i < excess or st.st_mtime < keep_after:
                try:
                    p.unlink()
                    self.totals["exports_deleted"] += 1
                except OSError as e:
                    self.prune_errors += 1
                    self.archive.last_error = f"retention: export {p.name}: {e}"[:300]

    # ------------------------------------------------------------------ snapshot
    def health(self, now: float) -> dict[str, Any]:
        """The snapshot's "logger" section (dashboard FILES tab)."""
        a = self.archive
        u = self.usage
        return {
            "enabled": self.enabled, "archive_dir": str(a.root), "journal_mode": a.journal_mode,
            "rx_rows": a.rx_rows, "rx_rows_skipped": a.rx_rows_skipped, "last_row_t": self.last_row_t,
            "row_age_s": None if self.last_row_t is None else now - self.last_row_t,
            "cadu_records": a.cadu_records, "cadu_skipped": a.cadu_skipped,
            "write_errors": a.write_errors,
            "paused": a.raw_paused, "paused_since": self.paused_since, "pauses": self.pauses,
            "free_bytes": self.free_bytes, "total_bytes": self.total_bytes, "free_t": self.free_t,
            "free_error": self.free_error, "min_free_bytes": self.min_free_bytes,
            "resume_free_bytes": int(self.min_free_bytes * RESUME_FACTOR) if self.min_free_bytes else None,
            "used_bytes": self.used_bytes, "max_bytes": self.max_bytes or None,
            "raw_bytes": u["raw_bytes"], "db_file_bytes": u["db_file_bytes"], "db_used_bytes": u["db_used_bytes"],
            "exports_bytes": u["exports_bytes"], "exports": u["exports"], "days": u["days"],
            "oldest_day": u["oldest_day"], "usage_t": u["t"], "keep_days": self.keep_days or None,
            "prune_running": self._prune_cutoff is not None, "prune_cutoff_t": self._prune_cutoff,
            "last_prune": self.last_prune, "prune_errors": self.prune_errors,
            "next_prune_check_t": self._prune_next, "totals": dict(self.totals),
        }

