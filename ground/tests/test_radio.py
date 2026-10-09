"""E22-400T37S specifics (EBYTE "E22-xxxT37S User Manual" v1.5) and their ground handling."""
from dssq.ccsds.tm import FHP_ONLY_IDLE_DATA, build_frame, encode_cadu
from dssq.framesync import FrameSynchronizer, RadioFault, RawCadu
from dssq.link import AIR_RATE_BPS, SENSITIVITY_EST_DBM, TX_POWER_DBM
from dssq.linkmgr import LinkManager
from dssq.radio.e22 import describe, regs_from_cfg


def _cadu(mcfc=0):
    return encode_cadu(build_frame(7, mcfc, 0, FHP_ONLY_IDLE_DATA, bytes(188), 0))


def test_t37s_rate_and_power_tables():
    # 7.2: air-rate codes 000, 001, 010 are all 2.4k; every power code is 37 dBm.
    assert AIR_RATE_BPS[0] == AIR_RATE_BPS[1] == AIR_RATE_BPS[2] == 2400
    assert AIR_RATE_BPS[7] == 62500
    assert set(TX_POWER_DBM.values()) == {37.0}
    # 2.2: -126 dBm typical at 2.4 kbit/s; faster rates are monotonically less sensitive.
    assert SENSITIVITY_EST_DBM[2] == -126.0
    assert all(SENSITIVITY_EST_DBM[c] < SENSITIVITY_EST_DBM[c + 1] for c in range(2, 7))


def test_fault_log_register_bit():
    regs = regs_from_cfg({"fault_log": True, "rssi_noise": True, "power": 0})
    assert regs[4] & 0x04 and regs[4] & 0x20            # REG1 bit 2 and bit 5
    assert describe(regs)["fault_log"] is True
    assert not regs_from_cfg({"fault_log": False})[4] & 0x04


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
