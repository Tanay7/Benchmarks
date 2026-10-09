"""Adaptive Link Manager — closed-loop link adaptation for an E22 UART link.

The E22 exposes only packet RSSI and the ambient-noise register, so the manager
works from *measured* margin statistics (RSSI minus the sensitivity estimate of
the current air rate) and the MCFC-based frame error rate — the same principle as
adaptive coding & modulation in modern links, built from what this radio offers.

Policy (evaluated every `period_s`, at most one change per `holdoff_s`):
  margin_p10 = 10th-percentile margin over the last 10 minutes (fade-aware)
  * LOW   (margin_p10 < target - 3 dB or FER > 10 %): step the air rate DOWN
  * HIGH  (margin_p10 > target + 9 dB and FER < 1 %): step the air rate UP if the
        predicted margin at the new rate stays >= target + 3 dB
The air rate is the only lever: the E22-400T37S has no output power levels (all
power codes = 37 dBm, EBYTE manual v1.5), and codes 0-1 are aliases of 2.4k, so
the usable range is codes 2 (2.4k, most sensitive) .. max_rate.
Modes: OFF | ADVISE (recommendation shown on displays) | AUTO (executed; air-rate
changes require the RCU so both ends can follow).
"""
from __future__ import annotations

from .link import MIN_EFFECTIVE_RATE_CODE, SENSITIVITY_EST_DBM


class LinkManager:
    def __init__(self, target_margin_db: float = 6.0, period_s: float = 60.0, holdoff_s: float = 600.0,
                 min_rate: int = MIN_EFFECTIVE_RATE_CODE, max_rate: int = 4):
        self.mode = "ADVISE"
        self.target = target_margin_db
        self.period_s = period_s
        self.holdoff_s = holdoff_s
        self.min_rate, self.max_rate = max(min_rate, MIN_EFFECTIVE_RATE_CODE), max_rate
        self.last_eval = 0.0
        self.last_action = -1e9
        self.recommendation: dict | None = None
        self._frames_prev = None

    def evaluate(self, now: float, rx: dict, rate_code: int, power_code: int | None) -> dict | None:
        if self.mode == "OFF" or now - self.last_eval < self.period_s:
            return None
        self.last_eval = now
        w10 = rx["windows"]["10m"]["rssi"]
        if w10.get("n", 0) < 10:
            self.recommendation = {"action": "NONE", "why": "insufficient data (< 10 frames in 10 min)"}
            return None
        sens = SENSITIVITY_EST_DBM.get(rate_code, -126.0)
        margin_p10 = w10["p10"] - sens
        fer = rx["frames"]["fer"] or 0.0
        rec = {"action": "NONE", "margin_p10_db": round(margin_p10, 1), "fer": fer,
               "target_db": self.target, "rate_code": rate_code, "power_code": power_code}
        if margin_p10 < self.target - 3 or fer > 0.10:
            if rate_code > self.min_rate:
                rec.update(action="LINKRATE", value=rate_code - 1, why="margin low: slower air rate")
            else:
                rec.update(why="margin low at 2.4k (slowest T37S rate, 37 dBm fixed): improve antennas/"
                               "height/feeders")
        elif margin_p10 > self.target + 9 and fer < 0.01:
            new = rate_code + 1
            if new <= self.max_rate:
                predicted = margin_p10 - (SENSITIVITY_EST_DBM[new] - sens)
                if predicted >= self.target + 3:
                    rec.update(action="LINKRATE", value=new,
                               why=f"margin high: faster air rate (predicted {predicted:.1f} dB)")
        self.recommendation = rec
        if self.mode == "AUTO" and rec["action"] != "NONE" and now - self.last_action > self.holdoff_s:
            self.last_action = now
            return rec
        return None

    def state(self) -> dict:
        return {"mode": self.mode, "target_margin_db": self.target, "recommendation": self.recommendation,
                "holdoff_s": self.holdoff_s, "rate_limits": [self.min_rate, self.max_rate]}
