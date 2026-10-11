"""CFDP class 1 (CCSDS 727.0-B-5): flight sender -> ground receiver.

tools/host_test (the EXACT flight sources built with the host compiler) writes
a pseudo-random 1000-octet file and the PDUs the flight CfdpSender produced for
it: a complete transfer, a cancelled one and an EGSE-style (relay) one. The
independent Python receiver must rebuild the identical file and verify the
checksum; the Python Sender and the EGSE cfdp_tx.py must reproduce the flight
PDUs octet for octet. The receiver is then driven through every outcome
(out-of-order / duplicate data, gaps, corruption, cancel, inactivity) with a
deterministic clock. Flight-dependent tests are skipped without a C++ toolchain.
"""
import itertools
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dssq.ccsds.cfdp import (ABANDONED, CANCELLED, CHECKSUM_FAILURE, COMPLETE, COND_CANCEL,
                             COND_FILESTORE_REJECTION, EGSE_SEQ_FLAG, INCOMPLETE, RECEIVING, CfdpError,
                             Eof, FileData, Metadata, OtherDirective, PduHeader, Receiver, Sender,
                             build_eof, build_file_data, build_metadata, modular_checksum, name_ok,
                             parse_pdu, safe_file_name)
from dssq.ccsds.crc import crc16_ccitt

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"
sys.path.insert(0, str(ROOT / "spacecraft" / "vgq1_flight" / "python"))
import cfdp_tx  # noqa: E402  (EGSE module, stdlib only)

SEQ = 0x000C0003                      # what tools/host_test/test_cfdp.cpp uses
NAME = "ssr_p12_345678.bin"
RELAY_SEQ, RELAY_NAME, RELAY_LEN = 0x80000007, "evr_20261010.jsonl", 600


class Clock:
    """Deterministic receiver clock."""

    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def ref_checksum(data: bytes) -> int:
    """Straightforward definition, independent of the implementation under test:
    zero-pad to whole 4-octet words, add the big-endian words, keep 32 bits."""
    padded = data + bytes(-len(data) % 4)
    total = 0
    for i in range(0, len(padded), 4):
        total += (padded[i] << 24) | (padded[i + 1] << 16) | (padded[i + 2] << 8) | padded[i + 3]
    return total % (1 << 32)


def feed(rx: Receiver, pdus, clock: Clock | None = None, dt: float = 0.1) -> list[dict]:
    events: list[dict] = []
    for p in pdus:
        if clock is not None:
            clock.t += dt
        events += rx.receive(p)
    return events


def only(rx: Receiver) -> dict:
    txs = rx.snapshot()["transactions"]
    assert len(txs) == 1
    return txs[0]


def make_data(n: int, seed: int = 1) -> bytes:
    return random.Random(seed).randbytes(n)


@pytest.fixture(scope="module")
def vectors(tmp_path_factory):
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("cfdp_vectors")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    subprocess.run([str(HOST / "host_test"), str(out)], check=True, stdout=subprocess.DEVNULL)
    for f in ("cfdp_source.bin", "cfdp_pdus.txt", "cfdp_cancel_pdus.txt", "cfdp_relay_pdus.txt"):
        if not (out / f).exists():
            pytest.skip(f"flight CFDP vector {f} not generated")
    return out


def _pdus(path: Path) -> list[bytes]:
    return [bytes.fromhex(line) for line in path.read_text().split()]


# --- checksum --------------------------------------------------------------------------

def test_checksum_known_values():
    assert modular_checksum(b"123456789") == 0x9F686A6C == ref_checksum(b"123456789")
    assert modular_checksum(b"123456789", 1) == (0x00313233 + 0x34353637 + 0x38390000)
    assert [modular_checksum(b"\xAA", o) for o in (0, 1, 2, 3, 4097)] == \
        [0xAA000000, 0x00AA0000, 0x0000AA00, 0x000000AA, 0x00AA0000]
    assert modular_checksum(b"\xFF\xFF\xFF\xFF\x00\x00\x00\x02") == 1        # mod 2^32
    assert modular_checksum(b"", 7, 0x1234) == 0x1234
    with pytest.raises(ValueError):
        modular_checksum(b"x", -1)


