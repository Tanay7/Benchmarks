"""CCSDS 121.0-B-3 lossless compression: ground decoder / reference encoder (dssq.ccsds.rice).

1. Official CCSDS test data (121B2TestData): the subset embedded in
   tools/host_test/test_rice.cpp (verbatim hex + SHA-256, parsed here so there is one
   copy) must decode to the .dat and re-encode to the .rz octet for octet. With
   CCSDS121_TESTDATA=<path>/121B2TestData every official file is checked (74 streams:
   AllOptions n = 1..32, LowEntropyOptions, ExtendedParameters J = 16/r = 256 and
   J = 64/r = 4096 with octet-aligned reference intervals).
2. Flight cross-check: the host test (the EXACT flight encoder built with the host
   compiler) writes rice_vectors.txt; every stream must decode to its samples and the
   Python encoder must reproduce it bit for bit. Skipped without a C++ toolchain.
3. Hand-derived streams, random and adversarial round trips over the parameter space,
   the output bound, and malformed-stream handling (only RiceError, never a crash).
"""
from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dssq.ccsds.rice import (MAX_SAMPLES, MISSION, RiceError, RiceParams, channel_major, decode,
                             encode, max_encoded_len, time_major)

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"
TESTDATA_ENV = "CCSDS121_TESTDATA"


# --- official data embedded in the host test -------------------------------------------------

_LITS = r'((?:"[0-9a-f]*"\s*)+)'
_INPUT_RE = re.compile(r'\{"([^"]+\.dat)",\s*(\d+),\s*"([0-9a-f]{64})",\s*' + _LITS + r'\}')
_CODED_RE = re.compile(r'\{"([^"]+\.rz)",\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(true|false),\s*'
                       r'"([0-9a-f]{64})",\s*' + _LITS + r'\}')


def _join(lits: str) -> bytes:
    return bytes.fromhex("".join(re.findall(r'"([0-9a-f]*)"', lits)))


def _samples(raw: bytes, width: int) -> list[int]:
    return [int.from_bytes(raw[i:i + width], "little") for i in range(0, len(raw), width)]


def _load_embedded():
    text = (HOST / "test_rice.cpp").read_text()
    block = text.split("// BEGIN CCSDS121 OFFICIAL VECTORS")[1]
    block = block.split("// END CCSDS121 OFFICIAL VECTORS")[0]
    inputs = [(f, int(w), sha, _join(lits)) for f, w, sha, lits in _INPUT_RE.findall(block)]
    coded = [(f, int(i), int(n), int(j), int(r), res == "true", sha, _join(lits))
             for f, i, n, j, r, res, sha, lits in _CODED_RE.findall(block)]
    return inputs, coded


INPUTS, CODED = _load_embedded()


def test_embedded_table_parsed_and_unaltered():
    assert len(INPUTS) == 5 and len(CODED) == 9
    for name, _w, sha, raw in INPUTS:
        assert hashlib.sha256(raw).hexdigest() == sha, name
    for name, *_rest, sha, rz in CODED:
        assert hashlib.sha256(rz).hexdigest() == sha, name


@pytest.mark.parametrize("entry", CODED, ids=[c[0] for c in CODED])
def test_embedded_official_streams(entry):
    name, idx, n, j, r, restricted, _sha, rz = entry
    _f, width, _s, raw = INPUTS[idx]
    x = _samples(raw, width)
    p = RiceParams(n=n, j=j, r=r, restricted=restricted)
    assert decode(rz, len(x), p) == x
    assert encode(x, p) == rz
    assert len(rz) <= max_encoded_len(len(x), p)


