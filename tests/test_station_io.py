"""Station I/O MCU link (display/frontpanel.py, D-78): the LED-matrix M line, the hello
line's Radio Control Unit state and their plumbing into GroundStation.station_io.

The sketch side (dssq-groundstation/sketch/sketch.ino parse_matrix_line(), the hello
format) is exercised by the station harness tools/host_test/station/test_e22.cpp; these
tests pin the Linux side of the same lines.
"""
from __future__ import annotations

import time

import pytest

from dssq.config import load_config
from dssq.display import frontpanel as fp
from dssq.gds import GroundStation


def _st(**matrix: object) -> dict:
    base = {"sync": "LOCK", "tx": False, "rssi_dbm": -82.4, "rssi_recent": True, "rssi_overload": None,
            "margin_db": None, "margin_floor": None, "lost_10m": 3, "clock_synced": None}
    base.update(matrix)
    return {"matrix": base}


# =============================================================================
# M line
# =============================================================================
def test_matrix_line_sends_what_the_gds_has_and_unknown_as_question_mark():
    assert fp.matrix_line(_st()) == "M|L|0|-82|?|3|?"
    assert fp.matrix_line(_st(sync="FLYWHEEL", tx=True, margin_db=12.6, clock_synced=False)) == "M|F|1|-82|13|3|0"
    assert fp.matrix_line(_st(sync="PACKET", margin_db=3.0, margin_floor=True, clock_synced=True)) == "M|P|0|-82|N3|3|1"
    assert fp.matrix_line(_st(sync="SEARCH", rssi_recent=False)) == "M|S|0|-|?|3|?"      # no packet in the LOS timeout
    assert fp.matrix_line(_st(rssi_dbm=None)) == "M|L|0|?|?|3|?"                       # a packet without RSSI
    assert fp.matrix_line(_st(rssi_overload=True)) == "M|L|0|O|?|3|?"
    assert fp.matrix_line(_st(sync=None, tx=None, lost_10m=None)) == "M|?|?|-82|?|?|?"
    assert fp.matrix_line({}) == "M|?|?|-|?|?|?"                                        # an older panel_status


def test_matrix_line_fields_stay_inside_the_sketch_parser_ranges():
    line = fp.matrix_line(_st(rssi_dbm=-1e9, margin_db=1e9, lost_10m=10**9))
    assert line == "M|L|0|-300|300|30000|?" and len(line) <= fp.LINE_MAX
    assert fp.matrix_line(_st(rssi_dbm=float("nan"), margin_db=float("inf"), lost_10m=True)) == "M|L|0|?|?|?|?"
    assert fp.matrix_line(_st(lost_10m=-1)) == "M|L|0|-82|?|?|?"


# =============================================================================
# Hello line: the RCU state the MCU reports
# =============================================================================
@pytest.mark.parametrize(("fields", "want"), [
    (["DSSQ-IO 4.0", "1", "1", "42"], ("DSSQ-IO 4.0", True, "OK", 42.0)),
    (["DSSQ-IO 4.0", "0", "0", "0"], ("DSSQ-IO 4.0", False, "FAILED", 0.0)),
    (["DSSQ-IO 4.0 SX126x", "-1", "1", "-1"], ("DSSQ-IO 4.0 SX126x", True, "NEVER", None)),
    (["DSSQ-IO 3.1", "1", "1"], ("DSSQ-IO 3.1", True, None, None)),          # older sketch: a constant 1
    (["DSSQ-IO 4.0", "1", "0", "-5"], ("DSSQ-IO 4.0", False, None, None)),   # unreadable age: unknown
    (["DSSQ-IO 4.0", "2", "0", "5"], ("DSSQ-IO 4.0", False, None, None)),    # unknown result
    (["DSSQ-IO 4.0", "1", "0", str(2**31)], ("DSSQ-IO 4.0", False, None, None)),
])
def test_parse_hello(fields, want):
    assert fp.parse_hello(fields) == want


