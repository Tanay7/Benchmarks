"""SDLS authenticated encryption: the ground's pure-Python AES-256-GCM and the flight
(C++) <-> ground (Python) cross-check.

Primitives (dssq.ccsds.aes_gcm, standard library only) against published known answers:
  FIPS-197 Appendix C.3 (AES-256) and the S-box octet for octet against the flight table;
  McGrew & Viega GCM specification test cases 13-16 (AES-256, 96-bit IV) in full;
  tamper / length / type rejection.
Flight <-> ground: builds tools/host_test (the EXACT flight AES / GCM / SDLS sources),
runs it into a temporary directory and checks its sdls_ae_vectors.txt in both directions:
  flight -> ground  every flight AES / GCM vector is reproduced byte for byte and every
                    flight-built frame decrypts and verifies on the ground
  ground -> flight  the ground builds the same frames byte-exact (GCM is deterministic
                    for a given key and IV), so anything the ground sends is what the
                    flight receive path was tested with
plus known answers shared with tools/host_test/test_crypto.cpp and SDLS behaviour
(tamper, replay, null SA, epoch rules). The C++ part is skipped automatically if no C++
toolchain is available. The `cryptography` package is NOT needed: when it happens to be
installed (development PC) one extra randomized cross-check runs against it.
"""
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dssq.ccsds.aes_gcm import SBOX, Aes256, AesGcm, InvalidTag
from dssq.ccsds.crc import crc16_ccitt
from dssq.ccsds.sdls import (AE_OVERHEAD, LABEL_TC_AE, LABEL_TM_AE, SERVICE_AE, SERVICE_AUTH,
                             SERVICE_NONE, SPI_TC_AE, SdlsReceiver, SdlsSender, SdlsTmReceiver,
                             SdlsTmSender, build_iv, derive_key, link_snapshot, parse_service)

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"
FLIGHT_AES = ROOT / "spacecraft" / "vgq1_flight" / "sketch" / "src" / "ccsds" / "aes.cpp"

MASTER = bytes(range(32))
NOOP = bytes.fromhex("18c0c000000001")
# Shared known answers (identical constants in tools/host_test/test_crypto.cpp).
KAT_TC_KEY = "6a0fc847b89c96f2a51182cb604701ce276d706174bda2907b13e4d1e7bdacf1"
KAT_TM_KEY = "e2344f0a091558afade4702042d0a9c9a9209464743e95fca02c3bd2ac3d6a59"
KAT_TC_AE = ("00a7002b0000025443000000000000000000073e53f988b9490963030370d67a616f9bb7736a06f68fbed67d")
KAT_TC_AUTH = "00a700230000010000000718c0c0000000010a45be9321446143549fe23fdaf3d435473a"
# FIPS-197 Appendix C.3 (AES-256): key, plaintext, ciphertext.
FIPS197_C3 = ("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
              "00112233445566778899aabbccddeeff", "8ea2b7ca516745bfeafc49904b496089")
# McGrew & Viega GCM specification, test cases 13-16 (AES-256, 96-bit IV); the same
# values as tools/host_test/test_crypto.cpp (crypto_gcm_test_vectors).
_K0 = "00" * 32
_K = "feffe9928665731c6d6a8f9467308308" * 2
_IV = "cafebabefacedbaddecaf888"
_P = ("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
      "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255")
_C = ("522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
      "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662898015ad")
GCM_SPEC = {   # name: (key, iv, aad, plaintext, ciphertext, tag)
    "gcm_tc13": (_K0, "00" * 12, "", "", "", "530f8afbc74536b9a963b4f1c4cb738b"),
    "gcm_tc14": (_K0, "00" * 12, "", "00" * 16, "cea7403d4d606b6e074ec5d3baf39d18",
                 "d0d1c8a799996bf0265b98b5d48ab919"),
    "gcm_tc15": (_K, _IV, "", _P, _C, "b094dac5d93471bdec1a502270e3cc6c"),
    "gcm_tc16": (_K, _IV, "feedfacedeadbeeffeedfacedeadbeefabaddad2", _P[:120], _C[:120],
                 "76fc6ece0f4e1768cddf8853bb2d551b"),
}
INT_FIELDS = {"hdr_len", "prot_end", "epoch", "sn"}


def tc_v1_header(length: int, seq: int = 0) -> bytes:
    return (0x00A7).to_bytes(2, "big") + (length - 1).to_bytes(2, "big") + bytes([seq])


def with_fecf(f: bytes) -> bytes:
    return f + crc16_ccitt(f).to_bytes(2, "big")


@pytest.fixture(scope="module")
def vectors(tmp_path_factory) -> dict[str, dict]:
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("sdls_vectors")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    subprocess.run([str(HOST / "host_test"), str(out)], check=True, stdout=subprocess.DEVNULL)
    cases: dict[str, dict] = {}
    for line in (out / "sdls_ae_vectors.txt").read_text().splitlines():
        if not line.strip():
            continue
        name, *fields = line.split()
        rec = {}
        for kv in fields:
            k, v = kv.split("=", 1)
            rec[k] = int(v) if k in INT_FIELDS else bytes.fromhex(v)
        cases[name] = rec
    return cases


