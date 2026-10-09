"""Space Data Link Security (CCSDS 355.0-B-2), authentication-only, ground side.

Protected TC frame:
  | TC hdr 5 | SPI 2 | SN 4 | data | MAC 16 (HMAC-SHA-256 truncated) | FECF 2 |
MAC input = TC primary header || security header || data field.

Every (re)transmission gets a FRESH sequence number: COP-1 go-back-N
retransmissions would otherwise be rejected as replays once a later frame had
been authenticated. The SN is persisted so it keeps increasing across restarts.
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

SDLS_HDR_LEN = 6
SDLS_MAC_LEN = 16


def mac(key: bytes, primary: bytes, sec_hdr: bytes, data: bytes) -> bytes:
    return hmac.new(key, primary + sec_hdr + data, hashlib.sha256).digest()[:SDLS_MAC_LEN]


class SdlsSender:
    def __init__(self, key: bytes | None, spi: int = 1, sn_file: str | Path | None = None):
        self.key = key
        self.spi = spi
        self.sn_file = Path(sn_file) if sn_file else None
        self.sn = 0
        if self.sn_file and self.sn_file.exists():
            self.sn = int(self.sn_file.read_text().strip() or 0)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def next_sn(self) -> int:
        self.sn = (self.sn + 1) & 0xFFFFFFFF
        if self.sn_file:
            self.sn_file.write_text(str(self.sn))
        return self.sn

    def overhead(self) -> int:
        return SDLS_HDR_LEN + SDLS_MAC_LEN if self.enabled else 0


class SdlsReceiver:
    """Reference verifier (used by the software spacecraft simulator and tests)."""

    def __init__(self, key: bytes, spi: int = 1):
        self.key, self.spi, self.last_sn, self.failures = key, spi, 0, 0

    def process(self, frame: bytes, data: bytes):
        if len(data) < SDLS_HDR_LEN + SDLS_MAC_LEN + 1:
            self.failures += 1
            return None, "BAD_LENGTH"
        spi = int.from_bytes(data[0:2], "big")
        sn = int.from_bytes(data[2:6], "big")
        body, tag = data[SDLS_HDR_LEN:-SDLS_MAC_LEN], data[-SDLS_MAC_LEN:]
        if spi != self.spi:
            self.failures += 1
            return None, "BAD_SPI"
        if not hmac.compare_digest(mac(self.key, frame[:5], data[:SDLS_HDR_LEN], body), tag):
            self.failures += 1
            return None, "BAD_MAC"
        if sn <= self.last_sn:
            self.failures += 1
            return None, "REPLAY"
        self.last_sn = sn
        return body, "OK"


def load_key(path: str | Path | None) -> bytes | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    key = bytes.fromhex(p.read_text().strip())
    if len(key) != 32:
        raise ValueError(f"{p}: expected a 256-bit (64 hex digit) key")
    return key
