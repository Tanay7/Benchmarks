"""Ground radio models, LoRa link mathematics, T-RF-03 analysis and the SX126x backend.

Expected values come from the documents, never from the code under test:
  * EBYTE E22-400T33D manual v1.05: 2.2 (-126/-128/-130 dBm at 2.4k), 7.2 (REG0 rates,
    REG1 b4..2 reserved), 7.3 (factory defaults); E22-400T37S manual v1.5: 2.2, 5.7, 7.2
  * Semtech SX1261/2 DS rev 2.2: Table 3-8 (LoRa sensitivity, FERR_L), 6.1.4 (time on
    air), Table 9-3 (Rx gain 0x96 / 0x94)
  * LoRa time on air of a 236-octet CADU, computed by hand below and cross-checked
    against an independent transcription of Semtech's reference-driver formula
"""
import importlib.util
import math
import sqlite3
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dssq import link
from dssq.archive import SCHEMA
from dssq.ccsds import CADU_LEN
from dssq.ccsds.tm import FHP_ONLY_IDLE_DATA, build_frame, encode_cadu
from dssq.framesync import FrameSynchronizer, RadioFault, RawCadu
from dssq.link import (AIR_RATE_BPS, E22_T33D, E22_T37S, MIN_EFFECTIVE_RATE_CODE, SENSITIVITY_EST_DBM, SX126X,
                       TX_POWER_DBM)
from dssq.linkmgr import LinkManager
from dssq.radio import sx126x as sx
from dssq.radio.e22 import describe, regs_from_cfg, sanitize_reg1
from dssq.radioctl import RadioManager

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("link_budget", ROOT / "tools" / "link_budget.py")
lb = importlib.util.module_from_spec(_spec)
sys.modules["link_budget"] = lb                 # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(lb)

F433 = 433.125e6


def _cadu(mcfc=0):
    return encode_cadu(build_frame(7, mcfc, 0, FHP_ONLY_IDLE_DATA, bytes(188), 0))


# ===================================================================== models
def test_model_registry_and_capabilities():
    assert link.DEFAULT_MODEL == E22_T33D and link.get_model(None).name == E22_T33D
    assert link.get_model("e22-400t37s").name == E22_T37S and link.get_model(" sx126x ").name == SX126X
    with pytest.raises(ValueError):
        link.get_model("E22-400T30D")
    assert [link.model_from_id(i).name for i in (1, 2, 3)] == [E22_T33D, E22_T37S, SX126X]   # contract 3.2
    t33, t37, sxm = (link.get_model(n) for n in (E22_T33D, E22_T37S, SX126X))
    assert (t33.has_fault_log, t37.has_fault_log, sxm.has_fault_log) == (False, True, False)
    assert (t33.packet_mode, t37.packet_mode, sxm.packet_mode) == (False, False, True)
    assert t33.has_noise_register and not sxm.has_noise_register
    assert link.CADU_OCTETS == CADU_LEN == 236
    assert link.channel_freq_mhz(23) == 433.125 and link.channel_freq_mhz(0) == 410.125
    with pytest.raises(ValueError):
        link.channel_freq_mhz(84)


def test_e22_rate_power_and_sensitivity_tables():
    # Both manuals 7.2: codes 000, 001, 010 are all 2.4k; 111 = 62.5k.
    for m in (E22_T33D, E22_T37S):
        assert [link.rate_bps(m, c) for c in range(8)] == [2400, 2400, 2400, 4800, 9600, 19200, 38400, 62500]
        assert link.get_model(m).min_effective_rate_code == 2
    assert [link.power_dbm(E22_T33D, c) for c in range(4)] == [33.0, 30.0, 27.0, 24.0]   # VERIFY T-RF-07
    assert {link.power_dbm(E22_T37S, c) for c in range(4)} == {37.0}                  # "no power levels"
    # 2.4k typical sensitivity (datasheet); faster rates scaled 10 log10(Rb / 2400) (estimated).
    assert link.sensitivity(E22_T33D, 2) == (-128.0, "datasheet")
    assert link.sensitivity(E22_T37S, 0) == (-126.0, "datasheet")
    assert link.sensitivity(E22_T33D, 4) == (round(-128 + 10 * math.log10(4), 1), "estimated")
    assert link.sensitivity(E22_T37S, 7) == (-111.8, "estimated")
    assert all(link.sensitivity(E22_T33D, c)[0] < link.sensitivity(E22_T33D, c + 1)[0] for c in range(2, 7))


