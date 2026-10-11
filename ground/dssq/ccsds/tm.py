"""Downlink Transfer Frames — TM (CCSDS 132.0-B-3) and USLP (732.1-B-2) — CLCW
(232.0-B-4) and CADU decoding (131.0-B).

Both formats are 200-octet frames on the same CADU. The format is detected per
frame from the first nibble (0xC = USLP TFVN '1100', TM TFVN is '00'), and both
parse into one TransferFrame. With SDLS (355.0-B-2) the caller passes the
security geometry of the data VCs (security header / trailer octets): the
protected region (TM data field / USLP TFDF) is split off, `data` stays empty
and `secured` is True until the SDLS layer hands the plaintext to
`with_plaintext()`. OID frames (TM VC7 / USLP VCID 63) never carry SDLS.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from . import (ASM, CADU_LEN, CODEBLOCK_LEN, FHP_NO_PACKET_START, FHP_ONLY_IDLE_DATA,
               RS_PARITY, RS_VIRTUAL_FILL, SPACECRAFT_ID, TM_DATA_LEN, TM_FECF_LEN,
               TM_FRAME_LEN, TM_OCF_LEN, TM_PRI_HDR_LEN)
from . import rs
from .crc import crc16_ccitt
from .randomizer import derandomize, randomize
from .uslp import (FHP_NONE, RULE_PACKETS, TFDZ_LEN, TM_HDR_LEN, UPID_IDLE, UPID_SPACE_PACKETS,
                   VCID_OID, UslpHeader, ident_text, idle_fill, is_uslp, parse_tfdf_header,
                   tfdf_header)

# protect(primary_header, plaintext) -> security header + protected data + trailer
Protector = Callable[[bytes, bytes], bytes]


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


def _uslp_fhp(fhp_raw: int | None, oid: bool, zone_len: int) -> int:
    """USLP FHP -> the TM conventions PacketExtractor uses (0x7FF none, 0x7FE OID)."""
    if oid:
        return FHP_ONLY_IDLE_DATA
    if fhp_raw is None or fhp_raw >= zone_len:      # 0xFFFF (none) or no usable start
        return FHP_NO_PACKET_START
    return fhp_raw


@dataclass
class TransferFrame:
    """One downlink frame, either format. `fhp` uses the TM conventions for both
    (offset, 0x7FF no packet start, 0x7FE only idle data) so PacketExtractor.push
    (vcfc, fhp, data) works unchanged; `fhp_raw` is the on-wire field."""
    raw: bytes
    version: int                    # TFVN: 0 TM, 12 USLP
    scid: int
    vcid: int
    ocf_flag: bool
    mcfc: int | None                # TM Master Channel Frame Count; None for USLP (no such field)
    vcfc: int                       # VC frame count (USLP: the VCF count)
    sec_hdr_flag: bool
    sync_flag: bool
    packet_order: bool
    seg_len_id: int
    fhp: int | None                 # None while a secured USLP TFDF is still protected
    data: bytes                     # TM data field / USLP TFDZ; b"" while secured
    clcw: Clcw | None
    fecf_ok: bool
    format: str = "TM"              # "TM" or "USLP"
    hdr_len: int = TM_PRI_HDR_LEN   # primary header octets = SDLS AAD prefix (TM 6, USLP 8)
    src_dest: int = 0               # USLP: 0 = SCID is the source
    map_id: int = 0
    frame_len: int = TM_FRAME_LEN   # USLP frame length field + 1
    bypass: bool = False
    pcc: bool = False
    vcf_count_len: int = 0
    rule: int | None = None         # USLP TFDF construction rule
    upid: int | None = None         # USLP protocol identifier
    fhp_raw: int | None = None
    sec_header: bytes = b""         # SDLS split, data VCs only
    protected: bytes = b""          # TM data field / USLP TFDF as on the wire
    sec_trailer: bytes = b""
    secured: bool = False           # protected region not yet replaced by its plaintext

    @property
    def is_oid(self) -> bool:
        return self.vcid == VCID_OID if self.format == "USLP" else self.fhp == FHP_ONLY_IDLE_DATA

    @property
    def primary_header(self) -> bytes:
        """The SDLS AAD prefix (TM primary header / USLP TFPH incl. VCF count)."""
        return self.raw[:self.hdr_len]

    @property
    def timecorr_key(self) -> int | None:
        """The u8 counter TIMECORR.ref_mcfc refers to: TM the MCFC; USLP the VCF
        count, and only VC0 frames can be time-correlation references."""
        if self.format == "TM":
            return self.mcfc
        return self.vcfc if self.vcid == 0 else None

    @property
    def ident(self) -> str | None:
        """Clear-text identification of an OID frame ("DE <callsign> VGQ-1")."""
        return ident_text(self.data) if self.is_oid else None

    @classmethod
    def parse(cls, f: bytes, sec_hdr_len: int = 0, sec_trl_len: int = 0) -> "TransferFrame":
        """Parses either format (auto-detected). Raises ValueError on a wrong length
        or an unusable USLP header (truncated / short)."""
        if len(f) != TM_FRAME_LEN:
            raise ValueError(f"transfer frame must be {TM_FRAME_LEN} octets, got {len(f)}")
        if is_uslp(f):
            return cls._parse_uslp(bytes(f), sec_hdr_len, sec_trl_len)
        w0 = (f[0] << 8) | f[1]
        dfs = (f[4] << 8) | f[5]
        ocf_flag = bool(w0 & 1)
        end = TM_FRAME_LEN - TM_FECF_LEN
        clcw = None
        if ocf_flag:
            o = end - TM_OCF_LEN
            clcw = Clcw.unpack(int.from_bytes(f[o:o + 4], "big"))
        fecf_ok = crc16_ccitt(f[:end]) == int.from_bytes(f[end:], "big")
        fhp = dfs & 0x7FF
        field_end = TM_PRI_HDR_LEN + TM_DATA_LEN
        data = bytes(f[TM_PRI_HDR_LEN:field_end])
        sec = dict(protected=data)
        if (sec_hdr_len or sec_trl_len) and fhp != FHP_ONLY_IDLE_DATA:
            a, b = TM_PRI_HDR_LEN + sec_hdr_len, field_end - sec_trl_len
            sec = dict(sec_header=bytes(f[TM_PRI_HDR_LEN:a]), protected=bytes(f[a:b]),
                       sec_trailer=bytes(f[b:field_end]), secured=True)
            data = b""
        return cls(
            raw=bytes(f), version=w0 >> 14, scid=(w0 >> 4) & 0x3FF, vcid=(w0 >> 1) & 7,
            ocf_flag=ocf_flag, mcfc=f[2], vcfc=f[3],
            sec_hdr_flag=bool(dfs >> 15), sync_flag=bool((dfs >> 14) & 1),
            packet_order=bool((dfs >> 13) & 1), seg_len_id=(dfs >> 11) & 3, fhp=fhp,
            data=data, clcw=clcw, fecf_ok=fecf_ok, fhp_raw=fhp, **sec)

    @classmethod
    def _parse_uslp(cls, f: bytes, sec_hdr_len: int, sec_trl_len: int) -> "TransferFrame":
        h = UslpHeader.unpack(f)
        end = TM_FRAME_LEN - TM_FECF_LEN
        clcw = None
        zone_end = end
        if h.ocf:
            zone_end = end - TM_OCF_LEN
            clcw = Clcw.unpack(int.from_bytes(f[zone_end:end], "big"))
        oid = h.vcid == VCID_OID
        common = dict(raw=f, version=f[0] >> 4, scid=h.scid, vcid=h.vcid, ocf_flag=h.ocf,
                      mcfc=None, vcfc=h.vcf_count & 0xFF, sec_hdr_flag=False, sync_flag=False,
                      packet_order=False, seg_len_id=3, clcw=clcw,
                      fecf_ok=crc16_ccitt(f[:end]) == int.from_bytes(f[end:], "big"),
                      format="USLP", hdr_len=h.length, src_dest=int(h.dest), map_id=h.map_id,
                      frame_len=h.frame_len, bypass=h.bypass, pcc=h.pcc, vcf_count_len=h.vcf_len)
        if (sec_hdr_len or sec_trl_len) and not oid:
            a, b = h.length + sec_hdr_len, zone_end - sec_trl_len
            return cls(fhp=None, data=b"", sec_header=f[h.length:a], protected=f[a:b],
                       sec_trailer=f[b:zone_end], secured=True, **common)
        tfdf = f[h.length:zone_end]
        rule, upid, fhp_raw, th = parse_tfdf_header(tfdf)
        zone = tfdf[th:]
        return cls(fhp=_uslp_fhp(fhp_raw, oid, len(zone)), data=zone, rule=rule, upid=upid,
                   fhp_raw=fhp_raw, protected=tfdf, **common)

    def with_plaintext(self, plaintext: bytes) -> "TransferFrame":
        """Returns the frame with the SDLS-recovered plaintext of the protected
        region (TM data field / USLP TFDF) in place: data, fhp (and the USLP TFDF
        header fields) become valid."""
        if not self.secured:
            raise ValueError("frame is not secured")
        if len(plaintext) != len(self.protected):
            raise ValueError("plaintext length differs from the protected region")
        if self.format == "TM":
            return replace(self, data=bytes(plaintext), secured=False)
        rule, upid, fhp_raw, th = parse_tfdf_header(plaintext)
        zone = bytes(plaintext[th:])
        return replace(self, data=zone, rule=rule, upid=upid, fhp_raw=fhp_raw,
                       fhp=_uslp_fhp(fhp_raw, False, len(zone)), secured=False)


TmFrame = TransferFrame          # v2 name, kept for existing callers
parse_frame = TransferFrame.parse


def data_field_len(fmt: str = "TM", sec_overhead: int = 0) -> int:
    """Packet-data octets per data-VC frame: TM 188 / USLP 183, minus the SDLS
    header + trailer (AE 14 + 16: TM 158 / USLP 153). Matches TmFramer::data_len."""
    return (TFDZ_LEN if fmt.upper() == "USLP" else TM_DATA_LEN) - sec_overhead


class FrameLossTracker:
    """Frames lost on the downlink, from the counters each format provides:
    TM — the MCFC counts every frame; USLP has no master-channel counter, so
    each VC's VCF count (OID VCID 63 included) is followed separately and a gap
    is noticed when that VC is next received."""

    def __init__(self):
        self._last: dict[tuple[str, int], int] = {}

    def update(self, f: TransferFrame) -> int:
        """Returns the number of frames missing before `f` (mod 256)."""
        key, value = (("TM", -1), f.mcfc) if f.format == "TM" else (("USLP", f.vcid), f.vcfc)
        prev = self._last.get(key)
        self._last[key] = value
        return 0 if prev is None else (value - prev - 1) & 0xFF


@dataclass
class CaduResult:
    ok: bool                       # frame recovered and FECF valid
    rs_corrected: int              # symbols corrected, -1 = uncorrectable
    frame: TransferFrame | None
    reason: str = ""
    corrected_positions: list = field(default_factory=list)


def decode_codeblock(codeblock: bytes, sec_hdr_len: int = 0, sec_trl_len: int = 0,
                     framing: str = "auto") -> CaduResult:
    """De-randomize, RS-decode and parse one 232-octet codeblock. `framing` is
    station.toml [link] tm_framing: "auto" accepts both formats, "TM"/"USLP"
    reject the other. The SDLS geometry applies to data-VC frames only."""
    if len(codeblock) != CODEBLOCK_LEN:
        return CaduResult(False, -1, None, "bad codeblock length")
    cb = derandomize(codeblock)
    fixed, nerr = rs.decode(cb, RS_VIRTUAL_FILL)
    if nerr < 0:
        return CaduResult(False, -1, None, "RS uncorrectable")
    positions = [i for i in range(len(cb)) if cb[i] != fixed[i]]
    try:
        frame = TransferFrame.parse(fixed[:TM_FRAME_LEN], sec_hdr_len, sec_trl_len)
    except ValueError as e:
        return CaduResult(False, nerr, None, f"unparseable frame: {e}", positions)
    if not frame.fecf_ok:
        return CaduResult(False, nerr, frame, "FECF mismatch after RS", positions)
    want = framing.upper()
    if want != "AUTO" and frame.format != want:
        return CaduResult(False, nerr, frame, f"{frame.format} frame, framing set to {want}", positions)
    if frame.format == "TM":
        if frame.version != 0:
            return CaduResult(False, nerr, frame, "bad TFVN", positions)
        if frame.scid != SPACECRAFT_ID:
            return CaduResult(False, nerr, frame, f"foreign SCID 0x{frame.scid:03X}", positions)
        return CaduResult(True, nerr, frame, "", positions)
    if frame.scid != SPACECRAFT_ID:
        return CaduResult(False, nerr, frame, f"foreign SCID 0x{frame.scid:04X}", positions)
    if frame.src_dest:
        return CaduResult(False, nerr, frame, "USLP frame addressed to a spacecraft", positions)
    if frame.frame_len != TM_FRAME_LEN:
        return CaduResult(False, nerr, frame, f"bad USLP frame length {frame.frame_len}", positions)
    if frame.rule is not None and frame.rule != RULE_PACKETS:
        return CaduResult(False, nerr, frame, f"unsupported TFDF construction rule {frame.rule}",
                          positions)
    return CaduResult(True, nerr, frame, "", positions)


def encode_cadu(frame: bytes) -> bytes:
    """Ground-side reference encoder (used by the simulator and tests)."""
    assert len(frame) == TM_FRAME_LEN
    cb = frame + rs.encode(frame)
    return ASM + randomize(cb)


def _trailer(f: bytes, clcw: int) -> bytes:
    f += clcw.to_bytes(4, "big")
    assert len(f) == TM_FRAME_LEN - TM_FECF_LEN, "frame field lengths do not add up to 200"
    return f + crc16_ccitt(f).to_bytes(2, "big")


def build_frame(vcid: int, mcfc: int, vcfc: int, fhp: int, data: bytes, clcw: int,
                scid: int = SPACECRAFT_ID, protect: Protector | None = None) -> bytes:
    """Builds a 200-octet TM frame (used by the simulator / tests). With `protect`
    `data` is the plaintext (188 - SDLS overhead octets) and protect(header, data)
    returns security header + protected data + trailer."""
    w0 = (0 << 14) | ((scid & 0x3FF) << 4) | ((vcid & 7) << 1) | 1
    dfs = (3 << 11) | (fhp & 0x7FF)
    hdr = w0.to_bytes(2, "big") + bytes([mcfc & 0xFF, vcfc & 0xFF]) + dfs.to_bytes(2, "big")
    body = protect(hdr, bytes(data)) if protect else bytes(data)
    assert len(body) == TM_DATA_LEN
    return _trailer(hdr + body, clcw)


def build_oid_frame(mcfc: int, vcfc: int, clcw: int, idle: bytes | None = None,
                    scid: int = SPACECRAFT_ID) -> bytes:
    """TM Only-Idle-Data frame: VC7, FHP 0x7FE, idle data = repeated `idle`."""
    return build_frame(7, mcfc, vcfc, FHP_ONLY_IDLE_DATA, idle_fill(idle, TM_DATA_LEN), clcw, scid)


def build_uslp_frame(vcid: int, vcfc: int, fhp: int, zone: bytes, clcw: int,
                     scid: int = SPACECRAFT_ID, upid: int = UPID_SPACE_PACKETS,
                     protect: Protector | None = None) -> bytes:
    """Builds a 200-octet USLP frame like the flight TmFramer in USLP mode:
    TFPH 8 (source flag 0, MAP 0, bypass 0, PCC 0, OCF 1, VCF count length 1) |
    TFDF header 3 (rule 000, `upid`, FHP) | TFDZ (183 - SDLS overhead) | CLCW |
    FECF. `fhp` may be given in either convention (0x7FF or 0xFFFF = none).
    With `protect`, protect(TFPH, TFDF) returns security header + protected
    TFDF + trailer."""
    hdr = UslpHeader(scid=scid, dest=False, vcid=vcid, frame_len=TM_FRAME_LEN, ocf=True,
                     vcf_len=1, vcf_count=vcfc & 0xFF).pack()
    fhp_raw = FHP_NONE if fhp in (FHP_NO_PACKET_START, FHP_NONE) else fhp
    tfdf = tfdf_header(RULE_PACKETS, upid, fhp_raw) + bytes(zone)
    body = protect(hdr, tfdf) if protect else tfdf
    assert len(hdr) == TM_HDR_LEN
    return _trailer(hdr + body, clcw)


def build_uslp_oid_frame(vcfc: int, clcw: int, idle: bytes | None = None,
                         scid: int = SPACECRAFT_ID) -> bytes:
    """USLP Only-Idle-Data frame: VCID 63, rule 000, UPID 31, FHP 0xFFFF,
    TFDZ = repeated `idle` (flight build_oid_frame in USLP mode)."""
    return build_uslp_frame(VCID_OID, vcfc, FHP_NONE, idle_fill(idle, TFDZ_LEN), clcw, scid,
                            upid=UPID_IDLE)


__all__ = ["Clcw", "TransferFrame", "TmFrame", "parse_frame", "CaduResult", "FrameLossTracker",
           "decode_codeblock", "encode_cadu", "build_frame", "build_oid_frame", "build_uslp_frame",
           "build_uslp_oid_frame", "data_field_len", "Protector",
           "ASM", "CADU_LEN", "CODEBLOCK_LEN", "FHP_NO_PACKET_START", "FHP_ONLY_IDLE_DATA",
           "RS_PARITY"]