def test_checksum_against_reference_any_split_any_order():
    rnd = random.Random(727)
    for n in list(range(0, 13)) + [999, 1000, 4096 + 3]:
        data = rnd.randbytes(n)
        ref = ref_checksum(data)
        assert modular_checksum(data) == ref
        assert cfdp_tx.modular_checksum(data) == ref
        for _ in range(20):
            cuts = sorted(rnd.randrange(n + 1) for _ in range(rnd.randrange(1, 6)))
            edges = [0] + cuts + [n]
            segs = [(a, data[a:b]) for a, b in itertools.pairwise(edges)]
            rnd.shuffle(segs)                                # any order, unaligned offsets
            s1 = s2 = 0
            for off, seg in segs:
                s1 = modular_checksum(seg, off, s1)
                s2 = cfdp_tx.modular_checksum(seg, off, s2)
            assert s1 == s2 == ref


# --- codec -------------------------------------------------------------------------------

def test_header_bit_layout():
    h = PduHeader(data_len=0x1234, source=1, seq=0x89ABCDEF, dest=2)
    assert h.pack().hex() == "241234030189abcdef02"
    assert PduHeader(file_data=True, data_len=0x1234, seq=0x89ABCDEF).pack()[0] == 0x34
    g = PduHeader.unpack(bytes.fromhex("3c001007" "aa" "1122334455667788" "bb"))
    assert (g.file_data, g.to_sender, g.unacknowledged, g.crc, g.large_file) == \
        (True, True, True, False, False)
    assert (g.entity_len, g.seq_len, g.source, g.seq, g.dest, g.length) == \
        (1, 8, 0xAA, 0x1122334455667788, 0xBB, 14)
    g = PduHeader.unpack(bytes.fromhex("2700009b" "1234" "00000009" "5678"))
    assert (g.crc, g.large_file, g.seg_ctrl, g.seg_metadata) == (True, True, True, True)
    assert (g.entity_len, g.seq_len, g.source, g.seq, g.dest) == (2, 4, 0x1234, 9, 0x5678)
    with pytest.raises(CfdpError):
        PduHeader.unpack(bytes.fromhex("04000003010000000102"))           # version 000
    with pytest.raises(ValueError):
        PduHeader(source=0x100).pack()


def test_builders_match_flight_octets():
    # The same octets tools/host_test/test_cfdp.cpp checks for the flight builders.
    assert build_metadata(0x000C0003, 1000, "a.bin").hex() == \
        "2400120301000c000302" "07" "00" "000003e8" "05612e62696e" "05612e62696e"
    assert build_file_data(0x000C0003, 0x100, b"\xDE\xAD\xBE").hex() == \
        "3400070301000c000302" "00000100" "deadbe"
    assert build_eof(0x000C0003, 0, 0x9F686A6C, 9).hex() == \
        "24000a0301000c000302" "04" "00" "9f686a6c" "00000009"
    assert build_eof(0x000C0003, COND_CANCEL, 0x01020304, 384).hex() == \
        "24000d0301000c000302" "04" "f0" "01020304" "00000180" "060101"
    seq = EGSE_SEQ_FLAG | 5
    assert cfdp_tx.build_metadata(seq, 77, "x.bin") == build_metadata(seq, 77, "x.bin")
    assert cfdp_tx.build_file_data(seq, 96, b"abc") == build_file_data(seq, 96, b"abc")
    assert cfdp_tx.build_eof(seq, COND_CANCEL, 5, 6) == build_eof(seq, COND_CANCEL, 5, 6)


