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
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import ClassVar

import pytest

from dssq import link
from dssq.archive import SCHEMA
from dssq.ccsds import CADU_LEN
from dssq.ccsds.tm import FHP_ONLY_IDLE_DATA, build_frame, encode_cadu
from dssq.framesync import FrameSynchronizer, RadioFault, RawCadu
from dssq.link import E22_T33D, E22_T37S, SX126X
from dssq.linkmgr import LinkManager
from dssq.radio import sx126x as sx
from dssq.radio.e22 import describe, regs_from_cfg, sanitize_reg1
from dssq.radioctl import RadioManager

ROOT = Path(__file__).resolve().parents[1]
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


def test_legacy_names_are_gone_and_the_model_tables_replace_them():
    # Phase B F-52: the default-model aliases were removed; callers use the model tables.
    for name in ("AIR_RATE_BPS", "SENSITIVITY_EST_DBM", "TX_POWER_DBM", "MIN_EFFECTIVE_RATE_CODE"):
        assert not hasattr(link, name)
    assert link.rate_bps(link.DEFAULT_MODEL, 2) == 2400 and link.power_dbm(link.DEFAULT_MODEL, 0) == 33.0
    assert link.get_model(link.DEFAULT_MODEL).min_effective_rate_code == 2
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


# ------------------------------- D-77: fault_log default and the configuration tool's mode-2 guard
def test_fault_log_defaults_to_on_for_the_t37s_only(tmp_path):
    from dssq.config import fault_log_default, load_config, merge, validate
    assert merge({"radio": {"model": E22_T37S}})["radio"]["fault_log"] is True
    for model in (E22_T33D, "auto", SX126X, "no-such-model"):
        assert merge({"radio": {"model": model}})["radio"]["fault_log"] is False
    assert merge({})["radio"]["fault_log"] is False and fault_log_default(None) is False
    assert merge({"radio": {"model": E22_T37S, "fault_log": False}})["radio"]["fault_log"] is False
    cfg = merge({"radio": {"model": E22_T37S}})
    assert validate(cfg) == [] and regs_from_cfg(cfg["radio"])[4] & 0x04
    # regs_from_cfg without the key: the same rule (on for the T37S, never on a T33D).
    assert regs_from_cfg({"model": E22_T37S})[4] & 0x04 and not regs_from_cfg({"model": E22_T33D})[4] & 0x04
    # station.toml names no fault_log: it follows the model there too, also through --model.
    assert load_config()["radio"]["fault_log"] is False
    assert load_config(radio_model=E22_T37S)["radio"]["fault_log"] is True
    toml = tmp_path / "station.toml"
    toml.write_text(f'[radio]\nmodel = "{E22_T37S}"\n')
    assert load_config(toml)["radio"]["fault_log"] is True
    toml.write_text(f'[radio]\nmodel = "{E22_T37S}"\nfault_log = false\n')
    assert load_config(toml)["radio"]["fault_log"] is False


class _FakeConfigPort:
    """SerialPort stand-in for the configuration tool: records what it would send."""
    opened: ClassVar[list["_FakeConfigPort"]] = []

    def __init__(self, path, baud, timeout_s=0.2):
        self.path, self.sent = path, []
        _FakeConfigPort.opened.append(self)

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.sent.append(bytes(data))

    def flush(self):
        pass

    def read(self, n, timeout_s=0.0):
        return b""                                   # no module answering

    def close(self):
        pass


def test_config_tool_sends_nothing_before_mode2_is_confirmed(monkeypatch):
    import io
    from dssq.radio import e22 as e22mod
    monkeypatch.setattr(e22mod, "SerialPort", _FakeConfigPort)
    _FakeConfigPort.opened = []
    monkeypatch.setattr(e22mod.sys, "stdin", io.StringIO(""))          # not a terminal: no prompt
    with pytest.raises(SystemExit, match="not confirmed: nothing sent"):
        e22mod.main(["--port", "/dev/e22-test", "--model", E22_T37S])
    assert _FakeConfigPort.opened == []

    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(e22mod.sys, "stdin", Tty(""))
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    with pytest.raises(SystemExit, match="not confirmed"):
        e22mod.main(["--port", "/dev/e22-test", "--model", E22_T33D])
    assert _FakeConfigPort.opened == []
    monkeypatch.setattr("builtins.input", lambda prompt="": " Mode2 ")
    with pytest.raises(SystemExit, match="no reply"):
        e22mod.main(["--port", "/dev/e22-test", "--model", E22_T33D])
    assert [p.sent for p in _FakeConfigPort.opened] == [[bytes([0xC1, 0x80, 0x07])]]
    _FakeConfigPort.opened = []
    monkeypatch.setattr(e22mod.sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="no reply"):
        e22mod.main(["--port", "/dev/e22-test", "--model", E22_T37S, "--mode2-confirmed"])
    assert [p.sent for p in _FakeConfigPort.opened] == [[bytes([0xC1, 0x80, 0x07])]]


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
    radio, b, _clk = sxb
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
    radio, b, _clk = sxb
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
    assert rm.wor_transmit(b"\x01\x02") is False and rm.off_home()      # not yet reported back on CH23
    b.handlers["rf_evt"](STATUS.format(up=11))                          # the MCU's status line: CH23
    assert rm.off_home() is None
    assert rm.wor_transmit(b"\x01\x02") is True and b.calls[-1] == ("rf_wake", ("0102",))   # 32-symbol preamble
    b.replies["rf_wake"] = 0
    assert rm.wor_transmit(b"\x01") is False and rm.write_regs(5, b"\x01") is False
    with pytest.raises(ValueError):
        RadioManager(radio, None, model=E22_T33D)


# ===================================================== T-RF-03 (link_budget)
T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
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
    rx = {"window_s": 600.0, "rssi": {"n": 50, "p10": -125.0}, "noise": {"n": 0}, "frames": 50, "lost": 12, "fer": 0.2}
    rec = lm.evaluate(1000.0, rx, rate_code=2, power_code=0)
    assert rec is None and lm.recommendation["action"] == "NONE"       # nothing left to try
    rec = lm.evaluate(2000.0, rx, rate_code=4, power_code=0)
    assert rec["action"] == "LINKRATE" and rec["value"] == 3
    good = {"window_s": 600.0, "rssi": {"n": 50, "p10": -90.0}, "noise": {"n": 5, "mean": -118.0}, "frames": 50,
            "lost": 0, "fer": 0.0}
    rec = lm.evaluate(3000.0, good, rate_code=4, power_code=0)
    assert rec is None or rec["action"] != "TXPWR"


def _lm_input(p10, noise=None, n=50, lost=0, window_s=600.0):
    return {"window_s": window_s, "rssi": {"n": n, "p10": p10},
            "noise": {"n": 5, "mean": noise} if noise is not None else {"n": 0}, "frames": n, "lost": lost,
            "fer": lost / (lost + n)}


def test_link_manager_margin_against_the_worst_case_and_the_noise_floor():
    """D-76: the margin is taken against the worst-case sensitivity (T33D 2.4k: -126 dBm, the
    datasheet "min"); a faster rate is never advised when the p10 RSSI is within 10 dB of the
    noise or no noise reading checks it; at the noise floor the margin is an upper bound that
    can still say "low"."""
    lm = LinkManager(target_margin_db=6.0, period_s=0, holdoff_s=0, model="E22-400T33D")
    lm.evaluate(1.0, _lm_input(-100.0, noise=-118.0), rate_code=2, power_code=0)
    rec = lm.recommendation
    assert (rec["sensitivity_dbm"], rec["sensitivity_basis"], rec["margin_p10_db"]) == (-126.0, "worst case", 26.0)
    assert rec["action"] == "LINKRATE" and rec["value"] == 3 and rec["noise_state"] == "SIGNAL"
    lm.evaluate(2.0, _lm_input(-100.0), rate_code=2, power_code=0)               # no noise reading
    assert lm.recommendation["action"] == "NONE" and "no noise reading" in lm.recommendation["why"]
    lm.evaluate(3.0, _lm_input(-110.0, noise=-118.0), rate_code=2, power_code=0)  # 8 dB above the noise
    rec = lm.recommendation
    assert rec["action"] == "NONE" and rec["noise_state"] == "NEAR_NOISE" and "less than 10 dB" in rec["why"]
    lm.evaluate(4.0, _lm_input(-121.0, noise=-122.0), rate_code=3, power_code=0)  # at the noise floor
    rec = lm.recommendation
    # the upper bound noise - S(code 3) = -122 - (-126 + 3.0) = 1 dB < target - 3: slower
    assert rec["noise_state"] == "NOISE_FLOOR" and rec["margin_p10_db"] == pytest.approx(1.0)
    assert rec["action"] == "LINKRATE" and rec["value"] == 2
    lm.evaluate(4.5, _lm_input(-117.0, noise=-118.0), rate_code=2, power_code=0)  # upper bound 8 dB: not low,
    assert lm.recommendation["action"] == "NONE"                                  # and never "high"
    lm.evaluate(5.0, _lm_input(-100.0, noise=-118.0, n=9), rate_code=2, power_code=0)
    assert lm.recommendation["why"] == "insufficient data (< 10 frames in 600 s)"
    assert lm.state("LINKRATE needs the Radio Control Unit")["executable"] is False
    assert lm.state()["executable"] is True and lm.state()["why_not"] is None


# ================================================== automatic frequency control (24.8)
# Limits recomputed here from the documents: SX1261/2 DS rev 2.2 Table 3-8 FERR_L = +-25 % of
# BW, at SF12 also +-50 ppm, the tighter applying; Table 6-1 SF12 demodulator SNR -20 dB.
FERR_SF12_62K5 = min(0.25 * 62_500.0, 50e-6 * F433)              # 15625 Hz
FERR_SF12_31K25 = min(0.25 * 31_250.0, 50e-6 * F433)             # 7812.5 Hz


