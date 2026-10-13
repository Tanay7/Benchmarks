"""End-to-end: GDS <-> v3 software spacecraft over a simulated RF channel (virtual clock).

Phase B PB-8.5 WS4: (a) the v3 world (HK beacon, SDLS AE both ways, tm_framing auto with a
USLP spacecraft, CFDP SSR export, SPECBURST, stored sequences, MAGCAL against the simulator's
known truth, TDM at LOS); (b) the packet-mode (SX126x) world: AFC, Doppler, RS rescue;
(c) the flight's integration_tm_ae.bin vector fed as an E22 [CADU][RSSI] byte stream;
(d) robustness of the Runner and the state (stage error boundary, TDM guard, bounded
close, missing Router Bridge, concurrent snapshots, pass windows, derived timers,
hibernation).
"""
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dssq import link, products
from dssq.ccsds import CADU_LEN
from dssq.ccsds.tm import FHP_ONLY_IDLE_DATA, build_frame, encode_cadu
from dssq.config import load_config
from dssq.gds import STAGES, GroundStation, Runner, _SimRcu, json_safe
from dssq.radioctl import RadioManager
from dssq.rxstats import PASS_WINDOW_MAX, at_resolution
from dssq.sim import MAG_OFFSET_UT, MAG_SOFT_IRON, SimSpacecraft

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "tools" / "host_test"
KEY = bytes(range(32))                       # the host_test vector key 00 01 .. 1F
T0 = 1_800_000_000.0                         # 2027-01-15


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += max(dt, 1e-3)


def _config(tmp_path, *, key=True, sn_known=True, **radio):
    """Station configuration on tmp_path. With `key`, the station's TC sequence-number file
    exists (sn_known, value 0: a station whose counter is known); sn_known=False models an
    existing key without its SN file (gds.py nonce-safety hold)."""
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    key_file = tmp_path / "sdls_key.hex"
    if key:
        key_file.write_text(KEY.hex() + "\n")
        if sn_known:
            (tmp_path / "archive").mkdir(exist_ok=True)
            (tmp_path / "archive" / "sdls_sn.txt").write_text("0")
    cfg["sdls"]["key_file"] = str(key_file)
    cfg["radio"].update(radio)
    cfg["science"]["field_ut"] = 50.0
    return cfg


class World:
    """GroundStation + SimSpacecraft on one virtual clock (time.time / time.sleep patched)."""

    def __init__(self, monkeypatch, tmp_path, *, key=True, sn_known=True, framing="TM", sim_key="same", radio=None,
                 sdls=None, archive=True, **simkw):
        self.clk = clk = Clock()
        monkeypatch.setattr(time, "time", clk)
        monkeypatch.setattr(time, "sleep", clk.sleep)
        self.cfg = cfg = _config(tmp_path, key=key, sn_known=sn_known, **(radio or {"air_rate": 7}))
        cfg["sdls"].update(sdls or {})
        self.gs = gs = GroundStation(cfg, archive=archive)
        kw = {"loss": 0.0, "max_errors": 4, "rssi_mean": -95.0, "fading_db": 1.0, "seed": 7}
        kw.update(simkw)
        self.sim = sim = SimSpacecraft(cfg, clock=clk, framing=framing,
                                       sdls_key=gs.sdls_tc.key if sim_key == "same" else sim_key, **kw)
        self.packet = sim.packet_mode
        self.writes = []
        mgr = (RadioManager(sim, None, clock=clk, sleep=clk.sleep) if self.packet else
               RadioManager(sim, _SimRcu(sim), clock=clk, sleep=clk.sleep, model=gs.settings.model.name))
        gs.attach_radio(sim, mgr, None)

    def run(self, seconds, step=0.25):
        gs, sim, clk = self.gs, self.sim, self.clk
        end = clk.t + seconds
        while clk.t < end:
            gs.ingest(sim.read(), clk.t)
            if self.packet:
                for p in sim.packets():
                    gs.ingest_packet(p)
            for chunk in gs.take_outbox():
                self.writes.append(chunk)
                sim.write(chunk)
            for corr in gs.take_afc_out():
                gs.afc_confirm(corr, sim.rf_foff(corr))
            if gs.radio_jobs:
                gs.run_radio_jobs()
            gs.tdm_service()
            gs.housekeeping(clk.t)
            clk.t += step

    def command(self, text, wait=30.0):
        r = self.gs.submit_command(text)
        if r["status"] == "CONFIRM":
            r = self.gs.confirm(r["id"])
        assert r["status"] == "QUEUED", r
        self.run(wait)
        rec = {x.id: x for x in self.gs.fop.all}[r["id"]]
        return rec.state

    def events(self, source=None):
        return [e["text"] for e in self.gs.events if source is None or e["source"] == source]


@pytest.fixture
def world(monkeypatch, tmp_path):
    return World(monkeypatch, tmp_path)


# =============================================================================
# (a) the v3 world
# =============================================================================
def test_v3_telemetry_beacon_and_sdls_ae_both_ways(world):
    w, gs = world, world.gs
    w.run(60)
    s = gs.snapshot()
    assert s["schema"] == 3 and s["version"] == "3.0.0" and s["sync"]["state"] == "LOCK"
    assert s["rx"]["frames"]["ok"] > 15 and s["rx"]["decoder"]["uncorrectable"] == 0
    assert s["rx"]["decoder"]["symbols_corrected"] > 0                            # RS exercised
    sd = s["link"]["sdls"]
    assert (sd["tc_service"], sd["tm_service"], sd["key_loaded"], sd["tc_sn_state"]) == ("AE", "AE", True, "OK")
    assert sd["tm_frames_ok"] >= 3 and sd["tm_auth_fail"] == 0 and sd["tm_replay"] == 0
    assert sd["spacecraft_tm_state"] == "PROTECTED" and not sd["mismatch_suspected"]
    assert s["link"]["ident"]["text"].startswith("DE N0CALL VGQ-1") and s["link"]["ident"]["count"] >= 1
    assert s["beacon"]["level"] == 3 and s["beacon"]["name"] == "URGENT"          # boots SAFE (safe_by_fdir)
    assert "beacon URGENT" in s["alarm_reasons"] and s["alarm"] == "RED"
    assert s["decoded"]["mode"] == "SAFE" and s["decoded"]["sdls_tm_service"] == "AE"
    assert w.command("NOOP") == "EXECUTED" and w.command("MODE CRUISE") == "EXECUTED"
    w.run(40)
    s = gs.snapshot()
    assert s["tlm"]["HK"]["values"]["fsw_mode"] == 2 and s["beacon"]["name"] == "NOMINAL"
    assert [h[1] for h in s["beacon"]["history"]][-2:] == [3, 0]
    assert w.sim.tc_ok >= 2 and w.sim.sdls.failures == 0                          # AE accepted on board
    assert s["link"]["sdls"]["tc_next_sn"] >= 3 and (Path(w.cfg["gds"]["archive_dir"]) / "sdls_sn.txt").exists()
    assert gs.panel_status()["beacon"] == 0
    from dssq.display.frontpanel import status_lines
    line, q = status_lines(gs.panel_status())
    assert line.startswith("S|1|") and line.endswith("|0") and q == "Q|-1"     # AOS, beacon NOMINAL, none pending
    json.dumps(json_safe(s), allow_nan=False)                                    # web-serialisable


def test_sdls_key_mismatch_is_refused_both_ways(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path, sim_key=bytes(32))
    w.run(40)
    s = w.gs.snapshot()
    assert s["link"]["sdls"]["tm_auth_fail"] > 0 and s["link"]["sdls"]["tm_frames_ok"] == 0
    assert "HK" not in s["tlm"]                                                   # nothing decoded from failed frames
    w.gs.submit_command("NOOP")
    w.run(40)
    assert w.sim.sdls.failures >= 1 and w.sim.tc_ok == 0


RESYNC_GAP = 1_000_000          # docs/17 section 7.6: at least 1 000 000 above the reported sdls_last_sn ...
TIME_T0 = 1_767_225_600         # ... and at least 4 numbers per second since 2026-01-01T00:00:00Z (the time floor)
TIME_RATE = 4
RESTORE_WAIT_S = 120            # flight config.h kSdlsSnWaitS: an HK this soon after a boot ends no hold


def _time_floor(t):
    return int((t - TIME_T0) * TIME_RATE)


def _sn_spy(gs):
    used = []
    take = gs.sdls_tc.next_sn
    gs.sdls_tc.next_sn = lambda: used.append(take()) or used[-1]
    return used