def test_legacy_names_follow_the_default_model():
    assert AIR_RATE_BPS[0] == AIR_RATE_BPS[1] == AIR_RATE_BPS[2] == 2400 and AIR_RATE_BPS[7] == 62500
    assert SENSITIVITY_EST_DBM[2] == -128.0 and TX_POWER_DBM[0] == 33.0
    assert MIN_EFFECTIVE_RATE_CODE == 2
    assert math.isclose(link.dbm_to_mw(30.0), 1000.0)


def test_measured_sensitivity_overrides_and_is_validated():
    table = {"E22-400T33D:4": -120.3, "SX126x:1": -141.2}
    assert link.sensitivity(E22_T33D, 4, table) == (-120.3, "measured")
    assert link.sensitivity(SX126X, 1, table) == (-141.2, "measured")
    assert link.sensitivity(E22_T33D, 3, table) == (-125.0, "estimated")        # not measured: unchanged
    assert link.parse_measured({"e22-400t37s:2": -126}) == {(E22_T37S, 2): -126.0}
    for bad in ({"T33D": -120.0}, {"E22-400T33D:9": -120.0}, {"Unknown:2": -120.0},
                {"E22-400T33D:2": -20.0}, {"E22-400T33D:2": "x"}, {"E22-400T33D:x": -120.0}):
        with pytest.raises(ValueError):
            link.parse_measured(bad)


# ================================================================== LoRa maths
# Hand computation (Semtech, SX1261/2 DS 6.1.4), PL = 236, CR 4/5, preamble 12, explicit
# header, CRC on, DE = 1 when Tsym >= 16.38 ms:
#   n_payload = 8 + ceil((8*236 - 4 SF + 28 + 16) / (4 (SF - 2 DE))) * 5,  T = (12 + 4.25 + n_payload) Tsym
#   code SF  BW kHz  Tsym ms   DE  ceil(...)           n_payload  T ms
#   0    12  31.25   131.072   1   ceil(1884/40) = 48  248        264.25 x 131.072 = 34635.776
#   1    12  62.5     65.536   1   ceil(1884/40) = 48  248        264.25 x  65.536 = 17317.888
#   2    12  125      32.768   1   ceil(1884/40) = 48  248        264.25 x  32.768 =  8658.944
#   3    11  125      16.384   1   ceil(1888/36) = 53  273        289.25 x  16.384 =  4739.072
#   4    10  125       8.192   0   ceil(1892/40) = 48  248        264.25 x   8.192 =  2164.736
#   5     9  125       4.096   0   ceil(1896/36) = 53  273        289.25 x   4.096 =  1184.768
#   6     8  125       2.048   0   ceil(1900/32) = 60  308        324.25 x   2.048 =   664.064
#   7     7  125       1.024   0   ceil(1904/28) = 68  348        364.25 x   1.024 =   372.992
AIRTIME_236_MS = [34635.776, 17317.888, 8658.944, 4739.072, 2164.736, 1184.768, 664.064, 372.992]


def test_lora_airtime_of_a_cadu_per_profile_matches_hand_computation():
    for code, want_ms in enumerate(AIRTIME_236_MS):
        assert link.profile_airtime_s(code) * 1000 == pytest.approx(want_ms, abs=1e-6), code
        assert link.airtime_s(SX126X, code) == (pytest.approx(want_ms / 1000, abs=1e-9), "computed")
    assert [link.SX126X_PROFILES[c].ldro for c in range(8)] == [True] * 4 + [False] * 4


