"""Web UI legitimacy of the receiver figures (D-76; R4 F-P0 / F-P1): what the station radio can
and cannot measure, and how the GDS reports it.

* the exact (Clopper-Pearson) FER bounds and the pure-Python incomplete beta function behind
  them, against closed forms, published values and the binomial tails they invert;
* RSSI readings: an E22 octet at the end of its range is an OVERLOAD, never a power;
* the received power in watts: the window mean_mw is the linear mean of the accepted readings,
  not 10^(mean dBm);
* the noise-floor rule of the margin (SIGNAL / NEAR_NOISE / NOISE_FLOOR / NOISE_UNKNOWN) and
  the worst-case sensitivity it is coloured against;
* frame loss without the AOS counter jump (it was counted modulo 256 across passes), the APID
  sequence baseline per pass, the frame-period-dependent windows, noise pairing per pass;
* the station clock state (dssq/clocksync.py) wired into the GDS: TDM refusal, snapshot,
  LED matrix, /metrics;
* no-value-as-number rules: OWLT and the measured range without a distance, the E22 CRC
  verdict, the register read-back and module evidence with rcu = false.
"""
from __future__ import annotations

import math
import time

import pytest

from dssq import clocksync, link
from dssq import rxstats as rxs
from dssq.ccsds.packets import SpacePacket
from dssq.ccsds.tm import FHP_ONLY_IDLE_DATA, build_frame, encode_cadu
from dssq.config import load_config
from dssq.display import frontpanel as fp
from dssq.display import web
from dssq.gds import GroundStation, json_safe

T0 = 1_800_000_000.0


def _binom_le(x: int, n: int, p: float) -> float:
    return math.fsum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(x + 1))


# =============================================================================
# Exact FER bounds
# =============================================================================
def test_regularized_incomplete_beta_against_closed_forms():
    # I_x(a, b) for integer a, b is a binomial tail: I_0.5(2, 3) = P(Bin(4, 0.5) >= 2) = 11/16
    assert rxs.betainc(2, 3, 0.5) == pytest.approx(11 / 16, abs=1e-14)
    assert rxs.betainc(1, 1, 0.3) == pytest.approx(0.3, abs=1e-14)                  # uniform
    assert rxs.betainc(1, 5, 0.2) == pytest.approx(1 - 0.8 ** 5, abs=1e-14)         # 1 - (1-x)^b
    assert rxs.betainc(5, 1, 0.2) == pytest.approx(0.2 ** 5, abs=1e-15)             # x^a
    assert (rxs.betainc(3, 4, 0.0), rxs.betainc(3, 4, 1.0)) == (0.0, 1.0)
    for a, b, p in ((2, 3, 0.25), (40, 160, 0.975), (1, 999, 0.05), (0.5, 0.5, 0.5)):
        assert rxs.betainc(a, b, rxs.beta_ppf(p, a, b)) == pytest.approx(p, abs=1e-12)
    assert rxs.beta_ppf(0.5, 0.5, 0.5) == pytest.approx(0.5, abs=1e-12)           # arcsine median
    with pytest.raises(ValueError):
        rxs.betainc(0, 1, 0.5)


def test_clopper_pearson_zero_losses_one_and_two_sided():
    """The task's worked example: 0 lost of 178 frames."""
    lo, hi, one = rxs.clopper_pearson(0, 178)
    assert lo == 0.0
    assert one == pytest.approx(1 - 0.05 ** (1 / 178)) and round(100 * one, 2) == 1.67
    assert hi == pytest.approx(1 - 0.025 ** (1 / 178)) and round(100 * hi, 2) == 2.05
    assert one == pytest.approx(3 / 178, rel=0.01)                                     # "rule of three"


@pytest.mark.parametrize(("x", "n", "lo", "hi"), [
    (1, 10, 0.002529, 0.445016),          # published Clopper-Pearson 95 % values (two-sided)
    (5, 10, 0.187086, 0.812914),
    (10, 10, 0.691503, 1.0),
    (0, 10, 0.0, 0.308497),
])
def test_clopper_pearson_known_values(x, n, lo, hi):
    a, b, _ = rxs.clopper_pearson(x, n)
    assert (a, b) == (pytest.approx(lo, abs=2e-6), pytest.approx(hi, abs=2e-6))