class AfcSim:
    """Spacecraft carrier at nominal + delta Hz; the chip reports chip_sign x (received - LO)
    plus +-15 Hz deterministic jitter; a station stub answers rf_foff with `result`."""

    def __init__(self, afc, delta, chip_sign=1, result=1, code=1):
        self.afc, self.delta, self.chip_sign, self.result = afc, delta, chip_sign, result
        self.period = link.profile_airtime_s(code) + 1.0
        self.t, self.i = 1000.0, 0
        self.commands = []

    def packet(self, snr=0.0, crc_ok=True, err=None):
        self.t += self.period
        self.i += 1
        if err is None:
            err = self.chip_sign * (self.delta - self.afc.correction_hz) + 15.0 * math.sin(1.7 * self.i)
        self.apply(self.afc.on_packet(self.t, err, snr, crc_ok))
        self.apply(self.afc.tick(self.t + 0.1, False))

    def apply(self, cmd):
        if cmd is not None:
            assert isinstance(cmd, int) and abs(cmd) <= link.AFC_RPC_MAX_HZ
            self.commands.append(cmd)
            self.afc.confirm(cmd, self.result, self.t + 0.2)


def _steps(sim, start=0):
    seq = [start, *sim.commands]
    return [b - a for a, b in pairwise(seq)]


def test_afc_limits_follow_the_datasheet():
    afc = link.AfcLoop(12, 62_500.0, F433)
    assert afc.limit_hz == pytest.approx(FERR_SF12_62K5) == pytest.approx(15_625.0)
    assert afc.bound_hz == pytest.approx(0.8 * 15_625.0) and afc.slew_hz == pytest.approx(0.25 * 15_625.0)
    assert afc.deadband_hz == pytest.approx(max(50.0, 0.02 * 62_500.0)) and afc.snr_min_db == -17.0
    narrow = link.AfcLoop(12, 31_250.0, F433)
    assert narrow.limit_hz == pytest.approx(FERR_SF12_31K25) and narrow.deadband_hz == pytest.approx(625.0)
    assert link.AfcLoop(7, 125_000.0, F433).limit_hz == pytest.approx(31_250.0)    # SF7: BW rule only
    for bad in ({"sf": 6}, {"sign": 0}, {"bw_hz": -1.0}, {"sign": True}):
        with pytest.raises(ValueError):
            link.AfcLoop(**{"sf": 12, "bw_hz": 62_500.0, "freq_hz": F433, **bad})


@pytest.mark.parametrize("delta", [7000.0, -7000.0])
def test_afc_converges_from_7_khz_within_slew_and_bound(delta):
    afc = link.AfcLoop(12, 62_500.0, F433)
    sim = AfcSim(afc, delta)
    for _ in range(30):
        sim.packet()
    assert afc.state == "TRACK" and abs(afc.correction_hz - delta) <= afc.deadband_hz
    assert sim.commands[0] == round(math.copysign(0.25 * FERR_SF12_62K5, delta))    # first step slew-limited
    assert len(sim.commands) == 2                                                    # then straight to the target
    assert all(abs(s) <= afc.slew_hz + 0.5 for s in _steps(sim))
    assert afc.counters["steps"] == 2 and afc.counters["sign_flips"] == 0 and not afc.pop_events()
    snap = afc.snapshot()
    assert {"enabled", "correction_hz", "estimate_hz", "residual_hz", "sign", "state"} <= set(snap)
    assert snap["estimate_hz"] == pytest.approx(delta, abs=20) and snap["sign"] == 1
    assert snap["doppler_hz"] == pytest.approx(delta, abs=20)                         # static offset 0


def test_afc_hysteresis_deadband():
    afc = link.AfcLoop(12, 62_500.0, F433)
    sim = AfcSim(afc, 1000.0)                                                        # inside 1250 Hz
    for _ in range(30):
        sim.packet()
    assert sim.commands == [] and afc.state == "TRACK" and afc.correction_hz == 0
    sim.delta = 1400.0                                                               # just outside
    for _ in range(20):
        sim.packet()
    assert len(sim.commands) == 1 and abs(sim.commands[0] - 1400) < 30
    for _ in range(40):                                                              # jitter never chatters
        sim.packet()
    assert len(sim.commands) == 1


def test_afc_bound_holds_at_80_percent_of_ferr():
    afc = link.AfcLoop(12, 31_250.0, F433, airtime_s=link.profile_airtime_s(0))
    sim = AfcSim(afc, 7000.0, code=0)
    for _ in range(40):
        sim.packet()
    assert afc.correction_hz == int(0.8 * FERR_SF12_31K25) == 6250
    assert all(abs(s) <= 0.25 * FERR_SF12_31K25 + 0.5 for s in _steps(sim))
    events = afc.pop_events()
    assert sum("bound" in text for _, _, text in events) == 1                        # reported once


def test_afc_rejects_low_snr_and_invalid_packets():
    afc = link.AfcLoop(12, 62_500.0, F433)
    sim = AfcSim(afc, 7000.0)
    for snr in (-17.5, None, float("nan"), True):
        sim.packet(snr=snr)
    for err, crc in ((float("nan"), True), (31_251.0, True), (7000.0, False), (True, True)):
        sim.packet(err=err, crc_ok=crc)
    assert afc.on_packet(sim.t + 30.0, None, 0.0) is None                            # no estimate
    assert sim.commands == [] and afc.counters["accepted"] == 0
    assert afc.counters["rejected_snr"] == 4 and afc.counters["rejected_invalid"] == 5
    for _ in range(3):
        sim.packet(snr=-17.0)                                                        # exactly SNRmin + 3 dB
    assert sim.commands and afc.counters["accepted"] == 3


def test_afc_holds_through_los_then_returns_to_the_static_offset():
    afc = link.AfcLoop(12, 62_500.0, F433, static_offset_hz=200.0)
    assert afc.correction_hz == 200 and afc.state == "STATIC"
    sim = AfcSim(afc, 7000.0)
    for _ in range(30):
        sim.packet()
    held = afc.correction_hz
    t = sim.t + 10.0
    assert afc.tick(t, True) is None and afc.state == "HOLD"
    assert afc.tick(t + 599.0, True) is None and afc.correction_hz == held
    assert afc.tick(t + 300.0, False) is None and afc.state == "TRACK"               # signal back early
    assert afc.tick(t + 400.0, True) is None and afc.state == "HOLD"
    cmd = afc.tick(t + 400.0 + link.AFC_LOS_HOLD_S, True)
    assert cmd == 200 and afc.state == "STATIC"
    afc.confirm(cmd, 1, t + 1001.0)
    assert afc.correction_hz == 200 and afc.estimate_hz is None
    assert [lvl for _, lvl, _ in afc.pop_events()] == ["INFO"] * 4


def test_afc_wrong_sign_is_detected_once_and_corrected():
    afc = link.AfcLoop(12, 62_500.0, F433)
    sim = AfcSim(afc, 7000.0, chip_sign=-1)                     # chip convention opposite to the belief
    for _ in range(40):
        sim.packet()
    assert sim.commands[0] == -3906                             # the wrong-way step ...
    assert afc.sign == -1 and afc.counters["sign_flips"] == 1   # ... is detected and the sign inverted
    assert abs(afc.correction_hz - 7000) <= afc.deadband_hz and afc.state == "TRACK"
    (event,) = afc.pop_events()
    assert event[1] == "WARN" and "inverted" in event[2]
    assert afc.residual_hz(25.0) == -25.0


def test_afc_second_divergence_faults_back_to_static():
    afc = link.AfcLoop(12, 62_500.0, F433, static_offset_hz=-100.0)
    sim = AfcSim(afc, 7000.0, chip_sign=-1)
    for _ in range(40):
        if afc.counters["sign_flips"] == 1:
            sim.chip_sign = 1                                   # adversarial: the convention changes again
        sim.packet()
    assert afc.state == "FAULT" and afc.correction_hz == -100 and sim.commands[-1] == -100
    assert [lvl for _, lvl, _ in afc.pop_events()] == ["WARN", "ERROR"]
    n = len(sim.commands)
    for _ in range(5):
        sim.packet()
    assert len(sim.commands) == n                               # stopped until an operator reset
    afc.reset()
    assert afc.state == "STATIC" and afc.tick(sim.t + 1, False) is None


def test_afc_rpc_failures_timeouts_and_settling():
    afc = link.AfcLoop(12, 62_500.0, F433)
    sim = AfcSim(afc, 7000.0, result=-2)
    for _ in range(3):
        sim.packet()
    assert sim.commands == [3906] and afc.correction_hz == 0 and afc.counters["rpc_failures"] == 1
    assert "radio down" in afc.pop_events()[0][2]
    sim.result = 1
    sim.packet()                                                # retried after AFC_RETRY_S
    assert sim.commands == [3906, 3906] and afc.correction_hz == 3906
    cmd = afc.on_packet(sim.t + 1.0, 50.0, 0.0)                 # inside the settling time: ignored
    assert cmd is None and afc.counters["skipped_settling"] == 1
    lone = link.AfcLoop(12, 62_500.0, F433)
    cmds = [lone.on_packet(100.0 + 20 * i, 5000.0, 0.0) for i in range(3)]
    assert cmds[:2] == [None, None] and cmds[2] == 3906         # never confirmed ...
    assert lone.on_packet(125.0, 5000.0, 0.0) is None and lone.counters["skipped_settling"] == 1
    assert lone.tick(141.0 + link.AFC_CONFIRM_TIMEOUT_S, False) is None and lone.counters["rpc_failures"] == 1
    assert "no rf_foff confirmation" in lone.pop_events()[0][2]
    lone.confirm(1234, 1, 200.0)                                # unexpected confirmation
    assert lone.correction_hz == 0 and "unexpected" in lone.pop_events()[0][2]


