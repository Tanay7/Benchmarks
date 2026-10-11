"""Flight <-> ground cross-check, and the ground dictionary's own consistency (Phase B PB-8.5 WS3).

Part 1 builds tools/host_test (the EXACT spacecraft protocol sources compiled for the host),
runs it, and decodes what the flight code wrote with the independent Python ground stack:
  * the v2 vectors (cadus.bin / packets.txt, rs_vector, cltu, tlm_vectors, sdls_frame);
  * the four integration downlinks of tools/host_test/test_integration.cpp, built by the
    flight Downlink (TM + SDLS AE epoch 7, USLP + AE, TM with the null SA, TM clear): every
    frame and packet bit-exact, the SDLS verdicts, the OID identification, one packet of
    every APID on its real-time VC, both SPECBURST encodings, a COMPLETE CFDP transfer of an
    SSR export, and the expected values of integration_expect.txt;
  * command_table.txt: the flight parse_user_data() verdict of every opcode at its range
    edges against cmdparse.check_user_data() (encode_dictionary + checks);
  * the uplink round trip: ground-built CLTUs (TC / USLP x NONE / AUTH / AE, COP-1 control,
    replayed SN, bad MAC) written to ground_tc.txt, run through the flight tc_accept() chain,
    flight verdicts asserted.
  * the flight headers' constants (APIDs, packet lengths, opcodes, error codes, VCs, bit
    names) against dictionary.py.
Part 2 runs without a C++ toolchain: dictionary structure, decom decode/encode, the ground
command verdict and the offline replay pipeline on a downlink built with the ground's
reference builders.
"""
from __future__ import annotations

import math
import random
import re
import shutil
import struct
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from dssq import cmdparse, replay, spectral
from dssq import dictionary as dd
from dssq.ccsds import ASM, CADU_LEN, NUM_DATA_VCS, VC_NAMES
from dssq.ccsds import cfdp as cf
from dssq.ccsds import rice
from dssq.ccsds import sdls as sd
from dssq.ccsds.packets import PacketExtractor, SpacePacket, build_idle_packet, build_packet
from dssq.ccsds.tc import (bc_set_vr, bc_unlock, build_cltu, build_tc_frame, build_uslp_tc_frame,
                           cltu_decode, parse_tc_frame)
from dssq.ccsds.timecode import CucTime
from dssq.ccsds.tm import (Clcw, build_frame, build_oid_frame, build_uslp_frame, build_uslp_oid_frame,
                           data_field_len, decode_codeblock, encode_cadu)
from dssq.decom import Decommutator, encode

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"
SRC = ROOT / "spacecraft" / "vgq1_flight" / "sketch" / "src"
INTEGRATION_SRC = HOST / "test_integration.cpp"
KEY = bytes(range(32))                      # test key 00 01 .. 1F (PB-8.5 WS1)
IDENT = "DE N0CALL VGQ-1"


