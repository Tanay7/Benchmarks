"""Telecommand: TC frames (232.0-B-4), CLTU/BCH (231.0-B-4), FARM-1 model (232.1-B-2)."""
from __future__ import annotations

from dataclasses import dataclass

from . import CLTU_START, CLTU_TAIL, SPACECRAFT_ID, TC_MAX_FRAME_LEN, TC_PRI_HDR_LEN
from .crc import crc16_ccitt

FILL = 0x55            # CLTU fill octet for the last codeblock


def _bch_remainder(info: bytes) -> int:
    reg = 0
    for byte in info:
        for b in range(7, -1, -1):
            fb = ((byte >> b) & 1) ^ ((reg >> 6) & 1)
            reg = (reg << 1) & 0x7F
            if fb:
                reg ^= 0x45          # g(x) = x^7 + x^6 + x^2 + 1
    return reg


def bch_codeblock(info7: bytes) -> bytes:
    assert len(info7) == 7
    return bytes(info7) + bytes([((~_bch_remainder(info7)) & 0x7F) << 1])


def build_tc_frame(data: bytes, seq: int, bypass: bool = False, control: bool = False,
                   vcid: int = 0, scid: int = SPACECRAFT_ID, sdls=None) -> bytes:
    """Builds a TC Transfer Frame; with `sdls` (an SdlsSender with a key) the data
    field is protected per CCSDS 355.0-B (security header + MAC trailer)."""
    from .sdls import SDLS_HDR_LEN, SDLS_MAC_LEN, mac
    secure = sdls is not None and sdls.enabled
    extra = SDLS_HDR_LEN + SDLS_MAC_LEN if secure else 0
    length = TC_PRI_HDR_LEN + extra + len(data) + 2
    if length > TC_MAX_FRAME_LEN:
        raise ValueError(f"TC frame too long ({length} > {TC_MAX_FRAME_LEN})")
    w0 = (0 << 14) | (int(bypass) << 13) | (int(control) << 12) | (scid & 0x3FF)
    w1 = ((vcid & 0x3F) << 10) | ((length - 1) & 0x3FF)
    hdr = w0.to_bytes(2, "big") + w1.to_bytes(2, "big") + bytes([seq & 0xFF])
    if secure:
        sh = sdls.spi.to_bytes(2, "big") + sdls.next_sn().to_bytes(4, "big")
        f = hdr + sh + data + mac(sdls.key, hdr, sh, data)
    else:
        f = hdr + data
    return f + crc16_ccitt(f).to_bytes(2, "big")


def build_cltu(frame: bytes) -> bytes:
    out = bytearray(CLTU_START)
    for i in range(0, len(frame), 7):
        chunk = frame[i:i + 7]
        chunk = chunk + bytes([FILL]) * (7 - len(chunk))
        out += bch_codeblock(chunk)
    out += CLTU_TAIL
    return bytes(out)


def bc_unlock() -> bytes:
    return b"\x00"


def bc_set_vr(vr: int) -> bytes:
    return bytes([0x82, 0x00, vr & 0xFF])


# --- Receiving end (used by the software spacecraft simulator) -------------------

def _syndrome(cb: bytes) -> int:
    return ((~_bch_remainder(cb[:7])) & 0x7F) ^ ((cb[7] >> 1) & 0x7F)


def _syndrome_table():
    tbl = {}
    base = bch_codeblock(bytes(7))
    for p in range(63):
        e = bytearray(base)
        if p < 56:
            e[p // 8] ^= 0x80 >> (p % 8)
        else:
            e[7] ^= 0x80 >> (p - 56)
        tbl[_syndrome(bytes(e))] = p
    return tbl


_SYN = _syndrome_table()


def cltu_decode(data: bytes):
    """Returns (info_octets, stats_dict) — mirrors the flight implementation."""
    st = {"ok": 0, "corrected": 0, "rejected": 0}
    i = data.find(CLTU_START)
    if i < 0:
        return b"", st
    i += 2
    out = bytearray()
    while i + 8 <= len(data):
        cb = bytearray(data[i:i + 8])
        i += 8
        s = _syndrome(bytes(cb))
        if s:
            p = _SYN.get(s)
            if p is None:
                st["rejected"] += 1
                break
            if p < 56:
                cb[p // 8] ^= 0x80 >> (p % 8)
            st["corrected"] += 1
        else:
            st["ok"] += 1
        out += cb[:7]
    return bytes(out), st


@dataclass
class TcFrameInfo:
    bypass: bool
    control: bool
    scid: int
    vcid: int
    length: int
    seq: int
    data: bytes


def parse_tc_frame(b: bytes) -> TcFrameInfo | None:
    if len(b) < TC_PRI_HDR_LEN + 2:
        return None
    w0 = int.from_bytes(b[0:2], "big")
    w1 = int.from_bytes(b[2:4], "big")
    length = (w1 & 0x3FF) + 1
    if (w0 >> 14) != 0 or length > len(b):
        return None
    if crc16_ccitt(b[:length - 2]) != int.from_bytes(b[length - 2:length], "big"):
        return None
    return TcFrameInfo(bool((w0 >> 13) & 1), bool((w0 >> 12) & 1), w0 & 0x3FF, w1 >> 10,
                       length, b[4], bytes(b[TC_PRI_HDR_LEN:length - 2]))


class Farm1Model:
    """Python model of the flight FARM-1 (spacecraft/.../ccsds/tc.cpp)."""

    def __init__(self, window: int = 10):
        self.window = window
        self.state = 1
        self.vr = 0
        self.farm_b = 0
        self.lockout = self.wait = self.retransmit = False

    def on_frame(self, f: TcFrameInfo) -> str:
        if f.bypass:
            self.farm_b = (self.farm_b + 1) & 3
            if not f.control:
                return "ACCEPT_BYPASS"
            if f.data == b"\x00":
                self.lockout = self.wait = self.retransmit = False
                self.state = 1
                return "CONTROL_UNLOCK"
            if len(f.data) == 3 and f.data[0] == 0x82 and f.data[1] == 0:
                if self.state != 3:
                    self.vr = f.data[2]
                    self.retransmit = self.wait = False
                    self.state = 1
                return "CONTROL_SET_VR"
            return "CONTROL_INVALID"
        if self.state == 3:
            return "DISCARD_LOCKOUT"
        pw = nw = self.window // 2
        diff = (f.seq - self.vr) & 0xFF
        if diff == 0:
            self.vr = (self.vr + 1) & 0xFF
            self.retransmit = False
            return "ACCEPT"
        if diff < pw:
            self.retransmit = True
            return "DISCARD_RETRANSMIT"
        if ((self.vr - f.seq) & 0xFF) <= nw:
            return "DISCARD_NEG_WINDOW"
        self.lockout = True
        self.state = 3
        return "DISCARD_LOCKOUT"