def _official_cases(root: Path):
    for n in range(1, 33):
        length, r = (256, 16) if n <= 16 else (512, 32)
        dat = root / "AllOptions" / f"test_p{length}n{n:02d}.dat"
        width = 1 if n <= 8 else 2 if n <= 16 else 4
        if n <= 4:
            for kind in ("basic", "restricted"):
                yield dat, width, dat.with_name(f"{dat.stem}-{kind}.rz"), RiceParams(
                    n=n, j=16, r=r, restricted=kind == "restricted")
        else:
            yield dat, width, dat.with_suffix(".rz"), RiceParams(n=n, j=16, r=r)
    for s in (1, 2, 3):
        dat = root / "LowEntropyOptions" / f"Lowset{s}_8bit.dat"
        for n in range(1, 9):
            if n <= 4:
                for kind in ("basic", "restricted"):
                    yield dat, 1, dat.with_name(f"{dat.stem}.n{n:02d}-{kind}.rz"), RiceParams(
                        n=n, j=16, r=64, restricted=kind == "restricted")
            else:
                yield dat, 1, dat.with_name(f"{dat.stem}.n{n:02d}.rz"), RiceParams(n=n, j=16, r=64)
    dat = root / "ExtendedParameters" / "sar32bit.dat"
    yield dat, 4, dat.with_name("sar32bit.j16.r256.rz"), RiceParams(n=32, j=16, r=256, pad_rsi=True)
    yield dat, 4, dat.with_name("sar32bit.j64.r4096.rz"), RiceParams(n=32, j=64, r=4096, pad_rsi=True)


def test_full_official_suite():
    root = os.environ.get(TESTDATA_ENV)
    if not root:
        pytest.skip(f"set {TESTDATA_ENV}=<path>/121B2TestData to run every official file")
    failures, total = [], 0
    for dat, width, rz, p in _official_cases(Path(root)):
        total += 1
        x = _samples(dat.read_bytes(), width)
        want = rz.read_bytes()
        if decode(want, len(x), p) != x or encode(x, p) != want:
            failures.append(rz.name)
    assert total == 74 and failures == []


# --- hand-derived streams (same derivations as the host test) --------------------------------

def test_hand_derived_streams():
    # 00000 | 0001001000110100 | 1 | fill 00: one sample padded to a zero block.
    assert encode([0x1234]).hex() == "0091a4"
    assert decode(bytes.fromhex("0091a4"), 1) == [0x1234]
    assert encode([0] * 16).hex() == "000004"
    # Extremes: theta = 0 -> every delta = 65535 -> no-compression, exactly the bound.
    ext = [0xFFFF if i & 1 else 0 for i in range(16)]
    assert encode(ext).hex() == "f0000f" + "f" * 58 + "f0"
    assert len(encode(ext)) == max_encoded_len(16) == 33
    # Constant slope: FS, k = 1 and k = 2 all cost 45 bits -> the smallest k (FS) wins.
    assert encode(list(range(2, 18))).hex() == "100022492492492480"
    assert decode(bytes.fromhex("100022492492492480"), 16) == list(range(2, 18))


def test_option_ids_table_5_1():
    for n in range(1, 33):
        p = RiceParams(n=n)
        assert p.id_len == (3 if n <= 8 else 4 if n <= 16 else 5)
        assert p.kmax == {3: 5, 4: 13, 5: 29}[p.id_len]
    assert [RiceParams(n=n, restricted=True).id_len for n in (1, 2, 3, 4)] == [1, 1, 2, 2]
    assert RiceParams(n=2, restricted=True).kmax == -1 and RiceParams(n=4, restricted=True).kmax == 1


# --- flight encoder cross-check --------------------------------------------------------------

@pytest.fixture(scope="module")
def flight_vectors(tmp_path_factory):
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("rice_vectors")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    # Not check=True: a failure elsewhere in the host test must not hide these vectors
    # (the host test reports its own failures).
    subprocess.run([str(HOST / "host_test"), str(out)], stdout=subprocess.DEVNULL)
    path = out / "rice_vectors.txt"
    if not path.exists():
        pytest.skip("flight rice vectors not generated")
    vectors = []
    for line in path.read_text().splitlines():
        name, n, j, r, res, cnt, sx, ex = line.split()
        raw = bytes.fromhex(sx)
        x = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2)]
        assert len(x) == int(cnt)
        vectors.append((name, RiceParams(n=int(n), j=int(j), r=int(r), restricted=res == "1"), x,
                        bytes.fromhex(ex)))
    return vectors