class _Gs:
    def __init__(self):
        self.hellos: list[tuple] = []

    def station_hello(self, *args):
        self.hellos.append(args)


def test_front_panel_passes_the_hello_on_and_sends_the_m_line():
    sent: list[tuple] = []

    class Bridge:
        def provide(self, name, fn):
            pass

        def notify(self, name, *args):
            sent.append((name, *args))

        def call(self, name, *args, timeout=3.0):
            return 1

    gs = _Gs()
    panel = fp.FrontPanel(gs, fp.BridgeLink(Bridge(), "test"))
    panel._handle("H|DSSQ-IO 4.0|1|1|7")
    panel._handle("H|DSSQ-IO 3.1|1|0")
    assert gs.hellos == [("DSSQ-IO 4.0", True, "OK", 7.0), ("DSSQ-IO 3.1", False, None, None)]

    class Status:
        def __init__(self):
            self.lines: list[str] = []

        def panel_status(self):
            panel.stop_ev.set()                      # one pass of the tx loop
            return {"los": False, "alarm": "NONE", "uplink": False, "beacon": 0, "pending_ids": [], **_st()}

        def station_status_sent(self, line):
            self.lines.append(line)

    panel.gs = Status()
    panel._tx_loop()
    assert [s[1] for s in sent] == ["S|1|0|0|0", "Q|-1", "M|L|0|-82|?|3|?"]
    assert all(s[0] == "fp_line" for s in sent) and panel.gs.lines == ["S|1|0|0|0"]


# =============================================================================
# GroundStation.station_io (D-78)
# =============================================================================
def _gs(tmp_path, monkeypatch, rcu: bool) -> GroundStation:
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    cfg["gds"]["rcu"] = rcu
    cfg["sdls"]["key_file"] = str(tmp_path / "no_key.hex")
    return GroundStation(cfg, archive=False)


def test_station_io_reports_the_rcu_state_never_a_constant(tmp_path, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    gs = _gs(tmp_path, monkeypatch, rcu=True)
    io = gs.snapshot()["station_io"]
    assert "rcu" not in io and io["rcu_configured"] is True
    assert (io["rcu_last"], io["rcu_last_t"], io["connected"]) == (None, None, False)
    gs.station_hello("DSSQ-IO 4.0", False, "NEVER", None)
    io = gs.snapshot()["station_io"]
    assert (io["rcu_last"], io["rcu_last_t"], io["connected"]) == ("NEVER", None, True)
    gs.station_hello("DSSQ-IO 4.0", False, "OK", 7.0)
    assert gs.snapshot()["station_io"]["rcu_last_t"] == now[0] - 7.0
    now[0] += 5.4
    gs.station_hello("DSSQ-IO 4.0", False, "OK", 12.0)       # whole seconds: no one-second jitter
    assert gs.snapshot()["station_io"]["rcu_last_t"] == now[0] - 5.4 - 7.0
    gs.station_hello("DSSQ-IO 4.0", True, "FAILED", 0.0)
    io = gs.snapshot()["station_io"]
    assert (io["rcu_last"], io["rcu_last_t"], io["kb"]) == ("FAILED", now[0], True)
    gs.station_hello("DSSQ-IO 3.1", True)                     # an older sketch: unknown again
    assert gs.snapshot()["station_io"]["rcu_last"] is None
    assert _gs(tmp_path / "b", monkeypatch, rcu=False).snapshot()["station_io"]["rcu_configured"] is False


def test_panel_status_matrix_fields_without_reception(tmp_path, monkeypatch):
    gs = _gs(tmp_path, monkeypatch, rcu=False)
    m = gs.panel_status()["matrix"]
    assert m == {"sync": "SEARCH", "tx": False, "rssi_dbm": None, "rssi_recent": False, "rssi_overload": None,
                 "margin_db": None, "margin_floor": None, "lost_10m": 0, "clock_synced": None}
    assert fp.matrix_line(gs.panel_status()) == "M|S|0|-|?|0|?"