def test_parse_round_trip_and_fields():
    md = parse_pdu(build_metadata(3, 1000, "a.bin"))
    assert isinstance(md, Metadata) and md.file_size == 1000 and md.src_name == md.dst_name == b"a.bin"
    assert md.checksum_type == 0 and not md.closure_requested and md.options == ()
    fd = parse_pdu(build_file_data(3, 0x100, b"xyz"))
    assert isinstance(fd, FileData) and fd.offset == 0x100 and fd.data == b"xyz"
    assert fd.segment_metadata is None
    eof = parse_pdu(build_eof(3, COND_CANCEL, 0xDEADBEEF, 384))
    assert isinstance(eof, Eof) and eof.condition == 15 and eof.checksum == 0xDEADBEEF
    assert eof.file_size == 384 and eof.fault_entity == 1
    assert parse_pdu(build_eof(3, 0, 1, 2)).fault_entity is None
    fin = parse_pdu(PduHeader(data_len=2).pack() + b"\x05\x00")             # Finished PDU
    assert isinstance(fin, OtherDirective) and fin.code == 5


def test_parse_general_forms():
    # PDU CRC (727.0-B-5: the data field length includes the 2-octet CRC)
    body = bytes([0x04, 0x00]) + (7).to_bytes(4, "big") + (9).to_bytes(4, "big")
    hdr = PduHeader(crc=True, data_len=len(body) + 2).pack()
    pdu = hdr + body + crc16_ccitt(hdr + body).to_bytes(2, "big")
    eof = parse_pdu(pdu)
    assert isinstance(eof, Eof) and eof.checksum == 7 and eof.file_size == 9
    with pytest.raises(CfdpError, match="CRC"):
        parse_pdu(pdu[:-1] + bytes([pdu[-1] ^ 1]))
    # large file: 64-bit offsets and sizes; segment metadata
    hdr = PduHeader(file_data=True, large_file=True, seg_metadata=True, data_len=1 + 2 + 8 + 3).pack()
    fd = parse_pdu(hdr + bytes([0x82, 0xAB, 0xCD]) + (1 << 40).to_bytes(8, "big") + b"abc")
    assert isinstance(fd, FileData) and fd.offset == 1 << 40 and fd.data == b"abc"
    assert fd.record_continuation == 2 and fd.segment_metadata == b"\xAB\xCD"
    # Metadata with an option TLV
    md_body = bytes([0x07, 0x40 | 0x0F]) + (5).to_bytes(4, "big") + b"\x01a\x01b" + b"\x02\x03hey"
    md = parse_pdu(PduHeader(data_len=len(md_body)).pack() + md_body)
    assert isinstance(md, Metadata) and md.closure_requested and md.checksum_type == 15
    assert md.src_name == b"a" and md.dst_name == b"b"
    assert md.options[0].type == 2 and md.options[0].value == b"hey"


@pytest.mark.parametrize("raw", [
    b"",
    bytes.fromhex("24"),
    build_metadata(3, 10, "a.bin")[:-1],                      # truncated
    build_metadata(3, 10, "a.bin") + b"\x00",                 # trailing octet
    bytes.fromhex("2400070301000000030207000000000a05"),      # source LV past the end
    bytes.fromhex("34000303010000000302000000"),              # File Data shorter than its offset
    bytes.fromhex("24000b0301000000030204f0000000000000000006"),  # TLV past the end
    bytes.fromhex("24000003010000000302"),                    # directive without a code
])
def test_parse_rejects_malformed(raw):
    with pytest.raises(CfdpError):
        parse_pdu(raw)


def test_names():
    assert name_ok(NAME) and name_ok("evr_20261010.jsonl") and not name_ok(".x") and not name_ok("a/b")
    assert not name_ok("x" * 65) and name_ok("x" * 64)
    assert cfdp_tx.name_ok("x" * 46) and not cfdp_tx.name_ok("x" * 47)
    assert safe_file_name(b"../../etc/passwd", 1, 2) == "passwd"
    assert safe_file_name(b"..\\win\\x y.bin", 1, 2) == "x_y.bin"
    assert safe_file_name(b"...", 1, 0x80000001) == "cfdp_01_80000001.bin"
    assert safe_file_name("café.txt".encode(), 1, 2) == "caf__.txt"
    assert safe_file_name(b"a" * 300, 1, 2) == "a" * 64
    with pytest.raises(ValueError):
        build_metadata(1, 1, "../x")


