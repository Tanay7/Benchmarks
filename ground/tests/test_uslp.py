"""USLP (CCSDS 732.1-B-2) framing mode: flight <-> ground cross-check.

tools/host_test (the EXACT flight sources built with the host compiler) writes
USLP CADU streams — plain, and through a dummy FrameSecurity that marks its
security header/trailer and XORs the protected region with 0x3C — plus USLP TC
frames. The independent Python stack must decode them bit-exactly and build
identical TC frames. Flight-dependent tests are skipped without a C++ toolchain.
"""
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from dssq.ccsds import CADU_LEN, SPACECRAFT_ID, TM_FRAME_LEN
from dssq.ccsds.crc import crc16_ccitt
from dssq.ccsds.packets import PacketExtractor, build_packet
from dssq.ccsds.sdls import SDLS_HDR_LEN, SDLS_MAC_LEN, SdlsSender, mac
from dssq.ccsds.tc import (Farm1Model, bc_set_vr, bc_unlock, build_cltu, build_tc_frame,
                           cltu_decode, parse_tc_frame, tc_open_tfdf)
from dssq.ccsds.timecode import CucTime
from dssq.ccsds.tm import (Clcw, FrameLossTracker, TransferFrame, build_frame, build_oid_frame,
                           build_uslp_frame, build_uslp_oid_frame, data_field_len,
                           decode_codeblock, encode_cadu)
from dssq.ccsds.uslp import (FHP_NONE, TFDZ_LEN, UPID_IDLE, VCID_OID, UslpHeader, ident_text,
                             idle_fill)

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"
IDENT = b"DE N0CALL VGQ-1 "          # what tools/host_test/test_uslp.cpp passes
SEC_HDR, SEC_TRL = 14, 16              # geometry of the dummy FrameSecurity (= SDLS AE)


@pytest.fixture(scope="module")
def vectors(tmp_path_factory):
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("uslp_vectors")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    subprocess.run([str(HOST / "host_test"), str(out)], check=True, stdout=subprocess.DEVNULL)
    if not (out / "uslp_cadus.bin").exists():
        pytest.skip("flight USLP vectors not generated")
    return out


def _expected_packets(path: Path) -> dict[int, list[str]]:
    exp: dict[int, list[str]] = {0: [], 1: [], 3: []}
    for line in path.read_text().splitlines():
        if line.strip():
            vc, hx = line.split()
            exp[int(vc)].append(hx)
    return exp


def _cadus(path: Path):
    data = path.read_bytes()
    assert data and len(data) % CADU_LEN == 0
    for i in range(0, len(data), CADU_LEN):
        cadu = data[i:i + CADU_LEN]
        assert cadu[:4] == bytes.fromhex("1ACFFC1D")
        yield cadu


def _dummy_protect(state: dict):
    """Python twin of DummySecurity::protect (test_uslp.cpp)."""
    def protect(hdr: bytes, plain: bytes) -> bytes:
        state["sn"] += 1
        sh = (b"\xD5\xD5" + bytes([len(hdr)]) + len(plain).to_bytes(2, "big") +
              state["sn"].to_bytes(4, "big") + b"\xA5" * 5)
        ct = bytes(x ^ 0x3C for x in plain)
        return sh + ct + crc16_ccitt(hdr + sh + ct).to_bytes(2, "big") + b"\x5A" * 14
    return protect


def _check_uslp_tm_header(f: TransferFrame):
    """Explicit TFPH field values of every downlink USLP frame."""
    r = f.raw
    assert r[0] >> 4 == 0b1100 and f.version == 12                     # TFVN '1100'
    assert ((r[0] & 0xF) << 12 | r[1] << 4 | r[2] >> 4) == 0x00A7 == f.scid
    assert f.src_dest == 0 and f.map_id == 0 and (r[3] & 1) == 0          # source, MAP 0, EoFPH 0
    assert int.from_bytes(r[4:6], "big") == 199 and f.frame_len == TM_FRAME_LEN
    assert r[6] == 0x09 and f.ocf_flag and f.vcf_count_len == 1           # OCF 1, VCF length 1
    assert not f.bypass and not f.pcc and f.hdr_len == 8 and f.mcfc is None
    assert r[7] == f.vcfc


