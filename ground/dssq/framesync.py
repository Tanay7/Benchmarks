"""Frame synchronizer for the E22 byte stream (CCSDS 131.0-B ASM acquisition).

The ground E22 outputs every received LoRa packet as a burst of octets followed
(REG3 bit 7) by one RSSI octet. One packet = one CADU (236 octets), so the
stream looks like:  [CADU][RSSI][CADU][RSSI]...  interleaved, when we ask for it,
with ambient-noise replies  C1 00 02 <noise> <last>.

The synchronizer implements the classic SEARCH -> LOCK -> FLYWHEEL state machine
used by ground frame synchronizers:
  SEARCH   : slide over the stream looking for the 32-bit ASM with <= E_s bit errors
  LOCK     : expect the next ASM exactly one CADU (+RSSI octet) later, accept <= E_l errors
  FLYWHEEL : tolerate up to F consecutive misses at the expected position before
             falling back to SEARCH
Bit-error tolerance on the ASM matters because the ASM is *not* protected by RS.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .ccsds import ASM, CADU_LEN

ASM_INT = int.from_bytes(ASM, "big")


def _hamming32(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


@dataclass
class RawCadu:
    cadu: bytes
    rssi_dbm: int | None
    ert: float              # UNIX time the last octet of the packet was read
    asm_errors: int


@dataclass
class NoiseReply:
    noise_dbm: int
    last_rssi_dbm: int
    ert: float


@dataclass
class RadioFault:
    """Ground E22 abnormal-status report FF FF FF <code> (REG1 bit 2, T37S manual 5.7)."""
    code: int               # 1 under-voltage, 2 over-voltage, 3 over-temperature, 4 = 2 + 3
    ert: float


RADIO_FAULTS = {1: "UNDER-VOLTAGE (<4.5 V)", 2: "OVER-VOLTAGE (>15 V)", 3: "OVER-TEMPERATURE (>120 C)",
                4: "OVER-VOLTAGE + OVER-TEMPERATURE"}


class FrameSynchronizer:
    SEARCH, LOCK, FLYWHEEL = "SEARCH", "LOCK", "FLYWHEEL"

    def __init__(self, rssi_byte: bool = True, search_tol: int = 2, lock_tol: int = 4,
                 flywheel: int = 3, stale_s: float = 3.0):
        self.rssi_byte = rssi_byte
        self.search_tol = search_tol
        self.lock_tol = lock_tol
        self.flywheel_max = flywheel
        self.stale_s = stale_s
        self.buf = bytearray()
        self.buf_time = 0.0
        self.state = self.SEARCH
        self.misses = 0
        self.expect_noise_reply = False
        self.stats = {"asm_found": 0, "asm_bit_errors": 0, "slips": 0, "discarded_octets": 0,
                      "cadus": 0, "noise_replies": 0, "flywheel_frames": 0, "lock_losses": 0,
                      "radio_fault_reports": 0}

    @property
    def unit_len(self) -> int:
        return CADU_LEN + (1 if self.rssi_byte else 0)

    def feed(self, data: bytes, now: float | None = None) -> list:
        """Feed raw octets; returns a list of RawCadu / NoiseReply / RadioFault objects."""
        now = time.time() if now is None else now
        out = []
        if data:
            self.buf += data
            self.buf_time = now
        elif self.buf and now - self.buf_time > self.stale_s:
            # A partial unit that never completed (octets lost on the UART/air).
            self.stats["discarded_octets"] += len(self.buf)
            self.buf.clear()
            self._lose_lock()
        while True:
            item = self._step(now)
            if item is None:
                break
            out.append(item)
        return out

    def _lose_lock(self):
        if self.state != self.SEARCH:
            self.stats["lock_losses"] += 1
        self.state = self.SEARCH
        self.misses = 0

    def _try_noise_reply(self, now):
        b = self.buf
        if self.expect_noise_reply and len(b) >= 5 and b[0] == 0xC1 and b[1] == 0x00 and b[2] == 0x02:
            nr = NoiseReply(-(256 - b[3]), -(256 - b[4]), now)
            del b[:5]
            self.expect_noise_reply = False
            self.stats["noise_replies"] += 1
            return nr
        return None

    def _try_fault(self, now):
        b = self.buf
        if len(b) >= 4 and b[0] == 0xFF and b[1] == 0xFF and b[2] == 0xFF and 1 <= b[3] <= 4:
            f = RadioFault(b[3], now)
            del b[:4]
            self.stats["radio_fault_reports"] += 1
            return f
        return None

    def _emit(self, now, errors):
        b = self.buf
        cadu = bytes(b[:CADU_LEN])
        rssi = -(256 - b[CADU_LEN]) if self.rssi_byte else None
        del b[:self.unit_len]
        self.stats["cadus"] += 1
        self.stats["asm_found"] += 1
        self.stats["asm_bit_errors"] += errors
        return RawCadu(cadu, rssi, now, errors)

    def _step(self, now):
        b = self.buf
        nr = self._try_noise_reply(now) or self._try_fault(now)
        if nr:
            return nr
        if len(b) < 4:
            return None

        if self.state in (self.LOCK, self.FLYWHEEL):
            errs = _hamming32(int.from_bytes(b[:4], "big"), ASM_INT)
            if errs <= self.lock_tol:
                if len(b) < self.unit_len:
                    return None
                self.state, self.misses = self.LOCK, 0
                return self._emit(now, errs)
            # Not at the expected position: an inserted noise reply, foreign
            # packet or lost octets. Flywheel a few times, then re-search.
            self.misses += 1
            self.stats["slips"] += 1
            if self.misses > self.flywheel_max:
                self._lose_lock()
            else:
                self.state = self.FLYWHEEL
                self.stats["flywheel_frames"] += 1
            # fall through to a local search from the current position

        # SEARCH: find best ASM candidate at byte alignment
        for i in range(0, len(b) - 3):
            if b[i] == 0xC1 and self.expect_noise_reply:
                if len(b) - i < 5:
                    if i:
                        self.stats["discarded_octets"] += i
                        del b[:i]
                    return None
                if b[i + 1] == 0x00 and b[i + 2] == 0x02:
                    self.stats["discarded_octets"] += i
                    del b[:i]
                    return self._try_noise_reply(now)
            if b[i] == 0xFF and b[i + 1] == 0xFF and b[i + 2] == 0xFF and 1 <= b[i + 3] <= 4:
                self.stats["discarded_octets"] += i
                del b[:i]
                return self._try_fault(now)
            errs = _hamming32(int.from_bytes(b[i:i + 4], "big"), ASM_INT)
            if errs <= self.search_tol:
                if i:
                    self.stats["discarded_octets"] += i
                    del b[:i]
                if len(b) < self.unit_len:
                    return None
                self.state = self.LOCK if self.state == self.SEARCH else self.state
                return self._emit(now, errs)
        # Nothing found: keep the last 3 octets (an ASM may straddle reads).
        drop = max(0, len(b) - 3)
        if drop:
            self.stats["discarded_octets"] += drop
            del b[:drop]
        return None