@pytest.mark.parametrize(("x", "n"), [(1, 10), (3, 100), (7, 178), (57, 20_000), (499, 500)])
def test_clopper_pearson_inverts_the_binomial_tails(x, n):
    lo, hi, one = rxs.clopper_pearson(x, n)
    assert 1 - _binom_le(x - 1, n, lo) == pytest.approx(0.025, abs=1e-9)          # P(X >= x | lo) = alpha/2
    if x < n:
        assert _binom_le(x, n, hi) == pytest.approx(0.025, abs=1e-9)              # P(X <= x | hi) = alpha/2
        assert _binom_le(x, n, one) == pytest.approx(0.05, abs=1e-9)              # one-sided: alpha
    assert lo <= x / n <= one <= hi


def test_fer_block_labels_every_bound():
    assert rxs.fer_block(0, 0) is None
    b = rxs.fer_block(3, 97)
    assert set(b) == {"lost", "received", "fer", "confidence", "two_sided_lo", "two_sided_hi", "one_sided_hi"}
    assert (b["lost"], b["received"], b["fer"], b["confidence"]) == (3, 97, 0.03, 0.95)
    with pytest.raises(ValueError):
        rxs.fer_block(-1, 3)


# =============================================================================
# RSSI readings and the noise-floor rule
# =============================================================================
def test_rssi_octet_ends_are_overloads_not_powers():
    assert rxs.classify_rssi(-256, True) == "OVERLOAD" and rxs.classify_rssi(-1, True) == "OVERLOAD"
    assert rxs.classify_rssi(-2, True) == "OK" and rxs.classify_rssi(-100, True) == "OK"
    assert rxs.classify_rssi(-1.0, False) == "OK"                       # an SX126x RssiPkt/2 reading
    assert rxs.classify_rssi(-170, True) == "INVALID" and rxs.classify_rssi(float("nan"), False) == "INVALID"
    assert rxs.classify_rssi(None, True) is None


def test_margin_states_and_their_bounds():
    sens, worst = -128.0, -126.0
    a = rxs.margin_assessment(-100.0, -118.0, sens, worst)
    assert (a["state"], a["power_dbm"], a["margin_db"], a["worst_db"]) == ("SIGNAL", -100.0, 28.0, 26.0)
    a = rxs.margin_assessment(-112.0, -118.0, sens, worst)                        # 6 dB above the noise
    s = 10 * math.log10(10 ** -11.2 - 10 ** -11.8)
    assert a["state"] == "NEAR_NOISE" and a["power_dbm"] == pytest.approx(s) and a["worst_db"] == pytest.approx(s + 126)
    a = rxs.margin_assessment(-116.0, -118.0, sens, worst)                        # 2 dB above: below the noise
    assert (a["state"], a["margin_db"], a["worst_db"], a["max_db"], a["worst_max_db"]) == \
        ("NOISE_FLOOR", None, None, 10.0, 8.0)
    a = rxs.margin_assessment(-100.0, None, sens, None)
    assert (a["state"], a["margin_db"], a["worst_db"]) == ("NOISE_UNKNOWN", 28.0, None)
    assert rxs.margin_colour_db(a) == 28.0                                          # no worst case: typical
    assert rxs.margin_assessment(None, -118.0, sens, worst)["state"] is None
    # the 10 dB threshold: the noise adds 10 log10(10/9) = 0.46 dB, below the E22's 1 dB step
    assert 10 * math.log10(10 / 9) == pytest.approx(0.4576, abs=1e-4)


def test_worst_case_sensitivity_per_model():
    assert link.sensitivity_worst("E22-400T33D", 2) == (-126.0, "datasheet")
    assert link.sensitivity_worst("E22-400T37S", 0) == (-125.0, "datasheet")
    w, src = link.sensitivity_worst("E22-400T33D", 4)                              # 9.6k: scaled like typical
    assert src == "estimated" and w == pytest.approx(-126.0 + 10 * math.log10(4), abs=0.05)
    assert link.sensitivity_worst("SX126x", 1) == (None, None)
    meas = {"E22-400T33D:2": -127.5}
    assert link.sensitivity_worst("E22-400T33D", 2, link.parse_measured(meas)) == (-127.5, "measured")


# =============================================================================
# Receiver statistics
# =============================================================================
class _Frame:
    def __init__(self, mcfc, vcid=0, vcfc=None):
        self.mcfc, self.vcid, self.vcfc = mcfc, vcid, mcfc if vcfc is None else vcfc
        self.format, self.is_oid = "TM", False


