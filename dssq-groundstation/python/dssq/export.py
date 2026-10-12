"""CSV export of the ground archive (contract 39): receiver statistics, telemetry parameters,
events, frames, packets and commands of a time range.

    python -m dssq.export --from 2026-10-11T12:00:00Z --to 2026-10-11T13:00:00Z \\
                          --what rf,params,events [--out DIR] [--config station.toml | --archive DIR]

Times are ISO-8601 (a value without a zone is UTC) or unix seconds; the range is
[from, to). --what takes any of PRODUCTS (default: all). --archive overrides the
[gds] archive_dir of --config (App Lab: /home/arduino/ArduinoApps/<App>/data/archive);
--out defaults to <archive>/exports, where the dashboard lists the files too.

One file per product, VGQ1_<product>_<from>_<to>.csv (UTC, second resolution). Format:
UTF-8 (no byte-order mark), RFC 4180 quoting, "\\r\\n" line ends, one header row whose
names carry the unit in brackets (SI units and the radio engineering units dBm / dB;
"[count]" for counts, "[ratio]" for a fraction 0..1, "[0/1]" for a flag). Every row starts
with "utc [ISO-8601]" (millisecond resolution, "Z") and "unix_time [s]"; an empty field is a
value the station did not have (NULL). Free-text fields (event and command texts, frame
verdicts, names) that a spreadsheet would read as a formula (first character = + - @, tab or
CR) are prefixed with an apostrophe ("CSV injection": event texts can come from the
spacecraft); numbers are written unchanged (Python's shortest round-trip repr).

The database is opened READ-ONLY (sqlite URI mode=ro) and read in CHUNK_ROWS-row pages
ordered by (time, rowid), each page a short read transaction of its own, so memory stays
bounded whatever the range and the running GDS (WAL mode, dssq.archive) is never blocked.
A file is written as <name>.tmp and renamed when complete; a failure removes the .tmp.
Python standard library only (contract 23).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import math
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import dictionary as dic
from .archive import DB_NAME, EXPORTS_DIR, RX_STATS_COLUMNS

PRODUCTS = ("rf", "params", "events", "frames", "packets", "commands")
CHUNK_ROWS = 5000                   # rows per read transaction
FREE_CHECK_ROWS = 50_000            # free space re-checked every this many rows written
TIME_MIN = 0.0                      # 1970-01-01
TIME_MAX = 32_503_680_000.0         # 3000-01-01: beyond any station clock
NAME_PREFIX = "VGQ1_"
EXPORT_NAME_RE = re.compile(r"^VGQ1_(?:rf|params|events|frames|packets|commands)_[0-9]{8}T[0-9]{6}Z_"
                            r"[0-9]{8}T[0-9]{6}Z\.csv$")
TIME_HEADERS = ("utc [ISO-8601]", "unix_time [s]")
_UNIX_RE = re.compile(r"[+-]?[0-9]{1,12}(?:\.[0-9]{0,9})?")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class ExportError(ValueError):
    """An export that cannot be done (bad range or product, no database, low disk space)."""


# =============================================================================
# Value formatting
# =============================================================================
def iso_utc(t: float | None) -> str:
    """ISO-8601 UTC with millisecond resolution and "Z"; "" for no time."""
    if t is None or not math.isfinite(t):
        return ""
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _num(v: Any) -> str:
    if v is None or isinstance(v, (bytes, str)):
        return "" if v is None else str(v)
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else ""
    return str(v)


def _text(v: Any) -> str:
    """A free-text field, neutralised against spreadsheet formula evaluation."""
    if v is None:
        return ""
    s = str(v)
    return "'" + s if s.startswith(_FORMULA_START) else s


def _time_cols(t: Any) -> list[str]:
    return [iso_utc(t), _num(t)] if isinstance(t, (int, float)) else ["", ""]


def parse_time(text: str | float | int) -> float:
    """Unix seconds from an ISO-8601 text (no zone = UTC; "Z" accepted) or a unix-seconds
    number / numeric text (so a bare "20261011" is unix seconds: write dates as 2026-10-11).
    Raises ExportError outside 1970..3000 or when unparseable."""
    if isinstance(text, bool):
        raise ExportError("time must be ISO-8601 or unix seconds, not a boolean")
    if isinstance(text, (int, float)):
        t = float(text)
    else:
        s = str(text).strip()
        if len(s) > 40:
            raise ExportError("time text too long")
        if _UNIX_RE.fullmatch(s):
            t = float(s)
        else:
            try:
                d = datetime.fromisoformat(s[:-1] + "+00:00" if s[-1:] in ("Z", "z") else s)
            except ValueError:
                raise ExportError(f"time {s!r} is neither ISO-8601 nor unix seconds") from None
            t = (d if d.tzinfo is not None else d.replace(tzinfo=UTC)).timestamp()
    if not math.isfinite(t) or not TIME_MIN <= t <= TIME_MAX:
        raise ExportError("time outside 1970-01-01 .. 3000-01-01")
    return t


def parse_products(what: str | Sequence[str] | None) -> list[str]:
    """Product names from "rf,params" or a list (None / "all" = every product), in PRODUCTS
    order without duplicates. Raises ExportError for an unknown name or an empty selection."""
    if what is None:
        return list(PRODUCTS)
    items = what.split(",") if isinstance(what, str) else list(what)
    if len(items) > 2 * len(PRODUCTS):
        raise ExportError("too many products")
    names: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            raise ExportError("product names must be strings")
        n = item.strip().lower()
        if n == "all":
            return list(PRODUCTS)
        if n not in PRODUCTS:
            raise ExportError(f"unknown product {item[:20]!r} (one of {', '.join(PRODUCTS)}, all)")
        names.add(n)
    if not names:
        raise ExportError(f"no product selected (one of {', '.join(PRODUCTS)}, all)")
    return [p for p in PRODUCTS if p in names]


def file_name(product: str, t0: float, t1: float) -> str:
    """VGQ1_<product>_<from>_<to>.csv (UTC to the second; matches EXPORT_NAME_RE)."""
    def stamp(t: float) -> str:
        return datetime.fromtimestamp(math.floor(t), UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{NAME_PREFIX}{product}_{stamp(t0)}_{stamp(t1)}.csv"


# =============================================================================
# Products
# =============================================================================
def _units() -> dict[tuple[str, str], str]:
    """(packet name, field name) -> unit, from the telemetry dictionary."""
    out: dict[tuple[str, str], str] = {}
    for apid, spec in dic.PACKETS.items():
        pname = dic.PACKET_NAMES.get(apid, str(spec.get("name", apid)))
        for f in spec["fields"]:
            out[(pname, str(f[0]))] = str(f[3])
    return out


@dataclass(frozen=True)
class Product:
    """One CSV product: the table, its time column, the selected columns after the time
    and the CSV header (after the two time columns) with the row formatter."""

    name: str
    table: str
    tcol: str
    columns: tuple[str, ...]
    headers: tuple[str, ...]
    fmt: Callable[[tuple[Any, ...]], list[str]]


RF_HEADERS = ("los [0/1]", "pass [count]", "receptions [count]", "rssi [dBm]", "snr [dB]", "snr_est [dB]",
              "noise_floor [dBm]", "link_margin [dB]", "freq_err [Hz]", "fer_10m [ratio]",
              "rs_symbols_corrected [count]", "codeblocks_uncorrectable [count]", "frames_ok [count]",
              "frames_lost [count]", "afc_correction [Hz]", "doppler [Hz]", "range_rate [m/s]")
assert len(RF_HEADERS) == len(RX_STATS_COLUMNS) - 1


def _fmt_rf(r: tuple[Any, ...]) -> list[str]:
    return [_num(v) for v in r]


def _make_fmt_params() -> Callable[[tuple[Any, ...]], list[str]]:
    units = _units()

    def fmt(r: tuple[Any, ...]) -> list[str]:
        scet, packet, name, value, limit = r
        return [*_time_cols(scet), _text(packet), _text(name), _num(value),
                _text(units.get((str(packet), str(name)), "")), _text(limit)]
    return fmt


def _fmt_events(r: tuple[Any, ...]) -> list[str]:
    return [_text(v) for v in r]


def _fmt_frames(r: tuple[Any, ...]) -> list[str]:
    *nums, reason = r
    return [*(_num(v) for v in nums), _text(reason)]


def _fmt_packets(r: tuple[Any, ...]) -> list[str]:
    scet, apid, name, seq, sclk, partition, vcid, source, raw = r
    data = bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else b""
    return [*_time_cols(scet), _num(apid), _text(name), _num(seq), _num(sclk), _num(partition), _num(vcid),
            _text(source), str(len(data)), data.hex()]


def _fmt_commands(r: tuple[Any, ...]) -> list[str]:
    cid, text, state, detail = r
    return [_num(cid), _text(text), _text(state), _text(detail)]


def products() -> dict[str, Product]:
    """The product table (built per export: the parameter units come from the dictionary)."""
    return {
        "rf": Product("rf", "rx_stats", "t", RX_STATS_COLUMNS[1:], RF_HEADERS, _fmt_rf),
        "params": Product("params", "params", "ert", ("scet", "packet", "name", "value", "limit_state"),
                          ("scet_utc [ISO-8601]", "scet_unix [s]", "packet", "parameter", "value [unit column]",
                           "unit", "limit_state"), _make_fmt_params()),
        "events": Product("events", "events", "t", ("level", "source", "text"), ("level", "source", "text"),
                          _fmt_events),
        "frames": Product("frames", "frames", "ert",
                          ("mcfc", "vcid", "vcfc", "fhp", "rs_corrected", "rssi", "snr", "ok", "reason"),
                          ("mcfc [count]", "vcid", "vcfc [count]", "first_header_pointer [octets]",
                           "rs_corrected [symbols]", "rssi_raw [dBm]", "snr [dB]", "ok [0/1]", "reason"), _fmt_frames),
        "packets": Product("packets", "packets", "ert",
                           ("scet", "apid", "name", "seq", "sclk", "partition", "vcid", "source", "raw"),
                           ("scet_utc [ISO-8601]", "scet_unix [s]", "apid", "packet", "seq [count]", "sclk [s]",
                            "partition", "vcid", "source", "length [octets]", "raw [hex]"), _fmt_packets),
        "commands": Product("commands", "commands", "t", ("id", "text", "state", "detail"),
                            ("id", "command", "state", "detail"), _fmt_commands),
    }


# =============================================================================
# Reading and writing
# =============================================================================
def open_readonly(db_path: Path) -> sqlite3.Connection:
    """A read-only connection to the archive database (never creates it). Raises ExportError."""
    if not db_path.is_file():
        raise ExportError(f"no archive database at {db_path}")
    try:
        con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0,
                              check_same_thread=False)
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error as e:
        raise ExportError(f"cannot open {db_path} read-only: {e}") from None
    return con


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def iter_rows(con: sqlite3.Connection, p: Product, t0: float, t1: float,
              chunk: int = CHUNK_ROWS) -> Iterator[tuple[Any, ...]]:
    """Rows of `p` with t0 <= time < t1 as (time, *columns), ordered by (time, rowid), read
    in pages of `chunk` rows (keyset pagination: each page is a separate short query)."""
    if not _has_table(con, p.table):                     # an archive older than the table
        return
    cols = ", ".join((p.tcol, *p.columns))
    base = f"SELECT rowid, {cols} FROM {p.table} WHERE {p.tcol} >= ? AND {p.tcol} < ?"
    last: tuple[float, int] | None = None
    while True:
        if last is None:
            page = con.execute(f"{base} ORDER BY {p.tcol}, rowid LIMIT ?", (t0, t1, chunk)).fetchall()
        else:
            page = con.execute(f"{base} AND ({p.tcol} > ? OR ({p.tcol} = ? AND rowid > ?)) "
                               f"ORDER BY {p.tcol}, rowid LIMIT ?",
                               (t0, t1, last[0], last[0], last[1], chunk)).fetchall()
        for row in page:
            yield tuple(row[1:])
        if len(page) < chunk:
            return
        last = (page[-1][1], page[-1][0])


@dataclass
class ExportResult:
    product: str
    name: str
    path: Path
    rows: int
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {"product": self.product, "name": self.name, "rows": self.rows, "size": self.size}


def _free_bytes(path: Path) -> int | None:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return None


def write_csv(con: sqlite3.Connection, p: Product, t0: float, t1: float, out_dir: Path,
              min_free_bytes: int = 0) -> ExportResult:
    """Writes one product file atomically (.tmp, then rename). With min_free_bytes, the
    export stops (ExportError, partial file removed) when the output file system's free
    space falls below it."""
    name = file_name(p.name, t0, t1)
    path = out_dir / name
    tmp = out_dir / (name + ".tmp")
    rows = 0
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\r\n")
            w.writerow([*TIME_HEADERS, *p.headers])
            for r in iter_rows(con, p, t0, t1):
                w.writerow([*_time_cols(r[0]), *p.fmt(r[1:])])
                rows += 1
                if min_free_bytes and rows % FREE_CHECK_ROWS == 0:
                    free = _free_bytes(out_dir)
                    if free is not None and free < min_free_bytes:
                        raise ExportError(f"export stopped: free space {free / 1e6:.0f} MB below the "
                                          f"[gds] archive_min_free_mb floor {min_free_bytes / 1e6:.0f} MB")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return ExportResult(p.name, name, path, rows, path.stat().st_size)


def export_range(db_path: Path, out_dir: Path, t0: float, t1: float, what: Sequence[str],
                 min_free_bytes: int = 0) -> list[ExportResult]:
    """Exports every product of `what` for [t0, t1) into out_dir (created). Raises
    ExportError (bad range, no database, low disk space), sqlite3.Error / OSError on I/O."""
    if not t0 < t1:
        raise ExportError("'from' must be earlier than 'to'")
    names = parse_products(list(what))
    out_dir.mkdir(parents=True, exist_ok=True)
    if min_free_bytes:
        free = _free_bytes(out_dir)
        if free is not None and free < min_free_bytes:
            raise ExportError(f"free space {free / 1e6:.0f} MB below the [gds] archive_min_free_mb floor "
                              f"{min_free_bytes / 1e6:.0f} MB: no export")
    table = products()
    con = open_readonly(db_path)
    try:
        return [write_csv(con, table[n], t0, t1, out_dir, min_free_bytes) for n in names]
    finally:
        con.close()


def list_exports(out_dir: Path, limit: int = 1000) -> list[dict[str, Any]]:
    """Export files a download would serve (EXPORT_NAME_RE, regular files), newest first."""
    out: list[dict[str, Any]] = []
    try:
        with os.scandir(out_dir) as it:
            for e in it:
                if EXPORT_NAME_RE.fullmatch(e.name) and e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    out.append({"name": e.name, "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        return []
    out.sort(key=lambda f: f["mtime"], reverse=True)
    return out[:limit]


# =============================================================================
# Command line
# =============================================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dssq.export",
                                 description="Export the DSS-Q ground archive to CSV (read-only access).")
    ap.add_argument("--from", dest="t_from", required=True, help="start, ISO-8601 (UTC if no zone) or unix s")
    ap.add_argument("--to", dest="t_to", required=True, help="end (exclusive), ISO-8601 or unix s")
    ap.add_argument("--what", default="all", help=f"comma-separated: {','.join(PRODUCTS)} or all (default)")
    ap.add_argument("--out", default=None, help="output directory (default <archive>/exports)")
    ap.add_argument("--config", default=None, help="station.toml (for [gds] archive_dir)")
    ap.add_argument("--archive", default=None, help="archive directory (overrides --config)")
    a = ap.parse_args(argv)
    try:
        t0, t1 = parse_time(a.t_from), parse_time(a.t_to)
        what = parse_products(a.what)
        if a.archive:
            root = Path(a.archive)
        else:
            from .config import archive_path, load_config
            root = archive_path(load_config(a.config))
        out = Path(a.out) if a.out else root / EXPORTS_DIR
        results = export_range(root / DB_NAME, out, t0, t1, what)
    except (ExportError, ValueError, OSError, sqlite3.Error) as e:
        print(f"export failed: {e}", file=sys.stderr)
        return 2
    print(f"{iso_utc(t0)} .. {iso_utc(t1)} from {root / DB_NAME}")
    for r in results:
        print(f"  {r.path}  {r.rows} rows  {r.size} octets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
