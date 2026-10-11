# =============================================================================
#  VGQ-1 EGSE — CFDP class-1 file sender (CCSDS 727.0-B-5, unacknowledged)
#
#  Builds the PDUs of the EGSE files the ground asks for with CFDPPUT source 1
#  (EGSE event log, egse_data/evr_YYYYMMDD.jsonl) or 2 (latest CADU archive,
#  egse_data/cadu_YYYYMMDD.bin). The PDUs travel to the MCU one by one through
#  the Router Bridge RPC cfdp_pdu(hex) -> 1 queued / 0 busy; the MCU checks each
#  one (CfdpRelayMonitor::acceptable) and downlinks it as a TM Space Packet
#  (APID 0x030, VC 3) exactly like its own SSR transfers.
#
#  Byte-identical to the flight builders (spacecraft/vgq1_flight/sketch/src/
#  ccsds/cfdp.cpp) in the mission profile: 10-octet header (version '001',
#  unacknowledged, toward the receiver, no CRC, 32-bit sizes, 1-octet entity
#  IDs, 4-octet sequence number), source 0x01 VGQ-1, destination 0x02 DSS-Q1;
#  Metadata (0x07, modular checksum, no closure, same source/destination name);
#  File Data (offset 32 + data); EOF (0x04, condition | checksum | size, plus
#  the Entity ID fault-location TLV for a non-zero condition).
#
#  Relay limits: the transaction sequence number has bit 31 set (EGSE range; the
#  MCU passes it in the cfdp_req notification), at most 96 data octets per File
#  Data PDU and file names of at most 46 characters, so every PDU is <= 110
#  octets and its 220-character hex text fits the 256-octet RPC buffer.
#
#  Standard library only (runs inside Arduino App Lab next to main.py).
#
#  Use from the App Lab loop (never from a Bridge handler):
#      tx = cfdp_tx.start_request(DATA, "1,2147483655,64")   # from cfdp_req
#      pdu = tx.current()                 # None once the EOF was queued
#      if Bridge.call("cfdp_pdu", pdu.hex(), timeout=5) == 1: tx.advance()
#      else: wait cfdp_tx.retry_delay_s(attempt) and offer the same PDU again
#      on cfdp_cancel: tx.cancel()        # the next current() is EOF(cancel)
# =============================================================================
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

ENTITY_VGQ1 = 0x01
ENTITY_DSSQ1 = 0x02
EGSE_SEQ_FLAG = 0x80000000
DIR_EOF, DIR_METADATA = 0x04, 0x07
CHECKSUM_MODULAR = 0
TLV_ENTITY_ID = 0x06
COND_NO_ERROR, COND_FILESTORE_REJECTION, COND_CANCEL = 0, 4, 15

SEG_LEN = 96                       # data octets per File Data PDU (relay limit)
MAX_PDU_LEN = 110                  # 10 header + 4 offset + 96 data
MAX_NAME_LEN = 46                  # (110 - 18) / 2: the Metadata PDU stays <= 110 octets
MAX_KIB = 65535                    # CFDPPUT max_kib field (u16)
SOURCE_EVENT_LOG, SOURCE_CADU_ARCHIVE = 1, 2
SOURCE_PATTERNS = {SOURCE_EVENT_LOG: "evr_*.jsonl", SOURCE_CADU_ARCHIVE: "cadu_*.bin"}

_NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


def name_ok(name: str) -> bool:
    """Mission file-name rule with the relay length limit (flight cfdp_name_ok)."""
    return 1 <= len(name) <= MAX_NAME_LEN and _NAME_RE.fullmatch(name) is not None


def modular_checksum(data: bytes, offset: int = 0, initial: int = 0) -> int:
    """`initial` plus the contribution of `data` at file offset `offset`: the sum
    mod 2^32 of big-endian 4-octet words aligned on offset 0, the last word
    zero-padded. Segments may be added in any order."""
    s = initial
    for i, b in enumerate(data):
        s += b << (8 * (3 - ((offset + i) & 3)))
    return s & 0xFFFFFFFF


def _header(file_data: bool, data_len: int, seq: int) -> bytes:
    # version '001' | type | toward receiver | unacknowledged | no CRC | small file
    return (bytes([0x20 | (0x10 if file_data else 0) | 0x04]) + data_len.to_bytes(2, "big")
            + bytes([0x03, ENTITY_VGQ1]) + seq.to_bytes(4, "big") + bytes([ENTITY_DSSQ1]))


