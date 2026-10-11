"""SDLS authenticated encryption: flight (C++) <-> ground (Python) cross-check.

Builds tools/host_test (the EXACT flight AES / GCM / SDLS sources), runs it into a
temporary directory and checks its sdls_ae_vectors.txt with an independent
implementation (the `cryptography` package) in both directions:
  flight -> ground  the ground decrypts / verifies every flight-built frame
  ground -> flight  the ground builds the same frames byte-exact (GCM is
                    deterministic for a given key and IV), so anything the
                    ground sends is what the flight receive path was tested with
plus known answers shared with tools/host_test/test_crypto.cpp and pure-Python
behaviour (tamper, replay, null SA, epoch rules). The C++ part is skipped
automatically if no C++ toolchain is available.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from dssq.ccsds.crc import crc16_ccitt  # noqa: E402
from dssq.ccsds.sdls import (AE_OVERHEAD, LABEL_TC_AE, LABEL_TM_AE, SERVICE_AE,  # noqa: E402
                             SERVICE_AUTH, SERVICE_NONE, SPI_TC_AE, SdlsReceiver, SdlsSender,
                             SdlsTmReceiver, SdlsTmSender, build_iv, derive_key, link_snapshot,
                             parse_service)

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"

MASTER = bytes(range(32))
NOOP = bytes.fromhex("18c0c000000001")
# Shared known answers (identical constants in tools/host_test/test_crypto.cpp).
KAT_TC_KEY = "6a0fc847b89c96f2a51182cb604701ce276d706174bda2907b13e4d1e7bdacf1"
KAT_TM_KEY = "e2344f0a091558afade4702042d0a9c9a9209464743e95fca02c3bd2ac3d6a59"
KAT_TC_AE = ("00a7002b0000025443000000000000000000073e53f988b9490963030370d67a616f9bb7736a06f68fbed67d")
KAT_TC_AUTH = "00a700230000010000000718c0c0000000010a45be9321446143549fe23fdaf3d435473a"
# McGrew-Viega GCM test cases 13-16 (AES-256): tags
SPEC_TAGS = {"gcm_tc13": "530f8afbc74536b9a963b4f1c4cb738b", "gcm_tc14": "d0d1c8a799996bf0265b98b5d48ab919",
             "gcm_tc15": "b094dac5d93471bdec1a502270e3cc6c", "gcm_tc16": "76fc6ece0f4e1768cddf8853bb2d551b"}
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


# --- primitives -------------------------------------------------------------------

def test_flight_aes_and_gcm_spec_vectors(vectors):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    c3 = vectors["aes_c3"]
    enc = Cipher(algorithms.AES(c3["key"]), modes.ECB()).encryptor()
    assert enc.update(c3["pt"]) + enc.finalize() == c3["ct"]
    for name, tag in SPEC_TAGS.items():
        assert vectors[name]["tag"].hex() == tag, name


def test_flight_gcm_vectors_match_cryptography(vectors):
    gcm = [n for n in vectors if n.startswith("gcm_")]
    assert len(gcm) == 16
    for name in gcm:
        v = vectors[name]
        aead = AESGCM(v["key"])
        assert aead.encrypt(v["iv"], v["pt"], v["aad"]) == v["ct"] + v["tag"], name
        assert aead.decrypt(v["iv"], v["ct"] + v["tag"], v["aad"]) == v["pt"], name


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
    assert len(seq) == 6
    for name in seq:
        v = vectors[name]
        f = v["frame"]
        assert crc16_ccitt(f[:-2]) == int.from_bytes(f[-2:], "big")
        r = rx.process(f, v["hdr_len"], v["prot_end"])
        assert r.verdict == "OK" and r.protected and r.data == v["plain"], name
        assert (r.epoch, r.sn) == (v["epoch"], v["sn"])
    assert vectors["tm_ae_5"]["hdr_len"] == 8 and len(vectors["tm_ae_5"]["plain"]) == 156   # USLP
    assert len(vectors["tm_ae_0"]["plain"]) == 158                                          # TM v1
    assert rx.frames_ok == 6 and rx.last == (6, 2)
    # replays (any earlier (epoch, sn)) are dropped and do not move the state
    for name in seq:
        v = vectors[name]
        assert rx.process(v["frame"], v["hdr_len"], v["prot_end"]).verdict == "REPLAY"
    assert rx.replay == 6 and rx.last == (6, 2)


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