def _sdls_events(gs):
    return [(e["level"], e["text"]) for e in gs.events if e["source"] == "SDLS"]


def _run_until_released(w, limit_s=300.0):
    """Runs the World until the hold ends; returns the clock interval (before, after) of the
    step that released it."""
    gs = w.gs
    t_end = w.clk.t + limit_s
    while gs.fop.uplink_blocked and w.clk.t < t_end:
        before = w.clk.t
        w.run(0.25)
    assert not gs.fop.uplink_blocked, "the hold never ended"
    return before, w.clk.t


def test_existing_key_without_sn_file_holds_and_resyncs_above_the_lost_tail(monkeypatch, tmp_path):
    """SEC-3: an existing key whose TC sequence-number file is missing (e.g. host mode run with
    another archive folder than the App's). The other archive radiated SN 1..45 but the
    spacecraft accepted only 1..41 (44 and 45 lost on the uplink, 42 and 43 COP-1
    retransmissions): sdls_last_sn 41. The station holds the uplink (yellow alarm) until the
    first protected HK - one reporting a restored (nonzero) number counts even right after a
    flight boot - and continues on the time floor, far above sdls_last_sn + 1 000 000 and every
    radiated number."""
    w = World(monkeypatch, tmp_path, sn_known=False)
    gs, sn_file = w.gs, tmp_path / "archive" / "sdls_sn.txt"
    w.sim.sdls.last_sn = 41
    radiated_before = set(range(1, 46))
    assert gs.fop.uplink_blocked and gs.sdls_tc.sn_state.startswith("BLOCKED: no TC sequence-number file")
    gs.housekeeping(w.clk.t)                                    # alarm evaluation
    assert any("TC uplink BLOCKED" in r for r in gs.snapshot()["alarm_reasons"])
    assert gs.submit_command("NOOP")["status"] == "REJECTED"   # nothing radiated while the number is unknown
    used = _sn_spy(gs)
    before, after = _run_until_released(w)
    assert before < w.sim.t0 + RESTORE_WAIT_S                    # a restored (nonzero) report: no boot wait
    n = gs.sdls_tc.sn
    assert _time_floor(before) <= n <= _time_floor(after) and n > 41 + RESYNC_GAP
    assert sn_file.read_text() == str(n)
    assert any(lv == "WARN" and "no SDLS TC sequence-number file" in t for lv, t in _sdls_events(gs))
    ev = [t for lv, t in _sdls_events(gs) if lv == "WARN" and "re-synchronised" in t]
    assert len(ev) == 1 and f"to {n} (the time floor" in ev[0]
    assert gs.submit_command("NOOP")["status"] in ("QUEUED", "SENT")
    w.run(40)
    assert w.sim.tc_ok >= 1 and w.sim.sdls.failures == 0 and used and not radiated_before & set(used)


@pytest.mark.parametrize("tc, tm", [("AUTH", "NONE"), ("AE", "NONE"), ("AUTH", "AE")])
def test_every_numbered_tc_service_holds_without_its_sn_file(monkeypatch, tmp_path, tc, tm):
    """SEC-4 / UPL-3: the hold covers TC AUTH as well as AE, with or without TM protection.
    With TM NONE no HK is authenticated, so an unprotected HK ends the hold (the time floor
    makes its value irrelevant); with TM AE only a protected one does."""
    w = World(monkeypatch, tmp_path, sn_known=False, sdls={"tc_service": tc, "tm_service": tm})
    gs = w.gs
    w.sim.sdls.last_sn = 41
    assert gs.fop.uplink_blocked and gs.submit_command("NOOP")["status"] == "REJECTED"
    used = _sn_spy(gs)
    before, after = _run_until_released(w)
    n = gs.sdls_tc.sn
    assert _time_floor(before) <= n <= _time_floor(after)
    source = "an SDLS-protected HK" if tm == "AE" else "an UNPROTECTED HK (TM service NONE)"
    assert any(lv == "WARN" and source in t for lv, t in _sdls_events(gs))
    assert gs.submit_command("NOOP")["status"] in ("QUEUED", "SENT")
    w.run(40)
    assert w.sim.tc_ok >= 1 and w.sim.sdls.failures == 0 and min(used) == n + 1


def _first_start_then_archive_lost(monkeypatch, tmp_path, sdls=None):
    """SN-1: a brand-new key's first start (held, released by the first HK after the restore
    wait), five PINGs radiated, then the archive is lost and a new GroundStation starts on
    the same key. Returns (World, recorded first-pass downlink, numbers used, new station)."""
    w = World(monkeypatch, tmp_path, sn_known=False, sdls=sdls)
    gs = w.gs
    recording: list[bytes] = []
    read = w.sim.read
    w.sim.read = lambda: recording.append(read()) or recording[-1]
    _run_until_released(w)
    w.run(5)
    used = _sn_spy(gs)
    for i in range(5):
        assert w.command(f"PING {i}") == "EXECUTED"
    assert used and w.sim.sdls.last_sn == max(used)
    gs.close()
    shutil.rmtree(tmp_path / "archive")                         # the archive (SN file, TM replay state) is lost
    w.clk.t += 60.0                                             # the station is restarted a minute later
    gs2 = GroundStation(w.cfg, archive=True)
    assert gs2.fop.uplink_blocked
    return w, b"".join(recording), used, gs2


def test_a_replayed_first_pass_hk_after_archive_loss_cannot_reuse(monkeypatch, tmp_path):
    """SN-1 (the reviewer's reproduction): the recorded first-pass downlink - protected HKs
    reporting sdls_last_sn 0 - is replayed into the station that lost its archive (a fresh
    TM replay state accepts it). The release lands on the time floor, above every number
    the key used, not on 0 + 1 000 000 = the key's first numbers."""
    w, first_pass, used, gs2 = _first_start_then_archive_lost(monkeypatch, tmp_path)
    try:
        gs2.ingest(first_pass, w.clk.t)
        assert not gs2.fop.uplink_blocked                       # the replayed HK did release it ...
        nxt = gs2.sdls_tc.next_sn()
        assert nxt not in used and nxt > max(used)              # ... but above every number used
        assert nxt == _time_floor(w.clk.t) + 1
    finally:
        gs2.close()


def test_a_forged_hk_under_tm_none_cannot_reuse(monkeypatch, tmp_path):
    """SN-1: with TM NONE any HK may end the hold; a forged one reporting sdls_last_sn 0
    still lands on the time floor."""
    w, _, used, gs2 = _first_start_then_archive_lost(monkeypatch, tmp_path, sdls={"tm_service": "NONE"})
    try:
        gs2._on_hk({"sdls_last_sn": 0, "uptime_s": 5000}, w.clk.t, False)
        assert not gs2.fop.uplink_blocked
        nxt = gs2.sdls_tc.next_sn()
        assert nxt not in used and nxt > max(used)
    finally:
        gs2.close()


def test_the_boot_hk_before_the_restore_never_ends_the_hold(monkeypatch, tmp_path):
    """SN-1: after a flight boot the HK reports sdls_last_sn 0 until the EGSE restores the
    counter (at most kSdlsSnWaitS = 120 s). No HK made within that time ends a hold; a later
    one does, on the time floor."""
    w, _, used, gs2 = _first_start_then_archive_lost(monkeypatch, tmp_path)
    try:
        for uptime in (0, 30, RESTORE_WAIT_S - 1):
            gs2._on_hk({"sdls_last_sn": 0, "uptime_s": uptime}, w.clk.t, True)
            assert gs2.fop.uplink_blocked
        gs2._on_hk({"sdls_last_sn": max(used), "uptime_s": RESTORE_WAIT_S}, w.clk.t, True)   # restored
        assert not gs2.fop.uplink_blocked and gs2.sdls_tc.sn == _time_floor(w.clk.t) > max(used)    # no gap on top
    finally:
        gs2.close()