def _rx(**radio) -> rxs.ReceiverStats:
    cfg = load_config()
    cfg["radio"].update(radio)
    return rxs.ReceiverStats(cfg)


def test_aos_counter_jump_is_not_lost_and_vc_baselines_restart():
    r = _rx()
    for i in range(5):
        r.on_frame(T0 + i, _Frame(i), 0, 0, 0)
    r.on_frame(T0 + 5, _Frame(7), 0, 0, 0)                                          # 2 lost in the pass
    r.tick(T0 + 1000, 90.0)                                                         # LOS
    r.on_frame(T0 + 3000, _Frame(200), 0, 0, 0)                                     # AOS: jump 192 (mod 256)
    assert (r.frames_lost, r.aos_gap_frames, r.per_vc[0]["lost"]) == (2, 192, 2)
    s = r.snapshot(T0 + 3001)["frames"]
    assert s["lost"] == 2 and s["aos_gap_frames"] == 192 and s["fer_ci"]["lost"] == 2 and s["fer_ci"]["received"] == 7
    assert s["fer_10m_ci"] == rxs.fer_block(0, 1)                                    # 10 min: only the AOS frame


def test_noise_pairs_only_within_the_pass():
    r = _rx()
    r.tick(T0, 90.0)
    r.on_noise(-118.0, T0)
    r.on_reception(T0 + 10, -100.0)
    assert r.snapshot(T0 + 11)["rf"]["snr_est_db"] == 18.0
    r.on_reception(T0 + 3600, -100.0)                                               # next pass, old noise
    rf = r.snapshot(T0 + 3601)["rf"]
    assert rf["snr_est_db"] is None and rf["margin_state"] == "NOISE_UNKNOWN" and rf["pr_est_dbm"] is None
    assert r.w["10m"]["snr_est"].stats(T0 + 3601)["n"] == 0             # window: the paired one aged out


def test_overload_and_invalid_readings_stay_out_of_every_statistic():
    r = _rx()
    r.on_noise(-118.0, T0)
    for i, raw in enumerate((-256, -1, -200, -100)):
        r.on_reception(T0 + i, raw)
    assert (r.rssi_overloads, r.rssi_invalid, r.overload_last_t, r.overloads_recent(T0 + 4)) == (2, 1, T0 + 1, 2)
    assert r.w["pass"]["rssi"].stats()["n"] == 1 and sum(r.rssi_hist.bins) == 1
    assert r.overloads_recent(T0 + 1 + rxs.OVERLOAD_RECENT_S + 1) == 0
    r.on_noise_octet(-256, T0 + 5)
    assert r.noise_rejected == 1 and r.noise_dbm == -118.0


def test_linear_mean_of_the_received_power_is_not_the_db_mean():
    """D-76, received power in watts: the window mean in watts is the mean of the per-reading
    powers, not 10^(mean dBm), the geometric mean. -100 and -110 dBm (1e-13 W and 1e-14 W):
    linear mean (1e-13 + 1e-14) / 2 = 5.5e-14 W; 10^((-105 - 30) / 10) = 3.2e-14 W."""
    mean_mw = rxs.linear_mean_mw([-100.0, -110.0])
    assert mean_mw == pytest.approx(5.5e-11, rel=1e-12)                             # mW
    assert round(mean_mw / 1000, 15) == 5.5e-14                                     # W
    assert round(10 ** ((-105.0 - 30) / 10), 15) == 3.2e-14                         # 10^(mean dBm): lower
    assert rxs.linear_mean_mw([-113.0, -113.0]) == pytest.approx(10 ** -11.3)       # no fluctuation: equal
    for v in ([-90.0, -120.0, -101.5], [-117.4, -117.5, -117.3], [-60.0]):          # AM >= GM, equal for one
        assert rxs.linear_mean_mw(v) >= 10 ** (math.fsum(v) / len(v) / 10) * (1 - 1e-12)
    # sentinels: no reading, and a sum beyond the float range, are no number
    assert rxs.linear_mean_mw([]) is None
    assert rxs.linear_mean_mw([4000.0]) is None and rxs.linear_mean_mw([3080.0, 3080.0]) is None
    w = rxs.Window(None, 10, power=True)
    assert w.stats() == {"n": 0, "truncated": False}                                 # empty: no mean_mw
    w.add(T0, -100.0)
    w.add(T0 + 1, -110.0)
    st = w.stats()
    assert st["mean"] == -105.0 and st["mean_mw"] == pytest.approx(5.5e-11, rel=1e-12)
    assert "mean_mw" not in rxs.Window(None, 10).stats() | rxs.Window(60.0, 10).stats()