# =============================================================================
# Part 1 - vectors written by the flight code
# =============================================================================
@pytest.fixture(scope="module")
def vectors(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("vectors")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    subprocess.run([str(HOST / "host_test"), str(out)], check=True, stdout=subprocess.DEVNULL)
    return out


def _integration_file(vectors: Path, name: str) -> Path:
    """A vector written by host_test (test_integration.cpp and the packet writers). A missing
    file FAILS: a skip would turn the flight <-> ground bit-exact checks green when a
    generator is deleted or stops writing (only a missing C++ toolchain skips, in `vectors`)."""
    assert INTEGRATION_SRC.exists(), f"{INTEGRATION_SRC} missing: the integration vectors cannot be generated"
    path = vectors / name
    if not path.exists():
        pytest.fail(f"host_test did not write {name}")
    return path


def test_flight_cadus_decode_bit_exact(vectors: Path) -> None:
    data = (vectors / "cadus.bin").read_bytes()
    assert len(data) % CADU_LEN == 0
    extract = {0: PacketExtractor(0), 1: PacketExtractor(1)}
    got: dict[int, list[str]] = {0: [], 1: []}
    mcfc_prev = None
    for i in range(0, len(data), CADU_LEN):
        cadu = data[i:i + CADU_LEN]
        assert cadu[:4] == ASM
        res = decode_codeblock(cadu[4:])
        assert res.ok and res.rs_corrected == 0, res.reason
        f = res.frame
        assert f is not None and f.mcfc is not None
        if mcfc_prev is not None:
            assert f.mcfc == (mcfc_prev + 1) & 0xFF
        mcfc_prev = f.mcfc
        if f.vcid in extract and f.fhp is not None:
            got[f.vcid] += [p.raw.hex() for p in extract[f.vcid].push(f.vcfc, f.fhp, f.data)]
    expected: dict[int, list[str]] = {0: [], 1: []}
    for line in (vectors / "packets.txt").read_text().split("\n"):
        if line.strip():
            vc, hx = line.split()
            expected[int(vc)].append(hx)
    assert got == expected


def test_flight_rs_vector(vectors: Path) -> None:
    from dssq.ccsds import rs
    cb = bytes.fromhex((vectors / "rs_vector.txt").read_text().strip())
    assert rs.encode(cb[:200]) == cb[200:]


def test_flight_cltu_matches_ground_encoder(vectors: Path) -> None:
    flight = bytes.fromhex((vectors / "cltu.txt").read_text().strip())
    info, _st = cltu_decode(flight)
    tf = parse_tc_frame(info)
    assert tf is not None
    assert build_cltu(info[:tf.length]) == flight


def _same(got: Any, want: str) -> bool:
    """The tlm_vectors.txt comparison: lists joined by ';', floats to 1e-3 / 1e-5 relative."""
    if isinstance(got, list):
        got = ";".join(str(x) for x in got)
    if isinstance(got, float):
        if want == "None":
            return False
        w = float(want)
        return abs(got - w) <= max(1e-3, abs(w) * 1e-5)
    return str(got) == want


def test_flight_telemetry_packets_decommutate(vectors: Path) -> None:
    """tlm_vectors.txt holds packets built by the FLIGHT packers with known values."""
    path = _integration_file(vectors, "tlm_vectors.txt")
    dec = Decommutator()
    n = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        name, hx, expect = line.split(" ", 2)
        pkt = SpacePacket.parse(bytes.fromhex(hx))
        assert dec.packet_name(pkt.apid) == name
        values = dec.decode(pkt)
        for kv in expect.split(","):
            k, v = kv.split("=")
            assert _same(values[k], v), (name, k, values[k], v)
        if pkt.apid in dd.PACKETS:                       # bit-exact inverse
            assert encode(pkt.apid, values) == pkt.user_data or pkt.apid == dd.APID_SPEC, name
        n += 1
    assert n >= 9


def test_flight_sdls_frame_verifies_on_ground(vectors: Path) -> None:
    """Frame built with the flight HMAC code must verify with Python's hmac module."""
    frame = bytes.fromhex((vectors / "sdls_frame.txt").read_text().strip())
    info = parse_tc_frame(frame)
    assert info is not None
    rx = sd.SdlsReceiver(KEY, spi=1)
    body, verdict = rx.process(frame, info.data)
    assert verdict == "OK" and body == bytes.fromhex("18c0c000000001") and rx.last_sn == 7


def test_ground_sdls_frame_layout_matches_flight(vectors: Path) -> None:
    """Ground-built protected frame equals the flight-built one for the same key/SN."""
    flight = bytes.fromhex((vectors / "sdls_frame.txt").read_text().strip())
    tx = sd.SdlsSender(KEY, spi=1)
    tx.sn = 6                                   # next_sn() -> 7
    assert build_tc_frame(bytes.fromhex("18c0c000000001"), seq=0, sdls=tx) == flight


# ---- the flight headers' constants ----------------------------------------------------
def _enum(text: str, pattern: str) -> dict[str, int]:
    return {m.group(1): int(m.group(2), 0) for m in re.finditer(pattern, text)}


def test_flight_header_constants_match_dictionary() -> None:
    tlm = (SRC / "fsw" / "tlm_packets.h").read_text()
    apids = _enum(tlm, r"\bAPID_(\w+)\s*=\s*(0x[0-9A-Fa-f]+)")
    assert apids == {"HK": dd.APID_HK, "RF": dd.APID_RF, "EVR": dd.APID_EVR, "CMDVER": dd.APID_CMDVER,
                     "TIMECORR": dd.APID_TIMECORR, "RFSCAN": dd.APID_RFSCAN, "MAG": dd.APID_MAG,
                     "ATT": dd.APID_ATT, "SPEC": dd.APID_SPEC, "ENV": dd.APID_ENV,
                     "SPECBURST": dd.APID_SPECBURST, "CFDP": dd.APID_CFDP, "TC": dd.APID_TC}
    lens = _enum(tlm, r"\bk(\w+)Len\s*=\s*(\d+)\s*;")
    for flight, name in (("Hk", "HK"), ("Rf", "RF"), ("CmdVer", "CMDVER"), ("TimeCorr", "TIMECORR"),
                         ("Mag", "MAG"), ("Att", "ATT"), ("Env", "ENV")):
        apid = next(a for a, s in dd.PACKETS.items() if s["name"] == name)
        assert lens[flight] == dd.packet_len(apid), name
    consts = _enum(tlm, r"\b(kEvrTextMax|kRfscanMaxChannels|kMaxPayload|kCmdVerStageMask|kCmdVerSourceShift)"
                        r"\s*=\s*(0x[0-9A-Fa-f]+|\d+)")
    stage = {n: (lsb, w) for n, lsb, w in dd.BITFIELDS[dd.APID_CMDVER]["stage"]}
    assert consts == {"kEvrTextMax": dd.EVR_TEXT_MAX, "kRfscanMaxChannels": dd.RFSCAN_MAX_CHANNELS,
                      "kMaxPayload": dd.CFDP_PDU_MAX, "kCmdVerStageMask": (1 << stage["stage"][1]) - 1,
                      "kCmdVerSourceShift": stage["cmd_source"][0]}
    assert stage["stage"][0] == 0                                    # I1: stage b1..0, source b7..6
    src = _enum(tlm, r"\bCMD_SRC_(\w+)\s*=\s*(\d+)")
    assert src == {"AD": dd.CMD_SOURCE_AD, "BD": dd.CMD_SOURCE_BD, "STORED": dd.CMD_SOURCE_STORED}
    assert "kCmdVerSeqBypass" not in tlm and "kCmdVerSeqStored" not in tlm  # retired tc_seq sentinels
    fdir = _enum(tlm, r"\bFDIR_(\w+)\s*=\s*1u\s*<<\s*(\d+)")
    assert [n for n, _ in sorted(fdir.items(), key=lambda kv: kv[1])] == dd.FDIR_BITS
    sns = _enum(tlm, r"\bSNS_(\w+)\s*=\s*1u\s*<<\s*(\d+)")
    assert [n for n, _ in sorted(sns.items(), key=lambda kv: kv[1])] == dd.SENSOR_BITS
    for prefix, names in (("MAG_F_", dd.MAG_FLAG_BITS), ("ATT_F_", dd.ATT_FLAG_BITS),
                          ("ENV_F_", dd.ENV_FLAG_BITS), ("RF_F_", dd.RF_FLAG_BITS)):
        bits = sorted(_enum(tlm, rf"\b{prefix}(\w+)\s*=\s*(0x[0-9A-Fa-f]+)").values())
        assert bits == [1 << i for i in range(len(names))], prefix
    cmd = (SRC / "fsw" / "commands.h").read_text()
    ops = _enum(cmd, r"\bOP_(\w+)\s*=\s*(0x[0-9A-Fa-f]+)")
    assert ops == {c.mnemonic: c.opcode for c in dd.COMMANDS.values()}
    errs = _enum(cmd, r"\bCE_(\w+)\s*=\s*(\d+)")
    assert set(errs.values()) == set(dd.CMD_ERRORS)
    vcs = _enum((SRC / "ccsds" / "ccsds_types.h").read_text(), r"\b(VC_\w+|kNumDataVcs)\s*=\s*(\d+)")
    assert vcs == {"VC_RT_ENG": 0, "VC_RT_SCI": 1, "VC_PLAYBACK": 2, "VC_FILE": 3, "VC_IDLE": 7,
                   "kNumDataVcs": NUM_DATA_VCS}
    spec = (SRC / "fsw" / "spectral.h").read_text()
    assert _enum(spec, r"\b(kSpecBurstHdrLen|kSpecV31Len)\s*=\s*(\d+)") == {
        "kSpecBurstHdrLen": spectral.BURST_HDR_LEN, "kSpecV31Len": spectral.SPEC_V31_LEN}


# ---- integration downlinks (test_integration.cpp) --------------------------------------
# stream -> (file, framing, SDLS mode as in integration_expect.txt, verdict of every data frame)
STREAMS: dict[str, tuple[str, str, str, str]] = {
    "tm_ae": ("integration_tm_ae.bin", "TM", "AE", "OK"),
    "uslp_ae": ("integration_uslp_ae.bin", "USLP", "AE", "OK"),
    "tm_nullsa": ("integration_tm_nullsa.bin", "TM", "NULL", "UNPROTECTED"),
    "tm_clear": ("integration_tm_clear.bin", "TM", "NONE", "NONE"),
}
REQUIRED = {"HK", "RF", "EVR", "CMDVER", "TIMECORR", "RFSCAN", "MAG", "ATT", "SPEC", "ENV", "SPECBURST"}


def _decode_stream(data: bytes, framing: str, sdls_mode: str) -> dict[str, Any]:
    """The ground downlink chain (decode_codeblock with the AE geometry, SdlsTmReceiver,
    PacketExtractor per VC 0..3) on one CADU stream; every frame must decode cleanly."""
    assert data and len(data) % CADU_LEN == 0
    ae = sdls_mode in ("AE", "NULL")                     # the null SA keeps the AE geometry
    rx = sd.SdlsTmReceiver(KEY if ae else None, "AE" if ae else "NONE")
    geometry = (sd.AE_HDR_LEN, sd.AE_TAG_LEN) if ae else (0, 0)
    ex = {vc: PacketExtractor(vc) for vc in range(NUM_DATA_VCS)}
    out: dict[str, Any] = {"verdicts": Counter(), "idents": Counter(), "cadus": len(data) // CADU_LEN,
                           "packets": {vc: [] for vc in range(NUM_DATA_VCS)}, "formats": set()}
    for i in range(0, len(data), CADU_LEN):
        cadu = data[i:i + CADU_LEN]
        assert cadu[:4] == ASM
        r = decode_codeblock(cadu[4:], *geometry, framing=framing)
        assert r.ok and r.rs_corrected == 0, r.reason
        f = r.frame
        assert f is not None
        out["formats"].add(f.format)
        if f.is_oid:
            out["idents"][f.ident] += 1
            continue
        verdict = "NONE"
        if f.secured:
            opened, res = rx.open_frame(f)
            verdict = res.verdict
            assert opened is not None, verdict
            f = opened
        out["verdicts"][verdict] += 1
        assert f.fhp is not None and f.vcid in ex
        out["packets"][f.vcid] += ex[f.vcid].push(f.vcfc, f.fhp, f.data)
    for e in ex.values():
        assert e.stats["bad_length"] == 0 and e.stats["vcfc_gaps"] == 0, e.stats
    return out


def _kv(text: str) -> dict[str, str]:
    return {} if text == "-" else dict(item.split("=", 1) for item in text.split(","))


def _read_expect(path: Path) -> dict[str, dict[str, Any]]:
    """integration_expect.txt, the format documented in test_integration.cpp:
      stream <file> framing=.. sdls=<AE|NULL|NONE> epoch=.. cadus=.. oid=.. protected=.. null_sa=.. ident=<hex>
      pkt <file> <vc> <name> <packet hex> <k=v,...|->      (transmission order per VC)
      cfdp <file> seq=.. name=.. size=.. checksum=.. pdus=.. content=<hex>
    Any other line is an error (a format change must not pass silently)."""
    out: dict[str, dict[str, Any]] = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        toks = line.split()
        if not toks or toks[0].startswith("#"):
            continue
        kind, file = toks[0], toks[1]
        rec = out.setdefault(file, {"stream": None, "pkts": {vc: [] for vc in range(NUM_DATA_VCS)}, "cfdp": None})
        if kind == "stream":
            rec["stream"] = dict(t.split("=", 1) for t in toks[2:])
        elif kind == "pkt" and len(toks) == 6:
            rec["pkts"][int(toks[2])].append((toks[3], toks[4].lower(), _kv(toks[5])))
        elif kind == "cfdp":
            rec["cfdp"] = dict(t.split("=", 1) for t in toks[2:])
        else:
            pytest.fail(f"{path.name}:{n}: unrecognised record {line[:60]!r}")
    return out


@pytest.fixture(scope="module")
def integration(vectors: Path) -> dict[str, dict[str, Any]]:
    expect = _read_expect(_integration_file(vectors, "integration_expect.txt"))
    out = {}
    for sid, (fn, framing, mode, _v) in STREAMS.items():
        assert fn in expect and expect[fn]["stream"] is not None, f"{fn} missing in integration_expect.txt"
        st = expect[fn]["stream"]
        assert (st["framing"], st["sdls"]) == (framing, mode), (sid, st)
        out[sid] = _decode_stream(_integration_file(vectors, fn).read_bytes(), framing, mode) | {"expect": expect[fn]}
    return out


def _spec_reencode_ok(user: bytes, values: dict[str, Any]) -> bool:
    """SPEC v3.1 re-encodes bit-exactly except the non-injective log code 0x8000."""
    want = bytearray(user)
    for k in range(36):
        if want[10 + 2 * k:12 + 2 * k] == b"\x80\x00":
            want[10 + 2 * k:12 + 2 * k] = b"\x00\x00"
    return spectral.pack_spec_v31(values) == bytes(want)


def test_integration_streams_frames_verdicts_ident(integration: dict[str, dict[str, Any]]) -> None:
    for sid, (_fn, framing, _mode, verdict) in STREAMS.items():
        s = integration[sid]
        st = s["expect"]["stream"]
        assert s["formats"] == {framing}, sid
        assert set(s["verdicts"]) == {verdict}, (sid, s["verdicts"])
        assert s["cadus"] == int(st["cadus"]) and sum(s["idents"].values()) == int(st["oid"]), (sid, st)
        assert s["verdicts"]["OK"] == int(st["protected"]) and s["verdicts"]["UNPROTECTED"] == int(st["null_sa"])
        assert bytes.fromhex(st["ident"]).decode("ascii").strip() == IDENT
        assert set(s["idents"]) == {IDENT}, (sid, s["idents"])


def test_integration_packets_bit_exact_with_expected_values(integration: dict[str, dict[str, Any]]) -> None:
    dec = Decommutator()
    for sid in STREAMS:
        s = integration[sid]
        names: Counter[str] = Counter()
        encodings: set[int] = set()
        for vc in range(NUM_DATA_VCS):
            got, want = s["packets"][vc], s["expect"]["pkts"][vc]
            assert [p.raw.hex() for p in got] == [hx for _n, hx, _kv in want], (sid, vc)
            for p, (name, _hx, kv) in zip(got, want, strict=True):
                assert dec.packet_name(p.apid) == name
                # Real time on the APID's VC, SSR playback on VC2; CFDP only on VC3, never recorded.
                assert vc in (dd.APID_VC[p.apid], 2) and (vc != 2 or p.apid != dd.APID_CFDP), (sid, name, vc)
                assert p.time is not None and p.seq_flags == 3
                values = dec.decode(p)
                for k, v in kv.items():
                    assert k in values, f"{sid} {name}: the ground decoder has no field {k}"
                    assert _same(values[k], v), (sid, name, k, values[k], v)
                if p.apid in dd.PACKETS:
                    ok = (_spec_reencode_ok(p.user_data, values) if p.apid == dd.APID_SPEC
                          else encode(p.apid, values) == p.user_data)
                    assert ok, (sid, name)
                if vc == 2:
                    continue
                names[name] += 1
                if p.apid == dd.APID_HK:
                    assert values["beacon_level"] == 2, sid
                if p.apid == dd.APID_CMDVER:                 # a stored-sequence command (I1)
                    assert values["cmd_source"] == dd.CMD_SOURCE_STORED and values["tc_seq"] == 0
                if p.apid == dd.APID_MAG:
                    assert values["flags"] == 0x30
                if p.apid == dd.APID_ATT:
                    assert values["flags"] == 0x7F
                if p.apid == dd.APID_SPECBURST:
                    encodings.add(values["encoding"])
                    smp = [(x["dt_ms"], x["raw"]) for x in values["samples"]]
                    hdr = (values["t0_ms"], values["rate_dhz"], values["gain_code"], values["int_cycles"])
                    assert spectral.pack_specburst(*hdr, smp, encoding=values["encoding"]) == p.user_data
                    assert spectral.pack_specburst(*hdr, smp) == p.user_data       # same choice as flight
        required = REQUIRED | ({"CFDP"} if s["expect"]["cfdp"] else set())
        assert required <= set(names), (sid, names)
        assert encodings == {0, 1}, (sid, encodings)


def _cfdp_receive(packets: list[SpacePacket], files: Path) -> list[dict[str, Any]]:
    files.mkdir(parents=True, exist_ok=True)
    rx = cf.Receiver(files)
    for p in packets:
        if p.apid == dd.APID_CFDP:
            rx.receive(p.user_data, 0.0)
    assert rx.counters["malformed"] == 0 and rx.counters["foreign"] == 0, rx.counters
    return rx.snapshot()["transactions"]


def _split_packets(blob: bytes) -> list[SpacePacket]:
    """An SSR export = concatenated Space Packets; every octet must belong to one."""
    out, i = [], 0
    for _ in range(len(blob)):
        if i == len(blob):
            break
        assert len(blob) - i >= 7, "trailing octets after the last packet"
        n = 7 + int.from_bytes(blob[i + 4:i + 6], "big")
        assert i + n <= len(blob), "packet runs past the end of the file"
        out.append(SpacePacket.parse(blob[i:i + n]))
        i += n
    return out


def test_integration_cfdp_transfer_complete(integration: dict[str, dict[str, Any]], tmp_path: Path) -> None:
    dec = Decommutator()
    for sid in STREAMS:
        s = integration[sid]
        want = s["expect"]["cfdp"]
        cfdp_pkts = [p for p in s["packets"][3] if p.apid == dd.APID_CFDP]
        if want is None:                       # tm_nullsa: partition 0, CFDPPUT refused (PB-1.13)
            assert not cfdp_pkts, sid
            continue
        txs = _cfdp_receive(cfdp_pkts, tmp_path / sid)
        assert len(txs) == 1, (sid, txs)
        tx = txs[0]
        assert tx["state"] == cf.COMPLETE and tx["checksum_ok"] and tx["missing_octets"] == 0, (sid, tx)
        content = Path(tx["path"]).read_bytes()
        assert content == bytes.fromhex(want["content"]) and tx["name"] == want["name"], sid
        assert (tx["seq"], len(content), len(cfdp_pkts)) == (int(want["seq"]), int(want["size"]), int(want["pdus"]))
        assert cf.modular_checksum(content) == int(want["checksum"])
        assert tx["seq"] < 0x80000000 and tx["seq"] >> 16 == 7          # MCU sequence, partition 7
        for p in _split_packets(content):                     # the SSR export decodes, packet by packet
            assert p.apid != dd.APID_CFDP and dec.decode(p) is not None


# ---- command table (flight parse_user_data at the range edges) -------------------------
def _cmd_error(tok: str) -> int:
    t = tok.upper().removeprefix("CE_")
    if re.fullmatch(r"\d+", t):
        return int(t)
    names = {v: k for k, v in dd.CMD_ERRORS.items()}
    return names[t]


def test_flight_command_table_matches_ground(vectors: Path) -> None:
    rows = [ln.split() for ln in _integration_file(vectors, "command_table.txt").read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert len(rows) >= 4 * len(dd.COMMANDS)
    seen_ops: set[int] = set()
    for row in rows:
        user = bytes.fromhex(row[0])
        flight = _cmd_error(row[1])
        ground_parse, why_p = cmdparse.check_user_data(user, parse_stage=True)
        ground_full, why_f = cmdparse.check_user_data(user)
        if user and user[0] in cmdparse.LATE_VALIDATED_OPCODES:
            # Flight parse checks the length only; a table entry may also carry the load-stage
            # verdict. Both must be consistent with the ground's two stages.
            assert flight in (ground_parse, ground_full), (row, why_p, why_f)
        else:
            assert flight == ground_full, (row, why_f)
        if user:
            seen_ops.add(user[0])
    assert {c.opcode for c in dd.COMMANDS.values()} <= seen_ops


# ---- uplink round trip (ground CLTU -> flight tc_accept) -------------------------------
STAGES = ["NO_CLTU", "FRAME_REJECTED", "SDLS_REJECTED", "TFDF_REJECTED", "FARM"]
PARSES = ["OK", "TOO_SHORT", "BAD_VERSION", "BAD_SCID", "BAD_LENGTH", "BAD_FECF", "BAD_HEADER", "BAD_TFDF"]
SDLS = ["OK", "DISABLED", "BAD_LENGTH", "BAD_SPI", "REPLAY", "BAD_MAC", "BAD_IV"]
VERDICTS = ["ACCEPT", "DISCARD_RETRANSMIT", "DISCARD_NEG_WINDOW", "DISCARD_LOCKOUT", "ACCEPT_BYPASS",
            "CONTROL_UNLOCK", "CONTROL_SET_VR", "CONTROL_INVALID"]


def _norm(tok: str, names: list[str], prefixes: tuple[str, ...] = ()) -> str:
    """A flight result token, numeric or symbolic, as the name in `names`."""
    if re.fullmatch(r"\d+", tok):
        return names[int(tok)]
    t = tok.upper().replace(" ", "_")
    for p in prefixes:
        t = t.removeprefix(p)
    return t


def _tc_frame(user: bytes, *, framing: str, service: str, seq: int = 0, bypass: bool = False,
              control: bool = False, sn: int = 0) -> bytes:
    tx = None if service == "NONE" else sd.SdlsSender(KEY, spi=1, service=service)
    if tx is not None:
        tx.sn = sn
    data = user if control else build_packet(dd.APID_TC, 0, user, None, tc=True)
    return build_tc_frame(data, seq, bypass=bypass, control=control, sdls=tx, framing=framing)


def _bad_mac(frame: bytes) -> bytes:
    f = bytearray(frame)
    f[-3] ^= 0x01                                      # last MAC / tag octet; FECF recomputed
    from dssq.ccsds.crc import crc16_ccitt
    return bytes(f[:-2]) + crc16_ccitt(bytes(f[:-2])).to_bytes(2, "big")


def _uplink_cases() -> list[tuple[str, str, str, bytes, dict[str, Any]]]:
    """(name, service, framing, frame, expected flight result); equal names share state."""
    ok = {"stage": "FARM", "parse": "OK", "sdls": "OK", "verdict": "ACCEPT", "cmd_error": 0}
    control = {"stage": "FARM", "parse": "OK", "sdls": "OK"}
    rejected = {"stage": "SDLS_REJECTED", "parse": "OK", "verdict": "-", "opcode": None}
    cases: list[tuple[str, str, str, bytes, dict[str, Any]]] = []
    noop = cmdparse.parse("NOOP").user_data
    for framing in ("TC", "USLP"):
        for svc in ("NONE", "AUTH", "AE"):
            exp = ok | {"opcode": 0x01} | ({"sdls": "DISABLED"} if svc == "NONE" else {})
            cases.append((f"noop_{framing}_{svc}".lower(), svc, framing,
                          _tc_frame(noop, framing=framing, service=svc), exp))
    for name, text in (("speccfg", "SPECCFG AGC 59 1 3 0"),
                       ("magcal", "MAGCAL OB 120 -40 7 16384 16384 16384 0 0 0")):
        ud = cmdparse.parse(text).user_data
        cases.append((f"{name}_tc_ae", "AE", "TC", _tc_frame(ud, framing="TC", service="AE"),
                      ok | {"opcode": ud[0]}))
    bad = bytes([0x16, 255, 59, 1, 12, 0])            # led_cur 12: drives the UV lamp above rating
    assert cmdparse.check_user_data(bad)[0] == cmdparse.CE_BAD_ARGUMENT
    cases.append(("speccfg_bad_tc_ae", "AE", "TC", _tc_frame(bad, framing="TC", service="AE"),
                  ok | {"opcode": 0x16, "cmd_error": cmdparse.CE_BAD_ARGUMENT}))
    (seqload,) = cmdparse.seqload_commands(cmdparse.parse_sequence("T+00:00:10 NOOP"))
    cases.append(("seqload_tc_ae", "AE", "TC", _tc_frame(seqload.user_data, framing="TC", service="AE"),
                  ok | {"opcode": cmdparse.SEQLOAD_OPCODE}))
    for framing in ("TC", "USLP"):
        cases.append((f"unlock_{framing}_ae".lower(), "AE", framing,
                      _tc_frame(bc_unlock(), framing=framing, service="AE", bypass=True, control=True),
                      control | {"verdict": "CONTROL_UNLOCK"}))
    cases.append(("setvr_tc_ae", "AE", "TC",
                  _tc_frame(bc_set_vr(5), framing="TC", service="AE", bypass=True, control=True),
                  control | {"verdict": "CONTROL_SET_VR"}))
    cases.append(("bd_noop_uslp_ae", "AE", "USLP", _tc_frame(noop, framing="USLP", service="AE", bypass=True),
                  ok | {"verdict": "ACCEPT_BYPASS", "opcode": 0x01}))
    replayed = _tc_frame(noop, framing="TC", service="AE", sn=40)
    cases.append(("replay_tc_ae", "AE", "TC", replayed, ok | {"opcode": 0x01}))
    cases.append(("replay_tc_ae", "AE", "TC", replayed, rejected | {"sdls": "REPLAY"}))
    for svc in ("AUTH", "AE"):
        cases.append((f"badmac_tc_{svc}".lower(), svc, "TC",
                      _bad_mac(_tc_frame(noop, framing="TC", service=svc)), rejected | {"sdls": "BAD_MAC"}))
    return cases


def test_ground_refuses_frames_the_link_cannot_carry() -> None:
    """C-08: SEQLOAD / MAGCAL TC packets are 26 octets: TC v1 + AE fits 64, USLP + AE not."""
    ud = cmdparse.parse("MAGCAL 0 0 0 0 16384 16384 16384 0 0 0").user_data
    pkt = build_packet(dd.APID_TC, 0, ud, None, tc=True)
    assert len(pkt) == 26
    assert len(build_tc_frame(pkt, 0, sdls=sd.SdlsSender(KEY, service="AE"))) == 63
    with pytest.raises(ValueError, match="too long"):
        build_uslp_tc_frame(pkt, 0, sdls=sd.SdlsSender(KEY, service="AE"))


def test_uplink_round_trip_through_flight_tc_accept(vectors: Path) -> None:
    _integration_file(vectors, "integration_expect.txt")      # test_integration.cpp present
    cases = _uplink_cases()
    (vectors / "ground_tc.txt").write_text(
        "".join(f"{n} {KEY.hex()} {svc} {fr} {build_cltu(frame).hex()}\n" for n, svc, fr, frame, _e in cases))
    subprocess.run([str(HOST / "host_test"), str(vectors)], check=True, stdout=subprocess.DEVNULL)
    results = [ln.split() for ln in _integration_file(vectors, "flight_tc_results.txt").read_text().splitlines()
               if ln.strip() and not ln.lstrip().startswith("#")]
    assert [r[0] for r in results] == [c[0] for c in cases]
    for (name, *_x, exp), r in zip(cases, results, strict=True):
        got = {"stage": _norm(r[1], STAGES, ("TCSTAGE::",)), "parse": _norm(r[2], PARSES, ("TC_",)),
               "sdls": _norm(r[3], SDLS, ("SDLS_",)), "verdict": _norm(r[4], VERDICTS, ("FARM1::",))}
        for k in ("stage", "parse", "sdls", "verdict"):
            if k in exp:
                assert got[k] == exp[k], (name, k, r)
        if exp.get("verdict") in ("ACCEPT", "ACCEPT_BYPASS"):    # opcode / cmd_error decimal
            assert (int(r[5]), int(r[6])) == (exp["opcode"], exp["cmd_error"]), (name, r)
        elif "opcode" in exp:
            assert r[5] == r[6] == "-", (name, r)


# =============================================================================
# Part 2 - ground only
# =============================================================================
def test_dictionary_structure() -> None:
    assert dd.packet_len(dd.APID_HK) == 78
    assert [dd.packet_len(a) for a in (dd.APID_RF, dd.APID_CMDVER, dd.APID_TIMECORR, dd.APID_MAG, dd.APID_ATT,
                                       dd.APID_SPEC, dd.APID_ENV)] == [49, 8, 10, 46, 37, 129, 24]
    assert not dd.VARIABLE_APIDS & set(dd.PACKETS)
    assert set(dd.PACKET_NAMES) == set(dd.PACKETS) | dd.VARIABLE_APIDS == set(dd.APID_VC)
    assert dd.APID_VC[dd.APID_CFDP] == 3 and VC_NAMES[3] == "FILE (CFDP)" and VC_NAMES[63] == "IDLE/OID (USLP)"
    for apid, spec in dd.PACKETS.items():
        names = [f[0] for f in spec["fields"]]
        assert len(names) == len(set(names)), spec["name"]
        for f in spec["fields"]:
            assert len(f) in (6, 7) and (len(f) == 6 or f[6] is None or apid == dd.APID_SPEC)
        assert dd.NO_NA.get(apid, frozenset()) <= {f[0] for f in spec["fields"] if f[1] in dd.NA}
    hk = [f[0] for f in dd.PACKETS[dd.APID_HK]["fields"]]
    assert "sdls_enabled" not in hk and hk.index("vc3_backlog") == hk.index("vc2_backlog") + 1
    assert hk[-6:] == ["seq_state", "seq_count", "seq_next_dt_s", "mag_bist", "magcal_state", "beacon_level"]
    assert dd.SDLS_TM_STATES == sd.TM_STATE_NAMES and dd.CMD_ERRORS[7] == "BUSY"
    assert {16, 17, 18, 19} <= set(dd.RADIO_FAULTS) and not hasattr(dd, "AIR_RATES_BPS")
    assert dd.SENSOR_INSTRUMENT_MASK == 0x0381 and len(dd.SENSOR_BITS) == 10
    assert list(spectral.EVR_SPECTRAL_IDS) == list(range(0x0701, 0x0708))


def test_command_dictionary() -> None:
    assert sorted(c.opcode for c in dd.COMMANDS.values()) == list(range(0x01, 0x21))
    assert {c.opcode for c in dd.COMMANDS.values() if c.hazardous} == {0x04, 0x08, 0x0C, 0x0E, 0x0F, 0x10,
                                                                         0x14, 0x15}
    assert cmdparse.seq_forbidden_opcodes() == {0x04, 0x08, 0x0C, 0x0E, 0x0F, 0x10, 0x14, 0x15, 0x1D, 0x1E}
    args = {mn: [a[0] for a in c.args] for mn, c in dd.COMMANDS.items()}
    assert args["SPECCFG"] == ["gain", "cycles", "lamps", "led_cur", "ind"]
    assert args["CFDPPUT"] == ["source", "max_kib"] and args["RADIOOPT"] == ["flags"]
    assert args["SPECMODE"] == ["mode", "n"] and args["SPECBASE"] == ["threshold_cdeg"]
    assert args["SPECDIAG"] == ["device", "n_sub"] and args["SEQCTRL"] == ["action", "slot"]
    assert args["MAGBIST"] == ["unit"]
    assert args["MAGCAL"] == ["unit", "off_x", "off_y", "off_z", "m11", "m22", "m33", "m12", "m13", "m23"]
    assert args["SEQLOAD"][:4] == ["slot", "flags", "t_s", "len"] and len(args["SEQLOAD"]) == 16
    assert dd.COMMANDS["SEQLOAD"].arg_octets == dd.COMMANDS["MAGCAL"].arg_octets == 19
    assert [c.mnemonic for c in dd.COMMANDS.values() if c.builder_only] == ["SEQLOAD"]
    assert all(bool(c.check) == bool(c.check_doc) for c in dd.COMMANDS.values())
    for bad in ({"mnemonic": "X", "opcode": 256}, {"mnemonic": "X", "opcode": 1, "args": [("a", "e", 0, 1, None)]},
                {"mnemonic": "X", "opcode": 1, "args": [("a", "B", 0, 256, None)]},
                {"mnemonic": "X", "opcode": 1, "args": [("a", "B", 0, 3, {"Lo": 1})]},
                {"mnemonic": "X", "opcode": 1, "args": [("a", "B", 0, 3, {"BIG": 9})]},
                {"mnemonic": "X", "opcode": 1, "args": [("a", "B", 0, 3, None), ("a", "B", 0, 3, None)]},
                {"mnemonic": "X", "opcode": 1, "check": lambda v: None}):
        with pytest.raises(ValueError):
            dd.CommandDef(**bad)                                       # type: ignore[arg-type]


def test_bit_and_bist_decoders() -> None:
    assert dd.decode_bits(0, dd.FDIR_BITS) == [] and dd.decode_bits(None, dd.FDIR_BITS) == []
    assert dd.decode_bits(0x4001, dd.FDIR_BITS) == ["RADIO_CFG", "HIBERNATE"]
    assert dd.decode_bits(0x0381, dd.SENSOR_BITS) == ["NICLA_ME", "AS7265X", "RM3100_OB", "RM3100_IB"]
    assert dd.decode_bits(0x80, dd.RF_FLAG_BITS) == ["b7"]                      # never dropped
    for bad in (-1, 1 << 32, 1.5, True, "3"):
        with pytest.raises(ValueError):
            dd.decode_bits(bad, dd.FDIR_BITS)                          # type: ignore[arg-type]
    b = dd.decode_mag_bist(0x8F)
    assert b["available"] and b["ob"] == {"tested": True, "x": True, "y": True, "z": True, "pass": True}
    assert b["ib"] == {"tested": True, "x": False, "y": False, "z": False, "pass": False}
    b = dd.decode_mag_bist(0x0B)
    assert b["ob"]["pass"] is False and b["ob"]["z"] is False and b["ib"]["tested"] is False
    assert dd.decode_mag_bist(None) == {"available": False, "ob": dd.decode_mag_bist(0)["ob"],
                                        "ib": dd.decode_mag_bist(0)["ib"]}
    with pytest.raises(ValueError):
        dd.decode_mag_bist(256)
    assert dd.checked_limits("x", 60, (-20, 0, 60, 75)) == "YELLOW"
    assert dd.checked_limits("x", 75, (-20, 0, 60, 75)) == "RED"
    assert dd.checked_limits("x", None, (-20, 0, 60, 75)) == "OK"


_RAW = {"B": (0, 0xFF), "b": (-0x80, 0x7F), "H": (0, 0xFFFF), "h": (-0x8000, 0x7FFF),
        "I": (0, 0xFFFFFFFF), "i": (-0x80000000, 0x7FFFFFFF)}


def _random_raw(apid: int, f: tuple, rng: random.Random) -> int:
    lo, hi = _RAW[f[1]]
    na = dd.field_na(apid, f)
    if na is not None and rng.random() < 0.15:
        return na
    if f[0] == "bore_az":
        lo, hi = -17999, 18000                      # the flight sends (-180, 180] deg
    for _ in range(100):
        r = rng.choice([lo, hi, rng.randint(lo, hi)])
        dec = f[6] if len(f) > 6 else None
        if r != na and not (dec is spectral.log_decode and r in (0x7FFF, 0x8000)):
            return r
    raise AssertionError("no raw value")


def test_decom_round_trip_every_fixed_apid() -> None:
    dec = Decommutator()
    rng = random.Random(20261011)
    for apid, spec in dd.PACKETS.items():
        fields = spec["fields"]
        for trial in range(60):
            raws = [_random_raw(apid, f, rng) for f in fields]
            user = struct.pack(">" + "".join(f[1] for f in fields), *raws)
            pkt = SpacePacket.parse(build_packet(apid, trial, user, CucTime(trial, 0)))
            values = dec.decode(pkt)
            for f, r in zip(fields, raws, strict=True):
                assert (values[f[0]] is None) == (r == dd.field_na(apid, f)), (spec["name"], f[0])
            assert encode(apid, values) == user, spec["name"]
            assert set(dec.limits(apid, values)) == {f[0] for f in fields if f[5] is not None}
        assert set(dec.units(apid)) == {f[0] for f in fields}


def test_decom_na_and_saturation_rules() -> None:
    dec = Decommutator()
    rf = {f[0]: 0 for f in dd.PACKETS[dd.APID_RF]["fields"]}
    rf.update(frame_period_ms=65535, uplink_rssi=None, uplink_snr=None, uplink_freq_err=None,
              sc_noise_floor=None, cltu_ok=65535, last_airtime=25.5)
    v = dec.decode(SpacePacket.parse(build_packet(dd.APID_RF, 0, encode(dd.APID_RF, rf), CucTime(1, 0))))
    assert v["frame_period_ms"] == 65535 and v["cltu_ok"] == 65535                   # saturated readings
    assert v["uplink_rssi"] is None and v["uplink_freq_err"] is None                 # sentinels
    assert v["last_airtime"] == pytest.approx(25.5)
    hk = {f[0]: 0 for f in dd.PACKETS[dd.APID_HK]["fields"]}
    hk.update(seq_next_dt_s=None, cfdp_seq=None, bus_temp=-12.34)
    data = encode(dd.APID_HK, hk)
    assert len(data) == 78 and data[73:75] == b"\xff\xff" and data[65:69] == b"\xff\xff\xff\xff"
    assert struct.unpack_from(">h", data, 28)[0] == -1234
    for bad in ({**hk, "bus_temp": 327.67},                  # collides with 0x7FFF
                {**hk, "fsw_mode": None},                    # u8: no sentinel
                {**hk, "fsw_mode": 256}, {**hk, "fsw_mode": 1.5}, {**hk, "fsw_mode": True},
                {**hk, "bus_temp": math.nan}, {**hk, "bus_temp": "1"}, {**hk, "no_such_field": 1}):
        with pytest.raises(ValueError):
            encode(dd.APID_HK, bad)
    with pytest.raises(KeyError):
        encode(dd.APID_EVR, {})


def test_decom_rejects_malformed_packets() -> None:
    dec = Decommutator()

    def pkt(apid: int, user: bytes) -> SpacePacket:
        return SpacePacket.parse(build_packet(apid, 0, user, CucTime(0, 0)))
    good = encode(dd.APID_ENV, {"flags": 0, "accuracy": 0})
    assert dec.decode(pkt(dd.APID_ENV, good))["p"] is None
    for user in (good[:-1], good + b"\x00"):
        with pytest.raises(ValueError, match="layout has 24"):
            dec.decode(pkt(dd.APID_ENV, user))
    with pytest.raises(KeyError):
        dec.decode(pkt(0x055, b"\x00"))
    assert dec.packet_name(0x055) == "APID_055" and dec.packet_name(dd.APID_CFDP) == "CFDP"
    evr = dec.decode(pkt(dd.APID_EVR, bytes([2, 0x07, 0x04]) + b"\x1b[31mBURST\x00\xff"))
    assert evr == {"severity": "WARN", "severity_code": 2, "event_id": 0x0704,
                   "text": "�[31mBURST��"}
    assert dec.decode(pkt(dd.APID_EVR, bytes([9, 0, 1])))["severity"] == "9"
    assert len(dec.decode(pkt(dd.APID_EVR, bytes([1, 0, 1]) + b"x" * 80))["text"]) == 80
    for user in (b"\x01\x00", bytes([1, 0, 1]) + b"x" * 81):
        with pytest.raises(ValueError):
            dec.decode(pkt(dd.APID_EVR, user))
    scan = dec.decode(pkt(dd.APID_RFSCAN, bytes([80, 4, 0x03, 0x2C]) + bytes([0x8A, 0x80, 0x00, 0x7F])))
    assert scan == {"first_ch": 80, "count": 4, "duration_ms": 812, "noise": [-118, None, 0, 127]}
    for user in (bytes([0, 0, 0, 0]), bytes([0, 85, 0, 0]) + bytes(85), bytes([81, 4, 0, 0]) + bytes(4),
                 bytes([0, 4, 0, 0]) + bytes(3), b"\x00\x01\x00"):
        with pytest.raises(ValueError):
            dec.decode(pkt(dd.APID_RFSCAN, user))
    assert dec.decode(pkt(dd.APID_CFDP, bytes(10))) == {"pdu_len": 10}
    for n in (9, 147):
        with pytest.raises(ValueError):
            dec.decode(pkt(dd.APID_CFDP, bytes(n)))
    burst = spectral.pack_specburst(5, 400, 2, 1, [(0, [100 + i for i in range(18)])], encoding=1)
    d = dec.decode(pkt(dd.APID_SPECBURST, burst))
    assert d["encoding"] == 1 and d["samples"][0]["raw"][17] == 117
    with pytest.raises(rice.RiceError):                      # 1 octet cannot hold 18 samples
        dec.decode(pkt(dd.APID_SPECBURST, burst[:12] + b"\x00"))
    assert issubclass(rice.RiceError, ValueError)


def test_check_user_data_mirrors_the_flight_parser() -> None:
    ce = cmdparse.check_user_data
    seqblk = cmdparse.pack_seqload(0, cmdparse.parse_sequence("T+00:00:01 NOOP")[0])
    stored_ssrclear = seqblk[:6] + b"\x01\x08" + bytes(11)
    stored_bad_mode = seqblk[:6] + b"\x02\x02\x09" + bytes(10)
    non_pd = struct.pack(">B3h6h", 0, 0, 0, 0, 8192, 8192, 8192, 8192, 8192, 8192)
    cases = [
        ("01", 0, 0), ("0100", 2, 2), ("", 2, 2), ("21", 1, 1), ("00", 1, 1), ("0cb007", 0, 0),
        ("0c1234", 6, 6), ("0cb0", 2, 2), ("0205", 3, 3), ("0204", 0, 0), ("16ff3b010b00", 0, 0),
        ("16043b010000", 3, 3), ("16ff3b010c00", 3, 3), ("16ff00010000", 3, 3), ("0a0400", 3, 3),
        ("0a0381", 0, 0), ("0600120000", 3, 3), ("0600220e10", 0, 0), ("130503", 3, 3), ("130303", 0, 0),
        ("1a0105", 3, 3), ("1a043c", 0, 0), ("1e0001", 3, 3), ("1e0305", 0, 0), ("1f03", 3, 3),
        ("20" + non_pd.hex(), 0, 3), ("20" + non_pd[:-1].hex(), 2, 2),
        ("1d" + seqblk.hex(), 0, 0), ("1d" + stored_ssrclear.hex(), 0, 3),
        ("1d" + stored_bad_mode.hex(), 0, 3), ("1d" + seqblk[:-1].hex() + "01", 0, 3),
        ("1d" + seqblk[:-1].hex(), 2, 2),
    ]
    for hx, parse_code, full_code in cases:
        assert ce(bytes.fromhex(hx), parse_stage=True)[0] == parse_code, hx
        assert ce(bytes.fromhex(hx))[0] == full_code, hx
    # The ground parser and the verdict agree on every command at its range edges.
    rng = random.Random(5)
    for c in dd.COMMANDS.values():
        if c.builder_only:
            continue
        for _ in range(40):
            vals = [rng.choice([lo, hi, lo - 1, hi + 1, rng.randint(lo, hi)]) for _n, _c, lo, hi, _e in c.args]
            try:
                cmdparse.parse(" ".join([c.mnemonic, *map(str, vals)]))
                accepted = True
            except cmdparse.CommandError:
                accepted = False
            fits = all(_RAW[code][0] <= v <= _RAW[code][1] for v, (_n, code, *_r) in zip(vals, c.args, strict=True))
            if fits:
                raw = bytes([c.opcode]) + struct.pack(">" + "".join(a[1] for a in c.args), *vals)
                assert (cmdparse.check_user_data(raw)[0] == 0) == accepted, (c.mnemonic, vals)


# ---- offline replay of a downlink built with the ground reference builders --------------
def _mux(packets: list[bytes], zone: int) -> list[tuple[int, bytes]]:
    """Packets of one VC -> (FHP, data zone) per frame, closed by an idle packet."""
    starts, off = [], 0
    for p in packets:
        starts.append(off)
        off += len(p)
    stream = b"".join(packets)
    pad = (-len(stream)) % zone
    if 0 < pad < 7:
        pad += zone
    if pad:
        starts.append(len(stream))
        stream += build_idle_packet(pad, 0)
    out = []
    for i in range(0, len(stream), zone):
        first = next((s - i for s in starts if i <= s < i + zone), 0x7FF)
        out.append((first, stream[i:i + zone]))
    return out


def _downlink(framing: str, sender: sd.SdlsTmSender | None, vcs: dict[int, list[bytes]]) -> bytes:
    """CADUs: one OID frame, then every VC's frames (TM MCFC / USLP VCF counts per VC)."""
    ovh = sender.header_len + sender.trailer_len if sender else 0
    zone = data_field_len(framing, ovh)
    clcw = Clcw.pack()
    idle = (IDENT + " ").encode()
    frames = [build_oid_frame(0, 0, clcw, idle) if framing == "TM" else build_uslp_oid_frame(0, clcw, idle)]
    protect = None
    if sender is not None:
        def protect(hdr: bytes, data: bytes) -> bytes:
            body = sender.protect(hdr, data)
            assert body is not None
            return body
    mcfc = 1
    for vc, pkts in vcs.items():
        for vcfc, (fhp, data) in enumerate(_mux(pkts, zone)):
            if framing == "TM":
                frames.append(build_frame(vc, mcfc, vcfc, fhp, data, clcw, protect=protect))
            else:
                frames.append(build_uslp_frame(vc, vcfc, fhp, data, clcw, protect=protect))
            mcfc += 1
    return b"".join(encode_cadu(f) for f in frames)


def _ground_packets() -> tuple[dict[int, list[bytes]], bytes]:
    t = CucTime(1000, 0)
    hk = {f[0]: 0 for f in dd.PACKETS[dd.APID_HK]["fields"]}
    hk.update(fsw_mode=2, beacon_level=2, sdls_tm_state=1, seq_next_dt_s=None)
    burst = spectral.pack_specburst(7, 333, 0, 1, [(k * 30, [500 + k] * 18) for k in range(3)])
    vc0 = [build_packet(dd.APID_HK, 1, encode(dd.APID_HK, hk), t),
           build_packet(dd.APID_EVR, 2, bytes([1, 0x07, 0x01]) + b"spectral event: angle 6.20 deg", t)]
    vc1 = [build_packet(dd.APID_SPECBURST, 3, burst, t)]
    content = b"".join(vc0 + vc1)                 # the "SSR export" is the packets themselves
    seq = 0x00070001                              # cfdp_make_seq(partition 7, transaction 1)
    vc3 = [build_packet(dd.APID_CFDP, i, pdu, t) for i, pdu in enumerate(cf.Sender(seq, "ssr_p7_1000.bin", content))]
    return {0: vc0, 1: vc1, 3: vc3}, content


@pytest.mark.parametrize("framing", ["TM", "USLP"])
def test_replay_pipeline_on_a_protected_downlink(framing: str, tmp_path: Path) -> None:
    vcs, content = _ground_packets()
    tx = sd.SdlsTmSender(KEY, "AE")
    assert tx.set_epoch(7)
    stream = _downlink(framing, tx, vcs)
    pipe = replay.Pipeline(key=KEY, tm_service="AE", framing="auto", cfdp_dir=tmp_path)
    got = [p for i in range(0, len(stream), CADU_LEN) for p in pipe.push(stream[i:i + CADU_LEN], 1.0)]
    assert [p.raw for _vc, p in got] == [p for v in (0, 1, 3) for p in vcs[v]]
    assert pipe.verdicts == {"OK": sum(1 for _ in range(0, len(stream), CADU_LEN)) - 1}
    assert pipe.idents == {IDENT: 1} and pipe.lost_frames == 0 and not pipe.decode_errors
    assert all(pipe.decode(p) is not None for _vc, p in got)
    assert pipe.cfdp is not None
    (tx_sum,) = pipe.cfdp.snapshot()["transactions"]
    assert tx_sum["state"] == cf.COMPLETE and Path(tx_sum["path"]).read_bytes() == content
    # Without the key the protected frames are counted, never decoded.
    blind = replay.Pipeline(key=None, tm_service="AE")
    assert not [p for i in range(0, len(stream), CADU_LEN) for p in blind.push(stream[i:i + CADU_LEN])]
    assert set(blind.verdicts) == {"NO KEY"} and blind.idents == {IDENT: 1}
    # A wrong framing setting rejects every frame.
    other = replay.Pipeline(key=KEY, tm_service="AE", framing="USLP" if framing == "TM" else "TM")
    other.push(stream[:CADU_LEN])
    assert sum(other.frames.values()) == 1 and next(iter(other.frames)).startswith("bad: ")


def test_replay_null_sa_without_key_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vcs, _content = _ground_packets()
    nullsa = sd.SdlsTmSender(KEY, "AE")
    nullsa.fallback = True                        # no epoch yet, fallback engaged (flight 0x0121)
    stream = _downlink("TM", nullsa, vcs)
    pipe = replay.Pipeline(key=None, tm_service="AE")
    n = len([p for i in range(0, len(stream), CADU_LEN) for p in pipe.push(stream[i:i + CADU_LEN])])
    assert n == sum(len(v) for v in vcs.values()) and set(pipe.verdicts) == {"UNPROTECTED"}
    # CLI: EGSE log format [u64 ns][u16 len][CADU], station.toml with an absolute key path.
    log = tmp_path / "cadu_20261011.bin"
    log.write_bytes(b"".join(struct.pack(">QH", 10**18 + k, CADU_LEN) + stream[k:k + CADU_LEN]
                             for k in range(0, len(stream), CADU_LEN)))
    key = tmp_path / "k.hex"
    key.write_text(KEY.hex())
    cfg = tmp_path / "station.toml"
    cfg.write_text(f'[sdls]\ntm_service = "AE"\nkey_file = "{key.as_posix()}"\n[link]\ntm_framing = "auto"\n')
    files = tmp_path / "files"
    assert replay.main(["--cadu-file", str(log), "--config", str(cfg), "--cfdp-dir", str(files)]) == 0
    out = capsys.readouterr().out
    assert "UNPROTECTED" in out and IDENT in out and "COMPLETE" in out and "SPECBURST" in out
    raw = tmp_path / "plain.bin"
    raw.write_bytes(stream)
    assert replay.main(["--cadu-file", str(raw), "--raw", "--config", str(cfg), "--values"]) == 0
    assert "'beacon_level': 2" in capsys.readouterr().out
    raw.write_bytes(stream[:-1])
    assert replay.main(["--cadu-file", str(raw), "--raw", "--config", str(cfg)]) == 1
    assert replay.main(["--cadu-file", str(tmp_path / "missing.bin")]) == 1
    with pytest.raises(ValueError):
        replay.Pipeline(framing="X")