def _semtech_reference_toa_s(pl, sf, bw_hz, cr, preamble, ldro, crc=True, implicit=False):
    """Independent transcription of the reference driver (numerator form, SF >= 7)."""
    num = (pl << 3) + (16 if crc else 0) - 4 * sf + 8 + (0 if implicit else 20)
    den = 4 * (sf - 2) if ldro else 4 * sf
    num = max(num, 0)
    intermediate = ((num + den - 1) // den) * (cr + 4) + preamble + 12
    return (4 * intermediate + 1) * (1 << (sf - 2)) / bw_hz


def test_lora_airtime_agrees_with_reference_formula_everywhere():
    for sf in range(7, 13):
        for bw in (31_250.0, 62_500.0, 125_000.0, 250_000.0, 500_000.0):
            ldro = (1 << sf) / bw >= link.LDRO_SYMBOL_S
            for cr in (1, 4):
                for pl in (0, 1, 13, 90, 236, 255):
                    want = _semtech_reference_toa_s(pl, sf, bw, cr, 12, ldro)
                    assert link.lora_airtime_s(pl, sf, bw, cr) == pytest.approx(want, rel=1e-12), (sf, bw, cr, pl)
    assert link.lora_airtime_s(10, 7, 125e3, explicit_header=False) == \
        pytest.approx(_semtech_reference_toa_s(10, 7, 125e3, 1, 12, False, implicit=True))
    with pytest.raises(ValueError):
        link.lora_airtime_s(256, 12, 125e3)


def test_lora_sensitivity_computed_and_datasheet():
    # -174 + 10 log10(BW) + 6 + SNRmin(SF), rounded to 0.1 dB. BW 31.25 kHz gives
    # -143.05 -> -143.1 (contract 2.2's table prints -143.0; the formula decides).
    comp = [link.lora_sensitivity_computed_dbm(p.sf, p.bw_hz) for p in link.SX126X_PROFILES.values()]
    assert comp == [-143.1, -140.0, -137.0, -134.5, -132.0, -129.5, -127.0, -124.5]
    # Table 3-8 (Rx boosted gain) replaces the computed value where it lists (SF, BW).
    got = [link.sensitivity(SX126X, c) for c in range(8)]
    assert got == [(-143.1, "computed"), (-140.0, "computed"), (-137.0, "datasheet"), (-134.5, "computed"),
                   (-132.0, "computed"), (-129.5, "computed"), (-127.0, "computed"), (-124.0, "datasheet")]
    assert link.SX1262_SENSITIVITY_DBM == {(7, 10.4): -134.0, (12, 10.4): -148.0, (7, 125.0): -124.0,
                                           (12, 125.0): -137.0, (7, 250.0): -121.0, (12, 250.0): -134.0,
                                           (7, 500.0): -117.0, (12, 500.0): -129.0}
    # Power-saving gain: Table 3-8 BW125 / SF12 -133 dBm; elsewhere 4 dB worse (estimated).
    assert link.sensitivity(SX126X, 2, rx_boost=False) == (-133.0, "datasheet")
    assert link.sensitivity(SX126X, 1, rx_boost=False) == (-136.0, "estimated")
    assert link.sensitivity(SX126X, 1)[0] - link.sensitivity(E22_T33D, 2)[0] == pytest.approx(-12.0)


def test_sx126x_power_codes_and_bit_rates():
    assert [link.power_dbm(SX126X, c) for c in range(4)] == [33.0, 28.0, 25.0, 21.0]   # drive + 11 dB
    assert link.power_dbm(SX126X, 0, pa_gain_db=15.0) == 33.0                           # module maximum
    assert link.rate_bps(SX126X, 1) == pytest.approx(12 * 62_500 / 4096 * 0.8)
    assert link.rate_label(SX126X, 0) == "SF12 BW31.25 CR4/5"


def test_frequency_offset_limits_and_doppler():
    # FERR_L: +-25 % BW, SF12 +-50 ppm, SF11 +-100 ppm, SF10 +-200 ppm - the tighter applies.
    assert link.lora_max_freq_offset_hz(12, 31_250, F433) == 7812.5                    # 18 ppm at 433 MHz
    assert link.lora_max_freq_offset_hz(12, 125_000, F433) == pytest.approx(50e-6 * F433)
    assert link.lora_max_freq_offset_hz(10, 125_000, F433) == 31250.0
    assert link.lora_max_freq_offset_hz(7, 125_000, F433) == 31250.0
    lim = 7812.5
    assert link.freq_offset_state(0.49 * lim, lim) == "OK"
    assert link.freq_offset_state(-0.6 * lim, lim) == "WARN"
    assert link.freq_offset_state(0.81 * lim, lim) == "ALARM"
    d = link.doppler(1000.0, 200.0, F433)
    assert d["doppler_hz"] == 800.0
    assert d["range_rate_m_s"] == pytest.approx(-299_792_458.0 * 800.0 / F433)


def test_describe_produces_the_snapshot_radio_block():
    keys = {"model", "packet_mode", "rate_code", "rate_label", "bps", "sf", "bw_khz", "cr", "power_dbm", "channel",
            "freq_mhz", "rx_boost", "cad", "sensitivity_dbm", "sensitivity_source"}          # contract 10
    e = link.describe({"air_rate": 2, "power": 0, "channel": 23})
    assert keys <= e.keys()
    assert (e["model"], e["packet_mode"], e["rate_label"], e["bps"], e["power_dbm"]) == (E22_T33D, False, "2.4k",
                                                                                         2400.0, 33.0)
    assert (e["sf"], e["rx_boost"], e["sensitivity_dbm"], e["sensitivity_source"]) == (None, None, -128.0, "datasheet")
    s = link.describe({"model": "SX126x", "air_rate": 1, "power": 0, "channel": 23,
                       "sx126x": {"rx_boosted_gain": True, "cad_before_tx": False},
                       "sensitivity_measured": {"SX126x:1": -141.0}})
    assert (s["sf"], s["bw_khz"], s["cr"], s["ldro"], s["rx_boost"], s["cad"]) == (12, 62.5, "4/5", True, True, False)
    assert (s["sensitivity_dbm"], s["sensitivity_source"], s["freq_offset_limit_hz"]) == (-141.0, "measured", 15625.0)
    assert s["airtime_s"] == pytest.approx(17.317888)
    with pytest.raises(ValueError):
        link.describe({"channel": 84})


# ============================================================== E22 registers
def test_t33d_never_sets_the_reserved_fault_log_bit():
    regs = regs_from_cfg({"model": E22_T33D, "fault_log": True, "rssi_noise": True, "power": 1})
    assert regs[4] & 0x1C == 0 and regs[4] & 0x20 and regs[4] & 3 == 1
    d = describe(regs, E22_T33D)
    assert (d["fault_log"], d["fault_log_supported"]) == (False, False)
    assert (d["power_dbm"], d["power_verified"]) == (30.0, False)
    assert describe(regs[:4] + bytes([regs[4] | 0x04]) + regs[5:], E22_T33D)["reserved_bits_set"] is True
    assert sanitize_reg1(0xFF, E22_T33D) == 0xE3 and sanitize_reg1(0xFF, E22_T37S) == 0xFF


def test_t37s_fault_log_register_bit():
    regs = regs_from_cfg({"model": E22_T37S, "fault_log": True, "rssi_noise": True, "power": 0})
    assert regs[4] & 0x04 and regs[4] & 0x20            # REG1 bit 2 and bit 5
    d = describe(regs, E22_T37S)
    assert d["fault_log"] is True and d["power_dbm"] == 37.0
    assert not regs_from_cfg({"model": E22_T37S, "fault_log": False})[4] & 0x04


def test_t33d_factory_defaults_decode_as_documented():
    # Manual 7.3: 433.125 MHz, channel 0x17, 2.4 kbit/s, 9600 8N1, 33 dBm.
    d = describe(bytes([0x00, 0x00, 0x00, 0x62, 0x00, 0x17, 0x00]), E22_T33D)
    assert (d["freq_mhz"], d["channel"], d["air_rate"], d["uart_bps"], d["parity_code"], d["power_dbm"]) == \
        (433.125, 23, "2.4k", 9600, 0, 33.0)


def test_register_encoding_rejects_bad_values_and_packet_models():
    with pytest.raises(ValueError):
        regs_from_cfg({"channel": 84})
    with pytest.raises(ValueError):
        regs_from_cfg({"power": 4})
    with pytest.raises(ValueError):
        regs_from_cfg({"model": SX126X})
    with pytest.raises(ValueError):
        describe(bytes(6))


# ---------------------------------------------------- E22 control via the RCU
class FakeE22:
    """Configuration-mode register file of an E22 behind a fake RCU."""

    def __init__(self, regs):
        self.regs = bytearray(regs)
        self.mode = 0
        self.out = b""
        self.writes = []

    def read(self):
        o, self.out = self.out, b""
        return o

    def write(self, d):
        self.writes.append(bytes(d))
        if d[:4] == bytes([0xC0, 0xC1, 0xC2, 0xC3]):
            self.out += bytes([0xC1, 0x00, 0x02, 0xA0, 0x90])
        elif self.mode == 2 and d[0] in (0xC0, 0xC2):
            a, n = d[1], d[2]
            self.regs[a:a + n] = d[3:3 + n]
            self.out += bytes([0xC1, a, n]) + bytes(self.regs[a:a + n])
        elif self.mode == 2 and d[0] == 0xC1:
            a, n = d[1], d[2]
            self.out += bytes([0xC1, a, n]) + bytes(self.regs[a:a + n])


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 1e-3)