def _run_stream(cadus: Path, packets: Path, fmt: str, secured: bool):
    extract = {v: PacketExtractor(v) for v in (0, 1, 3)}
    got: dict[int, list[str]] = {0: [], 1: [], 3: []}
    loss = FrameLossTracker()
    last_sn = 0
    oid = 0
    hdr_len = 8 if fmt == "USLP" else 6
    geometry = (SEC_HDR, SEC_TRL) if secured else (0, 0)
    for cadu in _cadus(cadus):
        res = decode_codeblock(cadu[4:], *geometry)
        assert res.ok and res.rs_corrected == 0, res.reason
        f = res.frame
        assert f.format == fmt
        assert loss.update(f) == 0, "frame counter gap (rollback after a refusal failed?)"
        if fmt == "USLP":
            _check_uslp_tm_header(f)
        if f.is_oid:
            oid += 1
            assert not f.secured and f.ident == "DE N0CALL VGQ-1"
            if fmt == "USLP":
                assert (f.vcid, f.rule, f.upid, f.fhp_raw) == (VCID_OID, 0, UPID_IDLE, FHP_NONE)
            continue
        if secured:
            assert f.secured and f.data == b""
            sh, st, prot = f.sec_header, f.sec_trailer, f.protected
            assert sh[:2] == b"\xD5\xD5" and sh[2] == hdr_len == len(f.primary_header)
            assert int.from_bytes(sh[3:5], "big") == len(prot) == (156 if fmt == "USLP" else 158)
            sn = int.from_bytes(sh[5:9], "big")
            assert sn > last_sn and sh[9:] == b"\xA5" * 5
            last_sn = sn
            assert st[:2] == crc16_ccitt(f.raw[:hdr_len + SEC_HDR + len(prot)]).to_bytes(2, "big")
            assert st[2:] == b"\x5A" * 14
            f = f.with_plaintext(bytes(x ^ 0x3C for x in prot))
        assert len(f.data) == data_field_len(fmt, (SEC_HDR + SEC_TRL) if secured else 0)
        if fmt == "USLP":
            assert f.rule == 0 and f.upid == 0
        got[f.vcid] += [p.raw.hex() for p in extract[f.vcid].push(f.vcfc, f.fhp, f.data)]
    assert oid > 0
    assert got == _expected_packets(packets)
    return got


def test_flight_uslp_cadus_decode_bit_exact(vectors):
    got = _run_stream(vectors / "uslp_cadus.bin", vectors / "uslp_packets.txt", "USLP", False)
    assert all(got[v] for v in (0, 1, 3)), "packets on VC0, VC1 and VC3 reassembled"


def test_ground_builder_reproduces_flight_uslp_frames(vectors):
    for cadu in _cadus(vectors / "uslp_cadus.bin"):
        f = decode_codeblock(cadu[4:]).frame
        if f.is_oid:
            again = build_uslp_oid_frame(f.vcfc, f.clcw.word, IDENT)
        else:
            again = build_uslp_frame(f.vcid, f.vcfc, f.fhp_raw, f.data, f.clcw.word, upid=f.upid)
        assert again == f.raw
        assert encode_cadu(again) == cadu


def test_flight_uslp_sdls_stream(vectors):
    _run_stream(vectors / "uslp_sec_cadus.bin", vectors / "uslp_sec_packets.txt", "USLP", True)
    # The ground builder with the same protector reproduces every protected frame.
    state = {"sn": 0}
    for cadu in _cadus(vectors / "uslp_sec_cadus.bin"):
        f = decode_codeblock(cadu[4:], SEC_HDR, SEC_TRL).frame
        if f.is_oid:
            continue
        p = f.with_plaintext(bytes(x ^ 0x3C for x in f.protected))
        state["sn"] = int.from_bytes(f.sec_header[5:9], "big") - 1
        assert build_uslp_frame(p.vcid, p.vcfc, p.fhp_raw, p.data, p.clcw.word,
                                protect=_dummy_protect(state)) == f.raw


def test_flight_tm_sdls_stream(vectors):
    _run_stream(vectors / "tm_sec_cadus.bin", vectors / "tm_sec_packets.txt", "TM", True)


