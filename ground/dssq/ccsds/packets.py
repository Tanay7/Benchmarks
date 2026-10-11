"""CCSDS Space Packets (133.0-B-2) and per-VC packet extraction using the FHP."""
from __future__ import annotations

from dataclasses import dataclass

from . import (APID_IDLE, FHP_NO_PACKET_START, FHP_ONLY_IDLE_DATA, SP_PRI_HDR_LEN,
               SP_SEC_HDR_LEN)
from .timecode import CucTime


@dataclass
class SpacePacket:
    raw: bytes
    version: int
    type_tc: bool
    sec_hdr: bool
    apid: int
    seq_flags: int
    seq_count: int
    data_len: int                  # octets in packet data field
    time: CucTime | None
    user_data: bytes

    @property
    def is_idle(self) -> bool:
        return self.apid == APID_IDLE

    @classmethod
    def parse(cls, raw: bytes) -> "SpacePacket":
        w0 = (raw[0] << 8) | raw[1]
        w1 = (raw[2] << 8) | raw[3]
        dlen = ((raw[4] << 8) | raw[5]) + 1
        sec = bool((w0 >> 11) & 1)
        apid = w0 & 0x7FF
        t = None
        body = raw[SP_PRI_HDR_LEN:SP_PRI_HDR_LEN + dlen]
        if sec and apid != APID_IDLE and len(body) >= SP_SEC_HDR_LEN:
            t = CucTime.unpack(body[:SP_SEC_HDR_LEN])
            body = body[SP_SEC_HDR_LEN:]
        return cls(raw=bytes(raw), version=w0 >> 13, type_tc=bool((w0 >> 12) & 1), sec_hdr=sec,
                   apid=apid, seq_flags=w1 >> 14, seq_count=w1 & 0x3FFF, data_len=dlen,
                   time=t, user_data=bytes(body))


def build_packet(apid: int, seq: int, payload: bytes, time: CucTime | None = None,
                 tc: bool = False) -> bytes:
    sec = time is not None
    df = (time.pack() if sec else b"") + payload
    w0 = (0 << 13) | (int(tc) << 12) | (int(sec) << 11) | (apid & 0x7FF)
    w1 = (3 << 14) | (seq & 0x3FFF)
    return w0.to_bytes(2, "big") + w1.to_bytes(2, "big") + (len(df) - 1).to_bytes(2, "big") + df


def build_idle_packet(total_len: int, seq: int) -> bytes:
    assert total_len >= 7
    w0 = APID_IDLE
    w1 = (3 << 14) | (seq & 0x3FFF)
    return (w0.to_bytes(2, "big") + w1.to_bytes(2, "big") +
            (total_len - 7).to_bytes(2, "big") + b"\x55" * (total_len - 6))


class PacketExtractor:
    """Reassembles Space Packets that may span consecutive frames of ONE VC.

    Follows the standard extraction rules: on a VC frame-count discontinuity the
    partial packet is discarded and extraction re-synchronises on the FHP.
    """

    MAX_PACKET = 4096

    def __init__(self, vcid: int):
        self.vcid = vcid
        self.buf = bytearray()
        self.synced = False
        self.last_vcfc: int | None = None
        self.stats = {"frames": 0, "vcfc_gaps": 0, "lost_frames": 0, "packets": 0,
                      "idle_packets": 0, "discarded_partials": 0, "bad_length": 0}

    def push(self, vcfc: int, fhp: int, data: bytes) -> list[SpacePacket]:
        self.stats["frames"] += 1
        if self.last_vcfc is not None:
            expected = (self.last_vcfc + 1) & 0xFF
            if vcfc != expected:
                self.stats["vcfc_gaps"] += 1
                self.stats["lost_frames"] += (vcfc - expected) & 0xFF
                if self.buf:
                    self.stats["discarded_partials"] += 1
                self.buf.clear()
                self.synced = False
        self.last_vcfc = vcfc

        if fhp == FHP_ONLY_IDLE_DATA:
            return []
        if not self.synced:
            if fhp == FHP_NO_PACKET_START:
                return []
            data = data[fhp:]
            self.synced = True
        self.buf += data
        return self._drain()

    def _drain(self) -> list[SpacePacket]:
        out: list[SpacePacket] = []
        while len(self.buf) >= SP_PRI_HDR_LEN:
            plen = SP_PRI_HDR_LEN + ((self.buf[4] << 8) | self.buf[5]) + 1
            if plen > self.MAX_PACKET or (self.buf[0] >> 5) != 0:
                # Header corrupt -> lose sync until next FHP.
                self.stats["bad_length"] += 1
                self.buf.clear()
                self.synced = False
                break
            if len(self.buf) < plen:
                break
            pkt = SpacePacket.parse(bytes(self.buf[:plen]))
            del self.buf[:plen]
            if pkt.is_idle:
                self.stats["idle_packets"] += 1
            else:
                self.stats["packets"] += 1
                out.append(pkt)
        return out