# --- primitives: dssq.ccsds.aes_gcm against published known answers ---------------

def test_aes256_fips197_appendix_c3():
    key, pt, ct = (bytes.fromhex(x) for x in FIPS197_C3)
    assert Aes256(key).encrypt_block(pt) == ct
    assert Aes256(bytearray(key)).encrypt_block(memoryview(pt)) == ct     # any bytes-like input


def test_sbox_is_the_flight_table():
    # FIPS-197 5.1.1 worked example: SubBytes({53}) = {ed}; the S-box is a permutation.
    assert SBOX[0x00] == 0x63 and SBOX[0x53] == 0xED and sorted(SBOX) == list(range(256))
    if not FLIGHT_AES.exists():
        pytest.skip(f"flight source not found: {FLIGHT_AES}")
    body = FLIGHT_AES.read_text().split("kSbox[256] = {", 1)[1].split("};", 1)[0]
    assert tuple(int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]{2}", body)) == SBOX


@pytest.mark.parametrize("name", sorted(GCM_SPEC))
def test_gcm_specification_test_cases(name):
    key, iv, aad, pt, ct, tag = (bytes.fromhex(x) for x in GCM_SPEC[name])
    gcm = AesGcm(key)
    sealed = gcm.encrypt(iv, pt, aad)
    assert sealed == ct + tag
    assert gcm.decrypt(iv, sealed, aad) == pt


def test_gcm_rejects_any_modification():
    key, iv, aad, pt, _, _ = (bytes.fromhex(x) for x in GCM_SPEC["gcm_tc16"])
    gcm = AesGcm(key)
    sealed = gcm.encrypt(iv, pt, aad)
    for i in range(len(sealed)):                       # every ciphertext and tag octet
        b = bytearray(sealed)
        b[i] ^= 1 << (i % 8)
        with pytest.raises(InvalidTag):
            gcm.decrypt(iv, b, aad)
    for i in range(len(aad)):
        a = bytearray(aad)
        a[i] ^= 0x80
        with pytest.raises(InvalidTag):
            gcm.decrypt(iv, sealed, a)
    for i in range(len(iv)):
        v = bytearray(iv)
        v[i] ^= 0x01
        with pytest.raises(InvalidTag):
            gcm.decrypt(v, sealed, aad)
    with pytest.raises(InvalidTag):
        gcm.decrypt(iv, sealed[:-1], aad)               # truncated
    with pytest.raises(InvalidTag):
        gcm.decrypt(iv, sealed, aad + b"\x00")          # extended AAD
    with pytest.raises(InvalidTag):
        AesGcm(bytes(32)).decrypt(iv, sealed, aad)      # wrong key
    assert gcm.decrypt(iv, sealed, aad) == pt           # the object holds no state


def test_gcm_input_validation():
    for n in (0, 16, 24, 31, 33, 64):
        with pytest.raises(ValueError, match="32 octets"):
            AesGcm(bytes(n))
    for bad in ("00" * 32, 32, None):
        with pytest.raises(TypeError):
            AesGcm(bad)                                 # type: ignore[arg-type]
    gcm = AesGcm(MASTER)
    for n in (0, 8, 11, 13, 16, 60):                   # 96-bit IVs only
        with pytest.raises(ValueError, match="96 bits"):
            gcm.encrypt(bytes(n), b"x")
        with pytest.raises(ValueError, match="96 bits"):
            gcm.decrypt(bytes(n), bytes(17))
    with pytest.raises(ValueError, match="tag"):
        gcm.decrypt(bytes(12), bytes(15))
    with pytest.raises(TypeError):
        gcm.encrypt(bytes(12), "plaintext")             # type: ignore[arg-type]
    for n in (0, 15, 17):
        with pytest.raises(ValueError, match="16 octets"):
            Aes256(MASTER).encrypt_block(bytes(n))
    assert len(gcm.encrypt(bytes(12), b"")) == 16       # empty plaintext: the tag alone


def test_flight_aes_and_gcm_vectors_byte_identical(vectors):
    c3 = vectors["aes_c3"]
    assert c3["ct"].hex() == FIPS197_C3[2] and Aes256(c3["key"]).encrypt_block(c3["pt"]) == c3["ct"]
    names = [n for n in vectors if n.startswith("gcm_")]
    assert len(names) == 16                             # 4 specification + 12 random lengths
    for name in names:
        v = vectors[name]
        gcm = AesGcm(v["key"])
        assert gcm.encrypt(v["iv"], v["pt"], v["aad"]) == v["ct"] + v["tag"], name
        assert gcm.decrypt(v["iv"], v["ct"] + v["tag"], v["aad"]) == v["pt"], name
    for name, case in GCM_SPEC.items():
        assert vectors[name]["ct"].hex() == case[4] and vectors[name]["tag"].hex() == case[5], name