def _vector_lines(vectors):
    out = {}
    for line in (vectors / "uslp_vectors.txt").read_text().splitlines():
        parts = line.split()
        if parts:
            kv = dict(x.split("=") for x in parts[2].split(",")) if len(parts) > 2 else {}
            out[parts[0]] = (bytes.fromhex(parts[1]), kv)
    return out


def test_ground_uslp_tc_frames_equal_flight(vectors):
    vec = _vector_lines(vectors)
    for name in ("TC_AD", "TC_BD", "TC_BC_UNLOCK", "TC_BC_SETVR"):
        flight, kv = vec[name]
        seq, bypass, control = int(kv["seq"]), kv["bypass"] == "1", kv["control"] == "1"
        data = bytes.fromhex(kv["data"])
        ground = build_tc_frame(data, seq, bypass, control, int(kv["vcid"]), framing="USLP")
        assert ground == flight, name
        # Explicit header fields: TFVN 12, SCID 0x00A7, destination flag, frame length.
        h = UslpHeader.unpack(flight)
        assert (flight[0] >> 4, h.scid, h.dest, h.frame_len - 1) == (12, 0x00A7, True, len(flight) - 1)
        assert h.vcf_len == (0 if bypass else 1) and not h.ocf and h.pcc == control
        info = parse_tc_frame(flight)
        assert info and info.format == "USLP" and info.hdr_len == 7 + h.vcf_len
        assert (info.seq, info.bypass, info.control, info.vcid, info.data) == \
            (seq, bypass, control, int(kv["vcid"]), data)
        assert info.upid == (1 if control else 0)
    assert vec["TC_BC_UNLOCK"][0][7] == 0xE1 and vec["TC_AD"][0][8] == 0xE0   # rule 111 | UPID


def test_flight_uslp_cltu_matches_ground(vectors):
    vec = _vector_lines(vectors)
    flight_cltu, frame = vec["TC_AD_CLTU"][0], vec["TC_AD"][0]
    info, st = cltu_decode(flight_cltu)
    assert st["rejected"] == 1 and info[:len(frame)] == frame      # tail ends the CLTU
    assert parse_tc_frame(info).seq == 5
    assert build_cltu(frame) == flight_cltu


# ---------------------------------------------------------------- pure Python

def test_uslp_header_roundtrip():
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randrange(8)
        h = UslpHeader(scid=rng.randrange(1 << 16), dest=bool(rng.randrange(2)), vcid=rng.randrange(64),
                       map_id=rng.randrange(16), frame_len=rng.randrange(8, 4000),
                       bypass=bool(rng.randrange(2)), pcc=bool(rng.randrange(2)),
                       ocf=bool(rng.randrange(2)), vcf_len=n, vcf_count=rng.randrange(1 << (8 * n)))
        b = h.pack()
        assert len(b) == 7 + n and UslpHeader.unpack(b + b"\x00") == h
    with pytest.raises(ValueError):
        UslpHeader.unpack(bytes([0xC0, 0x0A, 0x70, 0x01, 0, 0, 0]))      # truncated (EoFPH 1)


def test_ident_text_extraction():
    assert ident_text(idle_fill(IDENT, TFDZ_LEN)) == "DE N0CALL VGQ-1"
    assert ident_text(idle_fill(IDENT, 188)) == "DE N0CALL VGQ-1"
    assert ident_text(idle_fill(None, 188)) is None                       # 0x55 fill ("UUU...")
    assert ident_text(bytes(range(188))) is None
    f = TransferFrame.parse(build_oid_frame(3, 4, 0, IDENT))
    assert f.is_oid and f.vcid == 7 and f.fhp == 0x7FE and f.ident == "DE N0CALL VGQ-1"


def test_auto_detect_and_forced_framing():
    zone = build_packet(0x10, 1, b"\x01\x02", CucTime(1, 2))
    tm = encode_cadu(build_frame(0, 9, 3, 0, zone + bytes(188 - len(zone)), Clcw.pack(report_value=4)))
    us = encode_cadu(build_uslp_frame(0, 3, 0, zone + bytes(183 - len(zone)), Clcw.pack(report_value=4)))
    for cadu, fmt in ((tm, "TM"), (us, "USLP")):
        r = decode_codeblock(cadu[4:])
        assert r.ok and r.frame.format == fmt and r.frame.vcfc == 3 and r.frame.fhp == 0
        assert r.frame.clcw.report_value == 4 and r.frame.data.startswith(zone)
        assert decode_codeblock(cadu[4:], framing=fmt).ok
        other = "USLP" if fmt == "TM" else "TM"
        bad = decode_codeblock(cadu[4:], framing=other)
        assert not bad.ok and "framing" in bad.reason
    assert decode_codeblock(tm[4:]).frame.timecorr_key == 9
    assert decode_codeblock(us[4:]).frame.timecorr_key == 3


