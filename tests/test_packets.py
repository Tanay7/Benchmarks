"""PacketExtractor discontinuities the VC frame count cannot show (Phase C).

C01: a VC stream emptied on board (SSRCLEAR -> Downlink::reset_vc, or an MCU reboot whose
VC frame count wraps to look contiguous) leaves a stale partial packet; the next frame's
FHP (CCSDS 132.0-B-3 4.1.2.7.4) must win over the buffer.
C09 / I2: a change of SDLS status between frames of a VC is a discontinuity, and every
packet carries `protected` = True only if all its octets came from AE-verified frames.
"""
from __future__ import annotations

from dssq.ccsds import FHP_NO_PACKET_START
from dssq.ccsds.packets import PacketExtractor, build_idle_packet, build_packet
from dssq.ccsds.timecode import CucTime

FIELD = 100                                         # frame data field octets for these tests


def hk(seq: int, fill: int, n: int = 84) -> bytes:
    return build_packet(0x010, seq, bytes([fill]) * n, CucTime(1000 + seq, 0))   # 6 + 6 + 84 = 96


def frames(stream: bytes, starts: list[int], vcfc0: int) -> list[tuple[int, int, bytes]]:
    """Cuts a packet stream into frames with correct FHPs."""
    out = []
    for i in range(0, len(stream) - FIELD + 1, FIELD):
        fhp = next((s - i for s in starts if i <= s < i + FIELD), FHP_NO_PACKET_START)
        out.append(((vcfc0 + i // FIELD) & 0xFF, fhp, stream[i:i + FIELD]))
    return out


def packet_stream(pkts: list[bytes]) -> tuple[bytes, list[int]]:
    starts, pos = [], 0
    for p in pkts:
        starts.append(pos)
        pos += len(p)
    s = b"".join(pkts)
    pad = (-len(s)) % FIELD
    if 0 < pad < 7:
        pad += FIELD
    if pad:
        starts.append(len(s))
        s += build_idle_packet(pad, 0)
    return s, starts


# ---------------------------------------------------------------- C01
def test_stale_partial_packet_is_not_spliced_after_a_vc_reset():
    ex = PacketExtractor(2)
    old, starts = packet_stream([hk(1, 0xA1), hk(2, 0xA2), hk(3, 0xA3)])
    f0 = frames(old, starts, 10)[:2]                 # vcfc 10, 11: seq 1, 2 whole, seq 3 started
    got = [p for v, fhp, d in f0 for p in ex.push(v, fhp, d)]
    assert [p.seq_count for p in got] == [1, 2] and len(ex.buf) == 8
    new, nstarts = packet_stream([hk(100, 0x11), hk(101, 0x12)])
    got = [p for v, fhp, d in frames(new, nstarts, 12) for p in ex.push(v, fhp, d)]   # contiguous VCFC
    assert [p.seq_count for p in got] == [100, 101]
    assert all(p.user_data == bytes([0x11 + i]) * 84 for i, p in enumerate(got))
    assert ex.stats["vcfc_gaps"] == 0 and ex.stats["fhp_mismatches"] == 1 and ex.stats["discarded_partials"] == 1


def test_header_start_inside_a_continuing_packet_resyncs():
    ex = PacketExtractor(0)
    big = build_packet(0x011, 5, bytes(250), CucTime(1, 0))        # spans three frames
    s, st = packet_stream([big])
    fr = frames(s, st, 0)
    assert ex.push(*fr[0]) == []
    # the next frame claims a header at 10 although the 262-octet packet continues
    fresh = hk(9, 0x33)
    d = fr[1][2][:10] + fresh[:FIELD - 10]
    assert ex.push(1, 10, d) == [] and ex.stats["fhp_mismatches"] == 1
    tail = fresh[FIELD - 10:]                                      # 6 octets, then idle fill
    out = ex.push(2, len(tail), tail + build_idle_packet(FIELD - len(tail), 1))
    assert [p.seq_count for p in out] == [9] and ex.stats["fhp_mismatches"] == 1


def test_partial_header_split_across_frames_is_checked_too():
    ex = PacketExtractor(0)
    a = hk(1, 0x01)
    first = hk(0, 0x00, n=FIELD - 12 - 3)                           # leaves 3 octets of a's header
    d0 = first + a[:3]
    assert len(d0) == FIELD and [p.seq_count for p in ex.push(0, 0, d0)] == [0]
    rest = a[3:]                                                    # 93 octets: next header at 93
    out = ex.push(1, len(rest), rest + hk(2, 0x02)[:FIELD - len(rest)])
    assert [p.seq_count for p in out] == [1] and ex.stats["fhp_mismatches"] == 0
    ex2 = PacketExtractor(0)
    ex2.push(0, 0, d0)
    out = ex2.push(1, 1, bytes([0x55]) + hk(7, 0x07) + bytes(3))   # FHP 1 cannot end a's header
    assert [p.seq_count for p in out] == [7]                        # the FHP wins over the buffer
    assert ex2.stats["fhp_mismatches"] == 1 and ex2.stats["discarded_partials"] == 1


def test_consistent_stream_spanning_frames_has_no_mismatch():
    pkts = [build_packet(0x10 + i, i, bytes([i]) * (40 + 37 * i), CucTime(i, 0)) for i in range(8)]
    s, st = packet_stream(pkts)
    ex = PacketExtractor(1)
    got = [p for v, fhp, d in frames(s, st, 250) for p in ex.push(v, fhp, d, protected=True)]
    assert [p.raw for p in got] == pkts and all(p.protected is True for p in got)
    assert ex.stats["fhp_mismatches"] == ex.stats["sdls_discontinuities"] == ex.stats["discarded_partials"] == 0


# ---------------------------------------------------------------- C09 / I2
def test_null_sa_frame_cannot_splice_into_an_ae_frame():
    """The PoC: a genuine AE frame vcfc 10, a forged null-SA frame repeating vcfc 10 whose
    FHP points to a forged HK minus its last octet, then the genuine AE frame vcfc 11 whose
    first octet would complete it. The attacker even matched the genuine FHP (1)."""
    ex = PacketExtractor(0)
    g, _ = packet_stream([hk(1, 0x01)])
    assert [p.protected for p in ex.push(10, 0, g[:FIELD], protected=True)] == [True]
    forged = hk(66, 0xEE)
    atk = bytes(FIELD - len(forged) + 1) + forged[:-1]
    fhp_atk = FIELD - len(forged) + 1
    assert ex.push(10, fhp_atk, atk, protected=False) == []         # VCFC repeat: resync on its FHP
    nxt = bytes([0x99]) + hk(2, 0x02)[:FIELD - 1]                   # genuine: 1 continuation octet
    out = ex.push(11, 1, nxt, protected=True)                       # FHP consistent: only SDLS sees it
    assert [(p.seq_count, p.protected) for p in out] == [(2, True)] and ex.stats["fhp_mismatches"] == 0
    assert ex.stats["sdls_discontinuities"] == 2 and ex.stats["discarded_partials"] == 2


def test_per_packet_protected_flag():
    ex = PacketExtractor(0)
    s, st = packet_stream([hk(1, 1), hk(2, 2), hk(3, 3)])
    fr = frames(s, st, 0)
    flags = [p.protected for v, fhp, d in fr for p in ex.push(v, fhp, d, protected=False)]
    assert flags == [False, False, False]
    ex = PacketExtractor(0)
    flags = [p.protected for v, fhp, d in fr for p in ex.push(v, fhp, d)]
    assert flags == [None, None, None]                              # status not reported
    ex = PacketExtractor(0)
    out = ex.push(*fr[0], protected=True) + ex.push(*fr[1], protected=False)
    assert [(p.seq_count, p.protected) for p in out] == [(1, True)]    # seq 2 straddled the change
    assert [(p.seq_count, p.protected) for p in ex.push(*fr[2], protected=False)] == [(3, False)]
    assert ex.stats["sdls_discontinuities"] == 1 and ex.stats["discarded_partials"] == 1
