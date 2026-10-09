"""TM Transfer Frames (CCSDS 132.0-B-3), CLCW (232.0-B-4) and CADU decoding (131.0-B)."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import (ASM, CADU_LEN, CODEBLOCK_LEN, FHP_NO_PACKET_START, FHP_ONLY_IDLE_DATA,
               RS_PARITY, RS_VIRTUAL_FILL, SPACECRAFT_ID, TM_DATA_LEN, TM_FECF_LEN,
               TM_FRAME_LEN, TM_OCF_LEN, TM_PRI_HDR_LEN)
from . import rs
from .crc import crc16_ccitt
from .randomizer import derandomize, randomize


@dataclass
class Clcw:
    word: int
    control_word_type: int = 0
    version: int = 0
    status: int = 0
    cop_in_effect: int = 0
    vcid: int = 0
    no_rf_available: bool = False
    no_bit_lock: bool = False
    lockout: bool = False
    wait: bool = False
    retransmit: bool = False
    farm_b_counter: int = 0
    report_value: int = 0

    @classmethod
    def unpack(cls, w: int) -> "Clcw":
        return cls(
            word=w,
            control_word_type=(w >> 31) & 1,
            version=(w >> 29) & 3,
            status=(w >> 26) & 7,
            cop_in_effect=(w >> 24) & 3,
            vcid=(w >> 18) & 0x3F,
            no_rf_available=bool((w >> 15) & 1),
            no_bit_lock=bool((w >> 14) & 1),
            lockout=bool((w >> 13) & 1),
            wait=bool((w >> 12) & 1),
            retransmit=bool((w >> 11) & 1),
            farm_b_counter=(w >> 9) & 3,
            report_value=w & 0xFF,
        )

    @staticmethod
    def pack(vcid=0, no_rf=False, no_bitlock=False, lockout=False, wait=False,
             retransmit=False, farm_b=0, report_value=0) -> int:
        w = 1 << 24
        w |= (vcid & 0x3F) << 18
        w |= int(no_rf) << 15 | int(no_bitlock) << 14 | int(lockout) << 13
        w |= int(wait) << 12 | int(retransmit) << 11 | (farm_b & 3) << 9
        w |= report_value & 0xFF
        return w


@dataclass
class TmFrame:
    raw: bytes
    version: int
    scid: int
    vcid: int
    ocf_flag: bool
    mcfc: int
    vcfc: int
    sec_hdr_flag: bool
    sync_flag: bool
    packet_order: bool
    seg_len_id: int
    fhp: int
    data: bytes
    clcw: Clcw | None
    fecf_ok: bool

    @property
    def is_oid(self) -> bool:
        return self.fhp == FHP_ONLY_IDLE_DATA

    @classmethod
    def parse(cls, f: bytes) -> "TmFrame":
        if len(f) != TM_FRAME_LEN:
            raise ValueError(f"TM frame must be {TM_FRAME_LEN} octets, got {len(f)}")
        w0 = (f[0] << 8) | f[1]
        dfs = (f[4] << 8) | f[5]
        ocf_flag = bool(w0 & 1)
        end = TM_FRAME_LEN - TM_FECF_LEN
        clcw = None
        if ocf_flag:
            o = end - TM_OCF_LEN
            clcw = Clcw.unpack(int.from_bytes(f[o:o + 4], "big"))
        fecf_ok = crc16_ccitt(f[:end]) == int.from_bytes(f[end:], "big")
        return cls(
            raw=bytes(f), version=w0 >> 14, scid=(w0 >> 4) & 0x3FF, vcid=(w0 >> 1) & 7,
            ocf_flag=ocf_flag, mcfc=f[2], vcfc=f[3],
            sec_hdr_flag=bool(dfs >> 15), sync_flag=bool((dfs >> 14) & 1),
            packet_order=bool((dfs >> 13) & 1), seg_len_id=(dfs >> 11) & 3, fhp=dfs & 0x7FF,
            data=bytes(f[TM_PRI_HDR_LEN:TM_PRI_HDR_LEN + TM_DATA_LEN]),
            clcw=clcw, fecf_ok=fecf_ok)


@dataclass
class CaduResult:
    ok: bool                       # frame recovered and FECF valid
    rs_corrected: int              # symbols corrected, -1 = uncorrectable
    frame: TmFrame | None
    reason: str = ""
    corrected_positions: list = field(default_factory=list)


def decode_codeblock(codeblock: bytes) -> CaduResult:
    """De-randomize, RS-decode and parse one 232-octet codeblock."""
    if len(codeblock) != CODEBLOCK_LEN:
        return CaduResult(False, -1, None, "bad codeblock length")
    cb = derandomize(codeblock)
    fixed, nerr = rs.decode(cb, RS_VIRTUAL_FILL)
    if nerr < 0:
        return CaduResult(False, -1, None, "RS uncorrectable")
    positions = [i for i in range(len(cb)) if cb[i] != fixed[i]]
    frame = TmFrame.parse(fixed[:TM_FRAME_LEN])
    if not frame.fecf_ok:
        return CaduResult(False, nerr, frame, "FECF mismatch after RS", positions)
    if frame.version != 0:
        return CaduResult(False, nerr, frame, "bad TFVN", positions)
    if frame.scid != SPACECRAFT_ID:
        return CaduResult(False, nerr, frame, f"foreign SCID 0x{frame.scid:03X}", positions)
    return CaduResult(True, nerr, frame, "", positions)


def encode_cadu(frame: bytes) -> bytes:
    """Ground-side reference encoder (used by the simulator and tests)."""
    assert len(frame) == TM_FRAME_LEN
    cb = frame + rs.encode(frame)
    return ASM + randomize(cb)


def build_frame(vcid: int, mcfc: int, vcfc: int, fhp: int, data: bytes, clcw: int,
                scid: int = SPACECRAFT_ID) -> bytes:
    """Builds a 200-octet TM frame (used by the simulator / tests)."""
    assert len(data) == TM_DATA_LEN
    w0 = (0 << 14) | ((scid & 0x3FF) << 4) | ((vcid & 7) << 1) | 1
    dfs = (3 << 11) | (fhp & 0x7FF)
    f = bytearray()
    f += w0.to_bytes(2, "big") + bytes([mcfc & 0xFF, vcfc & 0xFF]) + dfs.to_bytes(2, "big")
    f += data
    f += clcw.to_bytes(4, "big")
    f += crc16_ccitt(bytes(f)).to_bytes(2, "big")
    return bytes(f)


__all__ = ["Clcw", "TmFrame", "CaduResult", "decode_codeblock", "encode_cadu", "build_frame",
           "ASM", "CADU_LEN", "CODEBLOCK_LEN", "FHP_NO_PACKET_START", "FHP_ONLY_IDLE_DATA",
           "RS_PARITY"]