def test_uslp_fhp_conventions_and_foreign_frames():
    f = TransferFrame.parse(build_uslp_frame(1, 0, 0x7FF, bytes(183), 0))
    assert f.fhp_raw == FHP_NONE and f.fhp == 0x7FF and f.timecorr_key is None
    oid = TransferFrame.parse(build_uslp_oid_frame(0, 0))
    assert oid.is_oid and oid.fhp == 0x7FE and oid.ident is None
    foreign = encode_cadu(build_uslp_frame(0, 0, 0, bytes(183), 0, scid=0x1234))
    assert decode_codeblock(foreign[4:]).reason == "foreign SCID 0x1234"
    tc_like = bytearray(build_uslp_frame(0, 0, 0, bytes(183), 0))
    tc_like[2] |= 0x08                                                # destination flag
    tc_like[198:] = crc16_ccitt(bytes(tc_like[:198])).to_bytes(2, "big")
    assert not decode_codeblock(encode_cadu(bytes(tc_like))[4:]).ok


def test_secured_frame_geometry_and_plaintext():
    state = {"sn": 0}
    plain_zone = bytes(range(153))
    raw = build_uslp_frame(2, 7, 5, plain_zone, 0, protect=_dummy_protect(state))
    f = TransferFrame.parse(raw, SEC_HDR, SEC_TRL)
    assert f.secured and f.fhp is None and f.rule is None and f.data == b""
    assert f.primary_header == raw[:8] and len(f.sec_header) == 14 and len(f.sec_trailer) == 16
    assert len(f.protected) == 156
    p = f.with_plaintext(bytes(x ^ 0x3C for x in f.protected))
    assert (p.fhp, p.rule, p.upid, p.data, p.secured) == (5, 0, 0, plain_zone, False)
    with pytest.raises(ValueError):
        p.with_plaintext(b"")
    oid = TransferFrame.parse(build_uslp_oid_frame(0, 0, IDENT), SEC_HDR, SEC_TRL)
    assert not oid.secured and oid.ident == "DE N0CALL VGQ-1"          # OID frames never carry SDLS


def test_frame_loss_tracker_per_vc_for_uslp():
    t = FrameLossTracker()
    frames = [build_uslp_frame(0, 0, 0, bytes(183), 0), build_uslp_frame(1, 0, 0, bytes(183), 0),
              build_uslp_frame(0, 1, 0, bytes(183), 0), build_uslp_frame(0, 4, 0, bytes(183), 0),
              build_uslp_frame(1, 1, 0, bytes(183), 0)]
    assert [t.update(TransferFrame.parse(f)) for f in frames] == [0, 0, 0, 2, 0]


def test_farm_on_uslp_frames():
    noop = b"\x18\xc0\xc0\x00\x00\x00\x01"
    farm = Farm1Model()

    def ad(ns):
        return farm.on_frame(parse_tc_frame(build_tc_frame(noop, ns, framing="USLP")))
    assert ad(0) == "ACCEPT" and farm.vr == 1
    assert ad(0) == "DISCARD_NEG_WINDOW"
    assert ad(3) == "DISCARD_RETRANSMIT"
    assert ad(120) == "DISCARD_LOCKOUT"
    bc = parse_tc_frame(build_tc_frame(bc_unlock(), 0, bypass=True, control=True, framing="USLP"))
    assert bc.upid == 1 and farm.on_frame(bc) == "CONTROL_UNLOCK"
    bc = parse_tc_frame(build_tc_frame(bc_set_vr(5), 0, bypass=True, control=True, framing="USLP"))
    assert farm.on_frame(bc) == "CONTROL_SET_VR" and farm.vr == 5
    assert ad(5) == "ACCEPT"
    with pytest.raises(ValueError):
        build_tc_frame(bc_unlock(), 0, bypass=False, control=True, framing="USLP")


