"""CFDP class 1 (CCSDS 727.0-B-5, unacknowledged file delivery), ground side.

Mirrors spacecraft/vgq1_flight/sketch/src/ccsds/cfdp.h and is cross-checked
against it bit-exactly by ground/tests/test_cfdp.py.

PDUs arrive one per TM Space Packet (APID 0x030, VC 3). Two kinds of
transaction share source entity 0x01 (VGQ-1): MCU transfers of the SSR contents
(sequence number < 0x80000000) and EGSE-built files relayed through the MCU
(>= 0x80000000).

PDU header (CCSDS bit order, widths in bits):
  version 3 '001' | PDU type 1 (0 directive, 1 file data) | direction 1 |
  transmission mode 1 (1 unacknowledged) | CRC flag 1 | large-file flag 1 |
  data field length 16 | segmentation control 1 | entity-ID length - 1 3 |
  segment-metadata flag 1 | sequence-number length - 1 3 | source entity ID |
  transaction sequence number | destination entity ID
Mission profile: 1-octet entity IDs, 4-octet sequence numbers, no PDU CRC,
32-bit file sizes (10-octet header). The parser also reads the general forms
(1/2/4/8-octet fields, PDU CRC-16, large files, segment metadata) so foreign
CFDP traffic can be inspected in interoperability tests.
Metadata (0x07): reserved 1 | closure requested 1 | reserved 2 | checksum type 4
  | file size | source name LV | destination name LV | TLV options.
File Data: [record continuation 2 | segment metadata length 6 | metadata] |
  offset | data.
EOF (0x04): condition code 4 | spare 4 | checksum 32 | file size |
  [fault location: Entity ID TLV, type 0x06].
Modular checksum: sum mod 2^32 of the big-endian 4-octet words of the file,
aligned on offset 0, the last word zero-padded.

Receiver outcome per transaction (snapshot()["transactions"][i]["state"]):
  COMPLETE          every octet 0..size-1 arrived and the checksum matches:
                    <files_dir>/<name>
  INCOMPLETE        EOF(no error) with gaps: <name>.partial (gaps zero-filled)
                    + <name>.json (missing ranges, [start, end) octets)
  CHECKSUM_FAILURE  all octets present, checksum mismatch: .partial + .json
  CANCELLED         EOF with a non-zero condition (15 = cancel request
                    received): .partial + .json
  ABANDONED         no PDU for `inactivity_s` (or the file exceeds the
                    receiver's size limit): .partial + .json
An existing file is never overwritten: a name already taken gets the
transaction id inserted ("<stem>.<source>-<seq><suffix>").
As in the standard's class-1 check-timer procedure, File Data arriving within
`check_s` after an EOF that left gaps can still complete the file
(INCOMPLETE -> COMPLETE, the .partial/.json are then removed).

The standard's text could not be retrieved in the development environment;
field layouts, directive and condition codes were cross-checked against two
independent 727.0-B-5 implementations (spacepackets 0.32.0, cfdp-py 0.7.0).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from array import array
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .crc import crc16_ccitt

VERSION = 1                        # '001': 727.0-B-4 and later
HDR_LEN = 10                       # mission profile header
ENTITY_VGQ1 = 0x01                 # spacecraft (MCU and EGSE)
ENTITY_DSSQ1 = 0x02                # this ground station
EGSE_SEQ_FLAG = 0x80000000         # EGSE-built (relayed) transactions

DIR_EOF, DIR_FINISHED, DIR_ACK, DIR_METADATA = 0x04, 0x05, 0x06, 0x07
DIR_NAK, DIR_PROMPT, DIR_KEEP_ALIVE = 0x08, 0x09, 0x0C
DIRECTIVE_NAMES = {DIR_EOF: "EOF", DIR_FINISHED: "Finished", DIR_ACK: "ACK",
                   DIR_METADATA: "Metadata", DIR_NAK: "NAK", DIR_PROMPT: "Prompt",
                   DIR_KEEP_ALIVE: "Keep Alive"}
CHECKSUM_MODULAR = 0
TLV_ENTITY_ID = 0x06

COND_NO_ERROR = 0
COND_FILESTORE_REJECTION = 4
COND_FILE_SIZE_ERROR = 6
COND_INACTIVITY = 8
COND_CANCEL = 15
CONDITION_NAMES = {
    0: "no error", 1: "positive ACK limit reached", 2: "keep alive limit reached",
    3: "invalid transmission mode", 4: "filestore rejection", 5: "file checksum failure",
    6: "file size error", 7: "NAK limit reached", 8: "inactivity detected",
    9: "invalid file structure", 10: "check limit reached", 11: "unsupported checksum type",
    14: "suspend request received", 15: "cancel request received"}

FLIGHT_SEG_LEN = 128               # data octets per File Data PDU, MCU (SSR) transfers
RELAY_SEG_LEN = 96                 # EGSE transfers (Router Bridge RPC buffer)
MAX_NAME_LEN = 64                  # mission file-name limit

RECEIVING = "RECEIVING"
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
CHECKSUM_FAILURE = "CHECKSUM_FAILURE"
CANCELLED = "CANCELLED"
ABANDONED = "ABANDONED"
FINAL_STATES = (COMPLETE, INCOMPLETE, CHECKSUM_FAILURE, CANCELLED, ABANDONED)

EVENT_SOURCE = "CFDP"
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
SNAPSHOT_MISSING_RANGES = 16       # ranges listed per transaction in snapshot()

Event = dict[str, Any]             # {"t": float, "level": str, "source": "CFDP", "text": str}


class CfdpError(ValueError):
    """A PDU that is malformed or outside what this codec accepts."""


def condition_name(code: int) -> str:
    return CONDITION_NAMES.get(code, f"reserved condition {code}")


# --- modular checksum -------------------------------------------------------------

_WORDS_OK = array("I").itemsize == 4


def modular_checksum(data: bytes, offset: int = 0, initial: int = 0) -> int:
    """`initial` plus the modular-checksum contribution of `data` lying at file
    offset `offset` (any alignment; segments may be added in any order)."""
    if offset < 0:
        raise ValueError("negative file offset")
    s = initial
    mv = memoryview(data)
    lead = min((-offset) % 4, len(mv))
    for i in range(lead):                                  # up to the next word boundary
        s += mv[i] << (8 * (3 - ((offset + i) & 3)))
    body = mv[lead:]
    whole = len(body) - len(body) % 4
    if whole:
        if _WORDS_OK:
            words = array("I")
            words.frombytes(body[:whole])
            if sys.byteorder == "little":
                words.byteswap()
            s += sum(words)
        else:                                              # pragma: no cover - exotic platform
            s += sum(int.from_bytes(body[i:i + 4], "big") for i in range(0, whole, 4))
    for i, b in enumerate(body[whole:]):                   # zero-padded last word
        s += b << (8 * (3 - i))
    return s & 0xFFFFFFFF


# --- file names ---------------------------------------------------------------------

_NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
_NAME_BAD = re.compile(r"[^A-Za-z0-9._-]")


def name_ok(name: str, max_len: int = MAX_NAME_LEN) -> bool:
    """Mission file-name rule (same as the flight's cfdp_name_ok)."""
    return 1 <= len(name) <= min(max_len, MAX_NAME_LEN) and _NAME_RE.fullmatch(name) is not None


def safe_file_name(raw: bytes, source: int, seq: int) -> str:
    """A file name from a PDU that is safe to create inside the files directory:
    base name only, [A-Za-z0-9._-], no leading dot, <= 64 characters; a
    fallback derived from the transaction id when nothing usable is left."""
    text = raw.decode("ascii", errors="replace").replace("\\", "/").rsplit("/", 1)[-1]
    text = _NAME_BAD.sub("_", text).lstrip(".")[:MAX_NAME_LEN]
    return text or f"cfdp_{source:02x}_{seq:08x}.bin"


# --- PDU codec ------------------------------------------------------------------------

@dataclass(frozen=True)
class PduHeader:
    file_data: bool = False
    to_sender: bool = False
    unacknowledged: bool = True
    crc: bool = False
    large_file: bool = False
    data_len: int = 0              # PDU data field octets (incl. the CRC when present)
    seg_ctrl: bool = False
    seg_metadata: bool = False
    source: int = ENTITY_VGQ1
    seq: int = 0
    dest: int = ENTITY_DSSQ1
    entity_len: int = 1            # octets
    seq_len: int = 4               # octets

    @property
    def length(self) -> int:
        return 4 + 2 * self.entity_len + self.seq_len

    @property
    def fss_len(self) -> int:
        return 8 if self.large_file else 4

    def pack(self) -> bytes:
        if not (1 <= self.entity_len <= 8 and 1 <= self.seq_len <= 8 and 0 <= self.data_len <= 0xFFFF):
            raise ValueError("CFDP header field length out of range")
        for value, n in ((self.source, self.entity_len), (self.seq, self.seq_len),
                         (self.dest, self.entity_len)):
            if not 0 <= value < 1 << (8 * n):
                raise ValueError("CFDP entity ID / sequence number does not fit its length")
        b = bytearray([VERSION << 5 | int(self.file_data) << 4 | int(self.to_sender) << 3
                       | int(self.unacknowledged) << 2 | int(self.crc) << 1 | int(self.large_file)])
        b += self.data_len.to_bytes(2, "big")
        b.append(int(self.seg_ctrl) << 7 | (self.entity_len - 1) << 4
                 | int(self.seg_metadata) << 3 | (self.seq_len - 1))
        b += self.source.to_bytes(self.entity_len, "big")
        b += self.seq.to_bytes(self.seq_len, "big")
        b += self.dest.to_bytes(self.entity_len, "big")
        return bytes(b)

    @classmethod
    def unpack(cls, raw: bytes) -> PduHeader:
        if len(raw) < 4:
            raise CfdpError("CFDP PDU shorter than its fixed header")
        if raw[0] >> 5 != VERSION:
            raise CfdpError(f"CFDP version {raw[0] >> 5} (expected 1)")
        entity_len = ((raw[3] >> 4) & 7) + 1
        seq_len = (raw[3] & 7) + 1
        n = 4 + 2 * entity_len + seq_len
        if len(raw) < n:
            raise CfdpError("CFDP PDU shorter than its header")
        i = 4
        source = int.from_bytes(raw[i:i + entity_len], "big")
        i += entity_len
        seq = int.from_bytes(raw[i:i + seq_len], "big")
        i += seq_len
        dest = int.from_bytes(raw[i:i + entity_len], "big")
        return cls(file_data=bool(raw[0] & 0x10), to_sender=bool(raw[0] & 0x08),
                   unacknowledged=bool(raw[0] & 0x04), crc=bool(raw[0] & 0x02),
                   large_file=bool(raw[0] & 0x01), data_len=int.from_bytes(raw[1:3], "big"),
                   seg_ctrl=bool(raw[3] & 0x80), seg_metadata=bool(raw[3] & 0x08),
                   source=source, seq=seq, dest=dest, entity_len=entity_len, seq_len=seq_len)


@dataclass(frozen=True)
class Tlv:
    type: int
    value: bytes


@dataclass(frozen=True)
class Metadata:
    header: PduHeader
    closure_requested: bool
    checksum_type: int
    file_size: int
    src_name: bytes
    dst_name: bytes
    options: tuple[Tlv, ...] = ()


@dataclass(frozen=True)
class FileData:
    header: PduHeader
    offset: int
    data: bytes
    record_continuation: int = 0
    segment_metadata: bytes | None = None


@dataclass(frozen=True)
class Eof:
    header: PduHeader
    condition: int
    checksum: int
    file_size: int
    fault_entity: int | None = None


@dataclass(frozen=True)
class OtherDirective:
    """Finished / ACK / NAK / Prompt / Keep Alive: class-2 traffic, not used here."""
    header: PduHeader
    code: int
    params: bytes


Pdu = Metadata | FileData | Eof | OtherDirective


def _tlvs(b: bytes) -> tuple[Tlv, ...]:
    out: list[Tlv] = []
    i = 0
    while i < len(b):                                      # each step consumes >= 2 octets
        if i + 2 > len(b) or i + 2 + b[i + 1] > len(b):
            raise CfdpError("CFDP TLV runs past the data field")
        out.append(Tlv(b[i], bytes(b[i + 2:i + 2 + b[i + 1]])))
        i += 2 + b[i + 1]
    return tuple(out)


def parse_pdu(raw: bytes) -> Pdu:
    """Decodes exactly one PDU occupying all of `raw` (no trailing octets).
    Raises CfdpError for anything malformed, including a failed PDU CRC."""
    raw = bytes(raw)
    h = PduHeader.unpack(raw)
    if h.length + h.data_len != len(raw):
        raise CfdpError(f"CFDP length mismatch: header + data field = {h.length + h.data_len}, "
                        f"PDU = {len(raw)} octets")
    body = raw[h.length:]
    if h.crc:
        if len(body) < 2 or crc16_ccitt(raw) != 0:
            raise CfdpError("CFDP PDU CRC failure")
        body = body[:-2]
    fss = h.fss_len
    if h.file_data:
        i, rcs, seg_md = 0, 0, None
        if h.seg_metadata:
            if not body:
                raise CfdpError("CFDP File Data PDU without its segment metadata octet")
            rcs, md_len = body[0] >> 6, body[0] & 0x3F
            if 1 + md_len > len(body):
                raise CfdpError("CFDP segment metadata runs past the data field")
            seg_md = bytes(body[1:1 + md_len])
            i = 1 + md_len
        if i + fss > len(body):
            raise CfdpError("CFDP File Data PDU shorter than its offset")
        return FileData(h, int.from_bytes(body[i:i + fss], "big"), bytes(body[i + fss:]), rcs, seg_md)
    if not body:
        raise CfdpError("CFDP directive PDU without a directive code")
    code = body[0]
    if code == DIR_METADATA:
        if len(body) < 2 + fss + 1:
            raise CfdpError("CFDP Metadata PDU truncated")
        flags = body[1]
        size = int.from_bytes(body[2:2 + fss], "big")
        i = 2 + fss
        src_len = body[i]
        if i + 1 + src_len + 1 > len(body):
            raise CfdpError("CFDP Metadata source file name runs past the data field")
        src = bytes(body[i + 1:i + 1 + src_len])
        i += 1 + src_len
        dst_len = body[i]
        if i + 1 + dst_len > len(body):
            raise CfdpError("CFDP Metadata destination file name runs past the data field")
        dst = bytes(body[i + 1:i + 1 + dst_len])
        i += 1 + dst_len
        return Metadata(h, bool(flags & 0x40), flags & 0x0F, size, src, dst, _tlvs(body[i:]))
    if code == DIR_EOF:
        if len(body) < 2 + 4 + fss:
            raise CfdpError("CFDP EOF PDU truncated")
        i = 6 + fss
        fault = None
        for tlv in _tlvs(body[i:]):
            if tlv.type == TLV_ENTITY_ID and 1 <= len(tlv.value) <= 8:
                fault = int.from_bytes(tlv.value, "big")
        return Eof(h, body[1] >> 4, int.from_bytes(body[2:6], "big"),
                   int.from_bytes(body[6:6 + fss], "big"), fault)
    return OtherDirective(h, code, bytes(body[1:]))


def _header(file_data: bool, data_len: int, seq: int, source: int, dest: int) -> bytes:
    return PduHeader(file_data=file_data, data_len=data_len, source=source, seq=seq, dest=dest).pack()


def build_metadata(seq: int, file_size: int, name: str, *, source: int = ENTITY_VGQ1,
                   dest: int = ENTITY_DSSQ1) -> bytes:
    """Metadata PDU in the mission profile: modular checksum, no closure, the same
    name as source and destination file name, no options."""
    if not name_ok(name):
        raise ValueError(f"CFDP file name {name!r} violates the mission rule")
    if not 0 <= file_size <= 0xFFFFFFFF:
        raise ValueError("CFDP file size out of range")
    nm = name.encode("ascii")
    body = bytes([DIR_METADATA, CHECKSUM_MODULAR]) + file_size.to_bytes(4, "big")
    body += bytes([len(nm)]) + nm + bytes([len(nm)]) + nm
    return _header(False, len(body), seq, source, dest) + body


def build_file_data(seq: int, offset: int, data: bytes, *, source: int = ENTITY_VGQ1,
                    dest: int = ENTITY_DSSQ1) -> bytes:
    if not 0 <= offset <= 0xFFFFFFFF or len(data) > 0xFFFF - 4:
        raise ValueError("CFDP File Data offset / length out of range")
    return _header(True, 4 + len(data), seq, source, dest) + offset.to_bytes(4, "big") + bytes(data)


def build_eof(seq: int, condition: int, checksum: int, file_size: int, *, source: int = ENTITY_VGQ1,
              dest: int = ENTITY_DSSQ1) -> bytes:
    """EOF PDU; a non-zero condition appends the fault location (Entity ID TLV
    naming `source`, where the cancellation was initiated), as the flight does."""
    if not (0 <= condition <= 15 and 0 <= checksum <= 0xFFFFFFFF and 0 <= file_size <= 0xFFFFFFFF):
        raise ValueError("CFDP EOF field out of range")
    body = bytes([DIR_EOF, condition << 4]) + checksum.to_bytes(4, "big") + file_size.to_bytes(4, "big")
    if condition != COND_NO_ERROR:
        body += bytes([TLV_ENTITY_ID, 1, source])
    return _header(False, len(body), seq, source, dest) + body


# --- sender (simulator, tests) -------------------------------------------------------

class Sender:
    """Class-1 sending entity: the Python twin of the flight CfdpSender
    (Metadata, File Data..., EOF; cancel() -> EOF(condition 15) carrying the
    octets sent so far and their checksum). Produces byte-identical PDUs."""

    def __init__(self, seq: int, name: str, data: bytes, *, max_bytes: int = 0,
                 seg_len: int = FLIGHT_SEG_LEN, source: int = ENTITY_VGQ1, dest: int = ENTITY_DSSQ1):
        if not 0 <= seq <= 0xFFFFFFFF:
            raise ValueError("CFDP sequence number out of range")
        if not name_ok(name):
            raise ValueError(f"CFDP file name {name!r} violates the mission rule")
        if not 1 <= seg_len <= 0xFFFF - 4 or max_bytes < 0:
            raise ValueError("CFDP segment length / max_bytes out of range")
        if not (0 <= source <= 0xFF and 0 <= dest <= 0xFF):
            raise ValueError("CFDP entity ID out of range")
        self.seq, self.name, self.seg_len = seq, name, seg_len
        self.source, self.dest = source, dest
        self._data = bytes(data[:max_bytes] if max_bytes else data)
        if len(self._data) > 0xFFFFFFFF:
            raise ValueError("CFDP file too large for 32-bit file sizes")
        self.size = len(self._data)
        self.sent = 0
        self.checksum = 0
        self.condition: int | None = None       # of the EOF, once sent
        self.state = "METADATA"
        self._cond = COND_NO_ERROR

    @property
    def done(self) -> bool:
        return self.state == "IDLE"

    @property
    def progress_permille(self) -> int:
        if self.done and self.condition == COND_NO_ERROR:
            return 1000
        return self.sent * 1000 // self.size if self.size else 0

    def cancel(self) -> None:
        if self.state in ("METADATA", "FILE_DATA"):
            self._cond = COND_CANCEL
            self.state = "EOF_PENDING"

    def next_pdu(self) -> bytes | None:
        if self.state == "METADATA":
            self.state = "FILE_DATA" if self.size else "EOF_PENDING"
            return build_metadata(self.seq, self.size, self.name, source=self.source, dest=self.dest)
        if self.state == "FILE_DATA":
            seg = self._data[self.sent:self.sent + self.seg_len]
            pdu = build_file_data(self.seq, self.sent, seg, source=self.source, dest=self.dest)
            self.checksum = modular_checksum(seg, self.sent, self.checksum)
            self.sent += len(seg)
            if self.sent >= self.size:
                self.state = "EOF_PENDING"
            return pdu
        if self.state == "EOF_PENDING":
            size = self.size if self._cond == COND_NO_ERROR else self.sent
            self.condition = self._cond
            self.state = "IDLE"
            return build_eof(self.seq, self._cond, self.checksum, size, source=self.source,
                             dest=self.dest)
        return None

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(self.size + 3):                     # bounded: >= 1 octet per File Data PDU
            pdu = self.next_pdu()
            if pdu is None:
                return
            yield pdu


# --- receiver ----------------------------------------------------------------------------

def _add_range(ranges: list[list[int]], start: int, end: int) -> None:
    """Merges [start, end) into the sorted, disjoint, non-touching range list."""
    out: list[list[int]] = []
    placed = False
    for a, b in ranges:
        if b < start:
            out.append([a, b])
        elif a > end:
            if not placed:
                out.append([start, end])
                placed = True
            out.append([a, b])
        else:                                              # overlapping or touching: merge
            start, end = min(a, start), max(b, end)
    if not placed:
        out.append([start, end])
    out.sort()
    ranges[:] = out


def _covered(ranges: list[list[int]], start: int, end: int) -> bool:
    return any(a <= start and end <= b for a, b in ranges)


def _gaps(ranges: list[list[int]], size: int) -> list[list[int]]:
    out: list[list[int]] = []
    pos = 0
    for a, b in ranges:
        if a >= size:
            break
        if a > pos:
            out.append([pos, a])
        pos = max(pos, b)
    if pos < size:
        out.append([pos, size])
    return out


class _Log:
    """Event collector for one receive() / poll() call; `t` stamps the events
    (the frame's earth-received time when the GDS supplies it)."""

    def __init__(self, t: float):
        self.t = t
        self.items: list[Event] = []

    def add(self, level: str, text: str) -> None:
        self.items.append({"t": self.t, "level": level, "source": EVENT_SOURCE, "text": text})


@dataclass
class Transaction:
    """Receiving-side state of one (source entity, sequence number) transaction.
    `seen` is the receiver clock at the last PDU (timers); t_start / t_end are
    event times (ERT when supplied) for display."""
    source: int
    seq: int
    t_start: float
    seen: float
    name: str | None = None
    raw_name: bytes | None = None
    md_size: int | None = None          # file size announced by the Metadata
    checksum_type: int | None = None
    eof_size: int | None = None
    eof_checksum: int | None = None
    condition: int | None = None
    fault_entity: int | None = None
    state: str = RECEIVING
    reason: str = ""
    checksum_ok: bool | None = None
    checksum_computed: int | None = None
    path: str | None = None
    partial_path: str | None = None
    info_path: str | None = None
    t_end: float | None = None
    check_until: float | None = None    # INCOMPLETE: late File Data accepted until then
    pdus: int = 0
    file_data_pdus: int = 0
    duplicates: int = 0
    conflicts: int = 0
    rejected: int = 0
    stored: int = 0                     # octets held in `segments`
    ranges: list[list[int]] = field(default_factory=list)
    segments: dict[int, bytes] = field(default_factory=dict, repr=False)

    @property
    def tid(self) -> str:
        return f"{self.source:02x}/{self.seq:08x}"

    @property
    def origin(self) -> str:
        return "EGSE" if self.seq & EGSE_SEQ_FLAG else "MCU"

    @property
    def size(self) -> int | None:
        """Nominal file size: the EOF(no error) value, else the Metadata value."""
        if self.eof_size is not None and self.condition == COND_NO_ERROR:
            return self.eof_size
        return self.md_size if self.md_size is not None else self.eof_size

    @property
    def high(self) -> int:
        return self.ranges[-1][1] if self.ranges else 0

    @property
    def extent(self) -> int:
        """Octets the sender is known to have sent (EOF) or, before the EOF / when
        abandoned, the highest octet received: the span gaps are reported in."""
        if self.state in (RECEIVING, ABANDONED) or self.eof_size is None:
            return self.high
        return self.eof_size

    def received(self, limit: int | None = None) -> int:
        if limit is None:
            return sum(b - a for a, b in self.ranges)
        return sum(min(b, limit) - a for a, b in self.ranges if a < limit)

    def missing(self) -> list[list[int]]:
        return _gaps(self.ranges, self.extent)

    def image(self, length: int) -> bytes:
        """The file octets 0..length-1, gaps zero-filled."""
        buf = bytearray(length)
        for off in sorted(self.segments):
            if off < length:
                seg = self.segments[off][:length - off]
                buf[off:off + len(seg)] = seg
        return bytes(buf)

    def summary(self) -> dict[str, Any]:
        size = self.size
        received = self.received(size)
        missing = self.missing()
        if size:
            progress = round(100.0 * received / size, 1)
        else:
            progress = 100.0 if size == 0 and self.state == COMPLETE else 0.0
        return {
            "source": self.source, "seq": self.seq, "origin": self.origin, "name": self.name,
            "size": size, "received": received, "progress": progress,
            "state": self.state, "reason": self.reason, "checksum_ok": self.checksum_ok,
            "condition": self.condition,
            "condition_name": None if self.condition is None else condition_name(self.condition),
            "missing": missing[:SNAPSHOT_MISSING_RANGES], "missing_ranges": len(missing),
            "missing_octets": sum(b - a for a, b in missing),
            "path": self.path or self.partial_path or self.info_path, "pdus": self.pdus,
            "duplicates": self.duplicates, "t_start": self.t_start, "t_end": self.t_end,
        }


class Receiver:
    """Class-1 receiving entity (DSS-Q1).

    receive(pdu, t) takes the user data of every APID 0x030 packet (`t` = its
    ERT, used to stamp events; defaults to the clock); poll() runs the
    inactivity and check timers and should be called about once a second.
    Both return event dicts {"t", "level" ("INFO" | "WARN"), "source": "CFDP",
    "text"} ready for the GDS event log. snapshot() is the dashboard view.

    Timers always run on `clock` (injectable for tests), never on the supplied
    ERT, so replaying an old archive cannot trip the inactivity limit.
    Memory is bounded: only received octets are kept (never a buffer sized from
    an offset), each transaction is limited to `max_file_bytes`, at most
    `max_open` transactions are open (the least recently active one is
    abandoned to admit a new one) and `history` finished ones are remembered.
    """

    def __init__(self, files_dir: str | Path, *, inactivity_s: float = 600.0, check_s: float = 60.0,
                 local_entity: int = ENTITY_DSSQ1, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
                 max_open: int = 16, history: int = 50, clock: Callable[[], float] = time.time):
        if inactivity_s <= 0 or check_s < 0 or max_file_bytes <= 0 or max_open < 1 or history < 1:
            raise ValueError("CFDP receiver parameter out of range")
        self.files_dir = Path(files_dir)
        self.inactivity_s = float(inactivity_s)
        self.check_s = float(check_s)
        self.local_entity = local_entity
        self.max_file_bytes = max_file_bytes
        self.max_open = max_open
        self.history = history
        self.clock = clock
        self.open: dict[tuple[int, int], Transaction] = {}
        self.finished: dict[tuple[int, int], Transaction] = {}     # insertion order = finish order
        self.counters = {"pdus": 0, "malformed": 0, "foreign": 0, "unsupported": 0, "late": 0}
        self._last_warn: dict[str, float] = {}

    # -- public API ------------------------------------------------------------------

    def receive(self, raw: bytes, t: float | None = None) -> list[Event]:
        """Processes one PDU. Never raises on bad input: malformed, foreign and
        class-2 PDUs are counted (and reported, rate-limited) and dropped."""
        now = self.clock()
        log = _Log(now if t is None else t)
        self.counters["pdus"] += 1
        try:
            pdu = parse_pdu(raw)
        except CfdpError as e:
            self.counters["malformed"] += 1
            self._warn_limited(log, now, "malformed", f"malformed PDU dropped ({e}); "
                               f"{self.counters['malformed']} so far")
            return log.items
        h = pdu.header
        if h.dest != self.local_entity or h.to_sender:
            self.counters["foreign"] += 1                  # not addressed to this receiver
            return log.items
        if not h.unacknowledged or isinstance(pdu, OtherDirective):
            self.counters["unsupported"] += 1
            if isinstance(pdu, OtherDirective):
                what = f"{DIRECTIVE_NAMES.get(pdu.code, f'0x{pdu.code:02x}')} directive"
            else:
                what = "acknowledged-mode (class 2) PDU"
            self._warn_limited(log, now, "unsupported", f"{what} ignored: this receiver is class 1 only")
            return log.items
        key = (h.source, h.seq)
        tx = self.open.get(key)
        if tx is None:
            done = self.finished.get(key)
            if done is not None:
                in_check = done.check_until is not None and now <= done.check_until
                if isinstance(pdu, FileData) and in_check:
                    done.pdus += 1
                    self._file_data(done, pdu, log)
                    self._late_completion(done, now, log)
                else:
                    self.counters["late"] += 1             # retry / replay of a finished transfer
                return log.items
            tx = self._new(key, now, log)
        tx.pdus += 1
        tx.seen = now
        if isinstance(pdu, Metadata):
            self._metadata(tx, pdu, now, log)
        elif isinstance(pdu, FileData):
            self._file_data(tx, pdu, log)
        else:
            self._eof(tx, pdu, now, log)
        return log.items

    def poll(self, now: float | None = None) -> list[Event]:
        """Inactivity (-> ABANDONED) and check-timer expiry (releases the data an
        INCOMPLETE transaction kept for late File Data)."""
        now = self.clock() if now is None else now
        log = _Log(now)
        for tx in list(self.open.values()):
            if now - tx.seen >= self.inactivity_s:
                self._finish(tx, ABANDONED, now, log,
                             f"no PDU for {now - tx.seen:.0f} s ({condition_name(COND_INACTIVITY)}, "
                             f"limit {self.inactivity_s:.0f} s)")
        for tx in self.finished.values():
            if tx.check_until is not None and now > tx.check_until:
                tx.check_until = None
                tx.segments.clear()
                tx.stored = 0
        return log.items

    def snapshot(self) -> dict[str, Any]:
        """Dashboard view: open transactions first, then finished ones, newest first."""
        txs = list(self.open.values()) + list(reversed(list(self.finished.values())))
        return {"transactions": [tx.summary() for tx in txs], "counters": dict(self.counters)}

    # -- per PDU type ----------------------------------------------------------------

    def _new(self, key: tuple[int, int], now: float, log: _Log) -> Transaction:
        if len(self.open) >= self.max_open:
            oldest = min(self.open.values(), key=lambda x: x.seen)
            self._finish(oldest, ABANDONED, now, log, f"more than {self.max_open} transactions open")
        tx = Transaction(source=key[0], seq=key[1], t_start=log.t, seen=now)
        self.open[key] = tx
        return tx

    def _metadata(self, tx: Transaction, md: Metadata, now: float, log: _Log) -> None:
        if tx.raw_name is not None:
            if (md.src_name, md.file_size) != (tx.raw_name, tx.md_size):
                log.add("WARN", f"{tx.tid} second Metadata PDU differs from the first: ignored")
            else:
                tx.duplicates += 1
            return
        tx.raw_name, tx.md_size, tx.checksum_type = md.src_name, md.file_size, md.checksum_type
        wire_name = md.dst_name or md.src_name
        tx.name = safe_file_name(wire_name, tx.source, tx.seq)
        if tx.name.encode() != wire_name:
            log.add("WARN", f"{tx.tid} file name {wire_name!r} sanitised to {tx.name!r}")
        log.add("INFO", f"{tx.tid} receiving '{tx.name}' ({md.file_size} octets from the {tx.origin}, "
                f"checksum type {md.checksum_type})")
        if md.file_size > self.max_file_bytes:
            self._finish(tx, ABANDONED, now, log, f"file size {md.file_size} exceeds the receiver "
                         f"limit of {self.max_file_bytes} octets")
        elif tx.high > md.file_size:
            log.add("WARN", f"{tx.tid} {tx.high - md.file_size} octets received beyond the Metadata "
                    f"file size ({condition_name(COND_FILE_SIZE_ERROR)})")

    def _file_data(self, tx: Transaction, fd: FileData, log: _Log) -> None:
        tx.file_data_pdus += 1
        start, end = fd.offset, fd.offset + len(fd.data)
        limit = tx.size if tx.size is not None else self.max_file_bytes
        if end > limit or tx.stored + len(fd.data) > self.max_file_bytes:
            tx.rejected += 1
            if tx.rejected == 1:
                log.add("WARN", f"{tx.tid} File Data [{start}, {end}) beyond the file size {limit} "
                        f"rejected ({condition_name(COND_FILE_SIZE_ERROR)})")
            return
        if not fd.data:
            return
        if _covered(tx.ranges, start, end):
            tx.duplicates += 1
            old = tx.segments.get(start)
            n = min(len(old), len(fd.data)) if old is not None else 0
            if old is not None and old[:n] != fd.data[:n]:
                tx.conflicts += 1
                if tx.conflicts == 1:
                    log.add("WARN", f"{tx.tid} duplicate File Data at offset {start} with different "
                            "content: the first copy is kept")
            return
        prev = tx.segments.get(start)
        if prev is None or len(fd.data) > len(prev):
            tx.stored += len(fd.data) - (len(prev) if prev is not None else 0)
            tx.segments[start] = bytes(fd.data)
        _add_range(tx.ranges, start, end)

    def _eof(self, tx: Transaction, eof: Eof, now: float, log: _Log) -> None:
        tx.eof_size, tx.eof_checksum = eof.file_size, eof.checksum
        tx.condition, tx.fault_entity = eof.condition, eof.fault_entity
        if eof.condition != COND_NO_ERROR:
            # Cancelled by the sender: the EOF carries the octets sent so far and
            # their checksum, so a completely received prefix is still verified.
            if not _gaps(tx.ranges, eof.file_size):
                tx.checksum_computed = self._checksum(tx, eof.file_size)
                tx.checksum_ok = tx.checksum_computed == eof.checksum
            where = "" if eof.fault_entity is None else f" at entity 0x{eof.fault_entity:02x}"
            self._finish(tx, CANCELLED, now, log, f"condition {eof.condition} "
                         f"({condition_name(eof.condition)}){where} after {eof.file_size} octets sent")
            return
        if eof.file_size > self.max_file_bytes:
            self._finish(tx, ABANDONED, now, log, f"file size {eof.file_size} exceeds the receiver "
                         f"limit of {self.max_file_bytes} octets")
            return
        if tx.md_size is not None and tx.md_size != eof.file_size:
            log.add("WARN", f"{tx.tid} EOF file size {eof.file_size} differs from the Metadata file "
                    f"size {tx.md_size}: the EOF value is used")
        if tx.high > eof.file_size:
            log.add("WARN", f"{tx.tid} {tx.high - eof.file_size} octets beyond the EOF file size "
                    f"ignored ({condition_name(COND_FILE_SIZE_ERROR)})")
        if tx.name is None:
            tx.name = f"cfdp_{tx.source:02x}_{tx.seq:08x}.bin"
            log.add("WARN", f"{tx.tid} the Metadata PDU never arrived: saved as '{tx.name}'")
        self._evaluate(tx, now, log)

    def _evaluate(self, tx: Transaction, now: float, log: _Log) -> None:
        """COMPLETE / INCOMPLETE / CHECKSUM_FAILURE once an EOF(no error) is in."""
        size = tx.eof_size or 0
        missing = _gaps(tx.ranges, size)
        if missing:
            shown = ", ".join(f"[{a}, {b})" for a, b in missing[:4])
            shown += " ..." if len(missing) > 4 else ""
            tx.check_until = now + self.check_s if self.check_s > 0 else None
            self._finish(tx, INCOMPLETE, now, log, f"{sum(b - a for a, b in missing)} of {size} octets "
                         f"missing in {len(missing)} range(s): {shown}")
            return
        tx.checksum_computed = self._checksum(tx, size)
        if tx.checksum_type is not None and tx.checksum_type != CHECKSUM_MODULAR:
            tx.checksum_ok = None
            self._finish(tx, CHECKSUM_FAILURE, now, log,
                         f"checksum type {tx.checksum_type} not supported (modular only)")
            return
        tx.checksum_ok = tx.checksum_computed == tx.eof_checksum
        if tx.checksum_ok:
            self._finish(tx, COMPLETE, now, log, f"{size} octets, modular checksum "
                         f"{tx.checksum_computed:08x} verified")
        else:
            self._finish(tx, CHECKSUM_FAILURE, now, log, f"checksum {tx.checksum_computed:08x} "
                         f"computed, {tx.eof_checksum:08x} expected")

    def _late_completion(self, tx: Transaction, now: float, log: _Log) -> None:
        """An INCOMPLETE transaction within its check timer: evaluated again as
        soon as late File Data has filled every gap."""
        if tx.state != INCOMPLETE or _gaps(tx.ranges, tx.eof_size or 0):
            return
        self._remove(tx.partial_path, tx.info_path)
        tx.partial_path = tx.info_path = None
        self.finished.pop((tx.source, tx.seq), None)
        self.open[(tx.source, tx.seq)] = tx
        tx.state, tx.check_until = RECEIVING, None
        log.add("INFO", f"{tx.tid} late File Data filled every gap")
        self._evaluate(tx, now, log)

    # -- helpers ----------------------------------------------------------------------

    @staticmethod
    def _checksum(tx: Transaction, size: int) -> int:
        s = 0
        for off in sorted(tx.segments):
            if off < size:
                s = modular_checksum(tx.segments[off][:size - off], off, s)
        return s

    def _finish(self, tx: Transaction, state: str, now: float, log: _Log, reason: str) -> None:
        tx.state, tx.reason, tx.t_end = state, reason, log.t
        if state != INCOMPLETE:
            tx.check_until = None
        name = tx.name or f"cfdp_{tx.source:02x}_{tx.seq:08x}.bin"
        if state == COMPLETE:
            tx.path = self._write_complete(tx, name, log)
            log.add("INFO", f"{tx.tid} '{name}' COMPLETE: {reason}"
                    + (f" -> {tx.path}" if tx.path else ""))
        else:
            self._write_partial(tx, name, log)
            out = tx.partial_path or tx.info_path
            log.add("WARN", f"{tx.tid} '{name}' {state}: {reason}" + (f" -> {out}" if out else ""))
        self.open.pop((tx.source, tx.seq), None)
        self.finished[(tx.source, tx.seq)] = tx
        if tx.check_until is None:
            tx.segments.clear()                            # the data now lives on disk
            tx.stored = 0
        while len(self.finished) > self.history:
            self.finished.pop(next(iter(self.finished)))

    def _base(self, tx: Transaction, name: str) -> Path:
        """A path in files_dir taken neither by a file nor by a .partial/.json
        twin: `name`, else the name with the transaction id inserted."""
        p = Path(name)
        tag = f"{tx.source:02x}-{tx.seq:08x}"
        candidates = [name, f"{p.stem}.{tag}{p.suffix}"]
        candidates += [f"{p.stem}.{tag}.{k}{p.suffix}" for k in range(1, 100)]
        for c in candidates:
            base = self.files_dir / c
            if not any(base.with_name(base.name + ext).exists() for ext in ("", ".partial", ".json")):
                return base
        return self.files_dir / candidates[-1]

    def _write_complete(self, tx: Transaction, name: str, log: _Log) -> str | None:
        try:
            base = self._base(tx, name)
            self._atomic_write(base, tx.image(tx.eof_size or 0))
            return str(base)
        except OSError as e:
            log.add("WARN", f"{tx.tid} cannot write '{name}': {e}")
            return None

    def _write_partial(self, tx: Transaction, name: str, log: _Log) -> None:
        size = tx.size
        if tx.state in (INCOMPLETE, CHECKSUM_FAILURE):
            length = tx.eof_size or 0                      # the final size, gaps zero-filled
        else:                                              # what arrived, nothing invented
            length = tx.high if size is None else min(tx.high, size)
        info: dict[str, Any] = {
            "source": tx.source, "seq": tx.seq, "seq_hex": f"{tx.seq:08x}", "origin": tx.origin,
            "name": name,
            "raw_name": None if tx.raw_name is None else tx.raw_name.decode("ascii", "replace"),
            "state": tx.state, "reason": tx.reason, "condition": tx.condition,
            "condition_name": None if tx.condition is None else condition_name(tx.condition),
            "fault_entity": tx.fault_entity, "size": size, "metadata_size": tx.md_size,
            "eof_size": tx.eof_size, "received": tx.received(size), "missing": tx.missing(),
            "checksum_type": tx.checksum_type,
            "checksum_expected": None if tx.eof_checksum is None else f"{tx.eof_checksum:08x}",
            "checksum_computed": None if tx.checksum_computed is None else f"{tx.checksum_computed:08x}",
            "checksum_ok": tx.checksum_ok, "pdus": tx.pdus, "file_data_pdus": tx.file_data_pdus,
            "duplicates": tx.duplicates, "conflicts": tx.conflicts, "rejected": tx.rejected,
            "t_start": tx.t_start, "t_end": tx.t_end, "partial": None,
            "note": "missing ranges are [start, end) file offsets; they read as zeros in the .partial",
        }
        try:
            base = self._base(tx, name)
            if tx.segments and length > 0:
                part = base.with_name(base.name + ".partial")
                self._atomic_write(part, tx.image(length))
                tx.partial_path = str(part)
                info["partial"] = part.name
            js = base.with_name(base.name + ".json")
            self._atomic_write(js, (json.dumps(info, indent=1) + "\n").encode())
            tx.info_path = str(js)
        except OSError as e:
            log.add("WARN", f"{tx.tid} cannot write the partial file of '{name}': {e}")

    def _atomic_write(self, path: Path, data: bytes) -> None:
        """Readers never see a half-written file: write a temporary, then rename."""
        self.files_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    @staticmethod
    def _remove(*paths: str | None) -> None:
        for p in paths:
            if p:
                with contextlib.suppress(OSError):         # best effort: a stale .partial is harmless
                    os.remove(p)

    def _warn_limited(self, log: _Log, now: float, key: str, text: str, every_s: float = 10.0) -> None:
        last = self._last_warn.get(key)
        if last is None or now - last >= every_s or now < last:
            self._last_warn[key] = now
            log.add("WARN", text)