def _check_seq(seq: int) -> None:
    if not EGSE_SEQ_FLAG <= seq <= 0xFFFFFFFF:
        raise ValueError(f"EGSE transaction sequence number {seq:#x} outside 0x80000000..0xFFFFFFFF")


def build_metadata(seq: int, file_size: int, name: str) -> bytes:
    _check_seq(seq)
    if not name_ok(name) or not 0 <= file_size <= 0xFFFFFFFF:
        raise ValueError(f"bad CFDP Metadata (name {name!r}, size {file_size})")
    nm = name.encode("ascii")
    body = bytes([DIR_METADATA, CHECKSUM_MODULAR]) + file_size.to_bytes(4, "big")
    body += bytes([len(nm)]) + nm + bytes([len(nm)]) + nm
    return _header(False, len(body), seq) + body


def build_file_data(seq: int, offset: int, data: bytes) -> bytes:
    _check_seq(seq)
    if not 1 <= len(data) <= SEG_LEN or not 0 <= offset <= 0xFFFFFFFF:
        raise ValueError("bad CFDP File Data segment")
    return _header(True, 4 + len(data), seq) + offset.to_bytes(4, "big") + bytes(data)


def build_eof(seq: int, condition: int, checksum: int, file_size: int) -> bytes:
    _check_seq(seq)
    if not (0 <= condition <= 15 and 0 <= checksum <= 0xFFFFFFFF and 0 <= file_size <= 0xFFFFFFFF):
        raise ValueError("bad CFDP EOF field")
    body = bytes([DIR_EOF, condition << 4]) + checksum.to_bytes(4, "big") + file_size.to_bytes(4, "big")
    if condition != COND_NO_ERROR:
        body += bytes([TLV_ENTITY_ID, 1, ENTITY_VGQ1])      # fault location: cancelled here
    return _header(False, len(body), seq) + body


class RelayTransfer:
    """One EGSE-built transaction, offered PDU by PDU to the MCU.

    current() is the PDU to (re)send; advance() moves on once the MCU has queued
    it, so a busy MCU only delays the transfer. The file size is fixed when the
    transfer starts (the CADU archive keeps growing while it is being sent;
    octets appended later are not part of this transfer). A file source is read
    one segment at a time; if it can no longer deliver (truncated, unreadable)
    the transfer ends with EOF(condition 4, filestore rejection), like the
    flight sender. cancel() turns the next PDU into EOF(condition 15) carrying
    the octets the MCU has accepted so far and their checksum."""

    def __init__(self, seq: int, name: str, source: bytes | str | Path, max_bytes: int = 0,
                 seg_len: int = SEG_LEN):
        _check_seq(seq)
        if not name_ok(name):
            raise ValueError(f"CFDP file name {name!r}: 1..{MAX_NAME_LEN} of [A-Za-z0-9._-], "
                             "no leading dot")
        if not 1 <= seg_len <= SEG_LEN or max_bytes < 0:
            raise ValueError("segment length / max_bytes out of range")
        self.seq, self.name, self.seg_len = seq, name, seg_len
        if isinstance(source, (bytes, bytearray)):
            self._data: bytes | None = bytes(source)
            self._path: Path | None = None
            size = len(self._data)
        else:
            self._data, self._path = None, Path(source)
            size = self._path.stat().st_size
        if max_bytes:
            size = min(size, max_bytes)
        if size > 0xFFFFFFFF:
            raise ValueError("file too large for 32-bit CFDP file sizes")
        self.size = size
        self.sent = 0                      # octets the MCU accepted
        self.checksum = 0                  # over those octets
        self.condition: int | None = None  # of the EOF, once queued
        self._cond = COND_NO_ERROR
        self._stage = "METADATA"           # METADATA | FILE_DATA | EOF | DONE
        self._pdu: bytes | None = None
        self._seg = b""

    @property
    def done(self) -> bool:
        return self._stage == "DONE"

    @property
    def progress_permille(self) -> int:
        if self.done and self.condition == COND_NO_ERROR:
            return 1000
        return self.sent * 1000 // self.size if self.size else 0

    def _read(self, offset: int, n: int) -> bytes:
        if self._data is not None:
            return self._data[offset:offset + n]
        if self._path is None:
            return b""
        try:
            with self._path.open("rb") as f:
                f.seek(offset)
                return f.read(n)
        except OSError:
            return b""

    def current(self) -> bytes | None:
        """The PDU to offer to the MCU (the same one until advance()), or None
        when the transfer is over."""
        if self._pdu is not None or self._stage == "DONE":
            return self._pdu
        if self._stage == "METADATA":
            self._pdu = build_metadata(self.seq, self.size, self.name)
            return self._pdu
        if self._stage == "FILE_DATA":
            want = min(self.seg_len, self.size - self.sent)
            seg = self._read(self.sent, want)
            if len(seg) == want:
                self._seg = seg
                self._pdu = build_file_data(self.seq, self.sent, seg)
                return self._pdu
            self._cond, self._stage = COND_FILESTORE_REJECTION, "EOF"   # source stopped delivering
        size = self.size if self._cond == COND_NO_ERROR else self.sent
        self._pdu = build_eof(self.seq, self._cond, self.checksum, size)
        return self._pdu

    def advance(self) -> None:
        """The MCU queued current(): move to the next PDU."""
        if self._pdu is None:
            return
        if self._stage == "METADATA":
            self._stage = "FILE_DATA" if self.size else "EOF"
        elif self._stage == "FILE_DATA":
            self.checksum = modular_checksum(self._seg, self.sent, self.checksum)
            self.sent += len(self._seg)
            if self.sent >= self.size:
                self._stage = "EOF"
        else:
            self.condition = self._cond
            self._stage = "DONE"
        self._pdu, self._seg = None, b""

    def cancel(self) -> None:
        """CFDPCANCEL: no effect once the EOF is being offered (the file is
        complete) or after it was queued."""
        if self._stage in ("METADATA", "FILE_DATA"):
            self._cond, self._stage = COND_CANCEL, "EOF"
            self._pdu, self._seg = None, b""

    def __iter__(self) -> Iterator[bytes]:
        """Every PDU in order, as if each were queued at the first offer."""
        for _ in range(self.size + 3):                     # >= 1 octet per File Data PDU
            pdu = self.current()
            if pdu is None:
                return
            yield pdu
            self.advance()


