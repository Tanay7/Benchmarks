"""Amateur station identification of the GROUND transmitter (dssq.gds.StationIdent).

The station transmits "DE <callsign> <name>" in plain ASCII while it commands: right after
the first uplink of a run, at least every 10 minutes while uplink transmissions continue,
and once when the uplink has been idle for ID_END_IDLE_S (and at least the FOP T1) after a
transmission - in an uplink window, or straight away while the downlink is lost (no window
comes), or when the GDS stops. The flight side (no CLTU -> RF cltu_bad only, no EVR, FDIR or
FARM-1 change) is checked with the flight code in tools/host_test/test_station_id.cpp; the
simulator mirrors it.
"""
import itertools
import re
import time
from pathlib import Path

import pytest
from test_end_to_end import T0, Clock, World, _config

from dssq import config as station_config
from dssq import link
from dssq.ccsds.tc import build_cltu, build_tc_frame, cltu_length
from dssq.cmdparse import parse_sequence, seqload_commands
from dssq.gds import (CAD_SYMBOLS, ID_END_IDLE_S, ID_INTERVAL_S, MIN_FRAME_PERIOD_S, TX_TURNAROUND_S, WINDOW_GUARD_S,
                      GroundStation, Runner, StationIdent, ident_text)
from dssq.radio import sx126x as sx
from dssq.radio.e22 import NOISE_QUERY

ROOT = Path(__file__).resolve().parents[1]
ID = b"DE N0CALL DSS-Q1"
FLIGHT_RX_QUIET_S = 0.1          # flight kE22RxQuietMs: the station must stop this long before the next frame


@pytest.fixture
def world(monkeypatch, tmp_path):
    return World(monkeypatch, tmp_path)


# ---------------------------------------------------------------- configuration
def test_callsign_default_validation_and_normalisation():
    cfg = station_config.load_config()
    assert cfg["station"]["callsign"] == "N0CALL" == station_config.CALLSIGN_PLACEHOLDER
    assert 'callsign = "N0CALL"' in station_config.DEFAULT_PATH.read_text()
    for ok, norm in (("g4abc", "G4ABC"), (" VK2/G4ABC ", "VK2/G4ABC"), ("G4ABC/P", "G4ABC/P"), ("W1AW", "W1AW"),
                     ("2E0ABC", "2E0ABC")):
        c = station_config.merge({"station": {"callsign": ok}})
        station_config.validate(c)
        assert c["station"]["callsign"] == norm
    for bad in ("", "AB", "ABCDEF", "123456", "G4 ABC", "G4ABC/", "/G4ABC", "G4//ABC", "G4ABC-1", "Ä4ABC",
                "G4ABCDEFGHIJKLMNO", 42, None, True):
        with pytest.raises(ValueError, match="callsign"):
            station_config.validate(station_config.merge({"station": {"callsign": bad}}))


def test_identification_text_is_printable_ascii():
    assert ident_text("N0CALL", "DSS-Q1") == "DE N0CALL DSS-Q1"
    assert ident_text("G4ABC/P", "Station Üë#1 exé") == "DE G4ABC/P Station1ex"   # ASCII only
    assert ident_text("G4ABC", "!!!") == "DE G4ABC"
    long = ident_text("G4ABC", "A" * 64)
    assert long == "DE G4ABC " + "A" * 16
    for text in (ident_text("VK2/G4ABC/P", "x" * 64), "DE N0CALL DSS-Q1"):
        raw = text.encode("ascii")
        assert all(0x20 <= b < 0x7F for b in raw) and b"\xeb\x90" not in raw and len(raw) <= 112