# --- flight vectors -------------------------------------------------------------------

def test_flight_transfer_complete(vectors, tmp_path):
    src = (vectors / "cfdp_source.bin").read_bytes()
    pdus = _pdus(vectors / "cfdp_pdus.txt")
    clock = Clock()
    rx = Receiver(tmp_path / "files", clock=clock)
    events = feed(rx, pdus, clock)
    out = tmp_path / "files" / NAME
    assert out.read_bytes() == src
    t = only(rx)
    assert t["state"] == COMPLETE and t["checksum_ok"] is True and t["size"] == 1000
    assert t["received"] == 1000 and t["progress"] == 100.0 and t["missing"] == []
    assert t["seq"] == SEQ and t["source"] == 1 and t["origin"] == "MCU" and t["path"] == str(out)
    assert t["t_end"] is not None and t["t_start"] < t["t_end"]
    assert [e["level"] for e in events] == ["INFO", "INFO"]
    assert all(e["source"] == "CFDP" for e in events)
    assert "COMPLETE" in events[-1]["text"] and f"{ref_checksum(src):08x}" in events[-1]["text"]
    assert sorted(p.name for p in (tmp_path / "files").iterdir()) == [NAME]
    # a replay of the same PDUs changes nothing (finished transaction)
    assert feed(rx, pdus) == [] and rx.counters["late"] == len(pdus)


def test_python_and_egse_senders_match_flight(vectors):
    src = (vectors / "cfdp_source.bin").read_bytes()
    assert list(Sender(SEQ, NAME, src)) == _pdus(vectors / "cfdp_pdus.txt")
    s = Sender(SEQ, NAME, src)
    got, n_fd = [], 0
    for p in s:
        got.append(p)
        n_fd += p[0] >> 4 & 1
        if n_fd == 3:
            s.cancel()
    assert got == _pdus(vectors / "cfdp_cancel_pdus.txt") and s.condition == COND_CANCEL
    relay = _pdus(vectors / "cfdp_relay_pdus.txt")
    assert list(cfdp_tx.RelayTransfer(RELAY_SEQ, RELAY_NAME, src, max_bytes=RELAY_LEN)) == relay
    assert list(Sender(RELAY_SEQ, RELAY_NAME, src, max_bytes=RELAY_LEN, seg_len=96)) == relay
    assert max(len(p) for p in relay) <= cfdp_tx.MAX_PDU_LEN


def test_flight_cancelled_transfer(vectors, tmp_path):
    src = (vectors / "cfdp_source.bin").read_bytes()
    rx = Receiver(tmp_path, clock=Clock())
    events = feed(rx, _pdus(vectors / "cfdp_cancel_pdus.txt"))
    t = only(rx)
    assert t["state"] == CANCELLED and t["condition"] == COND_CANCEL
    assert t["condition_name"] == "cancel request received"
    assert t["size"] == 1000 and t["received"] == 384
    assert t["missing"] == []                          # nothing was lost in transit
    assert t["checksum_ok"] is True                    # the 384 octets sent verify against the EOF
    assert (tmp_path / f"{NAME}.partial").read_bytes() == src[:384]
    info = json.loads((tmp_path / f"{NAME}.json").read_text())
    assert info["state"] == CANCELLED and info["eof_size"] == 384 and info["fault_entity"] == 1
    assert not (tmp_path / NAME).exists()
    assert events[-1]["level"] == "WARN" and "CANCELLED" in events[-1]["text"]


def test_flight_relay_vector_to_receiver(vectors, tmp_path):
    src = (vectors / "cfdp_source.bin").read_bytes()
    rx = Receiver(tmp_path, clock=Clock())
    feed(rx, _pdus(vectors / "cfdp_relay_pdus.txt"))
    t = only(rx)
    assert t["state"] == COMPLETE and t["origin"] == "EGSE" and t["seq"] == RELAY_SEQ
    assert (tmp_path / RELAY_NAME).read_bytes() == src[:RELAY_LEN]


# --- receiver outcomes ------------------------------------------------------------------

