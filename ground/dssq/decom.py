"""Telemetry decommutation: Space Packet user data -> engineering values + limit states.

The ground half of the flight packers (spacecraft/.../fsw/tlm_packets.h, fsw/spectral.h),
driven entirely by dssq.dictionary (Phase B specification PB-1, PB-8.4):

  * fixed-layout APIDs (dictionary.PACKETS): the user data must have exactly the layout's
    length; per field the not-available sentinel (dictionary.field_na) decodes to None,
    else the optional 7th-element decoder is applied to the raw integer (SPEC v3.1 log
    code, azimuth wrap), else raw x scale (scale None keeps the raw integer);
  * EVR 0x012       severity u8 | event_id u16 | ASCII text (<= 80 characters);
  * RFSCAN 0x015    first_ch u8 | count u8 (1..84) | duration_ms u16 | noise i8[count];
  * SPECBURST 0x024 spectral.decode_specburst (raw or CCSDS 121.0-B-3 payload; a corrupt
                    Rice stream raises rice.RiceError, a ValueError);
  * CFDP 0x030      one CCSDS 727.0-B-5 PDU per packet -> {"pdu_len": n} only: the GDS
                    hands the user data to ccsds.cfdp.Receiver, which validates it.
A raw field listed in dictionary.BITFIELDS is replaced by its sub-fields (CMDVER stage
octet -> `stage` b1..0 and `cmd_source` b7..6, interface decision I1).

Everything that arrives over RF is untrusted, even when SDLS protected it (OID frames are
never protected and NONE is a valid service): every length is checked before use, and
decode() raises KeyError for an APID the dictionary does not know and ValueError for any
malformed packet, the two exceptions the GDS counts as decom errors. EVR text is ASCII; an
octet outside 0x20..0x7E is shown as U+FFFD so a console or log viewer can never be driven
by control or escape sequences received from the link.

encode() is the inverse for the fixed-layout APIDs (simulator, tests): scale inversion with
rounding half away from zero (the flight's lroundf), None -> the field's sentinel, and a
ValueError for anything the flight could not have sent.
"""
from __future__ import annotations

import math
import struct
from collections.abc import Callable, Mapping
from typing import Any

from . import spectral
from .ccsds.packets import SpacePacket
from .dictionary import (APID_CFDP, APID_EVR, APID_RFSCAN, APID_SPECBURST, BITFIELDS, CFDP_PDU_MAX,
                         CFDP_PDU_MIN, EVR_SEVERITY, EVR_TEXT_MAX, PACKET_NAMES, PACKETS,
                         RFSCAN_MAX_CHANNELS, FieldDef, checked_limits, field_na)

__all__ = ["Decommutator", "decode_cfdp", "decode_evr", "decode_rfscan", "encode"]

# Struct code -> inclusive raw range (the integer codes the dictionary uses).
_RANGE: dict[str, tuple[int, int]] = {
    "B": (0, 0xFF), "b": (-0x80, 0x7F), "H": (0, 0xFFFF), "h": (-0x8000, 0x7FFF),
    "I": (0, 0xFFFFFFFF), "i": (-0x80000000, 0x7FFFFFFF)}
# Raw-code encoder of every 7th-element decoder (encode() refuses a decoder not listed).
_INVERSE: dict[Callable[[int], Any], Callable[[float], int]] = {
    spectral.log_decode: spectral.log_encode,
    spectral.azimuth_from_cdeg: spectral.azimuth_to_cdeg,
}
_EVR_HDR = 3                        # severity u8 | event_id u16
_RFSCAN_HDR = 4                     # first_ch u8 | count u8 | duration_ms u16
_RFSCAN_NA = -128
_CHANNELS = 84                      # E22 / SX126x channel plan CH 0..83


def _decoder(fld: FieldDef) -> Callable[[int], Any] | None:
    dec = fld[6] if len(fld) > 6 else None
    return dec if callable(dec) else None


def _layout(apid: int) -> struct.Struct:
    return struct.Struct(">" + "".join(f[1] for f in PACKETS[apid]["fields"]))