# ---------------------------------------------------------------- timing rules
def test_timing_rules_pure():
    s = StationIdent("G4ABC", "DSS-Q1")
    assert s.due(0.0) is None and not s.pending                         # nothing transmitted
    s.on_transmit(100.0)
    assert s.due(100.0) == "periodic"                                    # first uplink of the run
    s.sent(100.0, "periodic")
    assert not s.pending and s.due(100.0) is None
    s.on_transmit(200.0)                                                 # same session
    assert s.due(200.0) is None and s.due(319.0) is None
    assert s.due(320.0) == "end"                                         # idle ID_END_IDLE_S
    assert s.due(320.0, end_idle_s=300.0) is None and s.due(500.0, end_idle_s=300.0) == "end"
    s.on_transmit(500.0)
    assert s.due(100.0 + ID_INTERVAL_S - 60.0 - 0.1, end_idle_s=1e9) is None
    assert s.due(100.0 + ID_INTERVAL_S - 60.0, end_idle_s=1e9) == "periodic"   # 10 min less the margin
    assert s.due(100.0 + ID_INTERVAL_S - 200.0, early_s=200.0, end_idle_s=1e9) == "periodic"
    assert s.due(101.0, early_s=1e6, end_idle_s=1e9) == "periodic"      # margin capped at the interval
    assert s.due(50.0, end_idle_s=1e9) == "periodic"                    # clock stepped back: identify
    s.sent(600.0, "periodic")
    assert s.count == 2 and s.snapshot() == {"callsign": "G4ABC", "text": "DE G4ABC DSS-Q1", "count": 2,
                                             "last_t": 600.0, "last_reason": "periodic", "pending_since": None}


def test_placeholder_callsign_warns_at_start(tmp_path):
    gs = GroundStation(_config(tmp_path), archive=False)
    try:
        assert any(e["level"] == "WARN" and e["source"] == "IDENT" and "N0CALL" in e["text"] for e in gs.events)
        st = gs.snapshot()["link"]["station_id"]
        assert st == {"callsign": "N0CALL", "text": "DE N0CALL DSS-Q1", "count": 0, "last_t": None,
                      "last_reason": None, "pending_since": None}
    finally:
        gs.close()
    cfg = _config(tmp_path)
    cfg["station"]["callsign"] = "G4ABC"
    gs = GroundStation(cfg, archive=False)
    try:
        assert not any(e["source"] == "IDENT" for e in gs.events)
    finally:
        gs.close()


# ---------------------------------------------------------------- E22 world
def _id_times(w):
    return [e["t"] for e in w.gs.events if e["source"] == "IDENT" and e["text"].startswith("station identification")]


def test_e22_id_follows_the_first_uplink_and_closes_the_session(world):
    w, gs = world, world.gs
    w.run(20)
    assert ID not in w.writes and gs.station_id.count == 0
    assert w.command("NOOP") == "EXECUTED"
    first_cltu = next(i for i, c in enumerate(w.writes) if c[:2] == b"\xeb\x90")
    assert w.writes[first_cltu + 1] == ID                                 # same window, right after the data
    s = gs.snapshot()["link"]["station_id"]
    assert s["count"] == 1 and s["last_reason"] == "periodic" and s["pending_since"] is None
    t_first = _id_times(w)[0]
    w.run(30)
    assert w.command("PING 7") == "EXECUTED"                              # second command: no new ID yet
    assert gs.station_id.count == 1 and gs.station_id.pending
    last_tx = gs.station_id.last_tx_t
    w.run(ID_END_IDLE_S + 30)
    assert gs.station_id.count == 2 and gs.station_id.last_reason == "end" and not gs.station_id.pending
    t_end = _id_times(w)[1]
    assert ID_END_IDLE_S <= t_end - last_tx <= ID_END_IDLE_S + 5.0 and t_end - t_first < ID_INTERVAL_S
    # The simulator, like the flight, counts each ID in RF cltu_bad and nothing else.
    sim = w.sim
    assert sim.cltu_bad == w.writes.count(ID) == 2 and sim.tc_bad == 0 and sim.cltu_ok == sim.tc_ok
    assert not any("TC frame rejected" in t for t in w.events())
    w.run(20)
    v = gs.tlm["RF"]["values"]
    assert v["cltu_bad"] == 2 and gs.tlm["HK"]["values"]["tc_frames_bad"] == 0
    assert gs.tlm["HK"]["values"]["farm_vr"] == sim.farm.vr == 2         # two AD frames; the IDs left FARM-1 alone


