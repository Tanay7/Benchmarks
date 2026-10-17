"""Receiver data logger (contract 39): rx_stats, CSV export, retention, free-space guard.

* rx_stats: one row per second from the GDS housekeeping against the v3 simulator (E22 and
  SX126x worlds, virtual clock); counter fields are per-interval increments that sum to the
  receiver totals; RSSI / SNR / margin / noise / FER / AFC / Doppler columns filled.
* Configuration: [gds] archive_keep_days / archive_max_gb / archive_min_free_mb defaults,
  validation and their documentation in config/station.toml.
* Free-space guard: pause below the floor (WARN, YELLOW alarm, cadu.bin and rx_stats skipped,
  frames still recorded), hysteresis, automatic resume (INFO).
* Retention: age and size limits, oldest day first, never the current UTC day, never a
  future-dated folder or a symbolic link, batched row deletion over several ticks, exports.
* dssq.export: CSV format (UTF-8, header units, ISO-8601 + unix columns, [from, to), NULL as
  empty, formula neutralisation), keyset paging, read-only access that never blocks the GDS
  writer, the CLI, and the authenticated web export with server-chosen names and the strict
  download path.
"""
import csv
import io
import itertools
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_end_to_end import T0, World, _config
from test_web import request, strict_json

from dssq import config as station_config
from dssq import datalog, export
from dssq.archive import RX_STATS_COLUMNS, Archive
from dssq.display import web
from dssq.gds import GroundStation

GROUND = Path(__file__).resolve().parents[1] / "dssq-groundstation" / "python"   # holds the dssq package
TOKEN = "logger-test-token"
DAY = 86_400.0