def _uslp_tc(tfdf_byte: int, data: bytes, **hdr) -> bytes:
    h = UslpHeader(dest=hdr.pop("dest", True), **hdr)
    h.frame_len = h.length + 1 + len(data) + 2
    f = h.pack() + bytes([tfdf_byte]) + data
    return f + crc16_ccitt(f).to_bytes(2, "big")


def test_uslp_tc_rejections():
    noop = b"\x18\xc0\xc0\x00\x00\x00\x01"
    assert parse_tc_frame(_uslp_tc(0xE0, noop, vcf_len=1, vcf_count=2)).seq == 2
    assert parse_tc_frame(_uslp_tc(0xE0, noop, dest=False, vcf_len=1)) is None     # source flag
    assert parse_tc_frame(_uslp_tc(0xE0, noop, vcf_len=0)) is None                 # AD w/o N(S)
    assert parse_tc_frame(_uslp_tc(0xE1, b"\x00", pcc=True, vcf_len=1)) is None    # PCC on AD
    assert parse_tc_frame(_uslp_tc(0x00, noop, vcf_len=1)) is None                 # rule 000
    assert parse_tc_frame(_uslp_tc(0xE1, noop, vcf_len=1)) is None                 # UPID 1 w/o PCC
    bad = bytearray(_uslp_tc(0xE0, noop, vcf_len=1))
    bad[-1] ^= 1
    assert parse_tc_frame(bytes(bad)) is None                                       # FECF


def test_tc_open_tfdf_deferred_for_sdls():
    noop = b"\x18\xc0\xc0\x00\x00\x00\x01"
    raw = build_tc_frame(noop, 9, framing="USLP")
    closed = parse_tc_frame(raw, open_tfdf=False)
    assert not closed.tfdf_open and closed.data == b"\xE0" + noop and closed.hdr_len == 8
    opened = tc_open_tfdf(closed)
    assert opened.tfdf_open and opened.data == noop and tc_open_tfdf(opened) is opened
    tc = parse_tc_frame(build_tc_frame(noop, 9), open_tfdf=False)
    assert tc.format == "TC" and tc.hdr_len == 5 and tc.tfdf_open and tc.data == noop


def test_uslp_tc_with_sdls_auth_and_custom_protector():
    key = bytes(range(32))
    noop = b"\x18\xc0\xc0\x00\x00\x00\x01"
    tx = SdlsSender(key, spi=1)
    tx.sn = 6
    f = build_tc_frame(noop, 3, sdls=tx, framing="USLP")
    hdr, sh = f[:8], f[8:8 + SDLS_HDR_LEN]
    tfdf = f[8 + SDLS_HDR_LEN:-2 - SDLS_MAC_LEN]
    assert sh == b"\x00\x01" + (7).to_bytes(4, "big") and tfdf == b"\xE0" + noop
    assert f[-2 - SDLS_MAC_LEN:-2] == mac(key, hdr, sh, tfdf)          # AAD = whole TFPH (8)
    info = parse_tc_frame(f, open_tfdf=False)
    assert info.hdr_len == 8 and info.data == sh + tfdf + f[-18:-2]

    class Protector:                       # the protect_tc / tc_overhead hook (SDLS AE)
        enabled = True
        calls = []

        def tc_overhead(self):
            return 30

        def protect_tc(self, header, plaintext):
            self.calls.append((header, plaintext))
            return b"\x00\x02" + bytes(12) + plaintext + b"\xEE" * 16
    p = Protector()
    f = build_tc_frame(noop, 4, sdls=p, framing="USLP")
    assert len(f) == 8 + 30 + 1 + len(noop) + 2 and p.calls == [(f[:8], b"\xE0" + noop)]
    f = build_tc_frame(noop, 4, sdls=p)                               # TC v1, same hook
    assert len(f) == 5 + 30 + len(noop) + 2 and p.calls[-1] == (f[:5], noop)
    with pytest.raises(ValueError):                                   # 8+30+1+24+2 > 64
        build_tc_frame(bytes(24), 4, sdls=p, framing="USLP")
    assert len(p.calls) == 2, "no protection (no SN/IV consumed) for a frame that is too long"
    assert SPACECRAFT_ID == 0x0A7