def test_gcm_matches_cryptography_package_if_installed():
    """Optional development-PC cross-check against an independent implementation."""
    aead = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
    ciphers = pytest.importorskip("cryptography.hazmat.primitives.ciphers")
    rng = random.Random(20261011)
    for n_pt in (0, 1, 15, 16, 17, 31, 32, 33, 156, 158, 183, 255, 1000):
        for n_aad in (0, 1, 13, 16, 20, 22, 40):
            key, iv, aad, pt = rng.randbytes(32), rng.randbytes(12), rng.randbytes(n_aad), rng.randbytes(n_pt)
            ref = aead.AESGCM(key).encrypt(iv, pt, aad)
            gcm = AesGcm(key)
            assert gcm.encrypt(iv, pt, aad) == ref, (n_pt, n_aad)
            assert gcm.decrypt(iv, ref, aad) == pt, (n_pt, n_aad)
        block = rng.randbytes(16)
        ecb = ciphers.Cipher(ciphers.algorithms.AES(key), ciphers.modes.ECB()).encryptor()
        assert Aes256(key).encrypt_block(block) == ecb.update(block) + ecb.finalize()


def test_sdls_ae_needs_no_third_party_package():
    """In a fresh interpreter where importing `cryptography` (or pyserial / tomli) fails,
    the TC AE known answer is still produced and verified."""
    code = "\n".join((
        "import sys",
        f"sys.path.insert(0, {str(ROOT / 'ground')!r})",
        "for name in ('cryptography', 'serial', 'tomli'):",
        "    sys.modules[name] = None             # any import of it now raises ImportError",
        "from dssq.ccsds.crc import crc16_ccitt",
        "from dssq.ccsds.sdls import SdlsReceiver, SdlsSender",
        "tx = SdlsSender(bytes(range(32)), service='AE')",
        "tx.sn = 6",
        "hdr = bytes.fromhex('00a7002b00')",
        f"f = hdr + tx.protect(hdr, bytes.fromhex({NOOP.hex()!r}))",
        "f += crc16_ccitt(f).to_bytes(2, 'big')",
        f"assert f.hex() == {KAT_TC_AE!r}, f.hex()",
        "assert SdlsReceiver(bytes(range(32)), service='AE').process(f, f[5:-2])[1] == 'OK'",
    ))
    subprocess.run([sys.executable, "-I", "-c", code], check=True, timeout=60)


def test_key_derivation_known_answer(vectors):
    assert derive_key(MASTER, LABEL_TC_AE).hex() == KAT_TC_KEY
    assert derive_key(MASTER, LABEL_TM_AE).hex() == KAT_TM_KEY
    k = vectors["kdf"]
    assert k["master"] == MASTER and k["tc"].hex() == KAT_TC_KEY and k["tm"].hex() == KAT_TM_KEY


# --- TC ---------------------------------------------------------------------------

def test_tc_ae_known_answer_python():
    tx = SdlsSender(MASTER, spi=1, service="AE")
    tx.sn = 6
    assert tx.spi == SPI_TC_AE and tx.overhead == AE_OVERHEAD
    hdr = tc_v1_header(5 + tx.overhead + len(NOOP) + 2)
    assert with_fecf(hdr + tx.protect(hdr, NOOP)).hex() == KAT_TC_AE
    rx = SdlsReceiver(MASTER, service="AE")
    f = bytes.fromhex(KAT_TC_AE)
    assert rx.process(f, f[5:-2]) == (NOOP, "OK") and rx.last_sn == 7


def test_auth_api_unchanged():
    from dssq.ccsds.tc import build_tc_frame, parse_tc_frame
    tx = SdlsSender(MASTER, spi=1)                     # v2 call: AUTH
    tx.sn = 6
    assert tx.service == SERVICE_AUTH and tx.overhead == 22
    assert build_tc_frame(NOOP, seq=0, sdls=tx).hex() == KAT_TC_AUTH
    tx.sn = 6
    hdr = tc_v1_header(36)
    assert with_fecf(hdr + tx.protect(hdr, NOOP)).hex() == KAT_TC_AUTH
    f = bytes.fromhex(KAT_TC_AUTH)
    assert SdlsReceiver(MASTER, spi=1).process(f, parse_tc_frame(f).data) == (NOOP, "OK")


def test_flight_tc_ae_frames_verify_on_ground(vectors):
    assert vectors["tc_ae_v1"]["frame"].hex() == KAT_TC_AE
    rx = SdlsReceiver(MASTER, service="AE")
    for name in ("tc_ae_v1", "tc_ae_uslp"):
        v = vectors[name]
        f, h = v["frame"], v["hdr_len"]
        assert crc16_ccitt(f[:-2]) == int.from_bytes(f[-2:], "big")
        assert rx.process(f, f[h:-2], hdr_len=h) == (v["plain"], "OK"), name
        assert rx.last_sn == v["sn"]
    assert vectors["tc_ae_uslp"]["plain"][0] == 0xE0       # TFDF header (rule 111, UPID 0) encrypted