def test_radio_manager_e22_t33d_power_and_survey():
    e22 = FakeE22(bytes([0, 0, 0, 0x62, 0x24, 23, 0x80, 0, 0]))    # T33D with a stray REG1 b2 set

    class Rcu:
        def set_mode(self, m):
            e22.mode = m
            return True

    clk = Clock()
    rm = RadioManager(e22, Rcu(), clock=clk, sleep=clk.sleep, model=E22_T33D)
    cfg = rm.read_config()
    assert cfg["model"] == E22_T33D and cfg["reserved_bits_set"] is True and cfg["channel"] == 23
    assert rm.set_power(2) and e22.regs[4] == 0x22                  # power 2, reserved bit cleared
    assert rm.set_air_rate(4) and e22.regs[3] & 7 == 4
    assert rm.noise_dbm() == -(256 - 0xA0)
    scan = rm.survey(20, 22)
    assert scan["noise"] == [-96, -96, -96] and scan["home"] == 23 and scan["home_restored"]
    assert e22.regs[5] == 23
    with pytest.raises(ValueError):
        rm.set_channel(84)


# ============================================================ SX126x backend
class FakeBridge:
    def __init__(self):
        self.handlers = {}
        self.calls = []
        self.replies = {}

    def provide(self, name, fn):
        self.handlers[name] = fn

    def call(self, method, *args, timeout=None):
        self.calls.append((method, args))
        r = self.replies.get(method, 1)
        if isinstance(r, list):
            r = r.pop(0) if r else 1
        if isinstance(r, BaseException):
            raise r
        return r


