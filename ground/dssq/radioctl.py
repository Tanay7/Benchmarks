"""Ground radio control through the Radio Control Unit (RCU).

The E22-400TBH-02 board's USB port carries only TXD/RXD. With its M0/M1 jumpers
removed and those pins driven by the GIGA R1 (front-panel MCU, which also reads
AUX), the GDS gains full software control of the ground E22:

  * configuration read-back / volatile writes (C1 / C2) — verifies the station
  * RF spectrum survey: step channels, read the ambient-noise register
  * Wake-on-Radio transmission (WOR-transmitter role, long preamble) to wake a
    hibernating spacecraft
  * the ground half of coordinated air-rate / channel changes

All procedures run in the GDS main thread (they briefly take the radio out of
NORMAL mode, so they are scheduled right after a frame has been received).
"""
from __future__ import annotations

import time

from .radio.e22 import describe

NORMAL, WOR, CONFIG, SLEEP = 0, 1, 2, 3


class RadioManager:
    def __init__(self, radio, rcu, clock=time.time, sleep=time.sleep):
        self.radio = radio
        self.rcu = rcu                   # object with set_mode(int)->bool (GIGA or simulator)
        self.clock = clock
        self.sleep = sleep
        self.regs: bytes | None = None
        self.last_scan: dict | None = None
        self.log: list[str] = []

    @property
    def available(self) -> bool:
        return self.rcu is not None

    # ------------------------------------------------------------- primitives
    def _mode(self, m: int) -> bool:
        ok = self.rcu.set_mode(m)
        self.sleep(0.05)                 # EBYTE: >= 2 ms after a mode change; AUX settle
        return ok

    def _wait(self, prefix: bytes, n: int, timeout: float = 1.0) -> bytes | None:
        buf, t0 = b"", self.clock()
        while self.clock() - t0 < timeout:
            buf += self.radio.read()
            i = buf.find(prefix)
            if i >= 0 and len(buf) - i >= n:
                return buf[i:i + n]
            self.sleep(0.01)
        return None

    def read_config(self) -> dict | None:
        if not self.available or not self._mode(CONFIG):
            return None
        self.radio.write(bytes([0xC1, 0x00, 0x09]))
        r = self._wait(bytes([0xC1, 0x00, 0x09]), 12)
        self._mode(NORMAL)
        if r is None:
            self.log.append("ground E22 config read: no reply")
            return None
        self.regs = r[3:]
        return describe(self.regs)

    def write_regs(self, start: int, values: bytes) -> bool:
        """Volatile register write (C2) with echo check, back to NORMAL mode."""
        if not self.available or not self._mode(CONFIG):
            return False
        self.radio.write(bytes([0xC2, start, len(values)]) + values)
        r = self._wait(bytes([0xC1, start, len(values)]), 3 + len(values))
        self._mode(NORMAL)
        ok = r is not None and r[3:] == values
        if ok and self.regs is not None:
            regs = bytearray(self.regs)
            regs[start:start + len(values)] = values
            self.regs = bytes(regs)
        return ok

    def set_channel(self, ch: int) -> bool:
        return self.write_regs(0x05, bytes([ch]))

    def set_air_rate(self, code: int) -> bool:
        if self.regs is None and self.read_config() is None:
            return False
        reg0 = (self.regs[3] & ~0x07) | (code & 7)
        return self.write_regs(0x03, bytes([reg0]))

    def noise_dbm(self) -> int | None:
        self.radio.write(bytes([0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x02]))
        r = self._wait(bytes([0xC1, 0x00, 0x02]), 5, 0.5)
        return None if r is None else -(256 - r[3])

    # ------------------------------------------------------------ procedures
    def survey(self, first: int, last: int) -> dict | None:
        """Ground RF survey: ambient noise on each channel (the station is deaf meanwhile)."""
        if not self.available:
            return None
        if self.regs is None and self.read_config() is None:
            return None
        home = self.regs[5]
        t0 = self.clock()
        noise = []
        for ch in range(first, last + 1):
            if not self.set_channel(ch):
                noise.append(None)
                continue
            self.sleep(0.06)
            noise.append(self.noise_dbm())
        self.set_channel(home)
        valid = [(n, first + i) for i, n in enumerate(noise) if n is not None]
        best = min(valid)[1] if valid else None
        self.last_scan = {"t": self.clock(), "first": first, "noise": noise, "quietest": best,
                          "duration_s": self.clock() - t0, "home": home}
        return self.last_scan

    def wor_transmit(self, cltu: bytes, wor_cycle_code: int = 3) -> bool:
        """Send one CLTU as a Wake-on-Radio transmitter (preamble spans the WOR cycle)."""
        if not self.available:
            return False
        if self.regs is None and self.read_config() is None:
            return False
        reg3 = self.regs[6]
        wor_reg3 = (reg3 & ~0x0F) | 0x08 | (wor_cycle_code & 7)     # WOR role = transmitter
        if not self.write_regs(0x06, bytes([wor_reg3])):
            return False
        self._mode(WOR)
        self.radio.write(cltu)
        self.sleep((wor_cycle_code + 1) * 0.5 + 1.0)                 # preamble + payload
        self._mode(NORMAL)
        return self.write_regs(0x06, bytes([reg3]))                  # restore receiver role