def test_ground_tc_ae_frames_equal_flight(vectors):
    for name in ("tc_ae_v1", "tc_ae_uslp"):
        v = vectors[name]
        f, h = v["frame"], v["hdr_len"]
        tx = SdlsSender(v["master"], service="AE")
        tx.sn = v["sn"] - 1
        assert with_fecf(f[:h] + tx.protect(f[:h], v["plain"])) == f, name


def test_tc_ae_tamper_and_replay():
    f = bytes.fromhex(KAT_TC_AE)
    rx = SdlsReceiver(MASTER, service="AE")
    for off, mask, want in ((22, 0x01, "BAD_MAC"),        # ciphertext
                            (4, 0x01, "BAD_MAC"),         # primary header (AAD)
                            (18, 0x0F, "BAD_MAC"),        # IV sn 7 -> 8 (AAD)
                            (len(f) - 3, 0x80, "BAD_MAC"),  # tag
                            (7, 0x01, "BAD_IV"),
                            (6, 0x03, "BAD_SPI")):
        b = bytearray(f)
        b[off] ^= mask
        assert rx.process(bytes(b), bytes(b[5:-2])) == (None, want), off
    assert rx.last_sn == 0 and rx.failures == 6           # failures never advance the SN
    assert rx.process(f, f[5:-2]) == (NOOP, "OK")
    assert rx.process(f, f[5:-2]) == (None, "REPLAY") and rx.last_sn == 7


def test_sender_sn_persists_across_services(tmp_path):
    sn_file = tmp_path / "sdls_sn.txt"
    a = SdlsSender(MASTER, 1, sn_file)
    a.protect(tc_v1_header(36), NOOP)
    b = SdlsSender(MASTER, 1, sn_file, service="AE")      # restart in AE: SN continues
    sh = b.protect(tc_v1_header(44), NOOP)[:14]
    assert sh == SPI_TC_AE.to_bytes(2, "big") + build_iv(0x5443, 0, 2) and sn_file.read_text() == "2"


def test_service_selection():
    assert parse_service("ae") == SERVICE_AE and parse_service("AUTH") == SERVICE_AUTH
    assert parse_service(None) == SERVICE_NONE and parse_service(2) == SERVICE_AE
    with pytest.raises(ValueError):
        parse_service("ENCRYPT")
    off = SdlsSender(None, service="AE")
    assert not off.enabled and off.overhead == 0 and off.protect(b"h", NOOP) == NOOP
    late = SdlsSender(None, service="AE")
    late.key = MASTER                                       # key assigned later: AE key derived
    late.sn = 6
    hdr = tc_v1_header(44)
    assert with_fecf(hdr + late.protect(hdr, NOOP)).hex() == KAT_TC_AE


# --- TM ---------------------------------------------------------------------------

def test_flight_tm_frames_verify_on_ground(vectors):
    rx = SdlsTmReceiver(MASTER)
    seq = sorted((n for n in vectors if n.startswith("tm_ae_")), key=lambda n: int(n[6:]))
    assert len(seq) >= 6                       # 0..5 below; later ones: sn 0xFFFFFFFF, next epoch
    for name in seq:
        v = vectors[name]
        f = v["frame"]
        assert crc16_ccitt(f[:-2]) == int.from_bytes(f[-2:], "big")
        r = rx.process(f, v["hdr_len"], v["prot_end"])
        assert r.verdict == "OK" and r.protected and r.data == v["plain"], name
        assert (r.epoch, r.sn) == (v["epoch"], v["sn"])
    assert vectors["tm_ae_5"]["hdr_len"] == 8 and len(vectors["tm_ae_5"]["plain"]) == 156   # USLP
    assert len(vectors["tm_ae_0"]["plain"]) == 158                                          # TM v1
    assert (vectors["tm_ae_5"]["epoch"], vectors["tm_ae_5"]["sn"]) == (6, 2)
    last = (vectors[seq[-1]]["epoch"], vectors[seq[-1]]["sn"])
    assert rx.frames_ok == len(seq) and rx.last == last
    # replays (any earlier (epoch, sn)) are dropped and do not move the state
    for name in seq:
        v = vectors[name]
        assert rx.process(v["frame"], v["hdr_len"], v["prot_end"]).verdict == "REPLAY"
    assert rx.replay == len(seq) and rx.last == last


def test_flight_tm_frames_tamper(vectors):
    v = vectors["tm_ae_0"]
    rx = SdlsTmReceiver(MASTER)
    for off in (0, 3, 6 + 1, 6 + 13, 6 + 14 + 50, 6 + 14 + 158 + 15):
        b = bytearray(v["frame"])
        b[off] ^= 0x01
        r = rx.process(bytes(b), 6, 194)
        assert not r.ok and r.data is None, off
    assert rx.frames_ok == 0 and rx.last == (0, 0) and rx.auth_fail == 6
    # OCF / FECF are outside the protected region (checked by the FECF, not SDLS)
    b = bytearray(v["frame"])
    b[195] ^= 0x01
    assert rx.process(bytes(b), 6, 194).verdict == "OK"
    assert SdlsTmReceiver(bytes(32)).process(v["frame"], 6, 194).verdict == "BAD_MAC"   # wrong key


