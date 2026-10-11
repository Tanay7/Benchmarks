"""USLP (CCSDS 732.1-B-2, Unified Space Data Link Protocol) field packing.

Mirrors spacecraft/vgq1_flight/sketch/src/ccsds/uslp.h. Shared by tm.py (USLP
downlink frames, auto-detected next to TM frames) and tc.py (USLP TC frames).

Transfer Frame Primary Header (TFPH), CCSDS bit order (bit 0 = MSB of octet 0):
  TFVN 4 '1100' | SCID 16 | source-or-destination 1 | VCID 6 | MAP ID 4 |
  end-of-frame-primary-header 1 | frame length 16 (octets - 1) |
  bypass/sequence control 1 | protocol control command (PCC) 1 | spare 2 |
  OCF flag 1 | VCF count length 3 | VCF count 0..7 octets
Transfer Frame Data Field header:
  construction rule 3 | UPID 5 | FHP 16 (fixed-length rules 000/001/010 only)

Mission profile (docs/04_Space_Data_Link_ICD.md):
  TM  200 octets: TFPH 8 | [SDLS hdr] | TFDF hdr 3 (rule 000, UPID 0, FHP) |
      TFDZ 183 - SDLS | [SDLS trailer] | OCF 4 (CLCW) | FECF 2
  OID VCID 63, rule 000, UPID 31 (idle), FHP 0xFFFF, TFDZ = clear-text
      identification "DE <callsign> VGQ-1 " repeated; never SDLS
  TC  <= 64 octets: TFPH 7 (+1 VCF count = N(S) on Type-AD) | [SDLS hdr] |
      TFDF hdr 1 (rule 111, UPID 0 packet / 1 COP-1 directive) | data |
      [SDLS trailer] | FECF 2
"""
from __future__ import annotations

from dataclasses import dataclass

from . import SPACECRAFT_ID, TM_FECF_LEN, TM_FRAME_LEN, TM_OCF_LEN

TFVN = 0b1100
FIXED_HDR_LEN = 7                 # TFPH without the VCF count
TM_HDR_LEN = 8                    # downlink TFPH: 1-octet VCF count
TFDF_HDR_LEN = 3                  # rule|UPID + FHP (fixed-length rules)
TC_TFDF_HDR_LEN = 1               # rule|UPID (variable-length rule 111)
TFDZ_LEN = TM_FRAME_LEN - TM_HDR_LEN - TFDF_HDR_LEN - TM_OCF_LEN - TM_FECF_LEN   # 183
FHP_NONE = 0xFFFF                 # no packet header starts in this TFDZ
VCID_OID = 63                     # reserved for Only-Idle-Data frames

RULE_PACKETS = 0b000              # packets spanning fixed-length TFDZs
RULE_NO_SEGMENTATION = 0b111      # variable-length TFDZ, no segmentation
FIXED_LENGTH_RULES = (0b000, 0b001, 0b010)
UPID_SPACE_PACKETS = 0            # Space Packets / Encapsulation Packets
UPID_COP1 = 1                     # COP-1 control commands
UPID_IDLE = 31                    # idle data


def is_uslp(frame: bytes) -> bool:
    return len(frame) > 0 and (frame[0] >> 4) == TFVN