def test_linear_mean_uses_only_the_accepted_readings_of_every_window():
    """mean_mw is computed over the same readings as the dB statistics: calibrated, and no
    overload (E22 octet 0x00 / 0xFF) or invalid (< -160 dBm) reading; only rssi and noise
    carry it."""
    r = _rx(rssi_cal_offset_db=-3.0)
    for i, raw in enumerate((-256, -97, -1, -200, -107)):                           # two overloads, one invalid
        r.on_reception(T0 + i, raw)
    r.on_noise_octet(-256, T0 + 5)                                                    # rejected noise octet
    r.on_noise_octet(-115, T0 + 6)                                                    # -118 dBm calibrated
    snap = r.snapshot(T0 + 7)
    assert (r.rssi_overloads, r.rssi_invalid, r.noise_rejected) == (2, 1, 1)
    for w in ("1m", "10m", "pass"):
        rssi, noise = snap["windows"][w]["rssi"], snap["windows"][w]["noise"]
        assert (rssi["n"], rssi["mean"], rssi["min"], rssi["max"]) == (2, -105.0, -110.0, -100.0)
        assert rssi["mean_mw"] == pytest.approx(5.5e-11, rel=1e-12)
        assert noise["n"] == 1 and noise["mean_mw"] == pytest.approx(10 ** -11.8, rel=1e-12)
        assert [m for m, st in snap["windows"][w].items() if "mean_mw" in st] == list(rxs.POWER_METRICS)
    assert snap["rf"]["rssi_mw"] == pytest.approx(10 ** -11.0)                       # the newest: -110 dBm
    # the windows age out like the dB statistics: an empty 1-min window has no mean_mw
    assert "mean_mw" not in r.snapshot(T0 + 200)["windows"]["1m"]["rssi"]


def test_rate_and_link_manager_windows_follow_the_frame_period():
    r = _rx()
    assert (r.rate_window_s, r.lm_window_s) == (60.0, 600.0)
    r.set_frame_period(138.5)                                                       # SX126x profile 0 SAFE
    assert r.rate_window_s == pytest.approx(692.5) and r.lm_window_s == pytest.approx(1385.0)
    for i in range(11):
        r.on_reception(T0 + 138.5 * i, -120.0)
        r.on_frame(T0 + 138.5 * i, _Frame(i), 0, 100, 188)
    now = T0 + 138.5 * 10
    lm = r.lm_input(now)
    assert lm["rssi"]["n"] == 11 and lm["frames"] == 11 and lm["window_s"] == pytest.approx(1385.0)
    fr = r.snapshot(now)["frames"]
    assert fr["rate_window_s"] == pytest.approx(692.5) and fr["frames_per_min"] == pytest.approx(6 * 60 / 692.5)
    r.set_frame_period(float("nan"))                                                 # ignored
    assert r.lm_window_s == pytest.approx(1385.0)


def test_budget_needs_a_distance_for_the_measured_range():
    r = _rx()
    r.sc_rf = {"tx_power_code": 0, "radio_model": 1, "air_rate_code": 2}
    r.on_reception(T0, -100.0)
    b = r.snapshot(T0 + 1)["budget"]
    assert b["distance_km"] is None and b["owlt_s"] is None and b["range_at_sensitivity_km_est"] is None
    gain = 33.0 + 2.15 - 0.5 + r.gr_dbi - r.rx_loss_db
    free = rxs.range_for_loss_m(gain + 126.0, 433.125e6) / 1000
    assert b["range_at_sensitivity_free_space_km"] == pytest.approx(free)
    assert b["sensitivity_worst_dbm"] == -126.0 and b["measured_power_state"] == "NOISE_UNKNOWN"


# =============================================================================
# GroundStation: station clock, TDM, matrix, metrics, E22 facts
# =============================================================================
def _gs(tmp_path, **radio) -> GroundStation:
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    cfg["sdls"]["key_file"] = str(tmp_path / "missing_key.hex")
    cfg["radio"].update(radio)
    return GroundStation(cfg, archive=False)


def _cadu(mcfc: int, octet: int = 156) -> bytes:
    return encode_cadu(build_frame(0, mcfc, mcfc, FHP_ONLY_IDLE_DATA, bytes(188), 0)) + bytes([octet])


