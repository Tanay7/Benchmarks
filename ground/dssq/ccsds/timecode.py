"""CCSDS Unsegmented Time Code (CUC, 301.0-B-4) and SCLK<->SCET correlation.

Packet secondary headers carry CUC time with an *implicit* P-field 0x2E:
  0 | 010 (Level 2, agency-defined epoch) | 11 (4 coarse octets) | 10 (2 fine octets)

The spacecraft clock (SCLK) is a free-running counter since the last MCU boot.
Each boot starts a new SCLK *partition* (reported in HK), exactly like the
partitions of JPL spacecraft clocks. Ground converts SCLK to Spacecraft Event
Time (SCET, UTC) with a running linear correlation:

    SCET = ERT - OWLT - transmit_latency,   fitted as SCET = a + b * SCLK
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class CucTime:
    coarse: int
    fine: int

    @property
    def seconds(self) -> float:
        return self.coarse + self.fine / 65536.0

    def pack(self) -> bytes:
        return self.coarse.to_bytes(4, "big") + self.fine.to_bytes(2, "big")

    @classmethod
    def unpack(cls, b: bytes) -> "CucTime":
        return cls(int.from_bytes(b[0:4], "big"), int.from_bytes(b[4:6], "big"))

    @classmethod
    def from_seconds(cls, s: float) -> "CucTime":
        coarse = int(s)
        return cls(coarse, int(round((s - coarse) * 65536)) & 0xFFFF)

    def sclk_string(self, partition: int) -> str:
        """JPL-style SCLK string: partition/coarse.fine (fine in 1/65536 ticks)."""
        return f"{partition}/{self.coarse:010d}.{self.fine:05d}"


class SclkCorrelator:
    """Least-squares fit of SCET (UNIX seconds) against SCLK seconds.

    Samples come from TIMECORR packets: (SCLK at frame transmission, ERT of that
    frame). Light time and the radio's fixed latency are subtracted first.
    """

    def __init__(self, window: int = 64):
        self.samples: deque[tuple[float, float]] = deque(maxlen=window)
        self.partition: int | None = None
        self.a = 0.0
        self.b = 1.0
        self.residual_rms = 0.0

    def add(self, partition: int, sclk_s: float, scet_unix: float):
        if partition != self.partition:
            self.samples.clear()             # new SCLK partition: restart the fit
            self.partition = partition
        self.samples.append((sclk_s, scet_unix))
        self._fit()

    def _fit(self):
        n = len(self.samples)
        if n == 1:
            x, y = self.samples[0]
            self.a, self.b = y - x, 1.0
            self.residual_rms = 0.0
            return
        mx = sum(x for x, _ in self.samples) / n
        my = sum(y for _, y in self.samples) / n
        sxx = sum((x - mx) ** 2 for x, _ in self.samples)
        sxy = sum((x - mx) * (y - my) for x, y in self.samples)
        self.b = sxy / sxx if sxx > 0 else 1.0
        self.a = my - self.b * mx
        res = [(y - (self.a + self.b * x)) for x, y in self.samples]
        self.residual_rms = (sum(r * r for r in res) / n) ** 0.5

    @property
    def valid(self) -> bool:
        return len(self.samples) >= 1

    @property
    def drift_ppm(self) -> float:
        return (self.b - 1.0) * 1e6

    def scet(self, sclk_s: float) -> float | None:
        if not self.valid:
            return None
        return self.a + self.b * sclk_s