# =============================================================================
# Variable-length packets
# =============================================================================
def _ascii_text(raw: bytes) -> str:
    """Printable ASCII kept, every other octet -> U+FFFD (untrusted text, module docstring)."""
    return "".join(chr(b) if 0x20 <= b <= 0x7E else "�" for b in raw)


def decode_evr(d: bytes) -> dict[str, Any]:
    """EVR user data -> {severity (name, or the number as text when unknown), severity_code,
    event_id, text}. ValueError when shorter than 3 octets or the text exceeds 80."""
    if len(d) < _EVR_HDR:
        raise ValueError(f"EVR: {len(d)} octets < {_EVR_HDR}")
    if len(d) - _EVR_HDR > EVR_TEXT_MAX:
        raise ValueError(f"EVR: text of {len(d) - _EVR_HDR} characters > {EVR_TEXT_MAX}")
    return {"severity": EVR_SEVERITY.get(d[0], str(d[0])), "severity_code": d[0],
            "event_id": int.from_bytes(d[1:3], "big"), "text": _ascii_text(d[_EVR_HDR:])}


def decode_rfscan(d: bytes) -> dict[str, Any]:
    """RFSCAN user data -> {first_ch, count, duration_ms, noise: [dBm | None]}. The flight
    sends 1..84 channels inside CH 0..83 and exactly 4 + count octets (pack_rfscan)."""
    if len(d) < _RFSCAN_HDR:
        raise ValueError(f"RFSCAN: {len(d)} octets < {_RFSCAN_HDR}")
    first, n = d[0], d[1]
    if not 1 <= n <= RFSCAN_MAX_CHANNELS:
        raise ValueError(f"RFSCAN: channel count {n} outside 1..{RFSCAN_MAX_CHANNELS}")
    if first + n > _CHANNELS:
        raise ValueError(f"RFSCAN: channels {first}..{first + n - 1} beyond CH {_CHANNELS - 1}")
    if len(d) != _RFSCAN_HDR + n:
        raise ValueError(f"RFSCAN: {len(d)} octets for {n} channels (expected {_RFSCAN_HDR + n})")
    noise = [None if v == _RFSCAN_NA else v for v in struct.unpack_from(f">{n}b", d, _RFSCAN_HDR)]
    return {"first_ch": first, "count": n, "duration_ms": int.from_bytes(d[2:4], "big"),
            "noise": noise}


def decode_cfdp(d: bytes) -> dict[str, Any]:
    """CFDP packet -> {"pdu_len": n}; the PDU itself is validated by the CFDP receiver."""
    if not CFDP_PDU_MIN <= len(d) <= CFDP_PDU_MAX:
        raise ValueError(f"CFDP: PDU of {len(d)} octets outside {CFDP_PDU_MIN}..{CFDP_PDU_MAX}")
    return {"pdu_len": len(d)}