def test_e22_periodic_id_while_commanding_for_25_minutes(world):
    """A command every 50 s for 25 min: the station identifies right after the first uplink
    and then at least every 10 minutes (never more often than 10 min less the margin)."""
    w, gs = world, world.gs
    w.run(10)
    uplinks = []
    for _ in range(30):                                                  # bounded: 30 x 50 s
        r = gs.submit_command("NOOP")
        assert r["status"] == "QUEUED"
        w.run(50)
        uplinks.append(gs.station_id.last_tx_t)
    w.run(ID_END_IDLE_S + 20)
    ids = _id_times(w)
    reasons = [e["text"].split("(")[1].split(")")[0] for e in gs.events if e["source"] == "IDENT"
               and e["text"].startswith("station identification")]
    assert reasons[0] == "periodic" and reasons[-1] == "end" and set(reasons[1:-1]) == {"periodic"}
    gaps = [b - a for a, b in itertools.pairwise(ids[:-1])]                 # the periodic IDs
    assert len(ids) >= 4 and all(ID_INTERVAL_S - 60.0 <= g <= ID_INTERVAL_S for g in gaps), gaps
    # every uplink transmission is covered by an identification within 10 minutes
    assert all(any(0 <= t - u <= ID_INTERVAL_S for t in ids) for u in uplinks)
    assert w.sim.cltu_bad == len(ids) and w.sim.tc_bad == 0


def test_id_while_the_downlink_is_lost_goes_out_without_a_window(world, monkeypatch):
    w, gs = world, world.gs
    w.run(20)
    w.sim.inhibit_until = w.clk.t + 1e6                                  # the spacecraft falls silent
    w.run(gs.los_timeout_s() + 5)
    assert gs.rx.los
    n = len(w.writes)
    gs.station_id.on_transmit(w.clk.t)                                   # e.g. a WAKE transmission
    w.run(1)
    assert w.writes[n:] == [ID] and gs.station_id.last_reason == "periodic"


def test_wake_counts_as_a_transmission(world):
    w, gs = world, world.gs
    w.run(20)
    r = gs.submit_command("WAKE")
    if r["status"] == "CONFIRM":
        r = gs.confirm(r["id"])
    assert r["status"] not in ("REJECTED", "ERROR"), r
    w.run(5)
    assert any("wake-up" in t for t in w.events("RADIO"))
    assert gs.station_id.count >= 1 or gs.station_id.pending
    w.run(30)
    assert gs.station_id.count >= 1 and ID in w.writes


def test_runner_close_sends_the_closing_identification(tmp_path):
    r = Runner(_config(tmp_path), sim=True, web=False, archive=False)
    sent = []
    r.radio.write = sent.append
    r.gs.station_id.on_transmit(1000.0)
    r.close()
    assert sent == [ID] and r.gs.station_id.count == 1 and r.gs.station_id.last_reason == "end"
    (tmp_path / "b").mkdir()
    r2 = Runner(_config(tmp_path / "b"), sim=True, web=False, archive=False)
    sent2 = []
    r2.radio.write = sent2.append
    r2.close()
    assert sent2 == []                                                   # nothing transmitted: no ID


# ---------------------------------------------------------------- SX126x radio path
def test_sx126x_world_id_is_a_packet_the_spacecraft_ignores(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path, radio={"model": "SX126x", "air_rate": 4})
    w.run(30, step=0.5)
    assert w.command("NOOP", wait=60) == "EXECUTED"
    assert ID in w.writes and w.sim.cltu_bad == w.writes.count(ID) >= 1 and w.sim.tc_bad == 0


def test_sx126x_bridge_sends_the_id_as_one_rf_tx_packet():
    class Bridge:
        def __init__(self):
            self.calls = []

        def provide(self, name, fn):
            pass

        def call(self, method, *args, timeout=None):
            self.calls.append((method, args))
            return 1

    b = Bridge()
    radio = sx.Sx126xBridge(b, {"model": "SX126x", "air_rate": 1, "power": 0, "channel": 23}, poll_s=0.0)
    radio.write(ID)
    assert ("rf_tx", (ID.hex(),)) in b.calls and len(ID) <= sx.RF_TX_MAX_OCTETS


# ---------------------------------------------------------------- flight source
def test_flight_drops_a_non_cltu_packet_without_evr_or_fdir():
    """Flight::process_tc, NO_CLTU branch: cltu_bad and a bench-only log line, nothing else."""
    src = (ROOT / "vgq1-spacecraft" / "sketch" / "src" / "fsw" / "flight.cpp").read_text()
    m = re.search(r"if \(o\.stage == TcStage::NO_CLTU\) \{(.*?)\n  \}", src, re.S)
    assert m, "process_tc NO_CLTU branch not found"
    body = m.group(1)
    assert "++cltu_bad_;" in body and "return;" in body
    assert "evr(" not in body and "fdir" not in body and "farm" not in body and "no_bit_lock_" not in body


