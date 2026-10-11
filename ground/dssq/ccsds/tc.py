"""Telecommand: TC frames (232.0-B-4) and USLP TC frames (732.1-B-2), CLTU/BCH
(231.0-B-4), FARM-1 model (232.1-B-2).

USLP TC frame (station.toml [link] tc_framing = "USLP"; the spacecraft accepts
both formats and tells them apart by the version field):
  TFPH 7 (+1 VCF count = N(S) on Type-AD) | [SDLS hdr] | TFDF hdr 1 (rule 111,
  UPID 0 = Space Packet, 1 = COP-1 directive) | data | [SDLS trailer] | FECF 2
Type-AD: bypass 0, VCF count length 1. Type-BD: bypass 1, no count. Type-BC:
bypass 1, PCC 1, UPID 1 (Unlock 0x00 / Set V(R) 0x82 0x00 V(R), as for TC).
SDLS covers the TFDF; its AAD is the whole TFPH (incl. the VCF count).

An SDLS sender passed as `sdls` either implements
  tc_overhead() -> int                      security header + trailer octets
  protect_tc(header, plaintext) -> bytes    security header + protected data + trailer
(called once per frame, after the length check) or is the v2 SdlsSender
(authentication only: .key, .spi, .next_sn()), used directly.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import CLTU_START, CLTU_TAIL, SPACECRAFT_ID, TC_MAX_FRAME_LEN, TC_PRI_HDR_LEN
from .crc import crc16_ccitt
from .uslp import (RULE_NO_SEGMENTATION, TC_TFDF_HDR_LEN, UPID_COP1, UPID_SPACE_PACKETS,
                   UslpHeader, is_uslp)

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


def _security(sdls):
    """-> (overhead octets, protect(header, plaintext) -> bytes) for `sdls`, or (0, None)."""
    if sdls is None or not getattr(sdls, "enabled", False):
        return 0, None
    if hasattr(sdls, "protect_tc"):
        return int(sdls.tc_overhead()), sdls.protect_tc
    from .sdls import SDLS_HDR_LEN, SDLS_MAC_LEN, mac

    def auth(hdr: bytes, body: bytes) -> bytes:
        sh = sdls.spi.to_bytes(2, "big") + sdls.next_sn().to_bytes(4, "big")
        return sh + body + mac(sdls.key, hdr, sh, body)
    return SDLS_HDR_LEN + SDLS_MAC_LEN, auth


def build_tc_frame(data: bytes, seq: int, bypass: bool = False, control: bool = False,
                   vcid: int = 0, scid: int = SPACECRAFT_ID, sdls=None, framing: str = "TC",
                   map_id: int = 0) -> bytes:
    """Builds a TC Transfer Frame (`framing` "TC") or a USLP TC frame ("USLP");
    with `sdls` the data field / TFDF is protected per CCSDS 355.0-B (security
    header + trailer). `seq` is N(S) for Type-AD frames."""
    if framing.upper() == "USLP":
        return build_uslp_tc_frame(data, seq, bypass, control, vcid, scid, sdls, map_id)
    extra, protect = _security(sdls)
    length = TC_PRI_HDR_LEN + extra + len(data) + 2
    if length > TC_MAX_FRAME_LEN:
        raise ValueError(f"TC frame too long ({length} > {TC_MAX_FRAME_LEN})")
    w0 = (0 << 14) | (int(bypass) << 13) | (int(control) << 12) | (scid & 0x3FF)
    w1 = ((vcid & 0x3F) << 10) | ((length - 1) & 0x3FF)
    hdr = w0.to_bytes(2, "big") + w1.to_bytes(2, "big") + bytes([seq & 0xFF])
    f = hdr + (protect(hdr, bytes(data)) if protect else bytes(data))
    return f + crc16_ccitt(f).to_bytes(2, "big")


def build_uslp_tc_frame(data: bytes, seq: int, bypass: bool = False, control: bool = False,
                        vcid: int = 0, scid: int = SPACECRAFT_ID, sdls=None,
                        map_id: int = 0) -> bytes:
    """USLP TC frame: Type-AD (VCF count = N(S)), Type-BD, or Type-BC (`control`:
    PCC 1, UPID 1; must be bypass)."""
    if control and not bypass:
        raise ValueError("COP-1 control commands travel in Type-BC (bypass) frames")
    extra, protect = _security(sdls)
    vcf_len = 0 if bypass else 1
    tfdf = bytes([RULE_NO_SEGMENTATION << 5 | (UPID_COP1 if control else UPID_SPACE_PACKETS)])
    tfdf += bytes(data)
    length = 7 + vcf_len + extra + len(tfdf) + 2
    if length > TC_MAX_FRAME_LEN:
        raise ValueError(f"TC frame too long ({length} > {TC_MAX_FRAME_LEN})")
    hdr = UslpHeader(scid=scid, dest=True, vcid=vcid, map_id=map_id, frame_len=length,
                     bypass=bypass, pcc=control, ocf=False, vcf_len=vcf_len,
                     vcf_count=0 if bypass else seq & 0xFF).pack()
    f = hdr + (protect(hdr, tfdf) if protect else tfdf)
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
    """COP-1 Unlock control command (CCSDS 232.0-B-4, Type-BC data field)."""
    return b"\x00"


def bc_set_vr(vr: int) -> bytes:
    """COP-1 Set V(R) control command."""
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
    bypass: bool                   # USLP: bypass/sequence control flag
    control: bool                  # USLP: PCC flag
    scid: int
    vcid: int
    length: int
    seq: int                       # N(S); USLP: the VCF count (0 on Type-B)
    data: bytes
    format: str = "TC"             # "TC" or "USLP"
    hdr_len: int = TC_PRI_HDR_LEN  # primary header octets = SDLS AAD length (TC 5, USLP 7/8)
    map_id: int = 0
    upid: int | None = None        # USLP UPID once the TFDF is open
    tfdf_open: bool = True         # False while `data` still begins with the USLP TFDF header


def parse_tc_frame(b: bytes, open_tfdf: bool = True) -> TcFrameInfo | None:
    """Parses a TC or USLP TC frame (detected from the version field) and checks
    the FECF; None if invalid. For USLP the TFDF header is checked and stripped
    unless `open_tfdf` is False (SDLS order: parse -> SDLS -> tc_open_tfdf)."""
    if len(b) < TC_PRI_HDR_LEN + 2:
        return None
    if is_uslp(b):
        return _parse_uslp_tc(b, open_tfdf)
    w0 = int.from_bytes(b[0:2], "big")
    w1 = int.from_bytes(b[2:4], "big")
    length = (w1 & 0x3FF) + 1
    if (w0 >> 14) != 0 or length > len(b):
        return None
    if crc16_ccitt(b[:length - 2]) != int.from_bytes(b[length - 2:length], "big"):
        return None
    return TcFrameInfo(bool((w0 >> 13) & 1), bool((w0 >> 12) & 1), w0 & 0x3FF, w1 >> 10,
                       length, b[4], bytes(b[TC_PRI_HDR_LEN:length - 2]))


def _parse_uslp_tc(b: bytes, open_tfdf: bool) -> TcFrameInfo | None:
    """Same acceptance rules as the flight tc_parse (src/ccsds/tc.cpp)."""
    try:
        h = UslpHeader.unpack(b)
    except ValueError:
        return None
    ocf = 4 if h.ocf else 0
    length = h.frame_len
    if length > len(b) or length < h.length + TC_TFDF_HDR_LEN + ocf + 2:
        return None
    if crc16_ccitt(b[:length - 2]) != int.from_bytes(b[length - 2:length], "big"):
        return None
    if not h.dest or (not h.bypass and (h.vcf_len != 1 or h.pcc)):
        return None
    info = TcFrameInfo(h.bypass, h.pcc, h.scid, h.vcid, length,
                       h.vcf_count & 0xFF if h.vcf_len else 0,
                       bytes(b[h.length:length - 2 - ocf]), format="USLP", hdr_len=h.length,
                       map_id=h.map_id, tfdf_open=False)
    return tc_open_tfdf(info) if open_tfdf else info


def tc_open_tfdf(f: TcFrameInfo) -> TcFrameInfo | None:
    """Checks and strips the USLP TFDF header (rule 111; UPID 1 exactly when
    PCC is set). TC frames / an already open TFDF pass through; None if invalid."""
    if f.tfdf_open:
        return f
    if not f.data:
        return None
    rule, upid = f.data[0] >> 5, f.data[0] & 0x1F
    if rule != RULE_NO_SEGMENTATION or upid != (UPID_COP1 if f.control else UPID_SPACE_PACKETS):
        return None
    return replace(f, data=f.data[TC_TFDF_HDR_LEN:], upid=upid, tfdf_open=True)


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