STATUS = "S|DSSQ-IO 3.0 SX126x|SX1268|1|23|1|0|7|150|12|1|0|4|0|2|0|0|1|0|{up}"


@pytest.fixture
def sxb():
    clk = Clock()
    b = FakeBridge()
    radio = sx.Sx126xBridge(b, {"model": "SX126x", "air_rate": 1, "power": 0, "channel": 23}, clock=clk, poll_s=0.0)
    radio.attach()
    return radio, b, clk


def test_rf_rx_notifications_are_validated(sxb):
    radio, b, clk = sxb
    assert set(b.handlers) == {"rf_rx", "rf_evt"}
    cadu = _cadu(5)
    b.handlers["rf_rx"](cadu.hex().upper(), -1105, -1250, 812, 1)
    b.handlers["rf_rx"]("", -1270, -1900, -40, 0)                      # CRC failure: payload discarded
    for bad in (("abc", -1000, 0, 0, 1), ("zz", -1000, 0, 0, 1), ("", -1000, 0, 0, 1), ("00" * 256, -1000, 0, 0, 1),
                ("00", 10, 0, 0, 1), ("00", -1300, 0, 0, 1), ("00", -1000, 4000, 0, 1), ("00", -1000, 0, 600_000, 1),
                ("00", -1000, 0, 0, 2), ("00", True, 0, 0, 1), ("00", -1000, 0, 0), (b"00", -1000, 0, 0, 1)):
        b.handlers["rf_rx"](*bad)
    pk = radio.packets()
    assert len(pk) == 2 and radio.packets() == []
    assert pk[0] == link.RxPacket(cadu, clk.t, -110.5, -12.5, 812.0, True)
    assert (pk[1].data, pk[1].crc_ok, pk[1].rssi_dbm, pk[1].snr_db) == (b"", False, -127.0, -19.0)
    st = radio.snapshot()["stats"]
    assert (st["rx_packets"], st["rx_crc_fail"], st["rx_rejected"]) == (2, 1, 12)


def test_rx_queue_is_bounded_and_keeps_the_newest():
    b = FakeBridge()
    radio = sx.Sx126xBridge(b, {}, max_queue=3, poll_s=0.0)
    radio.attach()
    for i in range(5):
        b.handlers["rf_rx"](bytes([i]).hex(), -1000, 0, 0, 1)
    assert [p.data[0] for p in radio.packets()] == [2, 3, 4] and radio.stats["rx_overflow"] == 2


