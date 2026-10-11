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
from dssq.rxstats import PASS_WINDOW_MAX
from dssq.sim import MAG_OFFSET_UT, MAG_SOFT_IRON, SimSpacecraft

ROOT = Path(__file__).resolve().parents[2]
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


def _config(tmp_path, *, key=True, **radio):
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    key_file = tmp_path / "sdls_key.hex"
    if key:
        key_file.write_text(KEY.hex() + "\n")
    cfg["sdls"]["key_file"] = str(key_file)
    cfg["radio"].update(radio)
    cfg["science"]["field_ut"] = 50.0
    return cfg


class World:
    """GroundStation + SimSpacecraft on one virtual clock (time.time / time.sleep patched)."""

    def __init__(self, monkeypatch, tmp_path, *, key=True, framing="TM", sim_key="same", radio=None, **simkw):
        self.clk = clk = Clock()
        monkeypatch.setattr(time, "time", clk)
        monkeypatch.setattr(time, "sleep", clk.sleep)
        self.cfg = cfg = _config(tmp_path, key=key, **(radio or {"air_rate": 7}))
        self.gs = gs = GroundStation(cfg, archive=True)
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
    assert w.command("MODE TEST") == "EXECUTED"                                  # MAG every 4 s
    assert gs.magcal_action("start", 0)["status"] == "OK"
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
    if not (out / "integration_tm_ae.bin").exists():
        if not (HOST / "test_integration.cpp").exists():
            pytest.skip("tools/host_test/test_integration.cpp not in the tree yet (WS1)")
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
    assert any(e["text"].startswith("stored sequence") or "0xFE" in e["text"] or "stored" in e["text"]
               for e in s["events"])                                             # CMDVER tc_seq 0xFE
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
