"""Flight <-> ground cross-check.

Builds tools/host_test (which compiles the EXACT spacecraft protocol sources with
the host C++ compiler), runs it to generate a CADU stream, then decodes that
stream with the independent Python ground stack and requires bit-exact packet
recovery. Skipped automatically if no C++ compiler / make is available.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dssq.ccsds import CADU_LEN
from dssq.ccsds.packets import PacketExtractor
from dssq.ccsds.tc import cltu_decode, parse_tc_frame
from dssq.ccsds.tm import decode_codeblock
from dssq.decom import Decommutator

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"


@pytest.fixture(scope="module")
def vectors(tmp_path_factory):
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("vectors")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    subprocess.run([str(HOST / "host_test"), str(out)], check=True)
    return out


def test_flight_cadus_decode_bit_exact(vectors):
    data = (vectors / "cadus.bin").read_bytes()
    assert len(data) % CADU_LEN == 0
    extract = {0: PacketExtractor(0), 1: PacketExtractor(1)}
    got = {0: [], 1: []}
    mcfc_prev = None
    for i in range(0, len(data), CADU_LEN):
        cadu = data[i:i + CADU_LEN]
        assert cadu[:4] == bytes.fromhex("1ACFFC1D")
        res = decode_codeblock(cadu[4:])
        assert res.ok and res.rs_corrected == 0, res.reason
        f = res.frame
        if mcfc_prev is not None:
            assert f.mcfc == (mcfc_prev + 1) & 0xFF
        mcfc_prev = f.mcfc
        if f.vcid in extract:
            got[f.vcid] += [p.raw.hex() for p in extract[f.vcid].push(f.vcfc, f.fhp, f.data)]
    expected = {0: [], 1: []}
    for line in (vectors / "packets.txt").read_text().split("\n"):
        if line.strip():
            vc, hx = line.split()
            expected[int(vc)].append(hx)
    assert got == expected


def test_flight_rs_vector(vectors):
    from dssq.ccsds import rs
    cb = bytes.fromhex((vectors / "rs_vector.txt").read_text().strip())
    assert rs.encode(cb[:200]) == cb[200:]


def test_flight_cltu_matches_ground_encoder(vectors):
    from dssq.ccsds.tc import build_cltu
    flight = bytes.fromhex((vectors / "cltu.txt").read_text().strip())
    info, st = cltu_decode(flight)
    tf = parse_tc_frame(info)
    assert tf is not None
    frame = info[:tf.length]
    assert build_cltu(frame) == flight


def test_flight_telemetry_packets_decommutate(vectors):
    """tlm_vectors.txt holds packets built by the FLIGHT packers with known values."""
    path = vectors / "tlm_vectors.txt"
    if not path.exists():
        pytest.skip("flight packet vectors not generated")
    dec = Decommutator()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        name, hx, expect = line.split(" ", 2)
        from dssq.ccsds.packets import SpacePacket
        pkt = SpacePacket.parse(bytes.fromhex(hx))
        values = dec.decode(pkt)
        for kv in expect.split(","):
            k, v = kv.split("=")
            got = values[k]
            if isinstance(got, list):
                got = ";".join(str(x) for x in got)
            if isinstance(got, float):
                assert abs(got - float(v)) <= max(1e-3, abs(float(v)) * 1e-5), (name, k, got, v)
            else:
                assert str(got) == v, (name, k, got, v)


def test_flight_sdls_frame_verifies_on_ground(vectors):
    """Frame built with the flight HMAC code must verify with Python's hmac module."""
    from dssq.ccsds.sdls import SdlsReceiver
    frame = bytes.fromhex((vectors / "sdls_frame.txt").read_text().strip())
    info = parse_tc_frame(frame)
    assert info is not None
    rx = SdlsReceiver(bytes(range(32)), spi=1)
    body, verdict = rx.process(frame, info.data)
    assert verdict == "OK" and body == bytes.fromhex("18c0c000000001") and rx.last_sn == 7


def test_ground_sdls_frame_layout_matches_flight(vectors):
    """Ground-built protected frame equals the flight-built one for the same key/SN."""
    from dssq.ccsds.sdls import SdlsSender
    from dssq.ccsds.tc import build_tc_frame
    flight = bytes.fromhex((vectors / "sdls_frame.txt").read_text().strip())
    tx = SdlsSender(bytes(range(32)), spi=1)
    tx.sn = 6                                   # next_sn() -> 7
    assert build_tc_frame(bytes.fromhex("18c0c000000001"), seq=0, sdls=tx) == flight
