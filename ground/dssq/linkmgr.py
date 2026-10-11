"""Adaptive Link Manager - closed-loop rate adaptation on measured link margin.

The manager works from MEASURED margin statistics (RSSI minus the receiver sensitivity of
the current rate code, dssq.link.sensitivity() with its source) and the frame error rate
- the same principle as adaptive coding and modulation in modern links, built from what
these radios offer. It is model-aware (dssq.link registry):
  * E22 UART models: rate codes 0 and 1 are aliases of 2.4 kbit/s, so the ladder starts
    at code 2 (min_effective_rate_code); SX126x: LoRa profiles 0..7 are all distinct.
    [linkmgr] min_rate absent = the model's own minimum (E22 2, SX126x 0; PB-3.2); the
    limits follow a model change ([radio] model = "auto").
  * Higher code = faster and less sensitive for every model, so one ladder logic serves all.
  * The rate is the only lever used: the E22-400T37S has no output power levels (every
    code is 37 dBm, manual v1.5); the T33D and the SX126x do have power codes, but a power
    change would alter both ends' regulatory envelope and is left to the operator (TXPWR).

Policy (evaluated every `period_s`, at most one change per `holdoff_s`):
  margin_p10 = 10th-percentile margin over the last 10 minutes (fade-aware)
  * LOW   (margin_p10 < target - 3 dB or FER > 10 %): step the rate code DOWN
  * HIGH  (margin_p10 > target + 9 dB and FER < 1 %): step the rate code UP if the margin
          predicted at the new code (sensitivity difference) stays >= target + 3 dB
Modes: OFF | ADVISE (recommendation shown on displays) | AUTO (executed through the
coordinated LINKRATE procedure; needs the station radio control path).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import link


class LinkManager:
    def __init__(self, target_margin_db: float = 6.0, period_s: float = 60.0, holdoff_s: float = 600.0,
                 min_rate: int | None = None, max_rate: int = 4, model: str | link.RadioModel | None = None,
                 measured: Mapping[tuple[str, int], float] | None = None, rx_boost: bool = True):
        self.model = link.get_model(model)
        self.measured = dict(measured or {})
        self.rx_boost = rx_boost
        self.mode = "ADVISE"
        self.target = target_margin_db
        self.period_s = period_s
        self.holdoff_s = holdoff_s
        self._cfg_min = min_rate                 # None: the model's minimum
        self._cfg_max = max_rate
        self.last_eval = -1e18
        self.last_action = -1e18
        self.recommendation: dict[str, Any] | None = None

    @property
    def min_rate(self) -> int:
        floor = self.model.min_effective_rate_code
        return floor if self._cfg_min is None else max(self._cfg_min, floor)

    @property
    def max_rate(self) -> int:
        return max(self.min_rate, min(self._cfg_max, 7))

    def _sens(self, code: int) -> float:
        return link.sensitivity(self.model, code, self.measured, self.rx_boost)[0]

    def evaluate(self, now: float, rx: Mapping[str, Any], rate_code: int, power_code: int | None) -> dict | None:
        """rx = {"windows": {"10m": {"rssi": stats}}, "frames": {"fer": FER over the same 10 min}}
        (the ReceiverStats.snapshot() layout). Returns the recommendation when
        AUTO mode should act now, else None (the recommendation is kept for the displays)."""
        if self.mode == "OFF" or now - self.last_eval < self.period_s:
            return None
        self.last_eval = now
        if not isinstance(rate_code, int) or not 0 <= rate_code <= 7:
            self.recommendation = {"action": "NONE", "why": f"rate code {rate_code!r} unknown"}
            return None
        w10 = rx["windows"]["10m"]["rssi"]
        if w10.get("n", 0) < 10:
            self.recommendation = {"action": "NONE", "why": "insufficient data (< 10 frames in 10 min)"}
            return None
        sens = self._sens(rate_code)
        margin_p10 = w10["p10"] - sens
        fer = rx["frames"]["fer"] or 0.0
        rec: dict[str, Any] = {"action": "NONE", "margin_p10_db": round(margin_p10, 1), "fer": fer,
                               "target_db": self.target, "rate_code": rate_code, "power_code": power_code,
                               "model": self.model.name}
        if margin_p10 < self.target - 3 or fer > 0.10:
            if rate_code > self.min_rate:
                rec.update(action="LINKRATE", value=rate_code - 1, why="margin low: slower, more sensitive rate")
            else:
                rec.update(why=f"margin low at the slowest usable rate (code {self.min_rate}): improve the "
                               "antennas, height or feeders")
        elif margin_p10 > self.target + 9 and fer < 0.01:
            new = rate_code + 1
            if new <= self.max_rate:
                predicted = margin_p10 - (self._sens(new) - sens)
                if predicted >= self.target + 3:
                    rec.update(action="LINKRATE", value=new,
                               why=f"margin high: faster rate (predicted {predicted:.1f} dB)")
        self.recommendation = rec
        if self.mode == "AUTO" and rec["action"] != "NONE" and now - self.last_action > self.holdoff_s:
            self.last_action = now
            return rec
        return None

    def state(self) -> dict:
        return {"mode": self.mode, "model": self.model.name, "target_margin_db": self.target,
                "recommendation": self.recommendation, "holdoff_s": self.holdoff_s,
                "rate_limits": [self.min_rate, self.max_rate]}