def test_afc_from_settings_reconfigure_and_disabled():
    s = link.RadioSettings.from_config({"model": "SX126x", "air_rate": 1, "channel": 23, "freq_offset_cal_hz": 150.0})
    afc = link.AfcLoop.from_settings(s)
    assert afc.enabled and afc.static_hz == 150 and afc.limit_hz == pytest.approx(FERR_SF12_62K5)
    assert afc.settle_s == pytest.approx(link.profile_airtime_s(1) + link.AFC_SETTLE_MARGIN_S)
    sim = AfcSim(afc, 7000.0)
    for _ in range(30):
        sim.packet()
    cmd = afc.reconfigure(sim.t, 12, 31_250.0, F433)            # narrower profile: clamp to its bound
    assert cmd == 6250 and afc.state == "STATIC"
    afc.confirm(cmd, 1, sim.t + 0.1)
    assert afc.correction_hz == 6250 and afc.estimate_hz is None
    e22 = link.AfcLoop.from_settings(link.RadioSettings.from_config({"model": "E22-400T33D"}))
    assert e22.state == "DISABLED" and not e22.snapshot()["enabled"]
    assert e22.on_packet(1.0, 5000.0, 0.0) is None and e22.tick(2.0, True) is None
    clipped = link.AfcLoop(12, 31_250.0, F433, static_offset_hz=9000.0)
    assert clipped.static_hz == 6250 and "clipped" in clipped.pop_events()[0][2]


# ---- Phase B (WS4) ----
import contextlib  # noqa: E402
import csv  # noqa: E402
import errno  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

from dssq.config import load_config  # noqa: E402
from dssq.radio import e22  # noqa: E402
from dssq.radio.e22 import E22Serial, SerialPort  # noqa: E402

HOST_TEST = ROOT / "tools" / "host_test"


# ------------------------------------------- stdlib serial transport on a pseudo-terminal (F-08)
@pytest.fixture
def pty_pair():
    pty = pytest.importorskip("pty")
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    os.close(slave)              # SerialPort opens the device by name, like /dev/ttyUSB0
    yield master, path
    with contextlib.suppress(OSError):         # a test may have closed it ("unplugged")
        os.close(master)


def _drain(fd: int, n: int, timeout_s: float = 2.0) -> bytes:
    out, deadline = b"", time.monotonic() + timeout_s
    while len(out) < n and time.monotonic() < deadline:
        try:
            out += os.read(fd, n - len(out))
        except BlockingIOError:
            time.sleep(0.005)
    return out


def test_serial_port_raw_8n1_round_trip(pty_pair):
    termios = pytest.importorskip("termios")
    master, path = pty_pair
    with SerialPort(path, 9600, timeout_s=0.05) as port:
        attrs = termios.tcgetattr(port.fileno())
        assert attrs[4] == attrs[5] == termios.B9600
        assert attrs[2] & termios.CSIZE == termios.CS8 and not attrs[2] & (termios.PARENB | termios.CSTOPB)
        assert not attrs[3] & (termios.ICANON | termios.ECHO | termios.ISIG)
        assert not attrs[1] & termios.OPOST and not attrs[0] & (termios.IXON | termios.ICRNL)
        raw = bytes(range(256))               # every octet value passes untouched (no CR/LF mapping)
        os.write(master, raw)
        got = b""
        for _ in range(50):
            got += port.read(4096, timeout_s=0.1)
            if len(got) >= len(raw):
                break
        assert got == raw
        t0 = time.monotonic()
        assert port.read(timeout_s=0.1) == b""     # timeout, no data
        assert 0.08 <= time.monotonic() - t0 < 1.0
        port.write(b"\xc1\x00\x09\x0d\x0a")
        port.flush()
        assert _drain(master, 5) == b"\xc1\x00\x09\x0d\x0a"
        os.write(master, b"stale")
        time.sleep(0.05)
        port.reset_input_buffer()
        assert port.read(timeout_s=0.05) == b""
    assert not port.is_open
    port.close()                                   # idempotent
    with pytest.raises(OSError):
        port.read()


def test_serial_port_is_exclusive(pty_pair):
    _, path = pty_pair
    with SerialPort(path, 9600):
        with pytest.raises(OSError) as e:
            SerialPort(path, 9600)
        assert e.value.errno in (errno.EAGAIN, errno.EWOULDBLOCK) and path in str(e.value)
    SerialPort(path, 9600).close()                 # lock released on close


def test_serial_port_validation_and_errors(tmp_path, pty_pair):
    _, path = pty_pair
    for bad in (0, 9601, 62500, 230400, True, "9600"):
        with pytest.raises(ValueError):
            SerialPort(path, bad)
    for baud in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
        SerialPort(path, baud).close()
    with pytest.raises(OSError) as e:
        SerialPort(str(tmp_path / "ttyUSB9"), 9600)
    assert e.value.errno == errno.ENOENT and "ttyUSB9" in str(e.value) and "open" in str(e.value)
    plain = tmp_path / "file"
    plain.write_bytes(b"")
    with pytest.raises(OSError) as e:
        SerialPort(str(plain), 9600)
    assert e.value.errno == errno.ENOTTY
    with SerialPort(path, 9600) as port:
        with pytest.raises(ValueError):
            port.read(0)
        with pytest.raises(TypeError):
            port.write("text")


def test_e22_backend_survives_a_hang_up(pty_pair, monkeypatch):
    master, path = pty_pair
    monkeypatch.setattr(e22, "REOPEN_HOLDOFF_S", 0.0)
    radio = E22Serial(path)
    os.write(master, b"\x1a\xcf\xfc\x1d")
    got = b""
    for _ in range(20):
        got += radio.read()
        if got:
            break
    assert got == b"\x1a\xcf\xfc\x1d" and radio.connected
    radio.write(b"\xeb\x90")
    assert _drain(master, 2) == b"\xeb\x90"
    os.close(master)                               # the "USB-UART" disappears
    for _ in range(5):
        assert radio.read() == b""                 # never raises
    radio.write(b"\x00" * 10)                      # never raises; counted
    assert radio.faults >= 1 and radio.tx_dropped >= 10 and radio.last_error
    radio.close()
    with pytest.raises(OSError):
        E22Serial("/nonexistent/ttyUSB0")          # but a wrong port fails at start-up


# ------------------------------------- flight radio tables == dssq.link (F-54, contract 3.2 / 24.8)
@pytest.fixture(scope="module")
def radio_tables(tmp_path_factory):
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("radio_tables")
    subprocess.run(["make", "-s", "-C", str(HOST_TEST), "host_test"], check=True)
    subprocess.run([str(HOST_TEST / "host_test"), str(out)], check=True, stdout=subprocess.DEVNULL)
    path = out / "radio_tables.csv"
    if not path.exists():
        pytest.fail("host_test did not write radio_tables.csv")
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def test_flight_radio_tables_match_the_ground_link_model(radio_tables):
    assert list(radio_tables[0]) == ["model", "code", "bitrate_bps", "airtime_ms_236", "airtime_us_236", "sens_ddbm",
                                     "sens_source", "sens_saving_ddbm", "sens_saving_source", "ferr_limit_hz_ch23",
                                     "power_code_dbm"]
    assert sorted({(r["model"], int(r["code"])) for r in radio_tables}) == sorted(
        (m, c) for m in (E22_T33D, E22_T37S, SX126X) for c in range(8))
    f23 = link.channel_freq_mhz(23) * 1e6
    for r in radio_tables:
        m, c = r["model"], int(r["code"])
        where = f"{m} code {c}"
        assert int(r["bitrate_bps"]) == round(link.rate_bps(m, c)), where
        assert int(r["airtime_ms_236"]) == round(link.busy_s(m, c) * 1000), where       # frame-period basis
        boost, saving = link.sensitivity(m, c, None, True), link.sensitivity(m, c, None, False)
        assert (int(r["sens_ddbm"]), r["sens_source"]) == (round(boost[0] * 10), boost[1]), where
        assert (int(r["sens_saving_ddbm"]), r["sens_saving_source"]) == (round(saving[0] * 10), saving[1]), where
        if link.get_model(m).packet_mode:
            p = link.SX126X_PROFILES[c]
            assert int(r["airtime_us_236"]) == link.lora_airtime_us(236, p.sf, p.bw_hz), where
            assert float(r["ferr_limit_hz_ch23"]) == pytest.approx(link.lora_max_freq_offset_hz(p.sf, p.bw_hz, f23),
                                                                   abs=0.005), where
        else:
            assert int(r["airtime_us_236"]) == 0 and float(r["ferr_limit_hz_ch23"]) == 0.0, where
        if c <= 3:
            assert int(r["power_code_dbm"]) == round(link.power_dbm(m, c)), where
        else:
            assert r["power_code_dbm"] == "", where


def test_busy_time_model_values():
    # flight frame-period basis (contract 3.2): E22 = UART at 9600 baud + air at the code's rate x 1.3;
    # SX126x = the LoRa time on air of the 236-octet CADU, whole milliseconds rounded up.
    assert [link.busy_s(E22_T33D, c) for c in range(8)] == [1.267, 1.267, 1.267, 0.756, 0.5, 0.372, 0.308, 0.284]
    assert [link.busy_s(SX126X, c) for c in range(8)] == [34.636, 17.318, 8.659, 4.74, 2.165, 1.185, 0.665, 0.373]
    assert [link.lora_airtime_us(236, p.sf, p.bw_hz) for p in link.SX126X_PROFILES.values()] == \
        [round(ms * 1000) for ms in AIRTIME_236_MS]