def test_status_events_and_config_push_after_reboot(sxb):
    radio, b, clk = sxb
    assert not radio.connected and radio.get_config() is None
    b.handlers["rf_evt"](STATUS.format(up=100))
    assert radio.connected
    cfg = radio.get_config()
    assert (cfg["channel"], cfg["air_rate_code"], cfg["freq_mhz"], cfg["lbt"]) == (23, 1, 433.125, True)
    assert cfg["rx_boost"] and cfg["rx_boost_verified"] and cfg["rssi_byte"] and cfg["rssi_noise_enable"]
    assert radio.read() == b""
    assert b.calls == [("rf_cfg", (23, 1, 0, 3))]                       # pushed once the MCU is up
    radio.read()
    assert len(b.calls) == 1
    b.handlers["rf_evt"](STATUS.format(up=3))                          # uptime went back: MCU rebooted
    radio.read()
    assert radio.stats["mcu_reboots"] == 1 and b.calls[-1] == ("rf_cfg", (23, 1, 0, 3))
    for line in ("C|1|263", "T|1|7480|90", "T|0|7480|90", "E|3|no TxDone before the deadline", "E|5|recovered"):
        b.handlers["rf_evt"](line)
    assert radio.last_cad == {"t": clk.t, "detected": True, "duration_ms": 263}
    assert radio.stats["tx_done"] == 1 and radio.stats["tx_failed"] == 1
    assert [e["name"] for e in radio.events] == ["TX_TIMEOUT", "RECOVERED"]
    for bad in ("S|x", STATUS.format(up=1).replace("|1|23|", "|9|23|"), "C|2|5", "E|99|x", "Q|1", 42, "S" * 300):
        b.handlers["rf_evt"](bad)
    assert radio.stats["evt_rejected"] == 7
    clk.t += sx.STATUS_STALE_S + 1
    assert not radio.connected and radio.get_config() is None


def test_uplink_queueing_retries_and_rejections(sxb):
    radio, b, clk = sxb
    cltu = bytes(range(90))
    b.replies["rf_tx"] = [0, 1]                                        # queue full, then accepted
    radio.write(cltu)
    assert b.calls[-1] == ("rf_tx", (cltu.hex(),)) and radio.snapshot()["tx_backlog"] == 1
    radio.read()
    assert radio.snapshot()["tx_backlog"] == 0 and radio.stats["tx_sent"] == 1
    n = len(b.calls)
    radio.write(bytes([0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x02]))           # E22 noise query: never radiated
    radio.write(bytes(sx.RF_TX_MAX_OCTETS + 1))                        # does not fit the MCU RPC buffer
    assert len(b.calls) == n and radio.stats["e22_queries_ignored"] == 1 and radio.stats["tx_rejected"] == 1
    b.replies["rf_tx"] = TimeoutError("router")
    radio.write(cltu)
    assert radio.stats["call_errors"] == 1 and radio.snapshot()["tx_backlog"] == 1
    b.replies["rf_tx"] = -1                                            # definitive rejection: not retried
    radio.read()
    assert radio.snapshot()["tx_backlog"] == 0 and radio.stats["tx_rejected"] == 2


def test_control_calls(sxb):
    radio, b, clk = sxb
    assert radio.configure(channel=24) == 1 and b.calls[-1] == ("rf_cfg", (24, -1, -1, -1))
    assert radio.settings.channel == 24
    assert radio.configure(rate_code=0, cad=False) == 1 and b.calls[-1] == ("rf_cfg", (-1, 0, -1, 2))
    with pytest.raises(ValueError):
        radio.configure(channel=84)
    b.replies["rf_noise"] = -1053
    assert radio.noise_dbm() == -105.3
    for r in (sx.NOISE_NA, 5, "x"):
        b.replies["rf_noise"] = r
        assert radio.noise_dbm() is None
    assert radio.cad() is True and b.calls[-1] == ("rf_cad", ())
    b.replies["rf_cad"] = 0
    assert radio.cad() is False


def test_radio_manager_drives_the_sx126x_backend(sxb):
    radio, b, clk = sxb
    rm = RadioManager(radio, None, clock=clk, sleep=clk.sleep)
    assert rm.model.name == SX126X and not rm.available
    b.handlers["rf_evt"](STATUS.format(up=10))
    assert rm.available and rm.read_config()["channel"] == 23
    b.replies["rf_noise"] = -1100
    scan = rm.survey(22, 24)
    assert scan["noise"] == [-110.0, -110.0, -110.0] and scan["quietest"] == 22 and scan["home_restored"]
    assert [c[1][0] for c in b.calls if c[0] == "rf_cfg"] == [22, 23, 24, 23]
    assert rm.set_power(1) and b.calls[-1] == ("rf_cfg", (-1, -1, 1, -1))
    assert rm.wor_transmit(b"\x01\x02") is True and b.calls[-1] == ("rf_wake", ("0102",))   # 32-symbol preamble
    b.replies["rf_wake"] = 0
    assert rm.wor_transmit(b"\x01") is False and rm.write_regs(5, b"\x01") is False
    with pytest.raises(ValueError):
        RadioManager(radio, None, model=E22_T33D)