def _day(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y%m%d")


def _rows(archive_dir: Path, sql: str, args=()):
    con = sqlite3.connect(archive_dir / "gds.sqlite")
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _read_csv(path: Path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")                                 # UTF-8 without BOM
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
    return rows[0], rows[1:]


class FakeDisk:
    """shutil.disk_usage stand-in with a settable free space (octets)."""

    def __init__(self, free: int, total: int = 64_000_000_000):
        self.free, self.total = free, total

    def __call__(self, path):
        return datalog.DiskUsage(self.total, self.total - self.free, self.free)


# =============================================================================
# Configuration
# =============================================================================
def test_config_defaults_validation_and_documentation():
    cfg = station_config.load_config()
    g = cfg["gds"]
    assert (g["archive_keep_days"], g["archive_max_gb"], g["archive_min_free_mb"]) == (180, 8.0, 2048.0)   # D-80
    assert station_config.merge({})["gds"]["archive_keep_days"] == 180              # DEFAULTS mirror the file
    text = station_config.DEFAULT_PATH.read_text()
    for key in ("archive_keep_days", "archive_max_gb", "archive_min_free_mb", "VERIFY", "ArduinoApps"):
        assert key in text
    for key, bad in (("archive_keep_days", -1), ("archive_keep_days", 1.5), ("archive_keep_days", True),
                     ("archive_keep_days", 36_501), ("archive_max_gb", -0.1), ("archive_max_gb", "8"),
                     ("archive_max_gb", float("nan")), ("archive_min_free_mb", -1.0),
                     ("archive_min_free_mb", float("inf"))):
        c = station_config.merge({"gds": {key: bad}})
        with pytest.raises(ValueError, match=key):
            station_config.validate(c)
    for key, ok in (("archive_keep_days", 0), ("archive_max_gb", 0), ("archive_min_free_mb", 0.5)):
        station_config.validate(station_config.merge({"gds": {key: ok}}))


def test_absolute_archive_dir_keeps_working(tmp_path):
    """An external drive is an absolute archive_dir: App Lab keeps it, the logger uses it."""
    ext = tmp_path / "nvme" / "dssq-archive"
    cfg = station_config.load_config()
    cfg["gds"]["archive_dir"] = str(ext)
    cfg["gds"]["http_token"] = "x"
    station_config.resolve_app_paths(cfg, tmp_path / "app" / "data")
    assert cfg["gds"]["archive_dir"] == str(ext)
    gs = GroundStation(cfg, archive=True)
    try:
        assert gs.exports_dir == ext / "exports" and (ext / "gds.sqlite").is_file()
        assert gs.archive.journal_mode == "wal"
        assert gs.snapshot()["logger"]["archive_dir"] == str(ext)
    finally:
        gs.close()


# =============================================================================
# rx_stats
# =============================================================================
def test_rx_stats_one_row_per_second_e22(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path)
    w.run(90)
    gs = w.gs
    gs.archive.commit()
    adir = Path(w.cfg["gds"]["archive_dir"])
    rows = _rows(adir, f"SELECT {', '.join(RX_STATS_COLUMNS)} FROM rx_stats ORDER BY t")
    assert 85 <= len(rows) <= 91 and gs.archive.rx_rows == len(rows)
    ts = [r[0] for r in rows]
    assert all(1.0 <= b - a <= 1.25 for a, b in itertools.pairwise(ts))          # 1 Hz
    col = {c: [r[i] for r in rows] for i, c in enumerate(RX_STATS_COLUMNS)}
    # interval increments sum to the receiver totals
    assert sum(col["frames_ok"]) == gs.rx.frames_ok > 20
    assert sum(col["frames_lost"]) == gs.rx.frames_lost
    assert sum(col["rs_symbols"]) == gs.rx.sym_corrected > 0
    assert sum(col["n_rx"]) == gs.rx.receptions
    with_rx = [r for r in rows if r[RX_STATS_COLUMNS.index("n_rx")]]
    assert with_rx and all(r[RX_STATS_COLUMNS.index("rssi_dbm")] is not None for r in with_rx)
    i_rssi, i_margin = RX_STATS_COLUMNS.index("rssi_dbm"), RX_STATS_COLUMNS.index("margin_db")
    sens = gs.settings.sensitivity[0]
    assert all(r[i_margin] == pytest.approx(r[i_rssi] - sens) for r in with_rx)
    assert all(-110 < r[i_rssi] < -80 for r in with_rx)                          # sim rssi_mean -95 dBm
    no_rx = [r for r in rows if not r[RX_STATS_COLUMNS.index("n_rx")]]
    assert no_rx and all(r[i_rssi] is None and r[i_margin] is None for r in no_rx)
    assert col["los"][0] == 1 and col["los"][-1] == 0                              # LOS at start, then AOS
    assert col["pass_no"][-1] == 1
    assert col["fer_10m"][-1] is not None and 0.0 <= col["fer_10m"][-1] <= 1.0
    assert all(v is None for v in col["afc_hz"] + col["doppler_hz"])               # E22: no AFC / Doppler
    lg = gs.snapshot()["logger"]
    assert lg["enabled"] and lg["rx_rows"] == len(rows) and not lg["paused"] and lg["cadu_records"] > 20
    assert lg["row_age_s"] is not None and lg["row_age_s"] < 1.5


def test_rx_stats_packet_mode_afc_doppler_snr(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path, radio={"model": "SX126x", "air_rate": 4}, static_offset_hz=6000.0,
              doppler_rate_hz_s=0.5, seed=5)
    w.run(120, step=0.5)
    w.gs.archive.commit()
    rows = _rows(Path(w.cfg["gds"]["archive_dir"]),
                 "SELECT snr_db, freq_err_hz, afc_hz, doppler_hz, range_rate_m_s, n_rx FROM rx_stats ORDER BY t")
    rx = [r for r in rows if r[5]]
    assert rx and all(r[0] is not None and r[1] is not None for r in rx)          # measured SNR, freq error
    assert any(r[2] not in (None, 0.0) for r in rows)                              # AFC correction applied
    dop = [r for r in rows if r[3] is not None]
    assert dop and all(r[4] is not None for r in dop)


def test_interval_row_counters_are_increments(tmp_path):
    from dssq.rxstats import ReceiverStats
    rs = ReceiverStats(_config(tmp_path, key=False))
    rs.on_reception(T0 + 0.5, -100.0)
    rs.on_reception(T0 + 0.9, -98.0)
    rs.sym_corrected, rs.frames_ok = 7, 2
    r1 = rs.interval_row(T0, T0 + 1.0)
    assert r1["n_rx"] == 2 and r1["rssi_dbm"] == pytest.approx(-99.0) and r1["rs_symbols"] == 7
    assert r1["frames_ok"] == 2 and set(r1) == set(RX_STATS_COLUMNS)
    rs.frames_ok = 3
    r2 = rs.interval_row(T0 + 1.0, T0 + 2.0)
    assert r2["n_rx"] == 0 and r2["rssi_dbm"] is None and r2["frames_ok"] == 1 and r2["rs_symbols"] == 0


# =============================================================================
# Free-space guard
# =============================================================================
def test_free_space_guard_pauses_and_resumes(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path)
    gs = w.gs
    floor = int(gs.logger.min_free_bytes)
    disk = FakeDisk(floor * 10)
    gs.logger._disk_usage = disk
    w.run(20)
    assert not gs.logger.paused and not any("archive logging paused" in r for r in gs.alarm_reasons)
    rows0, cadu0, frames0 = gs.archive.rx_rows, gs.archive.cadu_records, gs.rx.frames_ok
    disk.free = floor - 1
    w.run(30)
    assert gs.logger.paused and gs.archive.raw_paused
    warn = [e for e in gs.events if e["source"] == "ARCHIVE" and "PAUSED" in e["text"]]
    assert len(warn) == 1 and warn[0]["level"] == "WARN"
    assert any("archive logging paused" in r for r in gs.alarm_reasons) and gs.alarm in ("YELLOW", "RED")
    assert gs.archive.rx_rows - rows0 <= 6 and gs.archive.rx_rows_skipped >= 20      # within one check period
    assert gs.archive.cadu_skipped > 0 and gs.rx.frames_ok > frames0
    gs.archive.commit()
    n_frames = _rows(Path(w.cfg["gds"]["archive_dir"]), "SELECT COUNT(*) FROM frames")[0][0]
    assert n_frames >= gs.rx.frames_ok - 1                                         # frames still recorded
    disk.free = int(floor * 1.02)                                                  # above the floor, below +5 %
    w.run(15)
    assert gs.logger.paused
    disk.free = int(floor * 1.06)
    w.run(15)
    assert not gs.logger.paused
    info = [e for e in gs.events if e["source"] == "ARCHIVE" and "resumed" in e["text"]]
    assert len(info) == 1 and info[0]["level"] == "INFO"
    rows1 = gs.archive.rx_rows
    w.run(10)
    assert gs.archive.rx_rows - rows1 >= 9 and gs.archive.cadu_records > cadu0
    snap = gs.snapshot()["logger"]
    assert snap["pauses"] == 1 and snap["free_bytes"] == int(floor * 1.06) and snap["min_free_bytes"] == floor
    assert snap["resume_free_bytes"] == int(floor * datalog.RESUME_FACTOR)


def test_free_space_guard_off_and_unreadable(tmp_path):
    a = Archive(tmp_path / "a")
    lg = datalog.DataLogger(a, {"archive_keep_days": 0, "archive_max_gb": 0, "archive_min_free_mb": 0},
                            disk_usage=FakeDisk(0))
    assert lg.tick(T0, lambda: {"t": T0}) == [] and not lg.paused and a.rx_rows == 1

    def broken(path):
        raise OSError(5, "I/O error (drive unplugged)")
    lg2 = datalog.DataLogger(a, {"archive_keep_days": 0, "archive_max_gb": 0, "archive_min_free_mb": 100},
                             disk_usage=broken)
    lg2.tick(T0, lambda: {"t": T0})
    assert lg2.free_error and "unplugged" in lg2.free_error and not lg2.paused
    a.close()


# =============================================================================
# Retention
# =============================================================================
def _seed(archive: Archive, now: float, days_back: list[int]) -> None:
    """Day folders (raw files of 100 kB) and rows of every pruned table at the given ages."""
    for k in days_back:
        t = now - k * DAY
        d = archive.root / _day(t)
        d.mkdir(exist_ok=True)
        (d / "cadu.bin").write_bytes(b"\x00" * 100_000)
        (d / "events.log").write_text("x\n")
        for i in range(30):
            ti = t - 60 + i
            archive.rx_stats({"t": ti, "los": 1, "n_rx": 0})
            archive.frame(ert=ti, mcfc=i, ok=1)
            archive.packet(ti, None, 16, "HK", i, None, 0, 0, b"\x01", {"bus_v": 5.0}, {})
            assert archive.db is not None                       # (Archive.event() files by the real date)
            archive.db.execute("INSERT INTO events VALUES (?,?,?,?)", (ti, "INFO", "TEST", f"seed {k}"))
    archive.commit()


def _cfg(keep=0, max_gb=0.0, free=0.0):
    return {"archive_keep_days": keep, "archive_max_gb": max_gb, "archive_min_free_mb": free}


def _run_logger(lg: datalog.DataLogger, now: float, ticks: int):
    ev = []
    for i in range(ticks):
        ev += lg.tick(now + i, lambda: {"t": now})
    return ev


def test_retention_by_age_never_today_future_or_links(monkeypatch, tmp_path):
    now = T0 + 3600.0                                                              # 2027-01-15 09:00 UTC
    monkeypatch.setattr(time, "time", lambda: now)
    a = Archive(tmp_path / "arc")
    _seed(a, now, [10, 9, 3, 2, 0])
    future = a.root / _day(now + 2 * DAY)
    future.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.bin").write_bytes(b"k")
    os.symlink(target, a.root / _day(now - 20 * DAY))                              # a link named like a day
    lg = datalog.DataLogger(a, _cfg(keep=3), disk_usage=FakeDisk(10**12))
    ev = _run_logger(lg, now + datalog.FIRST_PRUNE_CHECK_S, 3)
    days = {p.name for p in a.root.iterdir() if p.is_dir() and not p.is_symlink()}
    assert days == {_day(now - 3 * DAY), _day(now - 2 * DAY), _day(now), _day(now + 2 * DAY)}
    assert (a.root / _day(now - 20 * DAY)).is_symlink() and (target / "keep.bin").exists()
    cutoff = now - 3 * DAY
    for table, col in (("rx_stats", "t"), ("params", "ert"), ("frames", "ert")):
        assert _rows(a.root, f"SELECT COUNT(*) FROM {table} WHERE {col} < ?", (cutoff,))[0][0] == 0
        assert _rows(a.root, f"SELECT COUNT(*) FROM {table} WHERE {col} >= ?", (cutoff,))[0][0] >= 60
    assert _rows(a.root, "SELECT COUNT(*) FROM events WHERE t < ?", (cutoff,))[0][0] == 90   # kept: 10, 9, 3
    assert _rows(a.root, "SELECT COUNT(*) FROM packets")[0][0] == 150                       # not pruned
    msg = [t for lvl, t in ev if lvl == "INFO" and t.startswith("retention:")]
    assert len(msg) == 1 and "2 day folder(s)" in msg[0] and "archive_keep_days 3" in msg[0]
    assert "270 rx_stats / params / frames rows" in msg[0]
    h = lg.health(now)
    assert h["last_prune"]["days_deleted"] == [_day(now - 10 * DAY), _day(now - 9 * DAY)]
    assert h["totals"]["rows_deleted"] == 3 * 30 * 3 and not h["prune_running"]   # days 10, 9 and 3 (rows < cutoff)
    a.close()


def test_retention_by_size_oldest_first_in_batches(monkeypatch, tmp_path):
    now = T0 + 7200.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(datalog, "PRUNE_BATCH", 7)                                 # several ticks per prune
    a = Archive(tmp_path / "arc")
    _seed(a, now, [6, 5, 4, 1, 0])
    lg = datalog.DataLogger(a, _cfg(max_gb=0.00025), disk_usage=FakeDisk(10**12))  # 250 kB cap
    ev = []
    t = now + datalog.FIRST_PRUNE_CHECK_S
    for i in range(400):
        ev += lg.tick(t + i, lambda: {"t": now})
        if not lg.health(now)["prune_running"] and lg.used_bytes <= lg.max_bytes:
            break
    days = sorted(p.name for p in a.root.iterdir() if p.is_dir())
    assert days == [_day(now)]                                                     # today always stays
    msgs = [x for lvl, x in ev if x.startswith("retention:")]
    assert len(msgs) == 4 and all("archive_max_gb" in m for m in msgs)            # one day per prune
    assert [m.split("deleted (")[1].split(")")[0] for m in msgs] == [_day(now - k * DAY) for k in (6, 5, 4, 1)]
    today0 = datetime.fromtimestamp(now, UTC).replace(hour=0, minute=0, second=0).timestamp()
    assert _rows(a.root, "SELECT COUNT(*) FROM rx_stats WHERE t < ?", (today0,))[0][0] == 0
    assert _rows(a.root, "SELECT COUNT(*) FROM rx_stats WHERE t >= ?", (today0,))[0][0] >= 30
    assert lg.health(now)["db_used_bytes"] < lg.health(now)["db_file_bytes"]       # pages freed, file kept
    a.close()


def test_retention_prunes_exports(monkeypatch, tmp_path):
    now = T0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(datalog, "EXPORT_KEEP_MAX", 2)
    a = Archive(tmp_path / "arc")
    a.exports_dir.mkdir()
    for i in range(6):
        p = a.exports_dir / f"VGQ1_rf_2027011{i}T000000Z_2027011{i}T010000Z.csv"
        p.write_text("x")
        os.utime(p, (now - (12 - 2 * i) * DAY, now - (12 - 2 * i) * DAY))   # 12, 10, .. 2 days old
    lg = datalog.DataLogger(a, _cfg(keep=7), disk_usage=FakeDisk(10**12))
    _run_logger(lg, now + datalog.FIRST_PRUNE_CHECK_S, 1)
    left = sorted(p.name for p in a.exports_dir.iterdir())
    assert [x[8:16] for x in left] == ["20270114", "20270115"]                   # count cap after the age limit
    a.close()


# =============================================================================
# CSV export
# =============================================================================
@pytest.fixture
def filled_archive(tmp_path):
    a = Archive(tmp_path / "arc")
    for i in range(20):
        t = T0 + i
        a.rx_stats({"t": t, "los": 0, "pass_no": 1, "n_rx": i % 2, "rssi_dbm": -100.25 if i % 2 else None,
                    "snr_db": None, "fer_10m": 0.125, "frames_ok": i % 2, "frames_lost": 0})
        a.frame(ert=t, mcfc=i, vcid=0, vcfc=i, fhp=0, rs=2, rssi=-101, snr=9.5, ok=1, reason="")
    a.packet(T0 + 5, T0 + 4.5, 16, "HK", 3, 1234.5, 2, 0, b"\x01\x02\xff", {"bus_v": 5.02, "fsw_mode": 2}, {})
    a.event(T0 + 6, "WARN", "S/C EVR", "=HYPERLINK(\"http://x\") ünïcødé")
    a.event(T0 + 7, "INFO", "GDS", "plain, with \"quotes\"\nand a newline")
    a.command({"id": 4, "text": "-1+1", "state": "DONE", "detail": "@sum"})
    a.commit()
    yield a
    a.close()


def test_export_csv_format(filled_archive, tmp_path):
    a = filled_archive
    out = tmp_path / "out"
    res = export.export_range(a.db_path, out, T0 + 2, T0 + 12, export.PRODUCTS)
    by = {r.product: r for r in res}
    assert [r.product for r in res] == list(export.PRODUCTS)
    assert by["rf"].name == "VGQ1_rf_20270115T080002Z_20270115T080012Z.csv"
    assert export.EXPORT_NAME_RE.fullmatch(by["rf"].name)
    head, rows = _read_csv(by["rf"].path)
    assert head[:2] == ["utc [ISO-8601]", "unix_time [s]"] and len(head) == 2 + len(export.RF_HEADERS)
    assert "rssi [dBm]" in head and "range_rate [m/s]" in head and "fer_10m [ratio]" in head
    assert len(rows) == 10 == by["rf"].rows                                        # [from, to)
    assert rows[0][0] == "2027-01-15T08:00:02.000Z" and float(rows[0][1]) == T0 + 2
    assert rows[-1][1] == repr(T0 + 11)
    i = head.index("rssi [dBm]")
    assert rows[0][i] == "" and rows[1][i] == "-100.25"                            # NULL -> empty
    head, rows = _read_csv(by["params"].path)
    vals = {r[head.index("parameter")]: r for r in rows}
    assert set(vals) == {"bus_v", "fsw_mode"} and vals["bus_v"][head.index("value [unit column]")] == "5.02"
    assert vals["bus_v"][head.index("scet_utc [ISO-8601]")] == "2027-01-15T08:00:04.500Z"
    head, rows = _read_csv(by["events"].path)
    assert rows[0][head.index("text")].startswith("'=HYPERLINK") and "ünïcødé" in rows[0][head.index("text")]
    assert rows[1][head.index("text")] == "plain, with \"quotes\"\nand a newline"
    head, rows = _read_csv(by["commands"].path)                                    # T0 + now: outside the range
    assert rows == []
    head, rows = _read_csv(by["packets"].path)
    assert rows[0][head.index("raw [hex]")] == "0102ff" and rows[0][head.index("length [octets]")] == "3"
    head, rows = _read_csv(by["frames"].path)
    assert len(rows) == 10 and "rssi_raw [dBm]" in head and rows[0][head.index("snr [dB]")] == "9.5"
    assert not list(out.glob("*.tmp"))
    res = export.export_range(a.db_path, out, 0, 4e9, ["commands"])
    head, rows = _read_csv(res[0].path)
    assert rows[0][head.index("command")] == "'-1+1" and rows[0][head.index("detail")] == "'@sum"


def test_export_paging_is_identical_and_bounded(filled_archive, tmp_path, monkeypatch):
    a = filled_archive
    whole = export.export_range(a.db_path, tmp_path / "w", T0, T0 + 30, ["rf", "frames"])
    monkeypatch.setattr(export, "CHUNK_ROWS", 3)
    pages = export.export_range(a.db_path, tmp_path / "p", T0, T0 + 30, ["rf", "frames"])
    for x, y in zip(whole, pages, strict=True):
        assert x.path.read_bytes() == y.path.read_bytes() and x.rows == 20
    con = export.open_readonly(a.db_path)
    p = export.products()["rf"]
    seen = list(export.iter_rows(con, p, T0, T0 + 30, chunk=4))
    assert [r[0] for r in seen] == [T0 + i for i in range(20)]
    con.close()


def test_export_is_read_only_and_never_blocks_the_writer(filled_archive, tmp_path):
    a = filled_archive
    con = export.open_readonly(a.db_path)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM rx_stats")
    it = export.iter_rows(con, export.products()["rf"], T0, T0 + 30, chunk=5)
    next(it)                                                                       # a read in progress
    errors = a.write_errors
    for i in range(5):
        a.rx_stats({"t": T0 + 100 + i, "los": 1})
    a.commit()
    assert a.write_errors == errors                                                # WAL: writer not blocked
    assert len(list(it)) == 19
    con.close()
    with pytest.raises(export.ExportError, match="no archive database"):
        export.open_readonly(tmp_path / "missing" / "gds.sqlite")
    assert not (tmp_path / "missing").exists()


def test_export_time_and_product_parsing():
    assert export.parse_time("2027-01-15T08:00:00Z") == T0
    assert export.parse_time("2027-01-15T08:00:00") == T0                          # no zone = UTC
    assert export.parse_time("2027-01-15T09:00:00+01:00") == T0
    assert export.parse_time(str(int(T0))) == T0 and export.parse_time(T0 + 0.5) == T0 + 0.5
    for bad in ("yesterday", "", "1e99", True, "2027-13-01", "9" * 41, -1, float("nan")):
        with pytest.raises(export.ExportError):
            export.parse_time(bad)
    assert export.parse_products("events, rf,rf") == ["rf", "events"]
    assert export.parse_products("all") == list(export.PRODUCTS) == export.parse_products(None)
    for bad in ("", "rf,../etc", ["rf", 3], "tables"):
        with pytest.raises(export.ExportError):
            export.parse_products(bad)
    with pytest.raises(export.ExportError):
        export.export_range(Path("x"), Path("y"), T0, T0, ["rf"])


def test_export_cli(filled_archive, tmp_path):
    a = filled_archive
    out = tmp_path / "cli"
    cmd = [sys.executable, "-m", "dssq.export", "--archive", str(a.root), "--from", "2027-01-15T08:00:00Z",
           "--to", str(int(T0 + 20)), "--what", "rf,events", "--out", str(out)]
    r = subprocess.run(cmd, cwd=GROUND, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in out.iterdir())
    assert names == ["VGQ1_events_20270115T080000Z_20270115T080020Z.csv",
                     "VGQ1_rf_20270115T080000Z_20270115T080020Z.csv"]
    assert "20 rows" in r.stdout
    r = subprocess.run([*cmd[:-2], "--what", "secrets"], cwd=GROUND, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "unknown product" in r.stderr
    r = subprocess.run([sys.executable, "-m", "dssq.export", "--archive", str(a.root), "--from", "x", "--to", "1"],
                       cwd=GROUND, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "export failed" in r.stderr
    r = subprocess.run([sys.executable, "-m", "dssq.export", "--archive", str(a.root), "--from", "0", "--to",
                        "4000000000", "--what", "rf"], cwd=GROUND, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and (a.exports_dir / export.file_name("rf", 0, 4e9)).is_file()   # default --out
    assert export.file_name("rf", 0, 4e9) == "VGQ1_rf_19700101T000000Z_20961002T070640Z.csv"


# =============================================================================
# Web export (real GroundStation behind the server)
# =============================================================================
def test_web_export_authenticated_with_server_chosen_names(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path)
    w.run(40)
    gs = w.gs
    gs.cfg["gds"]["http_token"] = TOKEN
    monkeypatch.setattr(time, "time", lambda: w.clk.t)
    srv = web.start_web(gs, "127.0.0.1", 0)
    port = srv.server_address[1]
    body = {"from": export.iso_utc(T0), "to": export.iso_utc(w.clk.t + 1), "what": ["rf", "events"]}
    try:
        st, _, out = request(port, "POST", "/api/export", body)
        assert st == 403 and "X-DSSQ-Token" in strict_json(out)["error"]
        evil = {"X-DSSQ-Token": TOKEN, "Origin": "http://evil.example"}
        assert request(port, "POST", "/api/export", body, evil)[0] == 403
        assert request(port, "POST", "/api/export", body, {"X-DSSQ-Token": TOKEN}, ctype="text/plain")[0] == 415
        assert request(port, "POST", "/api/export", body, {"X-DSSQ-Token": TOKEN, "Host": "rebind.example"})[0] == 403
        st, _, out = request(port, "POST", "/api/export", {**body, "name": "../../x.csv", "dir": "/tmp"},
                             {"X-DSSQ-Token": TOKEN})
        r = strict_json(out)
        assert st == 200 and r["status"] == "OK", r
        names = [f["name"] for f in r["files"]]
        assert all(export.EXPORT_NAME_RE.fullmatch(n) for n in names) and len(names) == 2
        assert {p.name for p in gs.exports_dir.iterdir()} == set(names)
        rf = next(f for f in r["files"] if f["product"] == "rf")
        assert rf["rows"] >= 35                                                    # ~1 row/s over 40 s
        metrics = request(port, "GET", "/metrics")[2].decode()
        for fam in ("dssq_logger_rx_rows", "dssq_logger_paused", "dssq_logger_free_bytes", "dssq_logger_used_bytes"):
            assert f"\n{fam} " in metrics, fam
        listed = strict_json(request(port, "GET", "/api/exports")[2])
        assert {f["name"] for f in listed} == set(names)
        st, h, csv_body = request(port, "GET", "/api/exports/" + rf["name"])
        assert st == 200 and h["content-type"] == "text/csv; charset=utf-8"
        assert h["content-disposition"] == f'attachment; filename="{rf["name"]}"'
        assert csv_body.startswith(b"utc [ISO-8601],unix_time [s],los [0/1]")
        outside = tmp_path / "secret.csv"
        outside.write_text("SECRET")
        os.symlink(outside, gs.exports_dir / "VGQ1_rf_20270115T080000Z_20270115T090000Z.csv")
        for path in ("/api/exports/../gds.sqlite", "/api/exports/..%2fgds.sqlite", "/api/exports/gds.sqlite",
                     "/api/exports/VGQ1_rf_20270115T080000Z_20270115T090000Z.csv", "/api/exports/x.csv",
                     "/api/exports/" + rf["name"] + ".tmp"):
            st, _, b = request(port, "GET", path)
            assert st == 404 and b"SECRET" not in b, path
        for bad in ({"from": "x", "to": "y"}, {"to": body["to"]}, {**body, "what": ["rf", "../x"]},
                    {**body, "what": "nope"}, {**body, "what": []}, {**body, "from": True},
                    {**body, "from": body["to"], "to": body["from"]}, {**body, "what": ["rf"] * 7}):
            st, _, out = request(port, "POST", "/api/export", bad, {"X-DSSQ-Token": TOKEN})
            assert st == 400, (bad, out)
        assert any(e["source"] == "EXPORT" and "rows" in e["text"] for e in gs.events)
        gs.archive.raw_paused = True                                               # the guard refuses exports
        st, _, out = request(port, "POST", "/api/export", body, {"X-DSSQ-Token": TOKEN})
        assert st == 200 and strict_json(out)["status"] == "REFUSED"
        gs.archive.raw_paused = False
        assert gs._export_lock.acquire(blocking=False)                            # one export at a time
        try:
            st, _, out = request(port, "POST", "/api/export", body, {"X-DSSQ-Token": TOKEN})
            assert strict_json(out)["status"] == "BUSY"
        finally:
            gs._export_lock.release()
    finally:
        srv.shutdown()
        srv.server_close()


def test_snapshot_logger_section_is_json_and_shown(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path)
    w.run(10)
    lg = json.loads(json.dumps(w.gs.snapshot()))["logger"]
    for key in ("rx_rows", "last_row_t", "paused", "free_bytes", "min_free_bytes", "used_bytes", "max_bytes",
                "keep_days", "last_prune", "prune_running", "raw_bytes", "db_used_bytes", "exports"):
        assert key in lg, key
    script = (GROUND / "dssq" / "display" / "dashboard.html").read_text()
    for key in ("rx_rows", "last_row_t", "paused", "free_bytes", "used_bytes", "last_prune", "/api/export"):
        assert key in script, key
    gs = GroundStation(_config(tmp_path / "noarc", key=False), archive=False)
    assert gs.snapshot()["logger"]["enabled"] is False