def test_flight_null_sa_frame(vectors):
    v = vectors["tm_null"]
    rx = SdlsTmReceiver(MASTER)
    r = rx.process(v["frame"], v["hdr_len"], v["prot_end"])
    assert r.verdict == "UNPROTECTED" and r.ok and not r.protected and r.data == v["plain"]
    assert rx.unprotected == 1 and rx.last == (0, 0)
    strict = SdlsTmReceiver(MASTER, accept_null=False)
    assert strict.process(v["frame"], 6, 194).verdict == "BAD_SPI"
    b = bytearray(v["frame"])
    b[6 + 14 + 158] ^= 0x01                                 # non-zero MAC on a null-SA frame
    assert rx.process(bytes(b), 6, 194).verdict == "BAD_MAC"


def test_ground_tm_sender_equals_flight(vectors):
    for name in [n for n in vectors if n.startswith("tm_ae_")]:
        v = vectors[name]
        f, h = v["frame"], v["hdr_len"]
        tx = SdlsTmSender(MASTER)
        assert tx.set_epoch(v["epoch"])
        tx.sn = v["sn"] - 1
        assert tx.protect(f[:h], v["plain"]) == f[h:v["prot_end"]], name


def test_tm_sender_state_machine_mirrors_flight():
    tx = SdlsTmSender(MASTER)
    hdr, data = bytes(6), bytes(range(158))
    assert (tx.header_len, tx.trailer_len, tx.state) == (14, 16, 2)
    assert tx.protect(hdr, data) is None                    # no epoch: refused
    tx.fallback = True
    assert tx.state == 3 and tx.protect(hdr, data) == bytes(14) + data + bytes(16)
    assert not tx.set_epoch(0) and tx.set_epoch(5) and tx.state == 1
    rx = SdlsTmReceiver(MASTER)
    frames = [tx.protect(hdr, data) for _ in range(3)]
    assert tx.set_epoch(5) and tx.sn == 3                   # same epoch: no-op
    assert not tx.set_epoch(4) and tx.set_epoch(6) and tx.sn == 0
    frames.append(tx.protect(hdr, data))
    got = [rx.process(hdr + p + bytes(6), 6, 194) for p in frames]
    assert [(r.verdict, r.epoch, r.sn) for r in got] == [("OK", 5, 1), ("OK", 5, 2), ("OK", 5, 3),
                                                         ("OK", 6, 1)]
    assert all(r.data == data for r in got)
    snap = link_snapshot(SdlsSender(MASTER, service="AE"), rx)
    assert snap == {"tc_service": "AE", "tm_service": "AE", "tm_epoch": 6, "tm_last_sn": 1,
                    "tm_frames_ok": 4, "tm_auth_fail": 0, "tm_replay": 0, "tm_unprotected": 0,
                    "tc_next_sn": 1}
    rx.reset_replay()
    assert rx.process(hdr + frames[0] + bytes(6), 6, 194).verdict == "OK"


def test_tm_none_passthrough():
    rx = SdlsTmReceiver(None)
    tx = SdlsTmSender(None)
    data = bytes(188)
    assert rx.overhead == 0 and tx.header_len == 0 and tx.state == 0
    assert tx.protect(bytes(6), data) == data
    assert rx.process(bytes(6) + data + bytes(6), 6, 194).verdict == "NONE"
    assert link_snapshot(None, None)["tm_service"] == "NONE"


# --- integration with the frame layers (dssq.ccsds.tc / dssq.ccsds.tm seams) -------

def test_tc_builders_use_the_sdls_hooks(vectors):
    from dssq.ccsds import tc
    tx = SdlsSender(MASTER, service="AE")
    tx.sn = 6
    assert tc.build_tc_frame(NOOP, seq=0, sdls=tx).hex() == KAT_TC_AE
    if not hasattr(tc, "build_uslp_tc_frame"):
        pytest.skip("USLP TC builder not available")
    v = vectors["tc_ae_uslp"]
    tx.sn = v["sn"] - 1
    assert tc.build_uslp_tc_frame(NOOP, seq=5, sdls=tx) == v["frame"]


def test_tm_parser_seam_opens_flight_frames(vectors):
    from dssq.ccsds.tm import TransferFrame
    if not hasattr(TransferFrame, "with_plaintext"):
        pytest.skip("TM parser without the SDLS split")
    rx = SdlsTmReceiver(MASTER)
    for name in ("tm_ae_0", "tm_ae_5"):
        v = vectors[name]
        tf = TransferFrame.parse(v["frame"], 14, 16)
        assert tf.secured and tf.hdr_len == v["hdr_len"]
        opened, r = rx.open_frame(tf)
        assert r.verdict == "OK" and opened is not None and not opened.secured, name
        assert opened.data == (v["plain"] if v["hdr_len"] == 6 else v["plain"][3:]), name
    tf = TransferFrame.parse(vectors["tm_null"]["frame"], 14, 16)
    opened, r = rx.open_frame(tf)
    assert r.verdict == "UNPROTECTED" and opened.data == vectors["tm_null"]["plain"]