# ===================================================== T-RF-03 (link_budget)
T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
STEP_S, PERIOD_S = 120, 2.0
ATTEN = [60, 70, 80, 90, 100]
OK_FRAMES = [60, 60, 57, 42, 0]                 # FER 0, 0, 5 %, 30 %, 100 % of 60 expected


def _archive(tmp_path, atten=ATTEN, ok=OK_FRAMES, ref_dbm=-50.0, bad=3):
    db = sqlite3.connect(tmp_path / "gds.sqlite")
    db.executescript(SCHEMA)
    rows = []
    for i, (a, n) in enumerate(zip(atten, ok, strict=True)):
        start = T0.timestamp() + i * STEP_S
        for k in range(n):
            rows.append((start + 0.5 + k * PERIOD_S, k & 0xFF, 0, 0, 0, 0, ref_dbm - a, 5.0 - i, 1, ""))
        for k in range(bad if 0 < n < 60 else 0):
            rows.append((start + 1.0 + k * PERIOD_S, None, None, None, None, -1, None, None, 0, "RS uncorrectable"))
    db.executemany("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO params VALUES (?,?,?,?,?,?)", (T0.timestamp() + 30, None, "RF", "frame_period_ms",
                                                           PERIOD_S * 1000, "OK"))
    db.commit()
    db.close()
    steps = tmp_path / "steps.csv"
    lines = ["start_utc,end_utc,atten_db,rate_code"]
    for i, a in enumerate(atten):
        s = T0 + timedelta(seconds=i * STEP_S)
        lines.append(f"{s.isoformat().replace('+00:00', 'Z')},{(s + timedelta(seconds=STEP_S)).isoformat()},{a},2")
    steps.write_text("\n".join(lines) + "\n")
    return steps


def _run(argv, **kw):
    out = []
    rc = lb.sensitivity(argv, out=out.append, **kw)
    return rc, "\n".join(out)


def test_sensitivity_subcommand_interpolates_the_10_percent_point(tmp_path):
    steps = _archive(tmp_path)
    rc, text = _run(["--archive", str(tmp_path), "--steps", str(steps), "--ref-dbm", "-50"])
    # P_rx = -50 - atten; FER 5 % at -130 dBm and 30 % at -140 dBm -> -130 + (0.05 / 0.25)(-10) = -132.0
    assert rc == 0, text
    assert '"E22-400T33D:2" = -132.0' in text and "[radio.sensitivity_measured]" in text
    assert "frame period 2.000 s" in text                                # from the archived RF packet
    # Without --ref-dbm the reference comes from the RSSI of the strongest error-free step.
    rc, text = _run(["--archive", str(tmp_path), "--steps", str(steps), "--model", "E22-400T37S"])
    assert rc == 0 and '"E22-400T37S:2" = -132.0' in text and "strongest error-free step" in text


def test_sensitivity_subcommand_refuses_without_a_crossing(tmp_path):
    steps = _archive(tmp_path, atten=ATTEN[:3], ok=OK_FRAMES[:3])
    rc, text = _run(["--archive", str(tmp_path), "--steps", str(steps), "--ref-dbm", "-50"])
    assert rc == lb.EXIT_REFUSED
    assert "REFUSED" in text and "never reached 10%" in text and "sensitivity_measured" not in text


def test_sensitivity_subcommand_rejects_bad_inputs(tmp_path):
    steps = _archive(tmp_path)
    bad = tmp_path / "bad.csv"
    bad.write_text("start,end,atten\n1,2,3\n")
    rc, text = _run(["--archive", str(tmp_path), "--steps", str(bad)])
    assert rc == lb.EXIT_NO_DATA and "header" in text
    bad.write_text("start_utc,end_utc,atten_db,rate_code\n100,50,10,2\n")
    assert _run(["--archive", str(tmp_path), "--steps", str(bad)])[0] == lb.EXIT_NO_DATA
    rc, text = _run(["--archive", str(tmp_path / "nowhere"), "--steps", str(steps)])
    assert rc == lb.EXIT_NO_DATA and "not found" in text