def test_a_forged_hk_cannot_place_the_counter_at_a_tm_none_station(monkeypatch, tmp_path):
    """SDLS-FLOOR-1 (the reviewers' probe as a test): TC AE, TM NONE, the station clock right.
    A forged HK reporting a number far above the time floor would have placed the counter there
    (the gap branch), and the same kind of forgery at a later hold would have put it back onto
    the numbers radiated after the first one. An unprotected report above the floor is refused:
    the hold stays until a report at or below the floor, and every release lands on the floor."""
    w = World(monkeypatch, tmp_path, sn_known=False, sdls={"tc_service": "AE", "tm_service": "NONE"})
    gs = w.gs
    f1 = _time_floor(w.clk.t)
    gs._on_hk({"sdls_last_sn": f1 + 10**9, "uptime_s": 5000}, w.clk.t, False)   # forged #1
    assert gs.fop.uplink_blocked
    assert any(lv == "ERROR" and "an UNPROTECTED HK reports" in t for lv, t in _sdls_events(gs))
    _run_until_released(w)                                      # the genuine (unprotected) HK
    assert gs.sdls_tc.sn <= _time_floor(w.clk.t)
    used = _sn_spy(gs)
    for i in range(5):
        assert w.command(f"PING {i}") == "EXECUTED"
    gs.close()
    shutil.rmtree(tmp_path / "archive")
    w.clk.t += 30 * 86400.0                                     # a month later: a second hold
    gs2 = GroundStation(w.cfg, archive=True)
    try:
        assert gs2.fop.uplink_blocked
        for forged in (f1 + 10**9, min(used) - 1 - RESYNC_GAP):  # replayed #1, or placed below the used numbers
            gs2._on_hk({"sdls_last_sn": forged, "uptime_s": 5000}, w.clk.t, False)
        assert not gs2.fop.uplink_blocked                       # the second one is below the new floor
        nxt = gs2.sdls_tc.next_sn()
        assert nxt == _time_floor(w.clk.t) + 1 and nxt > max(used)
    finally:
        gs2.close()


def test_a_restored_report_soon_after_a_boot_ends_the_hold(monkeypatch, tmp_path):
    """SDLS-FLOOR-3: only a boot HK reporting 0 (the flight's RAM counter before the EGSE's
    restore) is ignored, with a rate-limited WARN; a protected HK 10 s after a boot that
    reports a restored (nonzero) number ends the hold on the time floor - a spacecraft that
    resets every minute no longer keeps the station held."""
    w = World(monkeypatch, tmp_path, sn_known=False)
    gs = w.gs
    for _ in range(3):
        gs._on_hk({"sdls_last_sn": 0, "uptime_s": 10}, w.clk.t, True)
    assert gs.fop.uplink_blocked
    warns = [t for lv, t in _sdls_events(gs) if lv == "WARN" and "after a spacecraft boot reports sdls_last_sn 0" in t]
    assert len(warns) == 1 and "an HK made 10 s after a spacecraft boot" in warns[0]
    gs._on_hk({"sdls_last_sn": 41, "uptime_s": 10}, w.clk.t, True)
    assert not gs.fop.uplink_blocked and gs.sdls_tc.sn == _time_floor(w.clk.t)


def test_the_simulator_never_touches_the_real_counter(monkeypatch, tmp_path):
    """SN-3: the software simulator (App Lab DSSQ_SIM=1, --sim --app-data: mode "sim" with the
    archive on) keeps its TC counter and TM replay state in memory: its spacecraft's HK can
    neither end the real station's hold nor write the real files."""
    cfg = _config(tmp_path, sn_known=False)
    archive = tmp_path / "archive"
    sim_gs = GroundStation(cfg, archive=True, mode="sim")
    try:
        assert not sim_gs.fop.uplink_blocked and sim_gs.sdls_tc.sn_file is None
        sim_gs._on_hk({"sdls_last_sn": 0, "uptime_s": 5000}, time.time(), True)
        assert sim_gs.sdls_tc.next_sn() == 1
    finally:
        sim_gs.close()
    assert not (archive / "sdls_sn.txt").exists() and not (archive / "sdls_tm_replay.txt").exists()
    real = GroundStation(cfg, archive=True)
    try:
        assert real.fop.uplink_blocked                          # still waiting for the real spacecraft
    finally:
        real.close()


def test_no_key_and_a_corrupt_sn_file_block_nothing(monkeypatch, tmp_path):
    """Item 29: without a key the uplink is unprotected (NONE) and takes no sequence number, so
    a corrupt sdls_sn.txt blocks nothing: no ERROR at start, no YELLOW "TC uplink BLOCKED",
    and commands go out."""
    w = World(monkeypatch, tmp_path, key=False)
    (tmp_path / "archive").mkdir(exist_ok=True)
    (tmp_path / "archive" / "sdls_sn.txt").write_text("garbage")
    gs = GroundStation(w.cfg, archive=True)
    try:
        assert not gs.fop.uplink_blocked and gs.sdls_tc.sn_state == "OK"
        assert not [t for lv, t in _sdls_events(gs) if lv == "ERROR"]
        gs.housekeeping(w.clk.t)
        assert not any("TC uplink BLOCKED" in r for r in gs.snapshot()["alarm_reasons"])
        assert gs.submit_command("NOOP")["status"] in ("QUEUED", "SENT")
        gs._on_hk({"sdls_last_sn": 0, "uptime_s": 5000}, w.clk.t, False)
        assert (tmp_path / "archive" / "sdls_sn.txt").read_text() == "garbage"   # not touched
    finally:
        gs.close()


def test_no_archive_keeps_and_uses_the_sn_file(monkeypatch, tmp_path):
    """SEC-4: --no-archive (archive=False) on a real station keeps the TC counter in
    <archive_dir>/sdls_sn.txt (security state, not archive data): it continues from the
    file and writes it, and a missing file holds the uplink as with an archive. Only the
    software simulator without an archive keeps the counter in memory."""
    cfg = _config(tmp_path)
    sn_file = tmp_path / "archive" / "sdls_sn.txt"
    sn_file.write_text("500")
    for run in (1, 2):
        gs = GroundStation(cfg, archive=False)
        try:
            assert not gs.fop.uplink_blocked and gs.sdls_tc.next_sn() == 500 + run
            assert sn_file.read_text() == str(500 + run)
        finally:
            gs.close()
    sn_file.unlink()
    gs = GroundStation(cfg, archive=False)
    try:
        assert gs.fop.uplink_blocked and not sn_file.exists()
        assert any("no SDLS TC sequence-number file" in t for _lv, t in _sdls_events(gs))
    finally:
        gs.close()
    sim = GroundStation(cfg, archive=False, mode="sim")
    try:
        assert not sim.fop.uplink_blocked and sim.sdls_tc.sn_file is None and sim.sdls_tc.next_sn() == 1
        assert not sn_file.exists()
    finally:
        sim.close()


def test_tm_framing_auto_follows_a_uslp_spacecraft(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path, framing="USLP")
    assert w.cfg["link"]["tm_framing"] == "auto"
    w.run(60)
    s = w.gs.snapshot()
    assert s["link"]["tm_framing_seen"]["USLP"] > 10 and s["link"]["tm_framing_seen"]["TM"] == 0
    assert s["link"]["sdls"]["tm_frames_ok"] >= 3 and s["tlm"]["HK"]["values"]["fsw_mode"] == 1
    assert s["time"]["timecorr_key"] == "VC0 VCF count"
    assert w.command("PING 4242") == "EXECUTED"


def test_cfdp_ssr_export_completes_and_is_archived(world):
    w, gs = world, world.gs
    w.run(60)
    assert w.command("CFDPPUT SSR 0", wait=5) == "EXECUTED"
    for _ in range(40):
        w.run(15)
        tr = gs.snapshot()["cfdp"]["transactions"]
        if tr and tr[-1]["state"] == "COMPLETE":
            break
    t = gs.snapshot()["cfdp"]["transactions"][-1]
    assert t["state"] == "COMPLETE" and t["checksum_ok"] and t["name"].startswith("ssr_p1_")
    path = Path(gs.snapshot()["cfdp"]["files_dir"]) / t["name"]
    assert path.exists() and path.stat().st_size == t["size"] > 0
    w.run(5)
    gs.flush()
    rows = gs.archive.db.execute("SELECT name, vcid FROM packets WHERE source = 'CFDP'").fetchall()
    assert rows and {v for _, v in rows} == {2} and "HK" in {n for n, _ in rows}
    assert any("COMPLETE" in e for e in w.events("CFDP"))


def test_specmode_burst_reaches_the_science_snapshot(world):
    w, gs = world, world.gs
    w.run(30)
    assert w.command("SPECMODE BURST 5", wait=60) == "EXECUTED"
    sci = gs.science_snapshot()
    assert "science" not in gs.snapshot()
    b = sci["spectral_burst"]
    assert b is not None and (b.get("n_samples") or any(p.get("n_samples") for p in b.get("previous", [])))
    assert b["compression"]["packets_raw"] + b["compression"]["packets_rice"] > 0
    assert gs.snapshot()["rx"]["decoder"]["rice_errors"] == 0