# ---------------------------------------------------------------- uplink window budget
LONG_CALL = "VK2/G4ABCDEFG/MM"                       # 16 characters: the longest legal callsign
LONG_NAME = "STATION-NAME-16C"                       # 16 characters kept by ident_text()
MODE_FACTOR = {"SAFE": 4.0, "CRUISE": 2.5, "ENCOUNTER": 2.0, "TEST": 2.0}   # flight kModePeriodX10 / 10


def _flight_period_s(model, code, mode):
    """Flight frame_period_ms(): busy x mode factor, >= busy / 0.5 (duty floor), >= 1.5 s."""
    b = link.busy_s(model, code)
    return max(b * MODE_FACTOR[mode], 2.0 * b, MIN_FRAME_PERIOD_S)


def _air_cost_s(model, code, octets, cad):
    """Independent of the GDS planner: one ground packet's transmitter time."""
    t = link.busy_s(model, code, octets) + TX_TURNAROUND_S
    if link.get_model(model).packet_mode and cad:
        t += CAD_SYMBOLS * link.SX126X_PROFILES[code].symbol_s
    return t


def _station(tmp_path, model, code):
    cfg = _config(tmp_path, model=model, air_rate=code)
    cfg["station"]["callsign"], cfg["station"]["name"] = LONG_CALL, LONG_NAME
    return GroundStation(cfg, archive=False, mode="sim")            # SDLS SN in memory: no file writes


def _top_up(gs, now, seqload, max_ext):
    """Keeps every uplink queue full of the longest frames: two external MCS frames (64 octets
    once re-wrapped with AE: CLTU 90), two BD SEQLOAD frames and AD SEQLOADs (CLTU 82)."""
    while len(gs.external_tc) < 2:
        gs.external_tc.append(max_ext)
    while len(gs.fop.bypass_q) < 2:
        gs.fop.submit(seqload[1], now)
    while len(gs.fop.queue) < 4:
        gs.fop.submit(seqload[0], now)


@pytest.mark.parametrize("model", [link.E22_T33D, link.E22_T37S, link.SX126X])
def test_window_plan_never_overruns_and_the_id_interval_holds(tmp_path, monkeypatch, model):
    """Every rate code and every flight mode, the longest identification text (36 octets) and
    every queue kept full of the longest CLTUs: what one window radiates (CLTUs + ID) always
    ends WINDOW_GUARD_S before the spacecraft's next frame in that mode, each window still
    carries at least one CLTU, the ID is never deferred, and IDs follow each other within
    10 minutes while the station transmits continuously."""
    import dataclasses
    from dssq.cmdparse import parse
    ad = seqload_commands(parse_sequence("T+00:00:10 NOOP\n"))[0]
    seqload = (ad, dataclasses.replace(ad, kind="BD"))
    bd_noop = parse("BD NOOP")
    worst = 0.0
    clk = Clock()
    monkeypatch.setattr(time, "time", clk)                             # each window is handled at its ERT
    for code in range(8):
        for mode in MODE_FACTOR:
            d = tmp_path / f"{code}-{mode}"
            d.mkdir()
            gs = _station(d, model, code)
            try:
                assert len(gs.station_id.payload) == 36
                ext_data = bytes(64 - 5 - gs.sdls_tc.overhead - 2)          # TC framing, AE: 64-octet frame
                max_ext = build_tc_frame(ext_data, 0, True, False, 0, sdls=None)
                assert cltu_length(len(max_ext) + gs.sdls_tc.overhead) == 90
                gs.fop._init_vs(0)
                period = _flight_period_s(model, code, mode)
                quiet = period - link.busy_s(model, code)
                t, ids, windows = 1.8e9, [], 0
                while windows * period < 2.2 * ID_INTERVAL_S:              # bounded: about 1320 s
                    if windows < 40:
                        _top_up(gs, t, seqload, max_ext)                       # the worst-case load
                    else:
                        gs.fop.submit(bd_noop, t)                              # then: keep transmitting
                    sn0 = gs.sdls_tc.sn
                    clk.t = t
                    gs._uplink_window(t)
                    chunks = [c for c in gs.take_outbox() if c != NOISE_QUERY]
                    cltus = [c for c in chunks if c[:2] == b"\xeb\x90"]
                    assert gs.sdls_tc.sn - sn0 == len(cltus)                  # no SDLS number on a deferred frame
                    if gs.station_id.payload in chunks:
                        ids.append(t)
                        assert chunks[-1] == gs.station_id.payload             # after the window's data
                    planned = sum(_air_cost_s(model, code, len(c), gs.settings.cad) for c in chunks)
                    worst = max(worst, (planned + WINDOW_GUARD_S) / quiet)
                    assert planned + WINDOW_GUARD_S <= quiet + 1e-9, (model, code, mode, [len(c) for c in chunks])
                    assert sum(link.busy_s(model, code, len(c)) for c in chunks) < quiet
                    assert cltus, (model, code, mode)                          # never starved
                    t += period
                    windows += 1
                assert ids and ids[0] == 1.8e9                                 # with the first uplink
                gaps = [b - a for a, b in itertools.pairwise(ids)]
                assert len(ids) >= 3 and max(gaps) <= ID_INTERVAL_S, (model, code, mode, gaps)
            finally:
                gs.close()
    assert worst <= 1.0