def parse_request(text: str) -> tuple[int, int, int]:
    """The MCU's cfdp_req notification "<source>,<seq>,<max_kib>" ->
    (source 1|2, seq >= 0x80000000, max_kib 0..65535). Raises ValueError."""
    parts = text.strip().split(",")
    if len(parts) != 3:
        raise ValueError(f"cfdp_req {text!r}: expected '<source>,<seq>,<max_kib>'")
    source, seq, max_kib = (int(p, 0) for p in parts)
    if source not in SOURCE_PATTERNS:
        raise ValueError(f"cfdp_req source {source}: the EGSE serves 1 (event log) and 2 (CADU archive)")
    _check_seq(seq)
    if not 0 <= max_kib <= MAX_KIB:
        raise ValueError(f"cfdp_req max_kib {max_kib} outside 0..{MAX_KIB}")
    return source, seq, max_kib


def source_file(data_dir: str | Path, source: int) -> Path | None:
    """Newest file of the requested kind in the EGSE data directory (names carry
    the UTC date as YYYYMMDD, so the lexically largest is the latest)."""
    pattern = SOURCE_PATTERNS.get(source)
    if pattern is None:
        return None
    files = sorted(p for p in Path(data_dir).glob(pattern) if p.is_file() and name_ok(p.name))
    return files[-1] if files else None


def start_request(data_dir: str | Path, text: str) -> RelayTransfer:
    """A transfer for a cfdp_req notification. Raises ValueError for a bad
    request and FileNotFoundError when there is no such file yet: the caller
    logs it and sends nothing (the ground then sees no transaction at all)."""
    source, seq, max_kib = parse_request(text)
    path = source_file(data_dir, source)
    if path is None:
        raise FileNotFoundError(f"no {SOURCE_PATTERNS[source]} in {data_dir}")
    return RelayTransfer(seq, path.name, path, max_bytes=max_kib * 1024)


def retry_delay_s(attempt: int) -> float:
    """Back-off before offering a refused PDU again: 0.25 s doubling to 4 s."""
    return 0.25 * (1 << min(max(attempt, 0), 4))