def test_flight_streams_decode_and_reencode_bit_exact(flight_vectors):
    assert len(flight_vectors) >= 30
    stats: dict[str, int] = {}
    for name, p, x, coded in flight_vectors:
        mission = (p.n, p.j, p.restricted) == (16, 16, False)
        assert decode(coded, len(x), p, stats=stats if mission else None) == x, name
        assert encode(x, p) == coded, name
        assert len(coded) <= max_encoded_len(len(x), p), name
    # The flight corpus exercises every option of the mission's 4-bit ID set.
    for key in ["zero", "ros", "se", "nc"] + [f"k{k}" for k in range(14)]:
        assert stats.get(key, 0) > 0, key


def test_flight_specburst_layout(flight_vectors):
    by_name = {v[0]: v for v in flight_vectors}
    _n, p, x, coded = by_name["specburst_54"]
    rows = time_major(decode(coded, 54, p), 18)
    assert len(rows) == 3 and all(len(row) == 18 for row in rows)
    assert channel_major(rows) == x
    assert rows[0][6:12] == [0xFFFF] * 6        # channels not converted in that BANK mode
    assert len(coded) < 2 * 54                   # smaller than raw -> the flight would send it


# --- round trips -------------------------------------------------------------------------------

def _patterns(rnd: random.Random, p: RiceParams, count: int) -> dict[str, list[int]]:
    xmax = p.xmax
    walk, cur = [], rnd.randrange(xmax + 1)
    for _ in range(count):
        cur = min(xmax, max(0, cur + rnd.randint(-3, 3)))
        walk.append(cur)
    return {
        "zeros": [0] * count,
        "max": [xmax] * count,
        "alternating": [xmax if i & 1 else 0 for i in range(count)],
        "ramp": [i % (xmax + 1) for i in range(count)],
        "random": [rnd.randrange(xmax + 1) for _ in range(count)],
        "walk": walk,
        "sparse": [1 if rnd.random() < 0.03 else 0 for _ in range(count)],
        "near_edges": [(xmax - rnd.randrange(2)) if (i // 5) & 1 else rnd.randrange(2)
                       for i in range(count)],
    }


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7, 8, 9, 12, 16, 17, 24, 31, 32])
def test_round_trip_parameter_space(n):
    rnd = random.Random(121 + n)
    for j in (8, 16, 32, 64):
        for r in (1, 3, 64, 128):
            for restricted in ((False, True) if n <= 4 else (False,)):
                p = RiceParams(n=n, j=j, r=r, restricted=restricted)
                count = rnd.choice([1, j - 1, j, j + 1, 5 * j + 3, 130 * j + 7])
                for kind, x in _patterns(rnd, p, count).items():
                    e = encode(x, p)
                    assert len(e) <= max_encoded_len(count, p), (kind, p)
                    assert decode(e, count, p) == x, (kind, p)


def test_bound_is_exact_for_incompressible_input():
    rnd = random.Random(5)
    for p in (MISSION, RiceParams(n=8, j=8, r=2), RiceParams(n=32, j=64, r=1),
              RiceParams(n=12, j=16, r=3, pad_rsi=True)):
        # Alternating extremes keep theta = 0, so every delta is maximal: no-compression.
        count = p.j * 7
        x = [p.xmax if i & 1 else 0 for i in range(count)]
        assert len(encode(x, p)) == max_encoded_len(count, p)
        y = [rnd.randrange(p.xmax + 1) for _ in range(count)]
        assert len(encode(y, p)) <= max_encoded_len(count, p)
    assert max_encoded_len(54) == 130 and max_encoded_len(1) == 33     # flight: same values


def test_mission_compresses_spectral_bursts():
    # Smooth per-channel series in channel-major order compress well below raw.
    rnd = random.Random(24)
    rows = [[200 + 2100 * c + rnd.randrange(9) for c in range(18)] for _ in range(3)]
    x = channel_major(rows)
    assert len(encode(x)) < 2 * len(x)
    assert time_major(decode(encode(x), len(x)), 18) == rows


# --- malformed streams -------------------------------------------------------------------------

def _bits_to_bytes(bits: str) -> bytes:
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