def test_predicted_cltu_lengths_match_the_built_cltus(tmp_path):
    """The planner books a frame before it is built: FOP _cltu_octets() and cltu_length()
    must equal the CLTU that is then radiated (BC, BD, AD; TC and USLP; NONE, AUTH, AE)."""
    import dataclasses
    from dssq.cmdparse import parse
    ad = seqload_commands(parse_sequence("T+00:00:10 NOOP\n"))[0]
    # AD first (a BC directive then holds AD frames until its CLCW confirmation), then BD, then BC.
    cmds = [parse("NOOP"), ad, parse("BD NOOP"), dataclasses.replace(ad, kind="BD"), parse("SETVR 7"), parse("UNLOCK")]
    for framing in ("TC", "USLP"):
        for svc in ("NONE", "AUTH", "AE"):
            d = tmp_path / f"{framing}-{svc}"
            d.mkdir()
            cfg = _config(d)
            cfg["link"]["tc_framing"], cfg["sdls"]["tc_service"] = framing, svc
            gs = GroundStation(cfg, archive=False)
            try:
                gs.fop._init_vs(0)
                for c in cmds:
                    if gs._uplink_check(c):
                        continue                                       # too long for this framing (C-08)
                    r = gs.fop.submit(c, 1.8e9)
                    want = gs.fop._cltu_octets(r)
                    got = gs.fop.next_cltus(1.8e9)
                    assert [len(x) for x in got] == [want], (framing, svc, c.text)
                    assert len(build_cltu(r.frame)) == cltu_length(len(r.frame)) == want
            finally:
                gs.close()


def test_wake_and_los_id_wait_for_the_window_transmissions(world):
    """WAKE (a radio job) and the identification while the downlink is lost have no downlink
    to protect, but they never start on top of a window's planned transmissions."""
    w, gs = world, world.gs
    w.run(20)
    w.clk.t += 1.5                                                     # a frame (the flight's are >= 1.5 s apart)
    gs.station_id.on_transmit(w.clk.t)
    gs._uplink_window(w.clk.t)                                         # ID + nothing else
    assert gs.take_outbox() == [ID]
    busy_s = gs._uplink_busy_until - gs.mono()                         # the busy deadline is on gs.mono
    assert busy_s > 0
    gs.radio_jobs.append(("WAKE", None))
    gs.run_radio_jobs()
    assert list(gs.radio_jobs) == [("WAKE", None)]                     # deferred
    w.clk.t += busy_s + 0.01
    gs.run_radio_jobs()
    assert not gs.radio_jobs and any("wake-up" in t for t in w.events("RADIO"))
    gs.station_id = StationIdent("N0CALL", "DSS-Q1")                   # a run whose first uplink is the WAKE
    gs.station_id.on_transmit(w.clk.t)
    gs.rx.los = True
    gs._uplink_busy_until = gs.mono() + 5.0
    gs.housekeeping(w.clk.t)
    assert gs.take_outbox() == []                                      # not before the window's end
    w.clk.t += 5.0
    gs.housekeeping(w.clk.t)
    assert gs.take_outbox() == [ID]