# =============================================================================
# Decommutator
# =============================================================================
class Decommutator:
    """Stateless apart from the precomputed layouts; safe to share between threads."""

    def __init__(self) -> None:
        self._fmt: dict[int, struct.Struct] = {apid: _layout(apid) for apid in PACKETS}
        # Per field: (name, scale, decoder, raw NA sentinel or None), in layout order.
        self._plan: dict[int, list[tuple[str, float | None, Callable[[int], Any] | None,
                                         int | None]]] = {
            apid: [(str(f[0]), f[2], _decoder(f), field_na(apid, f)) for f in spec["fields"]]
            for apid, spec in PACKETS.items()}

    @staticmethod
    def packet_name(apid: int) -> str:
        return PACKET_NAMES.get(apid, f"APID_{apid:03X}")

    def decode(self, pkt: SpacePacket) -> dict[str, Any]:
        """{field: engineering value} (None = not available). KeyError: APID unknown to the
        dictionary; ValueError: malformed packet (wrong length, bad count, corrupt payload)."""
        apid, d = pkt.apid, pkt.user_data
        if apid == APID_EVR:
            return decode_evr(d)
        if apid == APID_RFSCAN:
            return decode_rfscan(d)
        if apid == APID_SPECBURST:
            return spectral.decode_specburst(d)
        if apid == APID_CFDP:
            return decode_cfdp(d)
        plan = self._plan.get(apid)
        if plan is None:
            raise KeyError(f"unknown APID 0x{apid:03X}")
        s = self._fmt[apid]
        if len(d) != s.size:
            raise ValueError(f"{PACKET_NAMES[apid]}: {len(d)} octets of user data, the "
                             f"layout has {s.size}")
        out: dict[str, Any] = {}
        for (name, scale, dec, na), r in zip(plan, s.unpack(d), strict=True):
            if na is not None and r == na:
                out[name] = None
            elif dec is not None:
                out[name] = dec(r)
            elif scale is None:
                out[name] = r
            else:
                out[name] = r * scale
        for raw_name, subs in BITFIELDS.get(apid, {}).items():
            r = out.pop(raw_name)
            for sub, lsb, width in subs:
                out[sub] = None if r is None else (r >> lsb) & ((1 << width) - 1)
        return out

    @staticmethod
    def limits(apid: int, values: Mapping[str, Any]) -> dict[str, str]:
        """{field: 'OK' | 'YELLOW' | 'RED'} for every field that has limits."""
        spec = PACKETS.get(apid)
        if not spec:
            return {}
        return {f[0]: checked_limits(f[0], values.get(f[0]), f[5])
                for f in spec["fields"] if f[5] is not None}

    @staticmethod
    def units(apid: int) -> dict[str, str]:
        spec = PACKETS.get(apid)
        return {f[0]: f[3] for f in spec["fields"]} if spec else {}


# =============================================================================
# Inverse (simulator, tests)
# =============================================================================
def _number(name: str, v: Any) -> float | int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{name}: {v!r} is not a number")
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError(f"{name}: {v!r} is not finite (use None for 'not available')")
    return v


def _raw(apid: int, fld: FieldDef, v: Any) -> int:
    name, code, scale = str(fld[0]), str(fld[1]), fld[2]
    na = field_na(apid, fld)
    if v is None:
        if na is None:
            raise ValueError(f"{name}: None, but the field has no not-available value")
        return na
    x = _number(name, v)
    dec = _decoder(fld)
    if dec is not None:
        inv = _INVERSE.get(dec)
        if inv is None:
            raise ValueError(f"{name}: no encoder for decoder {getattr(dec, '__name__', dec)!r}")
        r = inv(float(x))
    elif scale is None:
        if isinstance(x, float) and not x.is_integer():
            raise ValueError(f"{name}: raw field needs an integer, got {x!r}")
        r = int(x)
    else:
        r = spectral.round_half_away(x / float(scale))
    lo, hi = _RANGE[code]
    if not lo <= r <= hi:
        raise ValueError(f"{name}: {v!r} -> raw {r} outside the '{code}' range {lo}..{hi}")
    if na is not None and r == na:
        raise ValueError(f"{name}: {v!r} encodes as the not-available sentinel {na}")
    return r


def encode(apid: int, values: Mapping[str, Any]) -> bytes:
    """User data of a fixed-layout APID from engineering values (the inverse of decode()).
    A missing or None field is sent as its not-available sentinel; ValueError for a field
    without one, an unknown name, a non-number, a value outside the field's raw range or
    one that would collide with the sentinel. KeyError for an APID without a layout."""
    spec = PACKETS[apid]
    fields = spec["fields"]
    values = dict(values)
    subnames: set[str] = set()
    for raw_name, subs in BITFIELDS.get(apid, {}).items():
        packed = 0
        for sub, lsb, width in subs:
            subnames.add(sub)
            v = values.pop(sub, 0)
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < 1 << width:
                raise ValueError(f"{sub}: {v!r} is not a {width}-bit unsigned integer")
            packed |= v << lsb
        values[raw_name] = packed
    unknown = sorted(set(values) - {str(f[0]) for f in fields} - subnames)
    if unknown:
        raise ValueError(f"{spec['name']}: no field named {', '.join(unknown)}")
    return _layout(apid).pack(*[_raw(apid, f, values.get(str(f[0]))) for f in fields])