def test_truncation_and_trailing_data():
    rnd = random.Random(7)
    x = [30000 + rnd.randrange(64) for _ in range(300)]
    e = encode(x)
    for i in range(len(e)):
        with pytest.raises(RiceError):
            decode(e[:i], len(x))
    with pytest.raises(RiceError):
        decode(e + b"\x00", len(x))                        # strict: trailing octet
    assert decode(e + b"\x00\x00", len(x), strict=False) == x
    with pytest.raises(RiceError):
        decode(bytes.fromhex("0091a5"), 1)                 # non-zero fill bit
    assert decode(bytes.fromhex("0091a5"), 1, strict=False) == [0x1234]


def test_structural_errors():
    ref = format(0x1234, "016b")
    # ROS with only 3 blocks left (count 48): non-conforming -> strict error, lenient decode.
    ros3 = _bits_to_bytes("00000" + ref + "00001")
    with pytest.raises(RiceError):
        decode(ros3, 48)
    assert decode(ros3, 48, strict=False) == [0x1234] * 48
    # Zero runs longer than the remaining segment (3 blocks): count codes 4 and 5.
    for fs in ("0001", "000001"):
        with pytest.raises(RiceError):
            decode(_bits_to_bytes("00000" + ref + fs), 48)
    # Second-Extension in a reference block whose first pair has delta_1 = 1 (gamma = 1).
    with pytest.raises(RiceError):
        decode(_bits_to_bytes("00001" + ref + "01" + "1" * 7), 16)
    # FS split value above 2^n - 1 (n = 4, FS option, m = 16).
    with pytest.raises(RiceError):
        decode(_bits_to_bytes("001" + "0101" + "0" * 16 + "1" + "1" * 14), 16, RiceParams(n=4))
    # Second-Extension pair (0, 2) at n = 1 (Restricted: SE ID 01): delta 2 > 1.
    with pytest.raises(RiceError):
        decode(_bits_to_bytes("01" + "1" + "1" + "000001" + "1" * 6), 16,
               RiceParams(n=1, restricted=True))


def test_corrupted_streams_never_crash():
    rnd = random.Random(1210)
    x = [rnd.randrange(1 << 16) if rnd.random() < 0.3 else 500 for _ in range(400)]
    clean = encode(x)
    outcomes = {"error": 0, "decoded": 0}
    for trial in range(400):
        e = bytearray(clean)
        for _ in range(1 + trial % 4):
            pos = rnd.randrange(len(e) * 8)
            e[pos // 8] ^= 0x80 >> (pos % 8)
        try:
            y = decode(bytes(e), len(x), strict=trial % 2 == 0)
        except RiceError:
            outcomes["error"] += 1
            continue
        assert len(y) == len(x) and all(0 <= v <= 0xFFFF for v in y)
        outcomes["decoded"] += 1
    for _ in range(200):
        junk = rnd.randbytes(rnd.randrange(0, 80))
        try:
            y = decode(junk, rnd.randrange(1, 200), RiceParams(n=rnd.randrange(1, 33)))
        except RiceError:
            continue
        assert all(isinstance(v, int) for v in y)
    assert outcomes["error"] > 0


# --- parameters and arguments ------------------------------------------------------------------

def test_parameter_and_argument_validation():
    for kw in ({"n": 0}, {"n": 33}, {"j": 12}, {"j": 128}, {"r": 0}, {"r": 4097},
               {"n": 5, "restricted": True}, {"n": True}, {"r": 1.0}, {"restricted": 1}):
        with pytest.raises(ValueError):
            RiceParams(**kw)
    for bad_count in (0, -1, MAX_SAMPLES + 1, True, 1.5):
        with pytest.raises(ValueError):
            decode(b"\x00", bad_count)
        with pytest.raises(ValueError):
            max_encoded_len(bad_count)
    with pytest.raises(ValueError):
        encode([])
    for bad in ([65536], [-1], [True], [1.0], ["1"]):
        with pytest.raises(ValueError):
            encode(bad)
    with pytest.raises(ValueError):
        encode([4096], RiceParams(n=12))
    with pytest.raises(TypeError):
        decode("0091a4", 1)                                  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        time_major([1, 2, 3], 2)
    with pytest.raises(ValueError):
        channel_major([[1, 2], [3]])