# ---------------------------------------------------------------- Phase D review: uplink (UPL-1..5)
def _station_clear(tmp_path, model, code):
    """A station without SDLS (the identification timing does not depend on it; no AES per frame)."""
    cfg = _config(tmp_path, model=model, air_rate=code)
    cfg["sdls"]["tc_service"] = "NONE"
    return GroundStation(cfg, archive=False, mode="sim")


def _run_with_frame_loss(gs, clk, period, loss, cmd):
    """Frames every `period` from T0 for 2.2 x 10 min with `cmd` queued in every window. From
    the first frame at or after the time the periodic ID falls due, `loss` frames are lost
    ("1", "2") or every frame until one period past the 10-minute deadline ("fade"); the
    housekeeping runs every second while frames are missing (the Runner runs it every step).
    Returns (ID times, sent by the housekeeping, CLTU times)."""
    gs.fop._init_vs(0)
    ids: list[float] = []
    cltus: list[float] = []
    by_hk = 0
    cycle = dropped = 0

    def collect(when, hk):
        nonlocal by_hk
        for c in gs.take_outbox():
            if c == gs.station_id.payload:
                ids.append(when)
                by_hk += hk
            elif c[:2] == b"\xeb\x90":
                cltus.append(when)

    t = T0
    while t < T0 + 2.2 * ID_INTERVAL_S:
        sid = gs.station_id
        if sid.count != cycle:
            cycle, dropped = sid.count, 0
        early = gs._ident_margins()[0]
        due_at = None if sid.last_t is None else sid.last_t + ID_INTERVAL_S - early
        lose = due_at is not None and t >= due_at and (
            t < due_at + early + period if loss == "fade" else dropped < int(loss))
        if lose:
            dropped += 1
            h = t
            while h < t + period:                                      # no window until the next frame
                clk.t = h
                gs.housekeeping(h)
                collect(h, 1)
                h += 1.0
        else:
            clk.t = t
            gs.rx.los, gs.rx.last_frame_t = False, t
            gs.fop.submit(cmd, t)
            gs._uplink_window(t)
            collect(t, 0)
        t += period
    return ids, by_hk, cltus


@pytest.mark.parametrize("model", [link.E22_T33D, link.E22_T37S, link.SX126X])
def test_periodic_id_keeps_10_minutes_when_frames_are_lost(tmp_path, monkeypatch, model):
    """UPL-1: every rate code and flight mode, a command in every window, and frames lost just
    when the periodic ID falls due. Up to ID_LOST_FRAMES (2) lost frames still leave a window
    within the 10 minutes (the early margin spans three frame periods); a fade past the
    deadline that is shorter than the LOS timeout makes the housekeeping send the ID on time
    without a window. IDs are never more than 10 minutes apart and no CLTU goes out more than
    10 minutes after the last ID."""
    from dssq.cmdparse import parse
    clk = Clock()
    monkeypatch.setattr(time, "time", clk)
    bd_noop = parse("BD NOOP")
    for code in range(8):
        for mode in MODE_FACTOR:
            for loss in ("2", "fade"):                                 # fewer lost frames: an earlier window
                d = tmp_path / f"{code}-{mode}-{loss}"
                d.mkdir()
                gs = _station_clear(d, model, code)
                try:
                    ids, by_hk, cltus = _run_with_frame_loss(gs, clk, _flight_period_s(model, code, mode), loss,
                                                             bd_noop)
                finally:
                    gs.close()
                gaps = [b - a for a, b in itertools.pairwise(ids)]
                assert len(ids) >= 3 and max(gaps) <= ID_INTERVAL_S, (model, code, mode, loss, gaps)
                if loss != "fade":
                    assert by_hk == 0, (model, code, mode, loss)          # a window carried every ID
                late = [u for u in cltus if u - max(i for i in ids if i <= u) > ID_INTERVAL_S]
                assert not late, (model, code, mode, loss, late)