# --- Phase B: crash-safe SDLS state (F-64), hardline CLTU (F-63), key tool ----------
import importlib.util  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402

from dssq import cltu as hardline  # noqa: E402
from dssq.ccsds import sdls as sdls_mod  # noqa: E402
from dssq.ccsds.tc import cltu_decode, parse_tc_frame, tc_open_tfdf  # noqa: E402
from dssq.cmdparse import parse, tc_packet  # noqa: E402
from dssq.config import load_config  # noqa: E402


def test_sn_file_is_written_atomically_before_use(tmp_path, monkeypatch):
    sn_file = tmp_path / "sdls_sn.txt"
    tx = SdlsSender(MASTER, 1, sn_file, service="AE")
    assert tx.sn_state == "OK" and not tx.uplink_blocked and not sn_file.exists()   # missing file: first use
    assert tx.next_sn() == 1 and sn_file.read_text() == "1"
    assert stat.S_IMODE(sn_file.stat().st_mode) == 0o600
    real_replace = os.replace

    def power_cut(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sdls_mod.os, "replace", power_cut)
    with pytest.raises(OSError):
        tx.next_sn()                                       # the frame must not be sent
    lock = tmp_path / "sdls_sn.txt.lock"                  # inter-process lock file (C11)
    assert tx.sn == 1 and sn_file.read_text() == "1" and sorted(tmp_path.iterdir()) == [sn_file, lock]
    monkeypatch.setattr(sdls_mod.os, "replace", real_replace)
    assert tx.next_sn() == 2 and sn_file.read_text() == "2"
    assert SdlsSender(MASTER, 1, sn_file).sn == 2         # restart continues


@pytest.mark.parametrize("content", ["", "  \n", "12x", "-4", "99999999999", "\xff\xfe"])
def test_corrupt_sn_file_blocks_until_a_protected_hk(tmp_path, content):
    sn_file = tmp_path / "sdls_sn.txt"
    sn_file.write_bytes(content.encode("latin-1"))
    tx = SdlsSender(MASTER, 1, sn_file, service="AE")
    assert tx.uplink_blocked and tx.sn_state.startswith("BLOCKED: SDLS SN file corrupt")
    with pytest.raises(RuntimeError):
        tx.protect(tc_v1_header(44), NOOP)
    assert tx.observe_spacecraft_sn(500, protected=False) is None and tx.uplink_blocked   # unprotected HK: ignored
    msg = tx.observe_spacecraft_sn(41, protected=True)
    assert "restored to 41" in msg and not tx.uplink_blocked and sn_file.read_text() == "41"
    assert tx.next_sn() == 42


def test_unprotected_hk_never_moves_the_sn(tmp_path):
    tx = SdlsSender(MASTER, 1, tmp_path / "sn.txt", service="AE")
    tx.next_sn()
    for bad in (0xFFFFFFFF, 10**6):
        assert tx.observe_spacecraft_sn(bad, protected=False) is None and tx.sn == 1
    for junk in (-1, 2**32, True, "7"):
        assert tx.observe_spacecraft_sn(junk, protected=True) is None and tx.sn == 1
    assert tx.observe_spacecraft_sn(1, protected=True) is None              # not ahead: nothing to do
    assert "raised 1 -> 9" in tx.observe_spacecraft_sn(9, protected=True) and tx.next_sn() == 10
    off = SdlsSender(None, 1, tmp_path / "x.txt", service="AE")             # no key: never blocked
    (tmp_path / "y.txt").write_text("garbage")
    assert not off.uplink_blocked and not SdlsSender(None, 1, tmp_path / "y.txt").uplink_blocked


def test_sn_shared_with_another_writer_is_never_reused(tmp_path):
    sn_file = tmp_path / "sdls_sn.txt"
    gds = SdlsSender(MASTER, 1, sn_file, service="AE")
    assert gds.next_sn() == 1
    tool = SdlsSender(MASTER, 1, sn_file, service="AE")                    # e.g. dssq.cltu, same file
    assert tool.next_sn() == 2 and tool.next_sn() == 3
    assert gds.next_sn() == 4                                              # re-read before use
    sn_file.write_text("oops")
    with pytest.raises(RuntimeError):
        gds.next_sn()                                                      # became corrupt: blocked
    assert gds.uplink_blocked