def test_stored_sequence_check_uplink_and_start(world):
    w, gs = world, world.gs
    w.run(30)
    text = "T+00:00:10 NOOP   # first\nT+00:00:20 PING 77\n"
    chk = gs.seq_check(text)
    assert chk["status"] == "OK" and [e["mnemonic"] for e in chk["entries"]] == ["NOOP", "PING"]
    bad = gs.seq_check("T+00:00:10 REBOOT 0xB007\n")
    assert bad["status"] == "ERROR" and bad["error"]["line_no"] == 1
    r = gs.seq_uplink(text)
    assert r["status"] == "CONFIRM" and not gs.fop.all                          # one confirmation for the batch
    q = gs.confirm(r["id"])
    assert q["status"] == "QUEUED" and len(q["ids"]) == 2
    w.run(30)
    assert [x.state for x in gs.fop.all] == ["EXECUTED", "EXECUTED"]
    assert w.command("SEQCTRL START 0", wait=60) == "EXECUTED"
    seq = gs.snapshot()["sequence"]
    assert seq["onboard"]["state_name"] == "COMPLETE" and seq["uplink"]["queued_ids"] == q["ids"]
    stored = [t for t in w.events("CMD") if t.startswith("stored sequence")]
    assert stored == ["stored sequence: opcode 0x01 EXECUTED", "stored sequence: opcode 0x09 EXECUTED"]


