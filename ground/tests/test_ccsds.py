"""Unit tests for the ground CCSDS layers (pure Python, no hardware)."""
import random

import pytest

from dssq.ccsds import (CADU_LEN, CODEBLOCK_LEN, RS_VIRTUAL_FILL, TM_DATA_LEN, TM_FRAME_LEN)
from dssq.ccsds import rs
from dssq.ccsds.crc import crc16_ccitt
from dssq.ccsds.packets import PacketExtractor, build_idle_packet, build_packet
from dssq.ccsds.randomizer import SEQUENCE, derandomize
from dssq.ccsds.tc import (Farm1Model, bc_set_vr, bc_unlock, build_cltu, build_tc_frame,
                           cltu_decode, parse_tc_frame)
from dssq.ccsds.timecode import CucTime, SclkCorrelator
from dssq.ccsds.tm import Clcw, TmFrame, build_frame, decode_codeblock, encode_cadu


def test_crc_check_value():
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_randomizer_prefix_and_period():
    assert SEQUENCE[:5] == bytes.fromhex("FF480EC09A")
    assert len(SEQUENCE) == 255
    data = bytes(range(256)) * 2
    assert derandomize(derandomize(data)) == data


def test_rs_conventional_matches_reference_codec():
    reedsolo = pytest.importorskip("reedsolo")
    # reedsolo's "generator" is the primitive FIELD ELEMENT; CCSDS uses alpha^11.
    codec = reedsolo.RSCodec(nsym=32, nsize=255, fcr=112, prim=0x187,
                             generator=rs.ALPHA_TO[rs.PRIM], c_exp=8)
    rng = random.Random(1)
    for _ in range(20):
        msg = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 224)))
        ref = bytes(codec.encode(msg))[len(msg):]
        assert rs.encode_conventional(msg) == ref


def test_rs_dual_basis_tables():
    assert rs.TALTAB[1] == 0x7B and rs.TALTAB[2] == 0xAF and rs.TALTAB[0x80] == 0x8D
    assert all(rs.TAL1TAB[rs.TALTAB[i]] == i for i in range(256))


@pytest.mark.parametrize("nerr", [0, 1, 5, 16])
def test_rs_corrects_up_to_16_errors(nerr):
    rng = random.Random(nerr)
    data = bytes(rng.randrange(256) for _ in range(TM_FRAME_LEN))
    cb = bytearray(data + rs.encode(data))
    for pos in rng.sample(range(CODEBLOCK_LEN), nerr):
        cb[pos] ^= rng.randrange(1, 256)
    fixed, n = rs.decode(bytes(cb), RS_VIRTUAL_FILL)
    assert n == nerr
    assert fixed[:TM_FRAME_LEN] == data


def test_rs_detects_17_errors_usually():
    rng = random.Random(99)
    detected = 0
    for trial in range(20):
        data = bytes(rng.randrange(256) for _ in range(TM_FRAME_LEN))
        cb = bytearray(data + rs.encode(data))
        for pos in rng.sample(range(CODEBLOCK_LEN), 17):
            cb[pos] ^= rng.randrange(1, 256)
        fixed, n = rs.decode(bytes(cb), RS_VIRTUAL_FILL)
        if n < 0 or fixed[:TM_FRAME_LEN] != data:
            detected += n < 0
            assert n < 0, "decoder must never silently mis-correct in this test set"
    assert detected == 20


def test_frame_roundtrip_with_errors():
    payload = bytes(range(TM_DATA_LEN))
    clcw = Clcw.pack(lockout=True, report_value=77, farm_b=2)
    frame = build_frame(1, 10, 20, 0, payload, clcw)
    cadu = bytearray(encode_cadu(frame))
    assert len(cadu) == CADU_LEN
    for pos in (10, 50, 100, 200):
        cadu[pos] ^= 0xFF
    res = decode_codeblock(bytes(cadu[4:]))
    assert res.ok and res.rs_corrected == 4
    f = res.frame
    assert (f.vcid, f.mcfc, f.vcfc, f.fhp) == (1, 10, 20, 0)
    assert f.clcw.lockout and f.clcw.report_value == 77 and f.clcw.farm_b_counter == 2
    assert f.clcw.cop_in_effect == 1


def test_packet_extraction_spanning_and_gap():
    t = CucTime(1234, 5678)
    pkts = [build_packet(0x10 + i, i, bytes([i]) * (50 + 30 * i), t) for i in range(6)]
    stream = b"".join(pkts)
    stream += build_idle_packet(TM_DATA_LEN * 4 - len(stream) % TM_DATA_LEN, 0)
    fields = [stream[i:i + TM_DATA_LEN] for i in range(0, len(stream) - TM_DATA_LEN + 1, TM_DATA_LEN)]
    starts, pos = [], 0
    for p in pkts:
        starts.append(pos)
        pos += len(p)
    ex = PacketExtractor(0)
    got = []
    for n, fld in enumerate(fields):
        lo = n * TM_DATA_LEN
        fhp = next((s - lo for s in starts if lo <= s < lo + TM_DATA_LEN), 0x7FF)
        got += ex.push(n, fhp, fld)
    assert [g.raw for g in got] == pkts
    assert got[2].time == t and got[2].apid == 0x12


def test_tc_cltu_roundtrip_and_farm():
    frame = build_tc_frame(b"\x18\xc0\xc0\x00\x00\x00\x01", seq=0)
    cltu = bytearray(build_cltu(frame))
    cltu[5] ^= 0x10                       # single bit error in first codeblock
    info, st = cltu_decode(bytes(cltu))
    assert st["corrected"] == 1
    tf = parse_tc_frame(info)
    assert tf and tf.seq == 0 and tf.data == b"\x18\xc0\xc0\x00\x00\x00\x01"
    farm = Farm1Model()
    assert farm.on_frame(tf) == "ACCEPT" and farm.vr == 1
    assert farm.on_frame(parse_tc_frame(build_tc_frame(b"\x00", 3))) == "DISCARD_RETRANSMIT"
    assert farm.on_frame(parse_tc_frame(build_tc_frame(b"\x00", 120))) == "DISCARD_LOCKOUT"
    assert farm.on_frame(parse_tc_frame(build_tc_frame(bc_unlock(), 0, bypass=True,
                                                       control=True))) == "CONTROL_UNLOCK"
    assert farm.on_frame(parse_tc_frame(build_tc_frame(bc_set_vr(5), 0, bypass=True,
                                                       control=True))) == "CONTROL_SET_VR"
    assert farm.vr == 5


def test_sclk_correlation_fit():
    c = SclkCorrelator()
    for k in range(10):
        c.add(1, 100.0 + k * 10, 1_700_000_000.0 + k * 10 * (1 + 50e-6))
    assert abs(c.drift_ppm - 50) < 1e-3
    assert abs(c.scet(150.0) - (1_700_000_000.0 + 50 * (1 + 50e-6))) < 1e-6


def test_tmframe_parse_rejects_wrong_length():
    with pytest.raises(ValueError):
        TmFrame.parse(b"\x00" * 10)