@pytest.mark.parametrize("model", [link.E22_T33D, link.E22_T37S, link.SX126X])
def test_late_or_batched_frames_never_overrun_the_quiet_window(tmp_path, monkeypatch, model):
    """UPL-2: what a window radiates must end FLIGHT_RX_QUIET_S before the spacecraft's next
    frame (ENCOUNTER, the shortest quiet window), also when the frame is handled late (a
    stalled radio thread) or several frames are handled in one batch (SX126x packets queued
    meanwhile, two E22 CADUs in one read): the window keeps only what is left of its quiet
    time, a frame whose quiet time is gone books nothing, and the busy deadline covers all of
    it. Every queue is kept full of the longest frames."""
    import dataclasses
    clk = Clock()
    monkeypatch.setattr(time, "time", clk)
    ad = seqload_commands(parse_sequence("T+00:00:10 NOOP\n"))[0]
    seqload = (ad, dataclasses.replace(ad, kind="BD"))
    for code in range(8):
        d = tmp_path / str(code)
        d.mkdir()
        gs = _station(d, model, code)
        try:
            gs.fop._init_vs(0)
            max_ext = build_tc_frame(bytes(64 - 5 - gs.sdls_tc.overhead - 2), 0, True, False, 0, sdls=None)
            b = link.busy_s(model, code)
            period = max(2.0 * b, MIN_FRAME_PERIOD_S)
            quiet = period - b
            for ages in ([0.0], [0.35], [0.7 * quiet], [quiet + 0.5], [period + 0.4, 0.4], [2.0, 0.48],
                         [0.0, 0.0]):
                now = clk.t = clk.t + 1000.0                               # the previous case is over
                _top_up(gs, now, seqload, max_ext)
                for age in sorted(ages, reverse=True):                     # oldest frame first
                    gs._uplink_window(now - age)
                chunks = [c for c in gs.take_outbox() if c != NOISE_QUERY]
                cost = sum(_air_cost_s(model, code, len(c), gs.settings.cad) for c in chunks)
                start = max(now, now - max(ages) + WINDOW_GUARD_S - FLIGHT_RX_QUIET_S)
                newest = now - min(ages)
                if chunks:
                    assert start + cost <= newest + quiet - FLIGHT_RX_QUIET_S + 1e-9, (model, code, ages, cost)
                    assert gs._uplink_busy_until - gs.mono() >= start + cost - now - 1e-6
                if min(ages) > quiet:
                    assert not chunks                                      # its quiet time is gone
                if ages == [0.0]:
                    assert chunks, (model, code)                           # on time: a full window
        finally:
            gs.close()


def test_runner_close_sends_an_id_left_in_the_outbox_first(tmp_path):
    """UPL-4: the housekeeping (after the outbox stage) queued the LOS identification - e.g.
    for a WAKE in this step's jobs stage - and the loop ended before the next outbox stage:
    close() radiates it, and does so before the other shutdown steps use up the budget."""
    r = Runner(_config(tmp_path), sim=True, web=False, archive=False)
    order: list[str] = []
    sent: list[bytes] = []
    r.radio.write = lambda data: order.append("ident") or sent.append(data)
    gs = r.gs
    tdm_final = gs.tdm_final
    gs.tdm_final = lambda: order.append("tdm") or tdm_final()
    gs.station_id.on_transmit(time.time())                             # the WAKE
    gs.rx.los = True
    gs.housekeeping()
    assert gs.station_id.count == 1 and not gs.station_id.pending      # queued, recorded as sent
    r.close()
    assert sent == [ID] and order[0] == "ident" and gs.station_id.count == 1


def test_a_wall_clock_step_back_does_not_hold_the_los_id_or_the_radio_jobs(world):
    """UPL-5: the window's busy deadline is on a monotonic clock. NTP steps the station clock
    back an hour after a window while the downlink is lost: the WAKE (and every radio job
    behind it) and the LOS identification go out once the window's air time is over, not an
    hour later."""
    w, gs = world, world.gs
    w.run(20)
    w.clk.t += 1.5                                                     # a frame (the flight's are >= 1.5 s apart)
    gs.station_id.on_transmit(w.clk.t)
    gs._uplink_window(w.clk.t)                                         # the first ID: busy for its air time
    assert gs.take_outbox() == [ID]
    gs.rx.los = True                                                   # the downlink is lost
    w.sim._on_tc = lambda *a: None                                     # (the simulator shares the stepped clock)
    w.clk.t -= 3600.0                                                  # NTP steps the clock back
    gs.radio_jobs.extend([("WAKE", None), ("VERIFY", None)])
    gs.run_radio_jobs()                                                # the next Runner step: still on the air
    assert [j for j, _ in gs.radio_jobs] == ["WAKE", "VERIFY"]
    w.clk.t += 2.0                                                     # the window's air time is over
    gs.run_radio_jobs()
    assert not gs.radio_jobs and any("wake-up" in t for t in w.events("RADIO"))
    gs.housekeeping(w.clk.t)                                           # the WAKE is to be identified
    assert gs.take_outbox() == [ID] and not gs.station_id.pending


