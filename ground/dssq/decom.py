"""Telemetry decommutation: Space Packet user data -> engineering values + limit states."""
from __future__ import annotations

import struct

from .ccsds.packets import SpacePacket
from .dictionary import APID_EVR, EVR_SEVERITY, NA, PACKETS, checked_limits


class Decommutator:
    def __init__(self):
        self._fmt = {}
        for apid, spec in PACKETS.items():
            codes = "".join(f[1] for f in spec["fields"])
            self._fmt[apid] = struct.Struct(">" + codes)

    @staticmethod
    def packet_name(apid: int) -> str:
        if apid == APID_EVR:
            return "EVR"
        return PACKETS.get(apid, {}).get("name", f"APID_{apid:03X}")

    def expected_length(self, apid: int) -> int | None:
        s = self._fmt.get(apid)
        return s.size if s else None

    def decode(self, pkt: SpacePacket) -> dict:
        """Returns {field: engineering value}. Unavailable sentinels map to None."""
        if pkt.apid == APID_EVR:
            d = pkt.user_data
            return {"severity": EVR_SEVERITY.get(d[0], str(d[0])),
                    "event_id": int.from_bytes(d[1:3], "big"),
                    "text": d[3:].decode("ascii", "replace")}
        spec = PACKETS.get(pkt.apid)
        if spec is None:
            raise KeyError(f"unknown APID 0x{pkt.apid:03X}")
        s = self._fmt[pkt.apid]
        if len(pkt.user_data) < s.size:
            raise ValueError(f"{spec['name']}: {len(pkt.user_data)} octets < {s.size}")
        raw = s.unpack_from(pkt.user_data)
        out = {}
        for (name, code, scale, _unit, _desc, _lim), r in zip(spec["fields"], raw):
            if code in NA and r == NA[code]:
                out[name] = None
            elif scale is None:
                out[name] = r
            else:
                out[name] = r * scale
        return out

    def limits(self, apid: int, values: dict) -> dict:
        spec = PACKETS.get(apid)
        if not spec:
            return {}
        return {f[0]: checked_limits(f[0], values.get(f[0]), f[5])
                for f in spec["fields"] if f[5] is not None}

    @staticmethod
    def units(apid: int) -> dict:
        spec = PACKETS.get(apid)
        return {f[0]: f[3] for f in spec["fields"]} if spec else {}