# --------------------------------------------- GDS AFC glue: rf_foff(hz) -> 1 | -1 | -2 | None (F-32)
class FoffStation:
    """rf_foff test double (contract 24.8): scripted results, or an exception (link failure)."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def rf_foff(self, hz):
        self.calls.append(hz)
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def close(self):
        return None


@pytest.fixture
def sx_runner(monkeypatch, tmp_path):
    from dssq.gds import Runner
    clk = Clock()
    clk.t = 1_800_000_000.0
    monkeypatch.setattr(time, "time", clk)
    cfg = load_config()
    cfg["radio"].update(model="SX126x", air_rate=1)
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    cfg["sdls"]["key_file"] = str(tmp_path / "no_key.hex")
    runner = Runner(cfg, sim=True, web=False, archive=False)
    yield runner, clk
    runner.close()


def _afc_wants(runner, clk, ferr=5000.0, max_packets=10):
    """Packets with a constant 5 kHz error until the AFC queues a correction."""
    gs = runner.gs
    for _ in range(max_packets):
        clk.t += 20.0
        gs.ingest_packet(link.RxPacket(b"", clk.t, -110.0, 0.0, ferr, True))
        if gs.afc_out:
            return list(gs.afc_out)
    raise AssertionError("the AFC queued no correction")


def _afc_events(gs):
    return [e["text"] for e in gs.events if e["source"] == "AFC"]


def test_gds_afc_glue_maps_every_rf_foff_result(sx_runner):
    runner, clk = sx_runner
    gs = runner.gs
    assert gs.afc.enabled and gs.afc.correction_hz == 0
    station = FoffStation([-1, -2, None, TimeoutError("router"), 1])
    runner.radio = station
    expect = ["out of range", "radio down", "no answer (bridge)", "no answer (bridge)"]
    for text in expect:
        assert _afc_wants(runner, clk) == [3906]                  # one slew step: 25 % of FERR_L (SF12 / 62.5 kHz)
        runner._stage_afc()
        assert gs.afc.correction_hz == 0 and f"rf_foff(3906) failed: {text}" in _afc_events(gs)[-1]
    assert gs.afc.counters["rpc_failures"] == 4
    _afc_wants(runner, clk)
    runner._stage_afc()
    assert gs.afc.correction_hz == 3906 and station.calls == [3906] * 5 and not gs.afc_out
    assert gs.snapshot()["rx"]["rf"]["afc"]["correction_hz"] == 3906


def test_gds_afc_glue_without_rf_foff(sx_runner):
    runner, clk = sx_runner
    gs = runner.gs

    class NoFoff:
        def close(self):
            return None

    runner.radio = NoFoff()
    for _ in range(2):
        _afc_wants(runner, clk)
        runner._stage_afc()
    warns = [t for t in _afc_events(gs) if "provides no rf_foff" in t]
    assert len(warns) == 1                                            # reported once
    assert gs.afc.correction_hz == 0 and gs.afc.counters["rpc_failures"] == 2
    runner.radio = None                                               # radio unavailable: confirmed, no extra WARN
    _afc_wants(runner, clk)
    runner._stage_afc()
    assert gs.afc.counters["rpc_failures"] == 3 and len([t for t in _afc_events(gs) if "provides no" in t]) == 1


def test_gds_afc_uses_set_freq_offset_of_the_committed_backend(sx_runner):
    runner, clk = sx_runner

    class Backend:                       # Sx126xBridge names the rf_foff call set_freq_offset
        def __init__(self):
            self.calls = []

        def set_freq_offset(self, hz):
            self.calls.append(hz)
            return 1

        def close(self):
            return None

    runner.radio = b = Backend()
    _afc_wants(runner, clk)
    runner._stage_afc()
    assert b.calls == [3906] and runner.gs.afc.correction_hz == 3906


# ------------------------------------------------------------ configuration checks (F-10, F-31)
def test_config_validation_rules():
    from dssq.config import DEFAULTS, merge, validate
    assert validate(merge({})) == [] and merge({}) == DEFAULTS
    cfg = merge({"radio": {"uart_code": 4}})
    with pytest.raises(ValueError, match="uart_code"):
        validate(cfg)
    assert validate(merge({"radio": {"model": "SX126x", "uart_code": 4}})) == []      # E22 models only
    for bad in ({"radio": {"sx126x": {"afc": "yes"}}}, {"radio": {"freq_offset_calibrated": 1}},
                {"science": {"field_ut": -3.0}}, {"radio": {"model": "E22-400T30D"}}):
        with pytest.raises(ValueError):
            validate(merge(bad))


# ---- Phase B (WS6b) ----
from dssq.config import merge, validate  # noqa: E402

STATUS20 = "S|DSSQ-IO 3.1 SX126x|SX1268|{state}|23|1|0|{flags}|150|12|1|0|4|0|2|0|0|1|0|{up}|{foff}"


def _status(state=1, flags=7, up=100, foff=0):
    return STATUS20.format(state=state, flags=flags, up=up, foff=foff)


# ------------------------------------------------- 20-field status line, flags b3..b5
def test_status_line_with_frequency_offset_and_flags(sxb):
    radio, b, _clk = sxb
    assert len(_status().split("|")) == 1 + sx.STATUS_BASE_LEN == 21 and len(sx.STATUS_FIELDS) == 22
    b.handlers["rf_evt"](_status(flags=7 | 8 | 16 | 32, foff=-1234))
    st = radio.mcu
    assert st["freq_offset_hz"] == -1234 and st["state_name"] == "RX" and st["uptime_s"] == 100
    assert st["tcxo_fallback"] and st["cfg_pending"] and st["calib_error"]          # b3, b4, b5
    assert st["cad_before_tx"] and st["rx_boost"] and st["rx_boost_verified"]       # b0, b1, b2
    cfg = radio.get_config()
    assert (cfg["freq_offset_hz"], cfg["tcxo_fallback"], cfg["cfg_pending"], cfg["calib_error"]) == \
        (-1234, True, True, True)
    b.handlers["rf_evt"](_status(flags=7, foff=50_000))
    assert not (radio.mcu["tcxo_fallback"] or radio.mcu["cfg_pending"] or radio.mcu["calib_error"])
    b.handlers["rf_evt"](STATUS.format(up=101))                       # 19 fields: an older sketch
    assert radio.mcu["freq_offset_hz"] is None and radio.mcu["uptime_s"] == 101
    n = radio.stats["evt_rejected"]
    for bad in (_status(flags=64), _status(foff=50_001), _status(foff=-50_001), _status(foff="1.5"),
                _status(foff="+5"), _status() + "|7"):
        b.handlers["rf_evt"](bad)
    assert radio.stats["evt_rejected"] == n + 6


# ------------------------------- Phase C: compiled chip / TCXO check (I3), uptime wrap
STATUS22 = ("S|DSSQ-IO 3.1 SX126x|SX1268|1|23|1|0|7|150|{rx_ok}|1|0|4|0|2|0|0|1|0|{up}|0|{chip}|{tcxo}")


def _status22(up=100, chip=1268, tcxo=1800, rx_ok=12):
    return STATUS22.format(up=up, chip=chip, tcxo=tcxo, rx_ok=rx_ok)


def _sx_radio(chip="SX1268", tcxo_v=1.8):
    clk = Clock()
    b = FakeBridge()
    radio = sx.Sx126xBridge(b, {"model": "SX126x", "air_rate": 1, "power": 0, "channel": 23,
                                "sx126x": {"chip": chip, "tcxo_v": tcxo_v}}, clock=clk, poll_s=0.0)
    radio.attach()
    return radio, b


def test_status_chip_and_tcxo_checked_against_station_toml():
    radio, b = _sx_radio()
    b.handlers["rf_evt"](_status22())
    st = radio.mcu
    assert (st["chip_id"], st["tcxo_mv"], st["hw_mismatch"]) == (1268, 1800, None) and not radio.events
    assert radio.get_config()["hw_mismatch"] is None and radio.snapshot()["hw_mismatch"] is None
    b.handlers["rf_evt"](_status(foff=0))                             # 20 fields (older sketch): no check
    assert (radio.mcu["chip_id"], radio.mcu["tcxo_mv"], radio.hw_mismatch) == (None, None, None)

    radio, b = _sx_radio(chip="SX1262", tcxo_v=0)                    # TOML edited, sketch not rebuilt
    b.handlers["rf_evt"](_status22())
    text = radio.hw_mismatch
    assert "SX1268 (DSSQ_SX_CHIP)" in text and "chip = SX1262" in text and "1800 mV" in text
    assert radio.get_config()["hw_mismatch"] == text == radio.snapshot()["hw_mismatch"]
    b.handlers["rf_evt"](_status22(up=105))
    assert [e["name"] for e in radio.events] == ["HW_MISMATCH"]       # raised once, not per status line
    radio.expected_chip, radio.expected_tcxo_mv = 1268, 1800
    b.handlers["rf_evt"](_status22(up=110))
    assert radio.hw_mismatch is None and radio.mcu["hw_mismatch"] is None
    n = radio.stats["evt_rejected"]
    for bad in (_status22(chip=12), _status22(tcxo=3301), _status22(tcxo=-1), _status22() + "|0",
                _status22().rsplit("|", 1)[0]):                       # 21 fields
        b.handlers["rf_evt"](bad)
    assert radio.stats["evt_rejected"] == n + 5


def test_uptime_wrap_after_49_7_days_is_not_a_reboot():
    radio, b = _sx_radio()
    wrap = (1 << 32) // 1000                                          # millis() / 1000 wraps at 4294967
    b.handlers["rf_evt"](_status22(up=wrap - 2))
    radio.read()
    pushes = radio.stats["config_pushes"]
    b.handlers["rf_evt"](_status22(up=3, rx_ok=13))                   # wrapped, counters kept going
    radio.read()
    assert radio.stats["mcu_reboots"] == 0 and radio.stats["config_pushes"] == pushes
    b.handlers["rf_evt"](_status22(up=wrap - 1))
    b.handlers["rf_evt"](_status22(up=2, rx_ok=0))                    # near the wrap, but counters reset
    assert radio.stats["mcu_reboots"] == 1
    b.handlers["rf_evt"](_status22(up=9000, rx_ok=5))
    b.handlers["rf_evt"](_status22(up=4, rx_ok=5))                    # far from the wrap: a reboot
    assert radio.stats["mcu_reboots"] == 2


# ---------------------------------------------- set_freq_offset / rf_foff (post-G1 check)
def test_rf_foff_returns_the_mcu_result_and_none_on_a_link_failure(sxb):
    radio, b, _clk = sxb
    assert radio.rf_foff.__doc__ and callable(radio.rf_foff)          # contract name (24.8)
    b.replies["rf_foff"] = [1, -1, -2, TimeoutError("router"), ConnectionError("down"), "x", True, 1]
    assert radio.rf_foff(1500) == sx.FOFF_APPLIED and radio.freq_offset_hz == 1500
    assert radio.rf_foff(-60) == sx.FOFF_RANGE and radio.freq_offset_hz == 1500    # not applied
    assert radio.set_freq_offset(700) == sx.FOFF_RADIO_DOWN and radio.freq_offset_hz == 1500
    for _ in range(4):                                                 # exceptions / odd replies
        assert radio.rf_foff(700) is None and radio.freq_offset_hz == 1500
    assert radio.set_freq_offset(-50_000) == 1 and radio.freq_offset_hz == -50_000
    assert [c for c in b.calls if c[0] == "rf_foff"] == [("rf_foff", (h,)) for h in
                                                          (1500, -60, 700, 700, 700, 700, 700, -50_000)]
    st = radio.stats
    assert (st["foff_set"], st["foff_failed"], st["call_errors"]) == (2, 2, 4)
    for bad in (50_001, -50_001, 1.0, True, "5"):
        with pytest.raises(ValueError):
            radio.rf_foff(bad)
    assert len([c for c in b.calls if c[0] == "rf_foff"]) == 8        # nothing sent for a bad value


# ------------------------------------------------- offset re-sync gating (Sx126xBridge)
def _foff_calls(b):
    return [c[1][0] for c in b.calls if c[0] == "rf_foff"]


def test_offset_resync_is_gated_on_a_fresh_idle_receiving_status():
    clk = Clock()
    b = FakeBridge()
    radio = sx.Sx126xBridge(b, {"air_rate": 1}, clock=clk, poll_s=0.0, freq_offset_hz=800)
    radio.attach()
    radio.read()
    assert _foff_calls(b) == [] and b.calls == []                      # MCU not heard yet
    b.handlers["rf_evt"](_status(foff=800))                            # station already tuned
    radio.read()
    assert _foff_calls(b) == [] and b.calls[-1][0] == "rf_cfg"         # config push only
    clk.t += 1
    b.handlers["rf_evt"](_status(foff=0, up=101))                      # e.g. MCU lost the value
    radio.read()
    assert _foff_calls(b) == [800] and radio.stats["foff_resyncs"] == 1
    clk.t += 1
    b.handlers["rf_evt"](_status(foff=0, up=102))
    radio.read()
    assert _foff_calls(b) == [800]                                     # paced: PUSH_RETRY_S
    for state, flags in ((3, 7), (2, 7), (4, 7), (0, 7), (1, 7 | 16)):  # TX, CAD, RESET, OFF, change pending
        clk.t += sx.PUSH_RETRY_S + 0.1
        b.handlers["rf_evt"](_status(state=state, flags=flags, foff=0, up=int(clk.t - 999_000)))
        radio.read()
    assert _foff_calls(b) == [800]
    clk.t += sx.PUSH_RETRY_S + 0.1
    b.handlers["rf_evt"](STATUS.format(up=int(clk.t - 999_000)))       # 19-field status: offset unknown
    radio.read()
    assert _foff_calls(b) == [800]
    b.handlers["rf_evt"](_status(foff=0, up=int(clk.t - 999_000) + 1))
    clk.t += sx.STATUS_STALE_S                                         # that status is now stale
    radio.read()
    assert _foff_calls(b) == [800]
    b.replies["rf_foff"] = 1
    assert radio.set_freq_offset(900) == 1 and _foff_calls(b) == [800, 900]
    clk.t += 1.0                                                       # within FOFF_SETTLE_S of the reply
    b.handlers["rf_evt"](_status(foff=800, up=int(clk.t - 999_000)))   # may predate the change
    radio.read()
    assert _foff_calls(b) == [800, 900]
    clk.t += sx.PUSH_RETRY_S
    b.handlers["rf_evt"](_status(foff=800, up=int(clk.t - 999_000)))   # settled and still wrong
    radio.read()
    assert _foff_calls(b) == [800, 900, 900] and radio.stats["foff_resyncs"] == 2


# ------------------------------------------- RS rescue: CRC-failed CADU-length payloads (F-40)
def test_parse_rf_rx_keeps_only_cadu_length_payloads_with_a_crc_error():
    cadu = _cadu(9)
    p = sx.parse_rf_rx((cadu.hex(), -1200, -1500, 300, 0), 5.0)
    assert p is not None and p.data == cadu and p.crc_ok is False
    short = sx.parse_rf_rx((cadu[:100].hex(), -1200, -1500, 300, 0), 5.0)
    assert short is not None and short.data == b"" and short.crc_ok is False        # not a frame: dropped
    longer = sx.parse_rf_rx(((cadu + b"\x00").hex(), -1200, -1500, 300, 0), 5.0)
    assert longer is None                       # > RF_RX_MAX_OCTETS (236): the station never forwards it
    ok = sx.parse_rf_rx((cadu[:100].hex(), -1200, -1500, 300, 1), 5.0)
    assert ok is not None and ok.data == cadu[:100] and ok.crc_ok                  # good packets: any length


def _corrupt(cadu: bytes, n: int) -> bytes:
    b = bytearray(cadu)
    for i in range(n):
        b[4 + 7 * i] ^= 0x5A                                           # codeblock octets, ASM intact
    return bytes(b)


def test_gds_rs_rescue_through_the_real_bridge(sx_runner):
    runner, clk = sx_runner
    gs = runner.gs
    b = FakeBridge()
    radio = sx.Sx126xBridge(b, {"model": "SX126x", "air_rate": 1}, clock=clk, poll_s=0.0)
    radio.attach()
    good, fixable, broken = _cadu(1), _corrupt(_cadu(2), 6), _corrupt(_cadu(3), 30)
    b.handlers["rf_rx"](good.hex(), -1100, 500, 100, 1)
    b.handlers["rf_rx"](fixable.hex(), -1250, -1700, 100, 0)          # LoRa CRC failed, RS corrects 6
    b.handlers["rf_rx"](broken.hex(), -1260, -1800, 100, 0)           # beyond RS t = 16: dropped
    b.handlers["rf_rx"]("", -1270, -1900, 100, 0)                     # non-CADU CRC failure (MCU sent "")
    pk = radio.packets()
    assert [len(p.data) for p in pk] == [236, 236, 236, 0] and [p.crc_ok for p in pk] == [True, False, False, False]
    before, afc_invalid = gs.rx.codeblocks, gs.afc.counters["rejected_invalid"]
    for p in pk:
        clk.t += 20.0
        gs.ingest_packet(p)
    dec = gs.snapshot()["rx"]["decoder"]
    assert (dec["rs_rescued"], dec["rs_rescue_failed"]) == (1, 1)
    assert gs.rx.codeblocks == before + 3                             # the empty payload never reaches RS
    assert gs.rx.packets_crc_fail == 3 and gs.rx.packets_ok >= 1
    assert gs.afc.counters["rejected_invalid"] == afc_invalid + 3     # CRC-failed packets never steer the AFC


# --------------------------- post-G1 rf_foff check: the WS4 AFC glue with the real class
def test_gds_afc_glue_with_the_real_sx126x_bridge(sx_runner):
    runner, clk = sx_runner
    gs = runner.gs
    b = FakeBridge()
    b.replies["rf_foff"] = [-1, -2, TimeoutError("router"), "garbage", 1]
    radio = sx.Sx126xBridge(b, {"model": "SX126x", "air_rate": 1}, clock=clk, poll_s=0.0)
    runner.radio = radio
    for text in ("out of range", "radio down", "no answer (bridge)", "no answer (bridge)"):
        assert _afc_wants(runner, clk) == [3906]
        runner._stage_afc()
        assert gs.afc.correction_hz == 0 and f"rf_foff(3906) failed: {text}" in _afc_events(gs)[-1]
    assert gs.afc.counters["rpc_failures"] == 4
    _afc_wants(runner, clk)
    runner._stage_afc()
    assert gs.afc.correction_hz == 3906 and radio.freq_offset_hz == 3906
    assert _foff_calls(b) == [3906] * 5 and radio.stats["foff_set"] == 1


# ---------------------------------------- radio model without code edits (contract 33)
def test_absent_or_auto_model_follows_the_spacecraft_and_explicit_stays():
    assert merge({})["radio"]["model"] == link.MODEL_AUTO and validate(merge({})) == []
    for name in ("auto", "AUTO", " Auto "):
        assert validate(merge({"radio": {"model": name}})) == []
    for bad in ("", "E22-400T30D", 3):
        with pytest.raises(ValueError):
            validate(merge({"radio": {"model": bad}}))
    s = link.RadioSettings.from_config(merge({})["radio"])
    assert s.model_auto and s.model.name == E22_T33D and link.describe(s)["model_auto"] is True
    assert not link.RadioSettings.from_config(load_config()["radio"]).model_auto     # station.toml: explicit
    assert link.is_auto_model(None) and link.is_auto_model("auto") and not link.is_auto_model(E22_T37S)
    assert link.get_model("auto") is link.get_model(None) is link.MODELS[E22_T33D]
    # The station's own E22 registers in auto mode: T33D rules (REG1 b2 never written).
    assert regs_from_cfg({**merge({})["radio"], "fault_log": True})[4] & 0x04 == 0


def _gds(monkeypatch, tmp_path, model):
    from dssq.gds import Runner
    clk = Clock()
    clk.t = 1_800_000_000.0
    monkeypatch.setattr(time, "time", clk)
    cfg = load_config()
    if model is None:
        del cfg["radio"]["model"]
        cfg = merge(cfg)                                               # absent key -> default "auto"
    else:
        cfg["radio"]["model"] = model
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    cfg["sdls"]["key_file"] = str(tmp_path / "no_key.hex")
    return Runner(cfg, sim=True, web=False, archive=False), clk


def _radio_events(gs):
    return [(e["level"], e["text"]) for e in gs.events if e["source"] == "RADIO"]


@pytest.mark.parametrize("model", [None, "auto"])
def test_gds_follows_the_reported_e22_model_in_auto_mode(monkeypatch, tmp_path, model):
    runner, clk = _gds(monkeypatch, tmp_path, model)
    try:
        gs = runner.gs
        assert gs.settings.model_auto and gs.settings.model.name == E22_T33D
        gs._on_rf({"radio_model": 1, "air_rate_code": 2}, clk.t)     # same model: nothing to do
        assert gs.settings.model.name == E22_T33D and not _radio_events(gs)
        gs._on_rf({"radio_model": 2, "air_rate_code": 2}, clk.t)     # spacecraft strapped to the T37S
        assert gs.settings.model.name == E22_T37S and gs.rx.settings.model.name == E22_T37S
        assert gs.link_mgr.model.name == E22_T37S and not gs.model_mismatch
        ev = _radio_events(gs)
        assert len(ev) == 1 and ev[0][0] == "INFO" and "following the spacecraft's radio model E22-400T37S" in ev[0][1]
        snap = gs.snapshot()["radio"]
        assert (snap["model"], snap["model_auto"], snap["power_dbm"], snap["sensitivity_dbm"]) == \
            (E22_T37S, True, 37.0, -126.0)
        gs._on_rf({"radio_model": 2, "air_rate_code": 2}, clk.t)
        assert len(_radio_events(gs)) == 1                            # no repeat while unchanged
        gs._on_rf({"radio_model": 3, "air_rate_code": 1}, clk.t)     # an SX126x spacecraft: not followed
        assert gs.settings.model.name == E22_T37S and gs.model_mismatch
        assert _radio_events(gs)[-1][0] == "WARN" and "SX126x" in _radio_events(gs)[-1][1]
        gs._on_rf({"radio_model": 1, "air_rate_code": 2}, clk.t)     # strap removed again
        assert gs.settings.model.name == E22_T33D and not gs.model_mismatch
        assert gs.snapshot()["radio"]["power_dbm"] == 33.0
    finally:
        runner.close()


def test_gds_with_an_explicit_model_never_follows(monkeypatch, tmp_path):
    runner, clk = _gds(monkeypatch, tmp_path, E22_T33D)
    try:
        gs = runner.gs
        assert not gs.settings.model_auto
        gs._on_rf({"radio_model": 2, "air_rate_code": 2}, clk.t)
        assert gs.settings.model.name == E22_T33D and gs.model_mismatch
        lvl, text = _radio_events(gs)[-1]
        assert lvl == "WARN" and "E22-400T37S" in text and "[radio] model" in text
        assert gs.snapshot()["radio"]["model_auto"] is False
    finally:
        runner.close()


# ---------------------------------------- Phase D gate: UDP TC interface is loopback only
def test_udp_tc_listener_is_loopback_only(tmp_path):
    """[interop] tc_frames_udp_listen radiates frames without authentication or hazardous
    CONFIRM (docs/17 T16): only 127.0.0.0/8 or [::1] are accepted, by validate() and again by
    the Runner before anything starts."""
    import socket
    import threading
    import time

    from test_end_to_end import _config

    from dssq.config import parse_udp_listen
    from dssq.gds import Runner
    for ok, want in (("127.0.0.1:10025", (socket.AF_INET, "127.0.0.1", 10025)),
                     ("127.4.5.6:1", (socket.AF_INET, "127.4.5.6", 1)),
                     ("[::1]:65535", (socket.AF_INET6, "::1", 65535))):
        assert parse_udp_listen(ok) == want
        validate(merge({"interop": {"tc_frames_udp_listen": ok}}))
    validate(merge({"interop": {"tc_frames_udp_listen": ""}}))                    # off (default)
    for bad in ("0.0.0.0:10025", "192.168.1.10:10025", "10.0.0.1:1", "[::]:10025", "[fe80::1]:10025",
                "[::ffff:192.168.1.1]:5", "localhost:10025", "example.com:10025", "127.0.0.1:0", "127.0.0.1:65536",
                "127.0.0.1", "[::1]", "999.0.0.1:5", " 127.0.0.1:10025", "127.0.0.1:10025 ", 10025):
        with pytest.raises(ValueError, match="tc_frames_udp_listen"):
            validate(merge({"interop": {"tc_frames_udp_listen": bad}}))
    cfg = _config(tmp_path)
    cfg["interop"]["tc_frames_udp_listen"] = "0.0.0.0:10025"                    # validate() bypassed
    before = threading.active_count()
    with pytest.raises(ValueError, match="loopback"):
        Runner(cfg, sim=True, web=False, archive=False)
    assert threading.active_count() == before                                    # nothing was started
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    cfg["interop"]["tc_frames_udp_listen"] = f"127.0.0.1:{port}"
    r = Runner(cfg, sim=True, web=False, archive=False)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
            tx.sendto(bytes(10), ("127.0.0.1", port))
        deadline = time.monotonic() + 5.0
        while not r.gs.external_tc and time.monotonic() < deadline:            # bounded wait
            time.sleep(0.01)
        assert list(r.gs.external_tc) == [bytes(10)]
    finally:
        r.close()


# ------------------------------------------- Phase D gate: SX126x receive bound, rf_cfg results
def test_rf_rx_bound_is_one_cadu_and_t_lines_follow_the_tx_slot(sxb):
    radio, b, _clk = sxb
    assert sx.RF_RX_MAX_OCTETS == link.CADU_OCTETS == 236
    b.handlers["rf_rx"]("00" * 236, -1000, 0, 0, 1)
    b.handlers["rf_rx"]("00" * 237, -1000, 0, 0, 1)                    # the station never forwards this
    pk = radio.packets()
    assert len(pk) == 1 and len(pk[0].data) == 236 and radio.snapshot()["stats"]["rx_rejected"] == 1
    b.handlers["rf_evt"](f"T|1|4321|{sx.RF_TX_MAX_OCTETS}")            # the longest TX slot (sketch kRfTxMaxOctets)
    assert radio.last_tx["octets"] == sx.RF_TX_MAX_OCTETS == 112 and radio.last_tx["airtime_ms"] == 4321
    n = radio.stats["evt_rejected"]
    b.handlers["rf_evt"](f"T|1|4321|{sx.RF_TX_MAX_OCTETS + 1}")
    assert radio.stats["evt_rejected"] == n + 1


def test_rf_cfg_queued_is_a_success_and_the_survey_takes_no_stale_sample():
    """Station sketch rf_cfg_exec: 1 applied, 2 queued (applied when the radio is idle -
    accepted), -1 range, -2 MCU busy (nothing changed). A queued channel change is not yet
    in effect, so the survey takes no noise sample on it."""
    class Radio:
        packet_mode = True
        connected = True

        def __init__(self, rc):
            self.rc = rc
            self.calls = []

        def configure(self, **kw):
            self.calls.append(kw)
            return self.rc

        def get_config(self):
            return {"channel": 23}

        def noise_dbm(self):
            return -110.0

    for rc, ok in ((1, True), (2, True), (-1, False), (-2, False), (None, False), (True, False)):
        rm = RadioManager(Radio(rc), None, sleep=lambda s: None)
        assert rm.set_channel(20) is ok and rm.set_air_rate(3) is ok and rm.set_power(1) is ok, rc
    queued = RadioManager(Radio(2), None, sleep=lambda s: None).survey(20, 22)
    assert queued["noise"] == [None, None, None] and queued["home_restored"] is True
    applied = RadioManager(Radio(1), None, sleep=lambda s: None).survey(20, 22)
    assert applied["noise"] == [-110.0, -110.0, -110.0] and applied["quietest"] == 20


# ------------------------------------- GSCAN: home channel and the station's own uplinks
class StationMcu:
    """The station sketch's SX126x rules as far as a survey meets them (sketch.ino
    rf_cfg_exec, queue_tx, sx_service, sx_resume_rx): rf_cfg builds on the WANTED
    configuration while a change is pending and returns 2 while a packet is arriving (the
    change waits for its end), else applies it at once and emits a status line; rf_tx queues
    up to 4 uplinks, which the loop sends on the channel ACTIVE at that moment (T| report)."""
    SLOTS = 4

    def __init__(self, ch=23):
        self.handlers, self.calls = {}, []
        self.active = self.wanted = ch
        self.pending = self.receiving = False
        self.txq, self.radiated = [], []                # radiated: (channel, octets)
        self.cfg_rc = None                              # forced rf_cfg reply (-2: MCU busy, nothing changed)
        self.before_cfg = None                          # hook(channel) before an rf_cfg is executed
        self.up = 10

    def provide(self, name, fn):
        self.handlers[name] = fn

    def status(self):
        self.up += 1
        flags = 7 | (16 if self.pending else 0)
        self.handlers["rf_evt"](f"S|DSSQ-IO 3.0 SX126x|SX1268|1|{self.active}|4|0|{flags}|150|12|1|0|4|0|2|0|0|1|"
                                f"{len(self.txq)}|{self.up}")

    def call(self, method, *a, timeout=None):
        self.calls.append((method, a))
        if method == "rf_cfg":
            if self.before_cfg:
                self.before_cfg(a[0])
            if self.cfg_rc is not None:
                return self.cfg_rc
            if a[0] >= 0:
                self.wanted = a[0]
            self.pending = True
            if self.receiving:
                return 2
            self._apply()
            return 1
        if method in ("rf_tx", "rf_wake"):
            if len(self.txq) >= self.SLOTS:
                return 0
            self.txq.append(len(a[0]) // 2)
            return 1
        if method == "rf_noise":
            return -1100
        return 1

    def _apply(self):
        self.active, self.pending = self.wanted, False
        self.status()

    def step(self):                                     # one loop pass in state RX
        if self.receiving:
            return
        if self.pending:
            self._apply()
        elif self.txq:
            n = self.txq.pop(0)
            self.radiated.append((self.active, n))
            self.handlers["rf_evt"](f"T|1|100|{n}")

    def end_of_packet(self):
        self.receiving = False
        if self.pending:
            self._apply()


@pytest.fixture
def sx_station(monkeypatch, tmp_path):
    """GroundStation + the real Sx126xBridge + StationMcu on one virtual clock; the MCU loop
    runs while the GDS sleeps (survey settle time)."""
    from test_end_to_end import _config

    from dssq.gds import GroundStation
    clk = Clock()
    clk.t = 1_800_000_000.0
    monkeypatch.setattr(time, "time", clk)
    monkeypatch.setattr(time, "sleep", clk.sleep)
    cfg = _config(tmp_path, model="SX126x", air_rate=4)
    gs = GroundStation(cfg, archive=False)
    mcu = StationMcu(gs.settings.channel)
    radio = sx.Sx126xBridge(mcu, cfg["radio"], clock=clk, poll_s=0.0)
    radio.attach()
    mcu.status()
    radio.read()                                        # the initial configuration push

    def sleep(s):
        clk.sleep(s)
        mcu.step()

    gs.attach_radio(radio, RadioManager(radio, None, clock=clk, sleep=sleep), None)
    gs.radio_jobs.clear()                               # the start-up VERIFY
    mcu.calls.clear()
    yield gs, radio, mcu, clk
    gs.close()


def test_a_queued_setchan_survives_a_gscan_behind_it(sx_station):
    """GM-1: SETCHAN while a packet is arriving is queued (rf_cfg 2) - the GDS's channel, not
    yet the MCU's active one. A GSCAN in the same pass returns to the GDS's channel."""
    gs, radio, mcu, _clk = sx_station
    home = gs.settings.channel
    assert home == 23 and home != 21
    mcu.receiving = True
    left = [2]                                          # the packet ends during the survey's 2nd rf_cfg

    def packet_ends(ch):
        left[0] -= 1
        if left[0] == 0:
            mcu.end_of_packet()
    mcu.before_cfg = packet_ends
    gs.radio_jobs.extend([("SETCHAN", 21), ("GSCAN", (80, 83))])
    gs.run_radio_jobs()
    assert not gs.radio_jobs and gs.settings.channel == 21
    scan = gs.radio_mgr.last_scan
    assert scan["home"] == 21 and scan["home_restored"] and None not in scan["noise"]
    assert [a[0] for m, a in mcu.calls if m == "rf_cfg"] == [21, 80, 81, 82, 83, 21]
    assert mcu.active == 21 and not mcu.pending and radio.settings.channel == 21
    assert not [t for lv, t in _radio_events(gs) if lv != "INFO"]


def test_back_to_back_gscans_end_on_the_home_channel(sx_station):
    """GM-1: the first survey's return is queued behind a packet arriving on the last
    surveyed channel; the second survey must not take that channel as its home."""
    gs, radio, mcu, _clk = sx_station

    def restore_meets_a_packet(ch):
        if ch == 23 and mcu.active == 83:
            mcu.receiving = True
    mcu.before_cfg = restore_meets_a_packet
    gs.radio_jobs.extend([("GSCAN", (80, 83)), ("GSCAN", (80, 81))])
    gs.run_radio_jobs()
    assert [j for j, _ in gs.radio_jobs] == ["GSCAN"]                  # waits: the return is only queued
    mcu.end_of_packet()                                                 # applied: the MCU reports CH23
    gs.run_radio_jobs()
    assert not gs.radio_jobs and gs.radio_mgr.last_scan["home"] == 23
    mcu.end_of_packet()
    assert mcu.active == 23 and radio.settings.channel == 23 and gs.settings.channel == 23
    radio.read()                                        # nothing left to repair: no configuration push
    assert [a[0] for m, a in mcu.calls if m == "rf_cfg"][-1] == 23 and not radio._tuned_away


def test_a_failed_return_from_the_survey_is_an_error_and_self_repairs(sx_station):
    """GM-1: the MCU refuses the return to the home channel (-2, nothing changed): ERROR
    event, the bridge's station configuration still holds the home channel and read() keeps
    re-sending it until the MCU takes it."""
    gs, radio, mcu, clk = sx_station

    def busy_on_return(ch):
        if ch == 23:
            mcu.cfg_rc = -2
    mcu.before_cfg = busy_on_return
    gs.radio_jobs.append(("GSCAN", (80, 82)))
    gs.run_radio_jobs()
    scan = gs.radio_mgr.last_scan
    assert scan["home_restored"] is False and mcu.active == 82
    errors = [t for lv, t in _radio_events(gs) if lv == "ERROR"]
    assert len(errors) == 1 and "did not return to CH23" in errors[0]
    assert radio.settings.channel == 23                 # survey steps never become the station configuration
    from dssq.radioctl import SURVEY_RESTORE_TRIES
    assert [a[0] for m, a in mcu.calls if m == "rf_cfg"] == [80, 81, 82] + [23] * SURVEY_RESTORE_TRIES
    mcu.before_cfg, mcu.cfg_rc = None, None             # the MCU recovers
    clk.t += sx.PUSH_RETRY_S
    mcu.status()
    radio.read()
    s = radio.settings                                  # the full station configuration is re-sent
    assert mcu.active == 23 and mcu.calls[-1] == ("rf_cfg", (23, s.rate_code, s.power_code, s.cad | s.rx_boost << 1))
    n = len(mcu.calls)
    clk.t += sx.PUSH_RETRY_S
    radio.read()
    assert len(mcu.calls) == n                          # repaired: no further push


def test_gscan_waits_for_the_window_uplinks_and_never_retunes_under_them(sx_station):
    """GM-2: the outbox stage hands a window's CLTU and ID to the MCU (rf_tx), then the jobs
    stage finds a GSCAN. It waits for the window plan and for the MCU's T| reports, so
    every uplink goes out on the home channel and no rf_tx falls inside the survey."""
    gs, radio, mcu, clk = sx_station
    gs.fop._init_vs(0)
    assert gs.submit_command("NOOP")["status"] == "QUEUED"
    gs._uplink_window(clk.t)
    out = gs.take_outbox()
    assert len(out) == 2 and out[0][:2] == b"\xeb\x90" and out[1].startswith(b"DE ")
    for chunk in out:                                   # Runner stage "outbox"
        radio.write(chunk)
    assert gs.submit_command("GSCAN 0 3")["status"] == "QUEUED"
    gs.run_radio_jobs()                                 # stage "jobs" of the same pass
    assert [j for j, _ in gs.radio_jobs] == ["GSCAN"] and not mcu.radiated
    clk.t += gs._uplink_busy_until - gs.mono() + 0.01   # the plan says done, the MCU has not reported
    gs.run_radio_jobs()
    assert [j for j, _ in gs.radio_jobs] == ["GSCAN"]
    assert "2 uplink(s) queued on the station MCU" in gs.radio_mgr.tx_busy()
    mcu.step()
    gs.run_radio_jobs()
    assert [j for j, _ in gs.radio_jobs] == ["GSCAN"]   # one still queued
    mcu.step()
    gs.run_radio_jobs()
    assert not gs.radio_jobs and gs.radio_mgr.last_scan["home_restored"]
    assert mcu.radiated == [(23, len(out[0])), (23, len(out[1]))]
    cfg = [i for i, (m, _a) in enumerate(mcu.calls) if m == "rf_cfg"]
    assert [mcu.calls[i][1][0] for i in cfg] == [0, 1, 2, 3, 23]
    assert not [m for m, _a in mcu.calls[cfg[0]:cfg[-1]] if m in ("rf_tx", "rf_wake")]
    assert any("ground RF survey CH0-3" in t for _lv, t in _radio_events(gs))


def test_gscan_wait_for_a_busy_transmitter_is_bounded(sx_station):
    """GM-2: uplinks the MCU cannot take (queue full: backlog) and a status that keeps
    showing a full TX queue hold a GSCAN at most _tx_wait_max_s(); then it is refused
    with a WARN and the jobs behind it run."""
    gs, radio, mcu, clk = sx_station
    mcu.txq = [90] * StationMcu.SLOTS                   # the MCU queue is full and does not move
    radio.write(b"\xeb\x90" + bytes(88))
    assert radio.snapshot()["tx_backlog"] == 1
    gs.radio_jobs.extend([("GSCAN", (0, 3)), ("NOISE", None)])
    gs.run_radio_jobs()
    assert [j for j, _ in gs.radio_jobs] == ["GSCAN", "NOISE"] and not mcu.radiated
    assert "waiting to be handed to the station MCU" in gs.radio_mgr.tx_busy()
    assert gs.radio_mgr.survey(0, 3, home=23) is None and "transmitter busy" in gs.radio_mgr.survey_refused
    radio._tx_backlog.clear()                           # handed over, never reported (a T| line lost)
    radio._tx_accepted()
    limit = gs._tx_wait_max_s()
    t0 = clk.t
    while clk.t - t0 < limit - 5.0:
        clk.t += 5.0
        mcu.status()                                    # fresh status lines: txq 4
        gs.run_radio_jobs()
        assert [j for j, _ in gs.radio_jobs] == ["GSCAN", "NOISE"]
    assert "station MCU in RX with 4 uplink(s) queued" in gs.radio_mgr.tx_busy()   # the count expired
    clk.t = t0 + limit
    gs.run_radio_jobs()
    assert not gs.radio_jobs and not [m for m, _a in mcu.calls if m == "rf_cfg"]
    warn = [t for lv, t in _radio_events(gs) if lv == "WARN"]
    assert len(warn) == 1 and "ground RF survey CH0-3 not run: station transmitter still busy" in warn[0]
    assert ("rf_noise", ()) in mcu.calls                # the job behind it ran


def test_tx_busy_follows_the_mcu_reports(sxb):
    radio, b, clk = sxb
    assert radio.tx_busy() is None
    b.handlers["rf_evt"](STATUS.format(up=10))
    radio.write(b"\x01")
    radio.write(b"\x02")
    assert radio.tx_busy().startswith("2 uplink(s) queued")
    b.handlers["rf_evt"]("T|1|50|1")
    assert radio.tx_busy().startswith("1 uplink(s) queued")
    b.handlers["rf_evt"]("T|0|50|1")                    # a TX timeout reports the slot too
    assert radio.tx_busy() is None
    radio.write(b"\x03")
    clk.t += radio._tx_report_timeout_s() + 0.01        # the report never came: taken as lost
    assert radio.tx_busy() is None
    radio.write(b"\x04")
    b.handlers["rf_evt"](STATUS.format(up=5))           # MCU reboot: its queue is gone
    assert radio.tx_busy() is None
    b.handlers["rf_evt"](STATUS.replace("|1|23|", "|3|23|").format(up=6))   # a status line in TX
    assert radio.tx_busy() == "station MCU in TX with 0 uplink(s) queued"
    clk.t += 0.1
    b.handlers["rf_evt"]("C|0|12")                      # a later report: the TX status is old news
    assert radio.tx_busy() is None
    with pytest.raises(ValueError):
        radio.configure(channel=20, power_code=1, transient=True)


def test_after_a_failed_return_nothing_is_radiated_off_channel(sx_station):
    """GM2-R1: the survey's three returns to CH23 fail (the Router Bridge restarts) and the
    MCU stays on CH83. Until it reports CH23 again the station holds every uplink: the WAKE
    queued behind the GSCAN waits, an identification written meanwhile stays in the backlog.
    Once the bridge is back, the re-push returns the MCU home, its status line confirms it,
    and both go out - on CH23."""
    gs, radio, mcu, clk = sx_station
    down = [False, False]                                              # (down now, has been down)

    def router(ch):
        if ch == 23 and mcu.active == 83 and not down[1]:
            down[0] = down[1] = True                                   # the outage starts at the return
        if down[0]:
            raise ConnectionError("arduino-router restarting")
    mcu.before_cfg = router
    gs.radio_jobs.extend([("GSCAN", (80, 83)), ("WAKE", None)])
    gs.run_radio_jobs()
    assert gs.radio_mgr.last_scan["home_restored"] is False and mcu.active == 83
    assert [j for j, _ in gs.radio_jobs] == ["WAKE"] and "after a survey" in gs.radio_mgr.off_home()
    error = next(t for lv, t in _radio_events(gs) if lv == "ERROR")    # visible: the event and the state
    assert "every uplink is held" in error and "transmitting" not in error
    assert "CH23" in gs.snapshot()["radio"]["sx126x"]["tx_hold"]
    radio.write(b"DE N0CALL DSS-Q1")                                   # e.g. an overdue identification
    radio.read()                                                       # the re-push fails: still down
    for _ in range(3):
        mcu.step()
    assert mcu.radiated == [] and radio.snapshot()["tx_backlog"] == 1
    down[0] = False                                                    # the bridge is back
    clk.t += sx.PUSH_RETRY_S
    mcu.status()
    radio.read()                                                       # re-push -> CH23, status, backlog
    gs.run_radio_jobs()
    for _ in range(3):
        mcu.step()
    assert not gs.radio_jobs and mcu.active == 23
    assert len(mcu.radiated) == 2 and all(ch == 23 for ch, _ in mcu.radiated)
    assert gs.snapshot()["radio"]["sx126x"]["tx_hold"] is None          # the hold cleared itself


def test_a_wall_clock_step_back_cannot_prolong_the_hold_after_a_survey(sx_station):
    """GM2-R1: the hold after a failed return ends with the MCU's report, whatever the wall
    clock does. (a) NTP steps the clock back an hour during the router outage: the station
    configuration is re-sent once the router is back, not an hour later. (b) The step comes
    between the accepted re-push (queued behind a packet) and the status line that reports
    CH23: that status line ends the hold."""
    gs, radio, mcu, clk = sx_station
    down = [False]

    def router(ch):
        if ch == 23 and mcu.active == 83:
            down[0] = True                                             # the outage starts at the return
        if down[0]:
            raise ConnectionError("arduino-router restarting")
    for step_during_outage in (True, False):
        mcu.before_cfg = router
        gs.radio_jobs.append(("GSCAN", (80, 83)))
        gs.run_radio_jobs()
        assert mcu.active == 83 and radio.home_unconfirmed()
        clk.t += sx.PUSH_RETRY_S
        radio.read()                                                   # the re-push fails: router down
        if step_during_outage:
            clk.t -= 3600.0
        down[0] = False
        mcu.before_cfg = None
        mcu.receiving = not step_during_outage                         # (b): a packet is arriving
        clk.t += sx.PUSH_RETRY_S
        mcu.status()
        radio.read()                                                   # the re-push
        if not step_during_outage:
            assert mcu.pending and radio.home_unconfirmed()            # queued (rf_cfg 2): not yet in effect
            clk.t -= 3600.0
            mcu.end_of_packet()                                        # applied: status line CH23
        assert mcu.active == 23 and radio.home_unconfirmed() is None, step_during_outage
        radio.write(b"DE N0CALL DSS-Q1")
        mcu.step()
        assert mcu.radiated[-1] == (23, 16)


def test_closing_id_waits_for_a_free_mcu_slot_or_reports_it_was_not_sent(tmp_path, monkeypatch):
    """UPL4-R1: at shutdown the MCU's 4 TX slots are still full with the last window's
    uplinks. The closing identification is handed over as soon as a slot frees (within the
    ident step) and only then recorded as sent; if none frees in time it stays unsent and an
    ERROR says the identification was NOT radiated."""
    from test_end_to_end import _config

    from dssq.gds import Runner
    real_sleep = time.sleep
    for frees in (True, False):
        d = tmp_path / str(frees)
        d.mkdir()
        cfg = _config(d, model="SX126x", air_rate=7)
        r = Runner(cfg, sim=True, web=False, archive=False)
        mcu = StationMcu(23)
        radio = sx.Sx126xBridge(mcu, cfg["radio"], poll_s=0.0)
        radio.attach()
        mcu.status()
        radio.read()
        r.radio = radio
        mcu.txq = [66] * StationMcu.SLOTS                              # the last window filled the MCU
        sid = r.gs.station_id
        sid.on_transmit(time.time())
        monkeypatch.setattr(time, "sleep", (lambda s, m=mcu: m.step()) if frees else (lambda s: real_sleep(0.005)))
        r.close()
        monkeypatch.setattr(time, "sleep", real_sleep)
        ident = b"DE N0CALL DSS-Q1".hex()
        accepted = [c for c in mcu.calls if c == ("rf_tx", (ident,))] and radio.snapshot()["tx_backlog"] == 0
        errors = [e["text"] for e in r.gs.events if e["source"] == "IDENT" and e["level"] == "ERROR"]
        if frees:
            assert accepted and sid.count == 1 and sid.last_reason == "end" and not errors
        else:
            assert radio.snapshot()["tx_backlog"] == 1 and sid.pending and sid.count == 0
            assert len(errors) == 1 and "closing identification NOT radiated" in errors[0]


def test_closing_id_goes_after_the_uplinks_queued_behind_an_earlier_id(tmp_path, monkeypatch):
    """UPL4-R1: the MCU's queue stays full across two windows (a busy channel holds its
    transmissions), so the bridge's backlog holds window k's identification and window k+1's
    CLTU behind it. That identification covers nothing radiated after it: close() hands a new
    one to the MCU after the CLTU, and only that one is recorded as the closing ID."""
    from test_end_to_end import _config

    from dssq.gds import Runner
    cfg = _config(tmp_path, model="SX126x", air_rate=7)
    r = Runner(cfg, sim=True, web=False, archive=False)
    mcu = StationMcu(23)
    radio = sx.Sx126xBridge(mcu, cfg["radio"], poll_s=0.0)
    radio.attach()
    mcu.status()
    radio.read()
    r.radio = radio
    sid = r.gs.station_id
    mcu.txq = [66] * StationMcu.SLOTS
    sid.on_transmit(time.time())
    radio.write(sid.payload)                                           # window k: its ID waits
    sid.sent(time.time(), "periodic")
    radio.write(b"\xeb\x90" + bytes(64))                               # window k+1: a CLTU behind it
    sid.on_transmit(time.time())
    assert radio.snapshot()["tx_backlog"] == 2 and sid.pending
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: mcu.step())           # slots free while close() waits
    r.close()
    monkeypatch.setattr(time, "sleep", real_sleep)
    for _ in range(StationMcu.SLOTS + 3):
        mcu.step()
    n_id = len(sid.payload)
    assert [n for _ch, n in mcu.radiated[-3:]] == [n_id, 66, n_id]    # ID, CLTU, then the closing ID
    assert radio.snapshot()["tx_backlog"] == 0 and sid.count == 2 and sid.last_reason == "end" and not sid.pending