# ---------------------------------------------------------------- second review (SN-1 proof, UPL5-R1)
def test_sdls_numbers_stay_below_3_per_second_whatever_the_downlink_does(tmp_path, monkeypatch):
    """SN-1: the time floor (ccsds/sdls.py) needs fewer than 4 SDLS numbers per second. Frames
    arriving every 0.2 s - a replayed recording or forged frames, faster than the flight ever
    sends (>= 1.5 s) - open a window at most once per WINDOW_MIN_SPACING_S, each with at most
    2 external + 2 FOP-1 frames; a WAKE goes at most once per WAKE_MIN_INTERVAL_S."""
    from dssq.cmdparse import parse
    from dssq.gds import WAKE_MIN_INTERVAL_S, WINDOW_MIN_SPACING_S
    assert 4 / WINDOW_MIN_SPACING_S + 1 / WAKE_MIN_INTERVAL_S < 3.0
    clk = Clock()
    monkeypatch.setattr(time, "time", clk)
    gs = _station(tmp_path, link.SX126X, 7)
    try:
        gs.fop._init_vs(0)
        ext = build_tc_frame(bytes(8), 0, True, False, 0, sdls=None)
        for _ in range(150):
            gs.fop.submit(parse("BD NOOP"), clk.t)
        gs.external_tc.extend([ext] * 60)
        sn0, t0 = gs.sdls_tc.sn, clk.t
        for k in range(300):                                               # 60 s of frames 0.2 s apart
            clk.t = t0 + 0.2 * k
            gs._uplink_window(clk.t)
            gs.take_outbox()
        used, elapsed = gs.sdls_tc.sn - sn0, clk.t - t0
        assert 0 < used <= 4 * (elapsed // WINDOW_MIN_SPACING_S + 1) and used / elapsed < 3.0, (used, elapsed)
    finally:
        gs.close()


def test_wake_at_most_once_per_interval(world):
    from dssq.gds import WAKE_MIN_INTERVAL_S
    w, gs = world, world.gs
    w.run(20)
    gs.radio_jobs.extend([("WAKE", None), ("WAKE", None)])
    gs.run_radio_jobs()
    assert [j for j, _ in gs.radio_jobs] == ["WAKE"]                   # the second one waits
    w.clk.t += WAKE_MIN_INTERVAL_S + 0.1
    gs.run_radio_jobs()
    assert not gs.radio_jobs and len([t for t in w.events("RADIO") if "wake-up transmitted" in t]) == 2


def test_a_clock_step_between_the_ert_stamp_and_the_window_holds_nothing(world):
    """UPL5-R1: the frame's ERT is stamped (wall clock), then NTP steps the clock back an hour
    before the radio thread opens its window. The window works in one time base (the steady
    clock, the frame's age clamped at 0): its busy deadline is one window long, and the
    windows after it carry their commands."""
    from dssq.cmdparse import parse
    w, gs = world, world.gs
    w.run(20)
    w.sim._on_tc = lambda *a: None                                     # (the simulator shares the stepped clock)
    w.clk.t += 1.5
    ert = w.clk.t                                                      # stamped on reception
    w.clk.t -= 3600.0                                                  # the step, before the window
    gs._uplink_window(ert)
    gs.take_outbox()
    assert gs._uplink_busy_until - gs.mono() < 2.0
    cltus = 0
    for _ in range(5):
        w.clk.t += 10.0
        gs.fop.submit(parse("BD NOOP"), w.clk.t)
        gs._uplink_window(w.clk.t)
        cltus += len([c for c in gs.take_outbox() if c[:2] == b"\xeb\x90"])
    assert cltus == 5