def test_protected_hk_never_lowers_a_file_the_hardline_tool_advanced(tmp_path):
    """C10: observe_spacecraft_sn re-reads the shared file before persisting; the HK lags a
    queued burst of hardline CLTUs, so max(in-memory, last_sn) alone would lower the file
    and the GDS would reuse the tool's GCM nonces."""
    sn_file = tmp_path / "sdls_sn.txt"
    sn_file.write_text("45")
    gds = SdlsSender(MASTER, 1, sn_file, service="AE")
    tool = SdlsSender(MASTER, 1, sn_file, service="AE")                    # dssq.cltu
    assert [tool.next_sn() for _ in range(5)] == [46, 47, 48, 49, 50]
    assert gds.sn == 45 and gds.observe_spacecraft_sn(46, protected=True) is None
    assert sn_file.read_text() == "50" and gds.next_sn() == 51
    assert "raised 51 -> 60" in gds.observe_spacecraft_sn(60, protected=True) and sn_file.read_text() == "60"


def test_sn_file_lock_serialises_the_read_modify_write(tmp_path):
    """C11: next_sn holds an exclusive flock on <file>.lock from the re-read to the rename,
    so a second writer (dssq.cltu in another process) cannot take the same number."""
    fcntl = pytest.importorskip("fcntl")
    import threading
    sn_file = tmp_path / "sdls_sn.txt"
    gds = SdlsSender(MASTER, 1, sn_file, service="AE")
    assert gds.next_sn() == 1
    fd = os.open(tmp_path / "sdls_sn.txt.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)                                         # the "other process"
    got: list[int] = []
    t = threading.Thread(target=lambda: got.append(gds.next_sn()))
    t.start()
    t.join(0.3)
    assert t.is_alive() and not got                                        # waits for the lock
    sdls_mod.atomic_write_text(sn_file, "41")                              # the tool used 2..41
    os.close(fd)
    t.join(5)
    assert got == [42] and sn_file.read_text() == "42"


def _take_sns(sn_file: str, n: int, out: str) -> None:
    tx = SdlsSender(MASTER, 1, sn_file, service="AE")
    Path(out).write_text(" ".join(str(tx.next_sn()) for _ in range(n)))


def test_two_processes_never_share_a_sequence_number(tmp_path):
    import multiprocessing as mp
    pytest.importorskip("fcntl")
    ctx = mp.get_context("fork")
    sn_file = tmp_path / "sdls_sn.txt"
    procs = [ctx.Process(target=_take_sns, args=(str(sn_file), 150, str(tmp_path / f"o{i}"))) for i in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0, 0]                           # no OSError (shared .tmp)
    used = [int(x) for i in range(2) for x in (tmp_path / f"o{i}").read_text().split()]
    assert sorted(used) == list(range(1, 301)) and sn_file.read_text() == "300"


def _tm_frames(epoch, n, hdr=bytes(6), data=bytes(range(158))):
    tx = SdlsTmSender(MASTER)
    tx.set_epoch(epoch)
    return [hdr + tx.protect(hdr, data) + bytes(6) for _ in range(n)]


def test_tm_replay_state_persisted_and_restored(tmp_path):
    state = tmp_path / "sdls_tm_replay.txt"
    frames = _tm_frames(5, 3)
    rx = SdlsTmReceiver(MASTER, state_file=state)
    assert [rx.process(f, 6, 194).verdict for f in frames[:2]] == ["OK", "OK"]
    assert state.read_text() == "5 2" and stat.S_IMODE(state.stat().st_mode) == 0o600
    again = SdlsTmReceiver(MASTER, state_file=state)                       # GDS restart
    assert again.last == (5, 2) and again.load_warning is None
    assert again.process(frames[0], 6, 194).verdict == "REPLAY"             # old genuine frame refused
    assert again.process(frames[2], 6, 194).verdict == "OK" and state.read_text() == "5 3"
    again.reset_replay()
    assert state.read_text() == "0 0"
    state.write_text("5 x")
    bad = SdlsTmReceiver(MASTER, state_file=state)
    assert bad.last == (0, 0) and "corrupt" in bad.load_warning
    blocked = tmp_path / "dir"
    blocked.mkdir()
    rx2 = SdlsTmReceiver(MASTER, state_file=blocked)                       # cannot be written
    assert rx2.process(frames[0], 6, 194).verdict == "OK" and "not saved" in rx2.persist_error


def test_tm_epoch_regression_is_counted(tmp_path):
    old, new = _tm_frames(5, 2), _tm_frames(6, 1)
    rx = SdlsTmReceiver(MASTER, state_file=tmp_path / "s.txt")
    assert rx.process(new[0], 6, 194).verdict == "OK"
    assert rx.process(old[1], 6, 194).verdict == "REPLAY" and rx.epoch_regressed == 1   # valid MAC, lower epoch
    assert rx.process(new[0], 6, 194).verdict == "REPLAY" and rx.epoch_regressed == 1   # same epoch: replay only
    forged = bytearray(old[0])
    forged[30] ^= 1
    assert rx.process(bytes(forged), 6, 194).verdict == "BAD_MAC" and rx.epoch_regressed == 1


def _cltu_cfg(tmp_path, framing, service):
    cfg = load_config()
    key_file = tmp_path / "sdls_key.hex"
    key_file.write_text(MASTER.hex() + "\n")
    cfg["sdls"].update(tc_service=service, key_file=str(key_file))
    cfg["link"]["tc_framing"] = framing
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    return cfg


@pytest.mark.parametrize("framing", ["TC", "USLP"])
@pytest.mark.parametrize("service", ["AUTH", "AE"])
def test_hardline_cltu_is_accepted_by_the_reference_receiver(tmp_path, framing, service):
    cfg = _cltu_cfg(tmp_path, framing, service)
    rx = SdlsReceiver(MASTER, service=service)
    for want_sn, text in ((1, "NOOP"), (2, "MODE CRUISE"), (3, "PING 4242")):
        cltu, info = hardline.build_hardline_cltu(cfg, text)
        assert len(cltu) <= hardline.MAX_HARDLINE_OCTETS == 110
        assert f"SDLS {service} SN {want_sn}" in info and framing in info
        frame, _ = cltu_decode(cltu)
        f = parse_tc_frame(frame, open_tfdf=False)
        assert f is not None and f.bypass and f.vcid == 0
        body, verdict = rx.process(frame[:f.length], f.data, hdr_len=f.hdr_len)
        assert verdict == "OK", (framing, service, text)
        f.data = body
        f = tc_open_tfdf(f)
        assert f is not None and f.data == tc_packet(parse(text), 0)
    assert (tmp_path / "archive" / "sdls_sn.txt").read_text() == "3"     # shared with the GDS


def test_hardline_cltu_rules(tmp_path, monkeypatch, capsys):
    cfg = _cltu_cfg(tmp_path, "USLP", "AE")
    with pytest.raises(hardline.HardlineError, match=r"frame \d+ > 64 octets with USLP/AE"):
        hardline.build_hardline_cltu(cfg, "MAGCAL 0 1200 -750 400 16384 16384 16384 0 0 0")      # C-08
    assert hardline.build_hardline_cltu(_cltu_cfg(tmp_path, "TC", "AE"), "MAGCAL 0 1200 -750 400 16384 16384 "
                                        "16384 0 0 0")[0]
    with pytest.raises(hardline.HardlineError, match="RF-only"):
        hardline.build_hardline_cltu(cfg, "UNLOCK")
    with pytest.raises(hardline.HardlineError, match="rejected"):
        hardline.build_hardline_cltu(cfg, "MODE WARP")
    (tmp_path / "archive" / "sdls_sn.txt").write_text("")
    with pytest.raises(hardline.HardlineError, match="BLOCKED"):
        hardline.build_hardline_cltu(cfg, "NOOP")
    nokey = _cltu_cfg(tmp_path, "TC", "AE")
    nokey["sdls"]["key_file"] = str(tmp_path / "missing.hex")
    cltu, info = hardline.build_hardline_cltu(nokey, "NOOP")
    assert "unprotected (no key file)" in info and len(cltu) == 26
    app = tmp_path / "app_data"
    cfg2 = load_config()
    hardline.apply_app_data(cfg2, app)
    assert cfg2["sdls"]["key_file"] == str(app / "sdls_key.hex") and cfg2["gds"]["archive_dir"].startswith(str(app))
    monkeypatch.chdir(tmp_path)
    assert hardline.main(["NOOP", "--app-data", str(app)]) == 0
    out = capsys.readouterr()
    assert out.out.strip().startswith("EB90") and "# BD NOOP TC" in out.err
    assert hardline.main(["MODE WARP"]) == 1


def test_gen_sdls_key_writes_both_files(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("gen_sdls_key", ROOT / "tools" / "gen_sdls_key.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(tool, "GROUND", tmp_path / "sdls_key.hex")
    monkeypatch.setattr(tool, "FLIGHT", tmp_path / "sdls_key.h")
    assert tool.main(["--spi", "7"]) == 0
    msg = capsys.readouterr().out
    key = bytes.fromhex((tmp_path / "sdls_key.hex").read_text().strip())
    assert len(key) == 32 and stat.S_IMODE((tmp_path / "sdls_key.hex").stat().st_mode) == 0o600
    header = (tmp_path / "sdls_key.h").read_text()
    assert "#pragma once" in header and "#include <stdint.h>" in header and "#define VGQ_SDLS_SPI 7" in header
    assert "static const uint8_t kVgqSdlsKey[32] = {" + ", ".join(f"0x{b:02X}" for b in key) + "};" in header
    assert "tc_service / tm_service" in msg and "enabled" not in msg
    assert "copy this file to <App>/data/sdls_key.hex (the App ignores a relative key_file)" in msg
    assert "A new key allows the EGSE boot counter to restart; with an old key it must never go back" in msg
    assert load_key_roundtrip(tmp_path / "sdls_key.hex") == key
    assert tool.main([]) == 1                                              # never overwritten silently
    assert tool.main(["--force"]) == 0 and bytes.fromhex((tmp_path / "sdls_key.hex").read_text()) != key
    assert tool.main(["--spi", "0", "--force"]) == 2


def load_key_roundtrip(path):
    return sdls_mod.load_key(path)