def test_egse_cfdp_tx_file_to_receiver(tmp_path):
    egse = tmp_path / "egse_data"
    egse.mkdir()
    data = make_data(5000, 3)
    (egse / "evr_20261009.jsonl").write_bytes(b"old")
    (egse / "evr_20261010.jsonl").write_bytes(data)
    tx = cfdp_tx.start_request(egse, "1,2147483655,0")
    assert tx.name == "evr_20261010.jsonl" and tx.size == 5000 and tx.seq == 0x80000007
    rx = Receiver(tmp_path / "files", clock=Clock())
    pdus = list(tx)
    assert all(len(p) <= cfdp_tx.MAX_PDU_LEN for p in pdus) and len(pdus) == 2 + -(-5000 // 96)
    feed(rx, pdus)
    t = only(rx)
    assert t["state"] == COMPLETE and t["origin"] == "EGSE" and t["checksum_ok"]
    assert (tmp_path / "files" / "evr_20261010.jsonl").read_bytes() == data
    assert tx.done and tx.progress_permille == 1000 and tx.checksum == ref_checksum(data)


def test_out_of_order_and_duplicate_file_data(tmp_path):
    data = make_data(3000, 4)
    pdus = list(Sender(0x10001, "ooo.bin", data))
    md, fds, eof = pdus[0], pdus[1:-1], pdus[-1]
    rnd = random.Random(5)
    rnd.shuffle(fds)
    stream = fds[:5] + [md] + fds[5:] + fds[:3] + [fds[7]]          # Metadata late, duplicates
    rx = Receiver(tmp_path, clock=Clock())
    events = feed(rx, stream + [eof, eof])                          # duplicate EOF too
    t = only(rx)
    assert t["state"] == COMPLETE and t["checksum_ok"] and t["duplicates"] == 4
    assert (tmp_path / "ooo.bin").read_bytes() == data
    assert rx.counters["late"] == 1 and not [e for e in events if e["level"] == "WARN"]
    assert rx.finished[(1, 0x10001)].ranges == [[0, 3000]]                 # touching segments merged


def test_missing_segment_incomplete_then_late_fill(tmp_path):
    data = make_data(1000, 6)
    pdus = list(Sender(0x20001, "gap.bin", data))                  # 128-octet segments
    lost = pdus.pop(3)                                             # File Data [256, 384)
    clock = Clock()
    rx = Receiver(tmp_path, clock=clock, check_s=60)
    events = feed(rx, pdus, clock)
    t = only(rx)
    assert t["state"] == INCOMPLETE and t["missing"] == [[256, 384]] and t["missing_octets"] == 128
    assert t["received"] == 872 and t["checksum_ok"] is None
    part = (tmp_path / "gap.bin.partial").read_bytes()
    assert len(part) == 1000 and part[:256] == data[:256] and part[256:384] == bytes(128)
    assert part[384:] == data[384:]
    info = json.loads((tmp_path / "gap.bin.json").read_text())
    assert info["state"] == INCOMPLETE and info["missing"] == [[256, 384]]
    assert info["partial"] == "gap.bin.partial"
    assert events[-1]["level"] == "WARN" and "[256, 384)" in events[-1]["text"]
    assert not (tmp_path / "gap.bin").exists()
    # the missing PDU arrives within the check timer: the file completes
    clock.t += 30
    events = rx.receive(lost)
    t = only(rx)
    assert t["state"] == COMPLETE and t["checksum_ok"] and (tmp_path / "gap.bin").read_bytes() == data
    assert not (tmp_path / "gap.bin.partial").exists() and not (tmp_path / "gap.bin.json").exists()
    assert [e["level"] for e in events] == ["INFO", "INFO"]


def test_gap_edges_one_octet_and_lost_tail(tmp_path):
    data = make_data(20, 13)
    rx = Receiver(tmp_path, clock=Clock(), check_s=0)
    feed(rx, [build_metadata(0x20003, 20, "one.bin"), build_file_data(0x20003, 0, data[:10]),
              build_file_data(0x20003, 11, data[11:]), build_eof(0x20003, 0, ref_checksum(data), 20)])
    t = only(rx)
    assert t["state"] == INCOMPLETE and t["missing"] == [[10, 11]] and t["received"] == 19
    # losing the last segment still yields a full-size .partial (zero-filled tail)
    data = make_data(1000, 14)
    pdus = list(Sender(0x20004, "tail.bin", data))
    del pdus[-2]                                                   # File Data [896, 1000)
    rx = Receiver(tmp_path, clock=Clock())
    feed(rx, pdus)
    t = only(rx)
    assert t["state"] == INCOMPLETE and t["missing"] == [[896, 1000]]
    part = (tmp_path / "tail.bin.partial").read_bytes()
    assert len(part) == 1000 and part[:896] == data[:896] and part[896:] == bytes(104)


def test_late_data_after_check_timer_is_ignored(tmp_path):
    data = make_data(700, 7)
    pdus = list(Sender(0x20002, "late.bin", data))
    lost = pdus.pop(2)
    clock = Clock()
    rx = Receiver(tmp_path, clock=clock, check_s=60)
    feed(rx, pdus, clock)
    clock.t += 61
    assert rx.poll() == []                                         # releases the kept data
    assert rx.receive(lost) == [] and rx.counters["late"] == 1
    assert only(rx)["state"] == INCOMPLETE and (tmp_path / "late.bin.partial").exists()


def test_corrupted_data_checksum_failure(tmp_path):
    data = make_data(1000, 8)
    pdus = list(Sender(0x30001, "bad.bin", data))
    fd = bytearray(pdus[4])
    fd[-1] ^= 0x40                                                 # bit error the frame CRC missed
    pdus[4] = bytes(fd)
    rx = Receiver(tmp_path, clock=Clock())
    events = feed(rx, pdus)
    t = only(rx)
    assert t["state"] == CHECKSUM_FAILURE and t["checksum_ok"] is False and t["missing"] == []
    info = json.loads((tmp_path / "bad.bin.json").read_text())
    bad = bytearray(data)
    bad[4 * 128 - 1] ^= 0x40                                       # last octet of File Data [384, 512)
    assert info["checksum_expected"] == f"{ref_checksum(data):08x}"
    assert info["checksum_computed"] == f"{ref_checksum(bytes(bad)):08x}"
    assert len((tmp_path / "bad.bin.partial").read_bytes()) == 1000
    assert not (tmp_path / "bad.bin").exists()
    assert events[-1]["level"] == "WARN" and "CHECKSUM_FAILURE" in events[-1]["text"]


def test_sender_cancel_mid_transfer(tmp_path):
    data = make_data(2000, 9)
    s = Sender(0x40001, "cxl.bin", data, seg_len=100)
    pdus = [s.next_pdu() for _ in range(6)]                        # Metadata + 5 x 100
    s.cancel()
    pdus.append(s.next_pdu())
    assert s.next_pdu() is None and s.done and s.progress_permille == 250
    rx = Receiver(tmp_path, clock=Clock())
    feed(rx, pdus[:3] + pdus[4:])                                  # one segment lost as well
    t = only(rx)
    assert t["state"] == CANCELLED and t["checksum_ok"] is None and t["missing"] == [[200, 300]]
    part = (tmp_path / "cxl.bin.partial").read_bytes()
    assert len(part) == 500 and part[:200] == data[:200] and part[300:] == data[300:500]


def test_inactivity_abandoned(tmp_path):
    data = make_data(1000, 10)
    pdus = list(Sender(0x50001, "idle.bin", data))
    clock = Clock()
    rx = Receiver(tmp_path, clock=clock, inactivity_s=600)
    feed(rx, pdus[:3], clock, dt=1.0)
    clock.t += 599.0
    assert rx.poll() == [] and only(rx)["state"] == RECEIVING
    assert only(rx)["missing"] == [] and only(rx)["progress"] == 25.6
    clock.t += 1.0
    events = rx.poll()
    t = only(rx)
    assert t["state"] == ABANDONED and len(events) == 1 and events[0]["level"] == "WARN"
    assert "inactivity" in events[0]["text"]
    assert (tmp_path / "idle.bin.partial").read_bytes() == data[:256]
    assert json.loads((tmp_path / "idle.bin.json").read_text())["state"] == ABANDONED
    # timers use the receiver clock, not the event time: an old ERT cannot trip them
    rx2 = Receiver(tmp_path / "replay", clock=clock, inactivity_s=600)
    rx2.receive(pdus[0], t=clock.t - 86400)
    assert rx2.poll() == [] and only(rx2)["t_start"] == clock.t - 86400


def test_metadata_lost_and_name_handling(tmp_path):
    data = make_data(300, 11)
    pdus = list(Sender(0x60001, "x.bin", data))
    rx = Receiver(tmp_path, clock=Clock())
    events = feed(rx, pdus[1:])                                    # Metadata lost
    t = only(rx)
    assert t["state"] == COMPLETE and t["name"] == "cfdp_01_00060001.bin"
    assert (tmp_path / "cfdp_01_00060001.bin").read_bytes() == data
    assert any("Metadata PDU never arrived" in e["text"] for e in events)
    # hostile name: confined to the files directory; an existing name is never overwritten
    body = bytes([0x07, 0x00]) + (3).to_bytes(4, "big") + b"\x0e../../evil.bin" * 2
    md = PduHeader(data_len=len(body), seq=0x60002).pack() + body
    for seq in (0x60002, 0x60003):
        raw = bytearray(md)
        raw[5:9] = seq.to_bytes(4, "big")
        feed(rx, [bytes(raw), build_file_data(seq, 0, b"abc"),
                  build_eof(seq, 0, ref_checksum(b"abc"), 3)])
    assert (tmp_path / "evil.bin").read_bytes() == b"abc"
    assert (tmp_path / "evil.01-00060003.bin").read_bytes() == b"abc"
    assert not (tmp_path.parent / "evil.bin").exists()


def test_foreign_malformed_and_class2_pdus(tmp_path):
    clock = Clock()
    rx = Receiver(tmp_path, clock=clock)
    assert rx.receive(build_file_data(1, 0, b"x", dest=3)) == []           # for another entity
    ev = rx.receive(b"\x00garbage") + rx.receive(b"\x00garbage")
    assert len(ev) == 1 and ev[0]["level"] == "WARN" and rx.counters["malformed"] == 2   # rate-limited
    clock.t += 11
    assert len(rx.receive(b"")) == 1
    acked = bytearray(build_file_data(1, 0, b"x"))
    acked[0] &= ~0x04                                                       # acknowledged mode
    ev = rx.receive(bytes(acked))
    assert rx.counters["unsupported"] == 1 and "class 2" in ev[0]["text"]
    assert rx.snapshot()["transactions"] == [] and rx.counters["foreign"] == 1


def test_resource_limits(tmp_path):
    clock = Clock()
    rx = Receiver(tmp_path, clock=clock, max_file_bytes=1000, max_open=2)
    feed(rx, [build_metadata(1, 5000, "big.bin")])
    assert only(rx)["state"] == ABANDONED and "exceeds" in only(rx)["reason"]
    # File Data beyond the announced size is rejected, not stored
    rx = Receiver(tmp_path / "b", clock=clock, max_file_bytes=1000, max_open=2)
    ev = feed(rx, [build_metadata(2, 10, "small.bin"), build_file_data(2, 7, b"abcd")])
    assert "rejected" in ev[-1]["text"] and only(rx)["received"] == 0
    # a third concurrent transaction abandons the least recently active one
    feed(rx, [build_metadata(3, 10, "c.bin")], clock, dt=1)
    feed(rx, [build_metadata(4, 10, "d.bin")], clock, dt=1)
    states = {t["seq"]: t["state"] for t in rx.snapshot()["transactions"]}
    assert states == {2: ABANDONED, 3: RECEIVING, 4: RECEIVING}
    # data with no Metadata yet is bounded by max_file_bytes, not by its offset
    feed(rx, [build_file_data(9, 0xFFFFFF00, b"x")])
    assert rx.open[(1, 9)].stored == 0 and rx.open[(1, 9)].rejected == 1


def test_empty_file_and_snapshot_keys(tmp_path):
    rx = Receiver(tmp_path, clock=Clock())
    feed(rx, list(Sender(0x70001, "empty.bin", b"")))
    t = only(rx)
    assert t["state"] == COMPLETE and t["progress"] == 100.0
    assert (tmp_path / "empty.bin").read_bytes() == b""
    contract = {"source", "seq", "name", "size", "received", "progress", "state", "checksum_ok",
                "missing", "path", "t_start", "t_end"}
    assert contract <= set(t) and set(rx.snapshot()) == {"transactions", "counters"}
    json.dumps(rx.snapshot())                                      # dashboard-serialisable


# --- EGSE sender ----------------------------------------------------------------------

def test_cfdp_tx_request_helpers(tmp_path):
    assert cfdp_tx.parse_request("2,0x80000001,64") == (2, 0x80000001, 64)
    for bad in ("0,2147483648,1", "1,5,1", "1,2147483648,70000", "1,2", "a,b,c", "3,2147483648,0"):
        with pytest.raises(ValueError):
            cfdp_tx.parse_request(bad)
    assert cfdp_tx.source_file(tmp_path, 2) is None
    for n in ("cadu_20261001.bin", "cadu_20261010.bin", "cadu_20260930.bin"):
        (tmp_path / n).write_bytes(n.encode())
    assert cfdp_tx.source_file(tmp_path, 2).name == "cadu_20261010.bin"
    with pytest.raises(FileNotFoundError):
        cfdp_tx.start_request(tmp_path, "1,2147483649,0")
    tx = cfdp_tx.start_request(tmp_path, "2,2147483649,0")
    assert tx.size == len("cadu_20261010.bin") and tx.name == "cadu_20261010.bin"
    assert [cfdp_tx.retry_delay_s(a) for a in (0, 1, 4, 9)] == [0.25, 0.5, 4.0, 4.0]
    with pytest.raises(ValueError):
        cfdp_tx.RelayTransfer(0x7FFFFFFF, "a.bin", b"x")                     # MCU sequence range
    with pytest.raises(ValueError):
        cfdp_tx.RelayTransfer(0x80000000, "n" * 47, b"x")                   # Metadata > 110 octets


def test_cfdp_tx_retry_cancel_and_failing_source(tmp_path):
    data = make_data(1000, 12)
    tx = cfdp_tx.RelayTransfer(0x80000010, "c.bin", data, max_bytes=500)
    first = tx.current()
    assert tx.current() == first                     # offered again until queued (MCU busy)
    sent = [first]
    tx.advance()
    for _ in range(2):
        sent.append(tx.current())
        tx.advance()
    pending = tx.current()                           # third segment offered, not queued yet
    tx.cancel()
    eof = tx.current()
    assert eof != pending
    tx.advance()
    assert tx.current() is None and tx.done and tx.condition == COND_CANCEL
    e = parse_pdu(eof)
    assert isinstance(e, Eof) and e.condition == COND_CANCEL and e.file_size == 192
    assert e.checksum == ref_checksum(data[:192]) and e.fault_entity == 1
    rx = Receiver(tmp_path, clock=Clock())
    feed(rx, sent + [eof])
    t = only(rx)
    assert t["state"] == CANCELLED and t["checksum_ok"] is True and t["size"] == 500
    # a file that shrinks under the sender ends the transfer with condition 4
    f = tmp_path / "evr_20261010.jsonl"
    f.write_bytes(data)
    tx = cfdp_tx.RelayTransfer(0x80000011, f.name, f)
    pdus = [tx.current()]
    tx.advance()
    f.write_bytes(data[:150])
    pdus += list(tx)
    e = parse_pdu(pdus[-1])
    assert isinstance(e, Eof) and e.condition == COND_FILESTORE_REJECTION and e.file_size == 96
    assert len(pdus) == 3 and tx.condition == COND_FILESTORE_REJECTION