def test_threshold_crossing_rules():
    assert lb.threshold_crossing([(-120, 0.0), (-130, 0.2)]) == \
        (-125.0, "FER 0.0% at -120.0 dBm -> 20.0% at -130.0 dBm")
    v, why = lb.threshold_crossing([(-120, 0.0), (-125, 0.2), (-130, 0.05)])
    assert v == -122.5 and "not monotonic" in why
    assert lb.threshold_crossing([(-120, 0.5), (-130, 0.6)])[0] is None
    assert lb.threshold_crossing([(-120, 0.0)])[0] is None


def test_sensitivity_from_raw_cadu_archive(tmp_path):
    day = tmp_path / T0.strftime("%Y%m%d")
    day.mkdir()
    rec = bytearray()
    for k in range(30):                                                  # 30 good + 30 corrupted of 60
        cadu = bytearray(_cadu(k))
        if k % 2:
            cadu[10:60] = bytes(50)                                       # beyond RS correction
        rec += struct.pack(">qhH", int((T0.timestamp() + 0.5 + 2 * k) * 1e9), -120, len(cadu)) + cadu
    (day / "cadu.bin").write_bytes(bytes(rec))
    frames = lb.load_frames(tmp_path, T0.timestamp(), T0.timestamp() + 120, "cadu")
    assert len(frames) == 30 and sum(f.ok for f in frames) == 15 and frames[0].rssi == -120.0


def test_live_session_timestamps_steps(tmp_path):
    _archive(tmp_path)
    answers = iter(["60", "", "70", "", "x", "80", "", "90", "", "100", "", ""])
    clock_t = [T0.timestamp()]

    def fake_input(prompt):
        if "close" in prompt:
            clock_t[0] += STEP_S                                         # the step lasts 120 s
        return next(answers)

    rc, text = _run(["--archive", str(tmp_path), "--live", "--rate-code", "2", "--ref-dbm", "-50",
                     "--steps-out", str(tmp_path / "live.csv")],
                    input_fn=fake_input, clock=lambda: clock_t[0], sleep=lambda s: None)
    assert rc == 0, text
    assert "not a number" in text and '"E22-400T33D:2" = -132.0' in text
    assert len(lb.read_steps(tmp_path / "live.csv", E22_T33D)) == 5


def test_budget_subcommand_runs_for_every_model(capsys):
    for m in (E22_T33D, E22_T37S, SX126X):
        assert lb.main(["budget", "--model", m, "--rate-code", "1", "--all-rates"]) == 0
    out = capsys.readouterr().out
    assert "-140.0 dBm (computed)" in out and "-128.0 dBm (datasheet)" in out and "-126.0 dBm (datasheet)" in out
    assert lb.main(["--model", "nope"]) == lb.EXIT_NO_DATA


# ======================================================== retained behaviour
def test_framesync_extracts_radio_fault_reports():
    fs = FrameSynchronizer(rssi_byte=True)
    stream = bytes([0xFF, 0xFF, 0xFF, 0x01]) + _cadu(1) + b"\xA0" + b"\x11\x22" + \
        bytes([0xFF, 0xFF, 0xFF, 0x03]) + _cadu(2) + b"\xA0"
    out = fs.feed(stream, 100.0)
    kinds = [type(o) for o in out]
    assert kinds == [RadioFault, RawCadu, RadioFault, RawCadu]
    assert out[0].code == 1 and out[2].code == 3
    assert out[1].rssi_dbm == -(256 - 0xA0)
    assert fs.stats["radio_fault_reports"] == 2


def test_link_manager_never_changes_power_and_stops_at_2k4():
    lm = LinkManager(target_margin_db=6.0, period_s=0, holdoff_s=0, min_rate=0, max_rate=4)
    lm.mode = "AUTO"
    assert lm.min_rate == 2                                # codes 0-1 are aliases of 2.4k
    rx = {"windows": {"10m": {"rssi": {"n": 50, "p10": -125.0}}}, "frames": {"fer": 0.2}}
    rec = lm.evaluate(1000.0, rx, rate_code=2, power_code=0)
    assert rec is None and lm.recommendation["action"] == "NONE"       # nothing left to try
    rec = lm.evaluate(2000.0, rx, rate_code=4, power_code=0)
    assert rec["action"] == "LINKRATE" and rec["value"] == 3
    good = {"windows": {"10m": {"rssi": {"n": 50, "p10": -90.0}}}, "frames": {"fer": 0.0}}
    rec = lm.evaluate(3000.0, good, rate_code=4, power_code=0)
    assert rec is None or rec["action"] != "TXPWR"