def test_unsynchronised_station_clock_refuses_tdm_records(tmp_path, monkeypatch):
    now = [T0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    gs = _gs(tmp_path)
    gs.station_clock = clocksync.StationClock(lambda: clocksync.ClockReading(
        False, "TIME_ERROR", True, 16_000_000, 16_000_000, "TIME_ERROR and STA_UNSYNC"))
    gs.housekeeping(T0)
    for i in range(3):
        gs.ingest(_cadu(i), T0 + i)
    s = gs.snapshot()
    assert s["rx"]["frames"]["ok"] == 3 and s["tdm"]["records"] == 0                # reception goes on
    assert s["health"]["tdm_rejected"] == 3 and s["health"]["tdm_rejected_clock"] == 3
    tdm = [e["text"] for e in s["events"] if e["source"] == "TDM"]
    assert len(tdm) == 1 and "not synchronised" in tdm[0] and "TIME_ERROR" in tdm[0]
    assert any(e["source"] == "CLOCK" and e["level"] == "WARN" for e in s["events"])
    assert s["station_clock"]["synced"] is False and s["station_clock"]["checked_t"] == T0
    assert gs.panel_status()["matrix"]["clock_synced"] is False
    assert fp.matrix_line(gs.panel_status()).endswith("|0")
    assert "\ndssq_station_clock_synced 0\n" in web.prometheus(json_safe(s))
    # unknown refuses nothing and is absent from /metrics
    gs.station_clock = clocksync.StationClock(lambda: clocksync.ClockReading(None, reason="sandbox"))
    now[0] = T0 + 20
    gs.housekeeping(T0 + 20)
    gs.ingest(_cadu(3), T0 + 20)
    s = gs.snapshot()
    assert s["tdm"]["records"] == 1 and s["station_clock"]["synced"] is None
    assert "dssq_station_clock_synced" not in web.prometheus(json_safe(s))


def test_e22_station_reports_what_it_cannot_measure(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: T0 + 2)
    gs = _gs(tmp_path)                                                              # E22-400T33D, rcu = false
    gs.ingest(_cadu(0), T0)
    gs.ingest(_cadu(1), T0 + 1)
    s = gs.snapshot()
    assert s["schema"] == 4
    assert s["rx"]["rf"]["packet"]["crc_ok"] is None                               # F-P0-2: no CRC verdict
    assert s["time"]["owlt_s"] is None                                              # F-P0-7: no distance
    assert s["radio"]["register_check"].startswith("not read back: no Radio Control Unit")
    assert s["radio"]["module_answering"] is True and "CADU" in s["radio"]["module_evidence"]
    assert s["linkmgr"]["executable"] is False and "Radio Control Unit" in s["linkmgr"]["why_not"]
    assert s["rx"]["rf"]["sensitivity_worst_dbm"] == -126.0
    text = web.prometheus(json_safe(s))
    assert "dssq_rf_packet_crc_ok" not in text and "dssq_rf_doppler" not in text
    assert "\ndssq_linkmgr_executable 0\n" in text and "dssq_sync_asm_found" in text and "dssq_serial_octets" in text
    m = gs.panel_status()["matrix"]
    assert (m["rssi_overload"], m["margin_floor"]) == (False, False)
    assert m["margin_db"] == pytest.approx(-100.0 - (-126.0))                       # octet 156 = -100 dBm, worst case


def test_apid_sequence_baseline_restarts_with_the_pass(tmp_path):
    gs = _gs(tmp_path)

    def pkt(seq):
        raw = bytes([0x00, 0x10, 0xC0 | (seq >> 8), seq & 0xFF, 0x00, 0x00, 0x00])
        return SpacePacket.parse(raw)

    for seq in (0, 1, 3):                                                            # one lost in the pass
        gs._apid_stats(pkt(seq), 0, T0 + seq)
    gs._apid_aos = {0x010}                                                           # AOS (_frame_done)
    gs._apid_stats(pkt(900), 0, T0 + 5000)                                           # sent while out of view
    gs._apid_stats(pkt(901), 0, T0 + 5010)
    st = gs.apid_stats[0x010]
    assert (st["lost"], st["seq_gaps"], st["aos_gap_packets"]) == (1, 1, 896)
    assert st["iat_s"] == pytest.approx(1.0 + 0.2 * (2.0 - 1.0) + 0.2 * (10.0 - 1.2))   # the LOS gap is no IAT