@dataclass
class UslpHeader:
    scid: int = SPACECRAFT_ID
    dest: bool = False            # False: SCID is the source (downlink); True: destination
    vcid: int = 0
    map_id: int = 0
    truncated: bool = False       # end-of-frame-primary-header flag
    frame_len: int = 0            # TOTAL frame octets (the field carries frame_len - 1)
    bypass: bool = False          # False sequence-controlled (Type-A), True expedited (Type-B)
    pcc: bool = False             # TFDF carries protocol control information
    ocf: bool = False
    vcf_len: int = 0              # VC frame count length, octets
    vcf_count: int = 0

    @property
    def length(self) -> int:
        return FIXED_HDR_LEN + self.vcf_len

    def pack(self) -> bytes:
        if not (0 <= self.scid <= 0xFFFF and 0 <= self.vcid <= 63 and 0 <= self.map_id <= 15
                and 0 <= self.vcf_len <= 7 and 1 <= self.frame_len <= 0x10000):
            raise ValueError("USLP header field out of range")
        if self.vcf_count >> (8 * self.vcf_len):
            raise ValueError("VCF count does not fit its length")
        b = bytearray(4)
        b[0] = TFVN << 4 | self.scid >> 12
        b[1] = (self.scid >> 4) & 0xFF
        b[2] = (self.scid & 0xF) << 4 | int(self.dest) << 3 | self.vcid >> 3
        b[3] = (self.vcid & 7) << 5 | self.map_id << 1          # EoFPH = 0
        b += (self.frame_len - 1).to_bytes(2, "big")
        b.append(int(self.bypass) << 7 | int(self.pcc) << 6 | int(self.ocf) << 3 | self.vcf_len)
        b += self.vcf_count.to_bytes(self.vcf_len, "big")
        return bytes(b)

    @classmethod
    def unpack(cls, b: bytes) -> "UslpHeader":
        """Parses a full TFPH; raises ValueError (short, not TFVN 1100, truncated)."""
        if len(b) < 4 or not is_uslp(b):
            raise ValueError("not a USLP frame")
        h = cls(scid=(b[0] & 0xF) << 12 | b[1] << 4 | b[2] >> 4, dest=bool(b[2] >> 3 & 1),
                vcid=(b[2] & 7) << 3 | b[3] >> 5, map_id=b[3] >> 1 & 0xF, truncated=bool(b[3] & 1))
        if h.truncated:
            raise ValueError("truncated USLP primary header")
        if len(b) < FIXED_HDR_LEN:
            raise ValueError("short USLP primary header")
        h.frame_len = int.from_bytes(b[4:6], "big") + 1
        h.bypass, h.pcc = bool(b[6] >> 7), bool(b[6] >> 6 & 1)
        h.ocf, h.vcf_len = bool(b[6] >> 3 & 1), b[6] & 7
        if len(b) < h.length:
            raise ValueError("short USLP primary header")
        h.vcf_count = int.from_bytes(b[FIXED_HDR_LEN:h.length], "big")
        return h


def tfdf_header(rule: int, upid: int, fhp: int | None = None) -> bytes:
    """TFDF header: 1 octet, plus the 16-bit FHP/LVOP for the fixed-length rules."""
    head = bytes([(rule & 7) << 5 | (upid & 0x1F)])
    if rule in FIXED_LENGTH_RULES:
        return head + (FHP_NONE if fhp is None else fhp).to_bytes(2, "big")
    return head


def parse_tfdf_header(b: bytes) -> tuple[int, int, int | None, int]:
    """-> (rule, upid, fhp or None, header length)."""
    if not b:
        raise ValueError("empty TFDF")
    rule, upid = b[0] >> 5, b[0] & 0x1F
    if rule in FIXED_LENGTH_RULES:
        if len(b) < TFDF_HDR_LEN:
            raise ValueError("short TFDF header")
        return rule, upid, int.from_bytes(b[1:3], "big"), TFDF_HDR_LEN
    return rule, upid, None, TC_TFDF_HDR_LEN


def idle_fill(pattern: bytes | None, n: int) -> bytes:
    """`n` octets of repeated `pattern` from its first octet (flight idle_fill());
    0x55 fill without a pattern."""
    if not pattern:
        return b"\x55" * n
    return (pattern * (n // len(pattern) + 1))[:n]


def ident_text(zone: bytes) -> str | None:
    """Clear-text identification carried as idle data in OID frames (TM VC7 /
    USLP VCID 63): the shortest repeating unit of the data field, when it is
    printable ASCII of at least 4 characters (plain 0x55 fill returns None)."""
    n = len(zone)
    unit = zone
    for p in range(1, n // 2 + 1):
        if (zone[:p] * (n // p + 1))[:n] == zone:
            unit = zone[:p]
            break
    if len(unit) < 4 or not all(0x20 <= c < 0x7F for c in unit):
        return None
    return unit.decode("ascii").strip() or None


__all__ = ["TFVN", "FIXED_HDR_LEN", "TM_HDR_LEN", "TFDF_HDR_LEN", "TC_TFDF_HDR_LEN", "TFDZ_LEN",
           "FHP_NONE", "VCID_OID", "RULE_PACKETS", "RULE_NO_SEGMENTATION", "FIXED_LENGTH_RULES",
           "UPID_SPACE_PACKETS", "UPID_COP1", "UPID_IDLE", "UslpHeader", "is_uslp", "tfdf_header",
           "parse_tfdf_header", "idle_fill", "ident_text"]
