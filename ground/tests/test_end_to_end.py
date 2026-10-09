"""End-to-end: GDS <-> software spacecraft over a simulated RF channel (virtual clock)."""
import json
import time

import pytest

from dssq.ccsds.sdls import SdlsSender
from dssq.config import load_config
from dssq.gds import GroundStation
from dssq.radioctl import RadioManager
from dssq.sim import SimSpacecraft


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


@pytest.fixture
def world(monkeypatch, tmp_path):
    clk = Clock()
    monkeypatch.setattr(time, "time", clk)
    monkeypatch.setattr(time, "sleep", clk.sleep)
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    cfg["commanding"]["t1_s"] = 30.0
    gs = GroundStation(cfg, archive=True)
    sim = SimSpacecraft(cfg, loss=0.0, max_errors=4, rssi_mean=-100, fading_db=1.0, seed=7, clock=clk)

    class Rcu:
        def set_mode(self, m):
            return sim.rcu_set_mode(m)

    gs.radio_mgr = RadioManager(sim, Rcu(), clock=clk, sleep=clk.sleep)

    def run(seconds, step=0.05):
        end = clk.t + seconds
        while clk.t < end:
            gs.ingest(sim.read(), clk.t)
            while gs.outbox:
                sim.write(gs.outbox.popleft())
            if gs.radio_jobs:
                gs.run_radio_jobs()
            clk.t += step
    return gs, sim, clk, run


def test_telemetry_flows_and_decodes(world):
    gs, sim, clk, run = world
    run(120)
    s = gs.snapshot()
    assert s["sync"]["state"] == "LOCK"
    assert s["rx"]["frames"]["ok"] > 10
    assert "HK" in s["tlm"] and s["tlm"]["HK"]["values"]["fsw_mode"] == 1       # SAFE after boot
    assert s["rx"]["decoder"]["symbols_corrected"] > 0                            # RS exercised
    assert s["rx"]["decoder"]["uncorrectable"] == 0
    assert s["rx"]["rf"]["noise_dbm"] is not None                                 # noise polling works
    json.dumps(__import__("dssq.display.web", fromlist=["clean"]).clean(s))      # web-serialisable


def test_command_round_trip_cop1(world):
    gs, sim, clk, run = world
    run(20)
    assert gs.submit_command("PING 4242")["status"] == "QUEUED"
    assert gs.submit_command("MODE ENCOUNTER")["status"] == "QUEUED"
    run(60)
    recs = {r.cmd.text: r for r in gs.fop.all}
    assert recs["PING 4242"].state == "EXECUTED"
    assert recs["MODE 3"].state == "EXECUTED"
    run(30)
    assert gs.tlm["HK"]["values"]["fsw_mode"] == 3
    assert gs.fop.nr == 2 and not gs.fop.lockout


def test_hazardous_needs_confirmation(world):
    gs, sim, clk, run = world
    r = gs.submit_command("SSRCLEAR")
    assert r["status"] == "CONFIRM"
    assert not gs.fop.all
    assert gs.confirm(r["id"])["status"] == "QUEUED"


def test_bad_command_rejected_locally(world):
    gs, *_ = world
    assert gs.submit_command("MODE WARP")["status"] == "REJECTED"
    assert gs.submit_command("TXPWR 9")["status"] == "REJECTED"


def test_sdls_authenticated_uplink(world, tmp_path):
    gs, sim, clk, run = world
    key = bytes(range(32))
    gs.sdls.key = key
    from dssq.ccsds.sdls import SdlsReceiver
    sim.sdls = SdlsReceiver(key)
    run(10)
    gs.submit_command("NOOP")
    run(40)
    assert gs.fop.all[-1].state == "EXECUTED"
    sim.sdls = SdlsReceiver(bytes(32))            # spacecraft with a different key
    gs.submit_command("PING 1")
    run(40)
    assert gs.fop.all[-1].state != "EXECUTED"
    assert sim.sdls.failures >= 1


def test_coordinated_air_rate_change(world):
    gs, sim, clk, run = world
    run(15)
    r = gs.submit_command("LINKRATE 4")
    assert r["status"] == "CONFIRM"
    gs.confirm(r["id"])
    run(120)
    assert sim.sc_rate == 4 and gs.cfg["radio"]["air_rate"] == 4
    assert gs.link_change is None
    assert sim.revert is None                      # spacecraft saw the confirming NOOP
    frames = gs.rx.frames_ok
    run(30)
    assert gs.rx.frames_ok > frames                # link alive at the new rate


def test_ground_rf_survey_and_spacecraft_rfscan(world):
    gs, sim, clk, run = world
    run(10)
    gs.submit_command("GSCAN 20 30")
    run(5)
    scan = gs.radio_mgr.last_scan
    assert scan and scan["quietest"] != 24 and len(scan["noise"]) == 11
    gs.submit_command("RFSCAN 20 30")
    run(40)
    assert gs.tlm["RFSCAN"]["values"]["count"] == 11


def test_dashboard_and_metrics_served(world):
    gs, sim, clk, run = world
    run(30)
    from dssq.display.web import clean, prometheus
    text = prometheus(clean(gs.snapshot()))
    assert "dssq_rf_rssi_dbm" in text and "dssq_tlm{" in text
    from dssq.display.frontpanel import build_pages
    pages = build_pages(gs.snapshot())
    assert len(pages) == 10 and all(p.count("|") == 7 for p in pages)