def test_magcal_collect_fit_and_uplink_recover_the_truth(world):
    w, gs = world, world.gs
    w.run(20)
    assert w.command("MODE TEST") == "EXECUTED"                                  # MAG every 4 s, averaged
    assert gs.magcal_action("start", 0)["status"] == "OK"
    w.run(30)
    cov = gs.snapshot()["magcal"]["units"]["0"]
    assert cov["coverage"]["n"] == 0 and cov["averaged_skipped"] > 0             # 4-sample means are not fitted
    assert any("PKTRATE MAG 1" in t for t in w.events("MAGCAL"))
    assert w.command("PKTRATE MAG 1") == "EXECUTED"                              # 1 s MAG: nsamples = 1
    w.run(420)
    r = gs.magcal_action("fit", 0)
    assert r["status"] == "OK", r
    fit = r["fit"]
    assert all(abs(a - b) < 0.1 for a, b in zip(fit["offset_ut"], MAG_OFFSET_UT[0], strict=True))
    m, s = fit["matrix"], MAG_SOFT_IRON[0]                                       # M = S^-1 (symmetric fit)
    prod = [[sum(m[i][k] * s[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    assert all(abs(prod[i][j] - (i == j)) < 0.01 for i in range(3) for j in range(3)), prod
    up = gs.magcal_action("uplink", 0)
    assert up["status"] == "CONFIRM"
    q = gs.confirm(up["id"])
    w.run(30)
    assert {x.id: x for x in gs.fop.all}[q["id"]].state == "EXECUTED"
    assert any("MAGCAL unit 0 loaded" in t for t in w.events("S/C EVR"))
    assert gs.snapshot()["magcal"]["units"]["0"]["fit"]["ok"]


def test_tdm_written_at_los(world):
    w, gs = world, world.gs
    w.run(60)
    r = gs.submit_command("TXINHIBIT 600")                                       # hazardous; no CMDVER can follow
    assert gs.confirm(r["id"])["status"] == "QUEUED"
    w.run(gs.los_timeout_s() + 10)
    s = gs.snapshot()
    assert s["rx"]["state"]["los"] and s["tdm"]["last_file"]
    path = Path(w.cfg["gds"]["archive_dir"]) / "tdm" / s["tdm"]["last_file"]
    tdm = products.parse_tdm(path.read_text(), strict=True)
    assert tdm.segments and len(tdm.segments[0].values("CARRIER_POWER")) == s["rx"]["frames"]["ok"] > 10
    assert any(e.startswith("pass TDM written") for e in w.events("TDM"))


def test_hazardous_and_bad_commands(world):
    w, gs = world, world.gs
    r = gs.submit_command("SSRCLEAR")
    assert r["status"] == "CONFIRM" and not gs.fop.all
    assert gs.panel_status()["pending_ids"] == [r["id"]]
    assert gs.confirm(r["id"])["status"] == "QUEUED"
    for bad in ("MODE WARP", "TXPWR 9", "CHANNEL 84 600"):
        assert gs.submit_command(bad)["status"] == "REJECTED"
    w.run(1)
    assert gs.submit_command("LINKCHAN 1")["status"] == "REJECTED"               # not in allowed_channels


def test_coordinated_air_rate_change(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path, radio={"air_rate": 2})
    w.run(30)
    r = w.gs.submit_command("LINKRATE 4")
    assert r["status"] == "CONFIRM"
    w.gs.confirm(r["id"])
    w.run(150)
    assert w.sim.sc_rate == 4 and w.gs.cfg["radio"]["air_rate"] == 4 and w.gs.link_change is None
    assert w.sim.revert is None                                                 # the confirming NOOP arrived
    frames = w.gs.rx.frames_ok
    w.run(30)
    assert w.gs.rx.frames_ok > frames


def test_ground_rf_survey_and_spacecraft_rfscan(world):
    w, gs = world, world.gs
    w.run(10)
    gs.submit_command("GSCAN 20 30")
    w.run(5)
    scan = gs.radio_mgr.last_scan
    assert scan and scan["quietest"] != 24 and len(scan["noise"]) == 11
    assert w.command("RFSCAN 20 30", wait=40) == "EXECUTED"
    assert gs.tlm["RFSCAN"]["values"]["count"] == 11


# =============================================================================
# (b) packet-mode (SX126x) world
# =============================================================================
def test_packet_mode_afc_doppler_rs_rescue_and_no_noise_query(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path, radio={"model": "SX126x", "air_rate": 4}, static_offset_hz=6000.0,
              doppler_rate_hz_s=0.5, crc_fail_prob=0.2, seed=5)
    assert w.packet and w.gs.afc.enabled
    w.run(900, step=0.5)
    s = w.gs.snapshot()
    afc = s["rx"]["rf"]["afc"]
    assert afc["steps"] >= 1 and afc["state"] in ("TRACK", "CHECK") and afc["sign_flips"] == 0
    assert abs(afc["residual_hz"]) <= afc["deadband_hz"] and w.sim.correction_hz == afc["correction_hz"]
    dop = s["rx"]["rf"]["doppler"]
    assert dop["source"] == "afc estimate" and abs(dop["doppler_hz"] - (6000 + 0.5 * 450)) < 600
    dec = s["rx"]["decoder"]
    assert dec["rs_rescued"] > 0 and dec["rs_rescue_failed"] == 0 and dec["uncorrectable"] == 0
    assert s["sync"]["state"] == "PACKET" and s["rx"]["frames"]["ok"] > 100
    assert not any(c[:4] == bytes([0xC0, 0xC1, 0xC2, 0xC3]) for c in w.writes)   # no E22 noise query
    assert s["rx"]["rf"]["freq_offset_state"] == "OK"
    assert w.command("NOOP", wait=60) == "EXECUTED"                             # uplink over the packet radio


# =============================================================================
# (c) the flight's integration vector through GroundStation.ingest
# =============================================================================
@pytest.fixture(scope="module")
def flight_vectors(tmp_path_factory):
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("vectors")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    subprocess.run([str(HOST / "host_test"), str(out)], check=True, stdout=subprocess.DEVNULL)
    if not (out / "integration_tm_ae.bin").exists():                            # never a skip (C59)
        pytest.fail("host_test did not write integration_tm_ae.bin")
    return out


def _expect(path):
    rec = {"pkts": [], "stream": None, "cfdp": None}
    for line in path.read_text().splitlines():
        toks = line.split()
        if len(toks) < 3 or toks[1] != "integration_tm_ae.bin":
            continue
        if toks[0] == "stream":
            rec["stream"] = dict(t.split("=", 1) for t in toks[2:])
        elif toks[0] == "cfdp":
            rec["cfdp"] = dict(t.split("=", 1) for t in toks[2:])
        elif toks[0] == "pkt":
            kv = {} if toks[5] == "-" else dict(i.split("=", 1) for i in toks[5].split(","))
            rec["pkts"].append((int(toks[2]), toks[3], kv))
    return rec


def test_flight_integration_vector_through_ingest(flight_vectors, monkeypatch, tmp_path):
    clk = Clock()
    monkeypatch.setattr(time, "time", clk)
    cfg = _config(tmp_path)
    gs = GroundStation(cfg, archive=True)
    data = (flight_vectors / "integration_tm_ae.bin").read_bytes()
    exp = _expect(flight_vectors / "integration_expect.txt")
    assert len(data) % CADU_LEN == 0 and exp["stream"]["sdls"] == "AE"
    rssi = (256 - 100).to_bytes(1, "big")                                          # E22 RSSI byte: -100 dBm
    for i in range(0, len(data), CADU_LEN):
        gs.ingest(data[i:i + CADU_LEN] + rssi, clk.t)
        clk.t += 2.0
    gs.housekeeping(clk.t)
    s = gs.snapshot()
    for key in ("schema", "version", "utc", "station", "spacecraft", "uptime_s", "mode", "alarm", "alarm_reasons",
                "beacon", "rx", "radio", "link", "sync", "vc", "apids", "tlm", "decoded", "time", "cop", "cfdp",
                "tdm", "sequence", "magcal", "health", "linkmgr", "pending_confirm", "evrs", "events",
                "anomalies", "console", "station_io", "cmd_stages", "cmd_errors"):
        assert key in s, key
    assert "science" not in s
    st = exp["stream"]
    sd = s["link"]["sdls"]
    assert sd["tm_frames_ok"] == int(st["protected"]) and sd["tm_epoch"] == int(st["epoch"]) == 7
    assert sd["tm_auth_fail"] == 0 and sd["tm_unprotected"] == int(st["null_sa"])
    assert s["link"]["ident"]["text"] == bytes.fromhex(st["ident"]).decode("ascii").rstrip()
    assert s["link"]["ident"]["count"] == int(st["oid"])
    assert s["rx"]["frames"]["ok"] == int(st["cadus"]) and s["rx"]["decoder"]["uncorrectable"] == 0
    hk = s["tlm"]["HK"]["values"]
    hk_exp = next(kv for vc, name, kv in exp["pkts"] if vc == 0 and name == "HK")
    for k, v in hk_exp.items():
        if v != "None" and k in hk and not isinstance(hk[k], (dict, list, str)):
            assert hk[k] == pytest.approx(float(v), rel=1e-6, abs=1e-6), k
    assert s["beacon"]["level"] == 2 and s["beacon"]["name"] == "IMPORTANT"
    assert s["decoded"]["sdls_tm_state"] and s["decoded"]["mode"] == "ENCOUNTER"
    for name in ("RF", "TIMECORR", "RFSCAN", "MAG", "ATT", "SPEC", "ENV", "EVR", "CMDVER", "SPECBURST"):
        assert name in s["apids"], name
    assert s["tlm"]["MAG"]["values"]["flags"] == 48 and s["tlm"]["ATT"]["values"]["flags"] == 127
    tr = s["cfdp"]["transactions"]
    assert tr and tr[-1]["state"] == "COMPLETE" and tr[-1]["checksum_ok"]
    assert tr[-1]["name"] == exp["cfdp"]["name"] and tr[-1]["size"] == int(exp["cfdp"]["size"])
    content = (Path(s["cfdp"]["files_dir"]) / tr[-1]["name"]).read_bytes()
    assert content.hex().startswith(exp["cfdp"]["content"][:64])
    assert s["rx"]["decoder"]["rice_errors"] == 0 and s["rx"]["decoder"]["decom_errors"] == 0
    assert any(e["text"].startswith("stored sequence") for e in s["events"])     # CMDVER cmd_source STORED (I1)
    sci = gs.science_snapshot()
    assert sci["spectral_burst"]["compression"]["packets_rice"] >= 1
    assert sci["spectral_burst"]["compression"]["packets_raw"] >= 1
    json.dumps(json_safe(s), allow_nan=False)
    gs.close()


# =============================================================================
# (d) robustness
# =============================================================================
@pytest.fixture
def runner(tmp_path):
    cfg = _config(tmp_path)
    r = Runner(cfg, sim=True, web=False, archive=False)
    yield r
    r.close()


def test_runner_step_survives_a_failure_in_every_stage(runner, monkeypatch):
    originals = {name: getattr(runner, f"_stage_{name}") for name in STAGES}
    for failing in STAGES:
        ran = []
        for name in STAGES:
            def stage(name=name, orig=originals[name], failing=failing, ran=ran):
                ran.append(name)
                if name == failing:
                    raise RuntimeError(f"injected {name}")
                return orig()

            monkeypatch.setattr(runner, f"_stage_{name}", stage)
        runner.step()
        assert ran == list(STAGES)                                                # the next stages still ran
        h = runner.gs.snapshot()["health"]
        assert h["stage_errors"][failing] >= 1 and h["last_error"]["stage"] == failing
    assert set(runner.gs.snapshot()["health"]["stage_errors"]) == set(STAGES)


def test_tdm_record_with_a_1970_clock_is_counted_not_fatal(tmp_path):
    gs = GroundStation(_config(tmp_path, key=False), archive=False)
    cadu = encode_cadu(build_frame(0, 0, 0, FHP_ONLY_IDLE_DATA, bytes(188), 0))
    gs.ingest(cadu + bytes([156]), 86_400.0)                                     # station clock = 1970-01-02
    s = gs.snapshot()
    assert s["health"]["tdm_rejected"] == 1 and s["rx"]["frames"]["ok"] == 1
    assert any(e["source"] == "TDM" and "NTP" in e["text"] for e in s["events"])


def test_received_power_text_at_the_radio_resolution():
    """D-70: the console shows RSSI, noise and SNR est. at the readings' resolution, no finer
    (E22 1 dB; SX126x RSSI 0.5 dB, noise 0.1 dB), rounded half up like the dashboard."""
    assert [at_resolution(v, 1.0) for v in (-39.2, -38.5, 32.5, -0.3)] == ["-39", "-38", "33", "0"]
    assert at_resolution(-113.26, 0.5) == "-113.5" and at_resolution(-72.96, 0.1) == "-73.0"
    assert at_resolution(None, 1.0) is None and at_resolution(float("nan"), 1.0) is None


def test_tdm_rejection_names_the_field_not_the_clock(tmp_path):
    """Record 2026-10-13-A: an E22 RSSI byte of 0x00 (-256 dBm) with a good clock was reported as
    a clock fault."""
    gs = GroundStation(_config(tmp_path, key=False), archive=False)
    cadu = encode_cadu(build_frame(0, 0, 0, FHP_ONLY_IDLE_DATA, bytes(188), 0))
    gs.ingest(cadu + bytes([0]), 1_791_850_000.0)                                # 2026-10-13
    s = gs.snapshot()
    assert s["health"]["tdm_rejected"] == 1 and s["rx"]["frames"]["ok"] == 1
    tdm = [e["text"] for e in s["events"] if e["source"] == "TDM"]
    assert len(tdm) == 1 and "rx_power_dbm" in tdm[0] and "NTP" not in tdm[0]


def test_close_is_bounded_with_a_stuck_front_panel(tmp_path):
    r = Runner(_config(tmp_path), sim=True, web=False, archive=False)
    stuck = threading.Event()

    class StuckPanel:
        def stop(self, timeout_s=1.0):
            stuck.wait(10.0)                                                     # never returns in time
            return False

    r.fp = StuckPanel()
    t0 = time.monotonic()
    r.close()
    assert time.monotonic() - t0 < 4.0
    r.close()                                                                    # idempotent
    stuck.set()


def test_runner_without_the_arduino_package_warns_and_runs(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "arduino", None)                            # import arduino -> ImportError
    cfg = _config(tmp_path)
    cfg["gds"].update(frontpanel=True, rcu=True)
    cfg["radio"]["port"] = str(tmp_path / "ttyUSB9")                             # no E22 either
    r = Runner(cfg, web=False, archive=False)
    try:
        ev = [e for e in r.gs.events if e["source"] == "GDS" and e["level"] == "WARN"]
        assert any("router bridge unavailable: front panel / RCU / SX126x radio disabled" in e["text"] for e in ev)
        assert r.fp is None and r.radio is None and r.gs.radio_unavailable
        for _ in range(3):
            r.step()                                                             # keeps running without hardware
        assert r.gs.snapshot()["radio"] is not None
    finally:
        r.close()


def test_snapshot_is_safe_while_another_thread_ingests(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path)
    chunks = []
    end = w.clk.t + 120
    while w.clk.t < end:
        d = w.sim.read()
        if d:
            chunks.append(d)
        w.clk.t += 0.25
    assert chunks
    stop = threading.Event()
    errors = []

    def feeder():                                 # the radio thread: ingest, then wait for the radio
        try:
            while not stop.is_set():
                for c in chunks:
                    w.gs.ingest(c, w.clk.t)
                    w.gs.housekeeping(w.clk.t)
                    stop.wait(0.0005)
        except Exception as e:                    # the test reports it
            errors.append(e)

    th = threading.Thread(target=feeder, daemon=True)
    th.start()
    try:
        for _ in range(1000):
            json.dumps(json_safe(w.gs.snapshot()), allow_nan=False)
    finally:
        stop.set()
        th.join(10)
    assert not errors and not th.is_alive()


def test_pass_window_is_cleared_at_aos_and_capped(world):
    w, rx = world, world.gs.rx
    w.run(30)
    assert rx.w["pass"]["rssi"].stats()["n"] > 0
    w.sim.inhibit_until = w.clk.t + 10_000
    w.run(w.gs.los_timeout_s() + 5)
    assert rx.los
    n_before = rx.w["pass"]["rssi"].stats()["n"]
    w.sim.inhibit_until = 0.0
    w.run(10)
    assert not rx.los and rx.passes == 2 and rx.w["pass"]["rssi"].stats()["n"] < n_before
    win = rx.w["pass"]["rssi"]
    win.clear()
    for i in range(PASS_WINDOW_MAX + 50):
        win.add(float(i), -100.0)
    assert len(win.d) == PASS_WINDOW_MAX and win.stats()["truncated"] and win.discarded == 50


def test_derived_los_and_fop_timeouts_for_sx126x_profile_0(tmp_path):
    gs = GroundStation(_config(tmp_path, key=False, model="SX126x", air_rate=0), archive=False)
    p = max(4.0 * link.busy_s(link.SX126X, 0), 1.5)
    assert p == pytest.approx(138.544)
    gs.housekeeping()
    assert gs.los_timeout_s() == pytest.approx(3 * p) and round(gs.los_timeout_s()) == 416
    assert gs.fop.t1_s == pytest.approx(2 * p) and round(gs.fop.t1_s) == 277
    assert gs.fop.verify_timeout_s == pytest.approx(4 * p) and round(gs.fop.verify_timeout_s) == 554
    e22 = GroundStation(_config(tmp_path, key=False, air_rate=7), archive=False)
    e22.housekeeping()
    assert e22.los_timeout_s() == float(e22.cfg["gds"]["los_timeout_s"]) and e22.fop.t1_s == 45.0


def test_los_is_suppressed_while_hibernating(world):
    w, gs = world, world.gs
    w.run(30)
    r = gs.submit_command("HIBERNATE 600")
    assert r["status"] == "CONFIRM" and gs.confirm(r["id"])["status"] == "QUEUED"
    w.run(10)
    assert {x.id: x for x in gs.fop.all}[r["id"]].state in ("RADIATED", "ACCEPTED")
    assert gs.snapshot()["beacon"]["hibernate_expected"]                       # confirmed only by the first beacon
    w.run(gs.los_timeout_s() + 30)
    s = gs.snapshot()
    assert s["rx"]["state"]["los"] and s["rx"]["state"]["hibernating"]
    assert not any(x.startswith("LOS") for x in s["alarm_reasons"]) and "LOS (hibernating)" in w.events("RX")
    w.run(1250)                                         # two beacons: the second carries the new HK
    s = gs.snapshot()
    assert s["beacon"]["hibernating"] and not s["beacon"]["hibernate_expected"]
    assert {x.id: x for x in gs.fop.all}[r["id"]].state == "EXECUTED"
    w.run(gs.los_timeout_s() + 30)
    s = gs.snapshot()
    assert s["rx"]["state"]["los"] and not any(x.startswith("LOS") for x in s["alarm_reasons"])
    w.sim.inhibit_until = w.clk.t + 10_000                                      # beacons stop
    w.run(7300, step=5.0)
    assert any(x.startswith("LOS: no beacon for") for x in gs.snapshot()["alarm_reasons"])
    assert gs.snapshot()["alarm"] == "RED"


# =============================================================================
# (e) Phase C regressions (receiver statistics, link manager, archive, replay, AFC, time,
#     command source, SDLS continuity, App Lab registration)
# =============================================================================
def _clear_cadu(mcfc, vcid=0, fhp=FHP_ONLY_IDLE_DATA, data=bytes(188)):
    return encode_cadu(build_frame(vcid, mcfc, mcfc, fhp, data, 0))


def _ingest_pass(gs, t0, mcfc0, n, dt=5.0, rssi=-90):
    for i in range(n):
        gs.ingest(_clear_cadu((mcfc0 + i) & 0xFF) + bytes([256 + rssi]), t0 + i * dt)
    return t0 + (n - 1) * dt


def test_pass_windows_and_link_manager_fer_ignore_the_los_gap(tmp_path):
    """C41: the AOS frame's samples belong to the new pass and the LOS gap is no
    inter-arrival time. C32: the inter-pass MCFC jump is not a frame error for the link
    manager (it stepped AUTO down at 32 dB margin and could never step up again)."""
    gs = GroundStation(_config(tmp_path, key=False, air_rate=4), archive=False)
    gs.link_mgr.mode = "AUTO"
    t = _ingest_pass(gs, T0, 0, 20)
    gs.housekeeping(t + 200.0)                                                   # LOS
    assert gs.rx.los
    t = _ingest_pass(gs, T0 + 3 * 3600.0, 20 + 112, 20)                          # 2160 frames sent out of view
    rx = gs.rx
    assert rx.frames_lost == 112 and rx.passes == 2                              # lifetime count unchanged
    assert rx.w["pass"]["rssi"].stats()["n"] == 20 and rx.w["pass"]["asm"].stats()["n"] == 20
    iat = rx.w["pass"]["iat"].stats()
    assert iat["n"] == 19 and iat["max"] == pytest.approx(5.0)
    assert rx.fer_window(t) == 0.0
    gs.link_mgr.last_eval = -1e18
    gs.housekeeping(t + 1.0)
    rec = gs.link_mgr.recommendation
    assert rec["fer"] == 0.0 and "margin low" not in rec.get("why", "")
    assert gs.snapshot()["rx"]["frames"]["fer_10m"] == 0.0
    t = _ingest_pass(gs, t + 5.0, (20 + 112 + 20 + 3) & 0xFF, 5)                # 3 frames lost IN the pass
    assert rx.fer_window(t) == pytest.approx(3 / 28)


def test_link_manager_rate_limits_follow_the_radio_model(tmp_path):
    """C34 (PB-3.2): [linkmgr] min_rate absent = E22 2, SX126x 0."""
    from dssq.config import validate
    from dssq.linkmgr import LinkManager
    cfg = _config(tmp_path, key=False, model="SX126x", air_rate=1)
    assert "min_rate" not in cfg["linkmgr"]
    sx = GroundStation(cfg, archive=False)
    assert sx.link_mgr.min_rate == 0
    lm = LinkManager(6.0, 0.0, 0.0, None, 4, model="SX126x")
    lm.mode = "ADVISE"
    rx = {"windows": {"10m": {"rssi": {"n": 20, "p10": -141.0}}}, "frames": {"fer": 0.0}}
    lm.evaluate(1.0, rx, 1, None)
    assert lm.recommendation["action"] == "LINKRATE" and lm.recommendation["value"] == 0
    e22 = GroundStation(_config(tmp_path, key=False, air_rate=4), archive=False)
    assert e22.link_mgr.min_rate == 2
    e22.link_mgr.model = link.get_model(link.SX126X)                             # follows a model change
    assert e22.link_mgr.min_rate == 0
    assert LinkManager(min_rate=0, model="E22-400T33D").min_rate == 2            # E22 codes 0-1 alias 2
    bad = _config(tmp_path, key=False)
    bad["linkmgr"].update(min_rate=5, max_rate=4)
    with pytest.raises(ValueError, match="min_rate"):
        validate(bad)


def test_archive_disk_full_keeps_reception_and_commanding(tmp_path, monkeypatch):
    """C33: a full disk (open -> ENOSPC, sqlite 'database or disk is full') stops the
    recording only: frames are still counted, CLCWs reach FOP-1, the alarm says why."""
    import builtins
    import errno
    import sqlite3
    gs = GroundStation(_config(tmp_path, key=False), archive=True)
    real_open = builtins.open

    def full_open(file, *a, **kw):
        if str(file).endswith(("cadu.bin", "events.log")):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(file, *a, **kw)

    class FullDb:
        def __init__(self, db):
            self.db = db

        def execute(self, *a, **kw):
            raise sqlite3.OperationalError("database or disk is full")

        executemany = execute
        commit = execute

        def close(self):
            self.db.close()

    monkeypatch.setattr(builtins, "open", full_open)
    gs.archive.db = FullDb(gs.archive.db)
    t = _ingest_pass(gs, T0, 0, 3)
    assert gs.rx.frames_ok == 3 and gs.rx.codeblocks == 3
    assert gs.fop.last_clcw is not None                                          # the CLCW reached FOP-1
    gs.housekeeping(t)
    s = gs.snapshot()
    assert s["health"]["archive_write_errors"] > 0 and s["health"]["archive_last_error"]
    assert any(r.startswith("archive write errors") for r in s["alarm_reasons"])
    assert any(e["source"] == "ARCHIVE" and e["level"] == "WARN" for e in s["events"])
    assert gs.submit_command("NOOP")["status"] == "QUEUED"
    gs.close()


def test_replay_accepts_the_asm_bit_errors_the_live_gds_accepts(tmp_path):
    """C35: the archive keeps the ASM as received; replay applies the lock tolerance."""
    from dssq.archive import Archive, read_cadu_file
    from dssq.framesync import LOCK_TOL
    from dssq.replay import Pipeline
    ar = Archive(tmp_path / "ar")
    cadus = [bytearray(_clear_cadu(i)) for i in range(4)]
    cadus[1][0] ^= 0x01                                                          # 1 ASM bit error
    cadus[2][0] ^= 0x0F                                                          # LOCK_TOL bit errors
    cadus[3][:4] = bytes(4)                                                      # not an ASM
    for i, c in enumerate(cadus):
        ar.raw_cadu(T0 + i, -90, bytes(c))
    ar.close()
    pipe = Pipeline()
    for _ert, _rssi, c in read_cadu_file(next((tmp_path / "ar").glob("*/cadu.bin"))):
        pipe.push(c)
    assert pipe.frames["TM OID"] == 3 and pipe.frames["bad: ASM"] == 1
    assert pipe.asm_bit_errors == 1 + LOCK_TOL


def test_afc_static_offset_returns_after_a_narrow_profile(tmp_path):
    """C36: a narrow profile clips the static offset; the calibration comes back on return."""
    p1, p0 = link.SX126X_PROFILES[1], link.SX126X_PROFILES[0]
    afc = link.AfcLoop(p1.sf, p1.bw_hz, 433.125e6, static_offset_hz=8000.0)
    assert afc.static_hz == 8000 and afc.bound_hz > 8000
    afc.reconfigure(1.0, p0.sf, p0.bw_hz, 433.125e6)
    assert afc.static_hz < 8000 and any("clipped" in e[2] for e in afc.pop_events())
    afc.reconfigure(2.0, p1.sf, p1.bw_hz, 433.125e6)
    assert afc.static_hz == 8000


def test_sim_time_correlation_is_unbiased(monkeypatch, tmp_path):
    """C37: the simulated E22 downlink includes the ground module's UART transfer, which
    _time_correlate removes: the correlated SCET of an SCLK matches the simulator's clock."""
    w = World(monkeypatch, tmp_path)
    w.run(20)
    gs = w.gs
    for _ in range(2):                                                           # TIMECORR on request
        assert gs.submit_command("TIMECORR")["status"] == "QUEUED"
        w.run(40, step=0.01)                                                     # ERT resolution 10 ms
    assert len(gs.corr.samples) >= 2
    now = w.clk.t
    err = gs.corr.scet(w.sim._sclk(now)) - now
    assert abs(err) < 0.03, err                                                  # was -0.247 s


def test_time_drift_and_residual_need_enough_samples(tmp_path):
    """C44: no fit, no number (PB-5.1)."""
    gs = GroundStation(_config(tmp_path, key=False), archive=False)
    t = gs.snapshot()["time"]
    assert t["corr_samples"] == 0 and t["drift_ppm"] is None and t["residual_rms_ms"] is None
    gs.corr.add(0, 100.0, T0)
    gs.corr.add(0, 200.0, T0 + 100.001)
    t = gs.snapshot()["time"]
    assert t["drift_ppm"] == pytest.approx(10.0, rel=1e-3) and t["residual_rms_ms"] is None
    gs.corr.add(0, 300.0, T0 + 200.0)
    assert gs.snapshot()["time"]["residual_rms_ms"] is not None


def test_alarm_red_count_and_stale_snr(tmp_path):
    """C40: the RED / YELLOW split travels with the reasons. C42: the last packet's RSSI is
    never combined with a noise reading taken after LOS."""
    gs = GroundStation(_config(tmp_path, key=False), archive=False)
    t = _ingest_pass(gs, T0, 0, 3)
    gs.rx.on_noise(-120.0, t)
    assert gs.snapshot()["rx"]["rf"]["snr_est_db"] == pytest.approx(30.0)
    gs.housekeeping(t + 7200.0)                                                  # LOS
    gs.rx.on_noise(-110.0, t + 7200.0)
    gs.tlm["HK"] = {"values": {"fdir_flags": 1}, "limits": {}}                  # a YELLOW reason (FDIR)
    gs.housekeeping(t + 7202.0)
    s = gs.snapshot()
    assert s["rx"]["rf"]["snr_est_db"] is None
    assert s["alarm"] == "RED" and s["alarm_reasons"][0] == "LOS" and s["alarm_red_count"] == 1
    assert s["alarm_reasons"][1].startswith("FDIR")


def test_ground_scan_is_calibrated_and_sx126x_has_no_rcu(monkeypatch, tmp_path):
    """C43: GSCAN readings carry rssi_cal_offset_db like the noise floor. C45: an SX126x
    station has no Radio Control Unit."""
    w = World(monkeypatch, tmp_path, radio={"air_rate": 7, "rssi_cal_offset_db": -3.0})
    w.run(10)
    rm, raw = w.gs.radio_mgr, []
    read = rm.noise_dbm
    monkeypatch.setattr(rm, "noise_dbm", lambda: raw.append(read()) or raw[-1])
    w.gs.submit_command("GSCAN 20 22")
    w.run(5)
    scan = w.gs.snapshot()["radio"]["last_scan"]
    assert len(raw) == 3 and None not in raw
    assert scan["calibrated"] and scan["noise"] == [n - 3.0 for n in raw]
    assert w.gs.snapshot()["radio"]["rcu"] is True
    (tmp_path / "sx").mkdir()
    sx = World(monkeypatch, tmp_path / "sx", radio={"model": "SX126x", "air_rate": 4})
    assert sx.gs.snapshot()["radio"]["rcu"] is False


def test_cmdver_routes_on_the_command_source(world):
    """I1: an AD N(S) of 254 / 255 is an ordinary AD command (the old in-band sentinels);
    the stored sequence is recognised by its source, never by tc_seq."""
    gs = world.gs
    gs._on_cmdver({"tc_seq": 254, "opcode": 1, "stage": 2, "cmd_source": 2, "error_code": 0, "ground_tag": 0}, T0)
    assert gs.console_lines[-1][1] == "stored sequence: opcode 0x01 EXECUTED"
    calls = []
    monkey = gs.fop.on_cmdver
    gs.fop.on_cmdver = lambda v, now: calls.append(v) or monkey(v, now)
    n = len(gs.console_lines)
    gs._on_cmdver({"tc_seq": 254, "opcode": 1, "stage": 2, "cmd_source": 0, "error_code": 0, "ground_tag": 0}, T0)
    assert len(calls) == 1 and not any(x[1].startswith("stored") for x in list(gs.console_lines)[n:])
    gs._on_cmdver({"tc_seq": 0, "opcode": 1, "stage": 2, "cmd_source": 3, "error_code": 0, "ground_tag": 0}, T0)
    assert len(calls) == 1 and any("unknown command source 3" in e["text"] for e in gs.events)


def test_spacecraft_sn_only_from_an_all_protected_hk(world, monkeypatch):
    """I2: the frame's SDLS status is passed to the packet extractor and the HK SN is
    observed with the packet's own flag."""
    import dataclasses
    gs = world.gs
    seen, frame_status = [], []
    orig = gs.sdls_tc.observe_spacecraft_sn
    monkeypatch.setattr(gs.sdls_tc, "observe_spacecraft_sn",
                        lambda sn, prot, *rest: seen.append(prot) or orig(sn, prot, *rest))
    world.run(40)
    assert seen and all(seen)                                                    # AE downlink: protected
    ex = gs.extract[0]
    push = ex.push

    def mixed_push(vcfc, fhp, data, protected=None):
        # As if every packet had started in a null-SA frame: the frame itself verified.
        frame_status.append(protected)
        return [dataclasses.replace(p, protected=False) for p in push(vcfc, fhp, data, protected=protected)]

    monkeypatch.setattr(ex, "push", mixed_push)
    n = len(seen)
    world.run(40)
    assert frame_status and all(st is True for st in frame_status)               # the frame status is passed
    assert len(seen) > n and not any(seen[n:])                                   # the packet's flag decides


def test_app_lab_registration_is_retried(tmp_path, monkeypatch):
    """C52: the router unreachable at start (App Lab AppError for the first seconds) no
    longer disables the front panel and the SX126x radio for the whole run."""
    import dssq.gds as gds_mod
    from dssq.display.frontpanel import BridgeLink

    provided: dict = {}

    class Bridge:
        up = False

        def provide(self, name, fn):
            if not Bridge.up:
                raise ConnectionError("Cannot reach the Arduino Router within 5s.")
            provided[name] = fn

        def notify(self, name, *args):
            if not Bridge.up:
                raise ConnectionError("down")

        def call(self, name, *args, timeout=3.0):
            if not Bridge.up:
                raise ConnectionError("down")
            return 1

    monkeypatch.setattr(gds_mod, "REGISTER_RETRY_S", 0.0)
    cfg = _config(tmp_path, key=False, model="SX126x", air_rate=4)
    cfg["gds"].update(frontpanel=True, rcu=False)
    r = Runner(cfg, bridge=BridgeLink(Bridge(), "test"), mode="applab", web=False, archive=False)
    try:
        assert r.fp is None and r.radio is None and r.gs.radio_unavailable
        r.step()
        assert r.fp is None and r.radio is None                                  # still down: retried
        Bridge.up = True
        r.step()
        assert r.fp is not None and r.radio is not None
        assert r.gs.radio_unavailable is None and r.gs.radio_mgr is not None
        assert {"fp_in", "rf_rx", "rf_evt"} <= set(provided)
    finally:
        r.close()


# =============================================================================
# (e) Phase D gate: simulator = flight for RSTCNT, TX duty and last_airtime
# =============================================================================
def test_rstcnt_clears_the_event_latched_fdir_flags(world):
    """CMD_LOSS, LOOP_OVERRUN, RATE_REVERT and SDLS_AUTH have no monitor that clears them:
    RSTCNT acknowledges them (flight kFdirClearedByRstcnt), so the YELLOW "FDIR" alarm can
    clear; a state-driven flag (I2C_BUS) stays."""
    from dssq.dictionary import FDIR_BITS, FDIR_CLEARED_BY_RSTCNT
    src = (ROOT / "vgq1-spacecraft" / "sketch" / "src" / "fsw" / "tlm_packets.h").read_text()
    m = re.search(r"kFdirClearedByRstcnt =\s*([^;]+);", src)
    assert m, "kFdirClearedByRstcnt not found in tlm_packets.h"
    names = {n.strip().removeprefix("FDIR_") for n in m.group(1).split("|")}
    assert sum(1 << FDIR_BITS.index(n) for n in names) == FDIR_CLEARED_BY_RSTCNT
    assert names == {"CMD_LOSS", "LOOP_OVERRUN", "RATE_REVERT", "SDLS_AUTH"}
    w, gs = world, world.gs
    w.run(20)
    w.sim.fdir |= FDIR_CLEARED_BY_RSTCNT | (1 << FDIR_BITS.index("I2C_BUS"))
    w.run(40)
    fdir = [r for r in gs.snapshot()["alarm_reasons"] if r.startswith("FDIR ")]
    assert fdir and all(n in fdir[0] for n in ("CMD_LOSS", "LOOP_OVERRUN", "RATE_REVERT", "SDLS_AUTH", "I2C_BUS"))
    assert w.command("RSTCNT") == "EXECUTED"
    w.run(40)
    assert gs.tlm["HK"]["values"]["fdir_flags"] == 1 << FDIR_BITS.index("I2C_BUS")
    assert [r for r in gs.snapshot()["alarm_reasons"] if r.startswith("FDIR ")] == ["FDIR I2C_BUS"]


def test_sx126x_sf12_duty_reads_50_percent_without_a_false_yellow(monkeypatch, tmp_path):
    """SX126x profile 1 (17.3 s per CADU) in ENCOUNTER: frame period 2 x busy, true duty 50 %.
    The flight's TxDutyMeter (mirrored by the simulator) reads 50.0 %, so the ground's YELLOW
    limit (55 %) never fires; the replaced 60 s window read 28.9 % / 57.7 %."""
    w = World(monkeypatch, tmp_path, radio={"model": "SX126x", "air_rate": 1})
    w.sim.mode = 3                                                     # ENCOUNTER
    seen, reasons = [], set()
    for _ in range(50):                                                # bounded: 50 x 15 s
        w.run(15, step=0.5)
        v = w.gs.tlm.get("RF", {}).get("values", {})
        if v.get("tx_duty") is not None:
            seen.append(v["tx_duty"])
        reasons.update(r for r in w.gs.snapshot()["alarm_reasons"] if "tx_duty" in r)
    first = next(i for i, d in enumerate(seen) if d > 0)               # RF packets before the first TX_DONE: 0
    assert len(seen) - first >= 20 and max(seen) <= 50.0 and min(seen[first:]) >= 49.0, seen
    assert not reasons


def test_sim_last_airtime_follows_the_flight(tmp_path):
    """Flight RF last_airtime_ds = last_airtime_ms_ / 100 (integer division): 0 at boot (flight.h
    initialiser) until the first transmission ends, then that frame's busy time - E22 2.4k
    1267 ms -> 1.2 s (the simulator once rounded it to 1.3 s) - and 0 again from a radio
    reconfiguration (apply_radio_config, e.g. TXPWR) until the next frame has ended."""
    from dssq.dictionary import APID_RF
    got: dict = {}

    def rf_of(sim, now):
        sim._emit_values = lambda apid, values, vc, t: got.update(values) if apid == APID_RF else None
        sim._rf(now)
        return got["last_airtime"]

    clk = Clock()
    sim = SimSpacecraft(_config(tmp_path, air_rate=2), clock=clk, loss=0.0, seed=1)
    busy = sim.busy_s()
    assert round(busy * 1000) == 1267 and rf_of(sim, clk.t) == 0.0          # nothing transmitted yet
    sim._transmit(clk.t)
    assert rf_of(sim, clk.t + busy - 0.01) == 0.0                           # still on the air
    assert rf_of(sim, clk.t + busy) == 1.2 == min(1267 // 100, 255) / 10    # truncated, not rounded
    sim._execute(bytes([0x03, 2]), 0, 0, clk.t + busy + 0.5)                # TXPWR: radio reconfigured
    assert rf_of(sim, clk.t + busy + 1.0) == 0.0
    sim._transmit(clk.t + 3.0)
    assert rf_of(sim, clk.t + 3.0 + busy) == 1.2
    sx_sim = SimSpacecraft(_config(tmp_path, model="SX126x", air_rate=0), clock=clk, loss=0.0, seed=1)
    sx_sim._transmit(clk.t)
    assert rf_of(sx_sim, clk.t + sx_sim.busy_s()) == 25.5                  # 34.6 s saturates at 25.5 s


def test_sim_duty_stays_at_the_cap_across_a_slow_to_fast_rate_change(tmp_path):
    """Flight item 11 mirrored: the last frame at the old (slow) rate is owed its own period, so
    neither the real cycle nor the reported duty exceeds 50 % when a coordinated change to a
    fast profile takes effect (SX126x profile 0 -> 1 in ENCOUNTER)."""
    clk = Clock()
    sim = SimSpacecraft(_config(tmp_path, model="SX126x", air_rate=0), clock=clk, loss=0.0, seed=1)
    sim.mode = 3                                                       # ENCOUNTER: period = 2 x busy
    worst, t_end = 0.0, clk.t + 600.0
    while clk.t < t_end:                                               # bounded: 600 s in 0.5 s steps
        if sim.frames_sent == 2 and sim.pending is None and sim.sc_rate == 0:
            sim.pending = ("sc_rate", 1, 1, 3600)                      # after the next (3rd) frame
        sim._run_spacecraft(clk.t)
        worst = max(worst, sim._duty(clk.t))
        clk.t += 0.5
    launches = [t for t, _b in sim.tx_hist]
    busy = [b for _t, b in sim.tx_hist]
    cycles = [b / (n - t) for t, n, b in zip(launches, launches[1:], busy, strict=False)]
    assert sim.sc_rate == 1 and len(cycles) >= 5
    assert max(cycles) <= 0.5 + 1e-9 and worst <= 0.5, (max(cycles), worst)
