"""Adaptive Link Manager - closed-loop rate adaptation on measured link margin.

The manager works from MEASURED margin statistics (RSSI minus the receiver sensitivity of
the current rate code, dssq.link with its source) and the frame error rate - the same
principle as adaptive coding and modulation in modern links, built from what these radios
offer. It is model-aware (dssq.link registry):
  * E22 UART models: rate codes 0 and 1 are aliases of 2.4 kbit/s, so the ladder starts
    at code 2 (min_effective_rate_code); SX126x: LoRa profiles 0..7 are all distinct.
    [linkmgr] min_rate absent = the model's own minimum (E22 2, SX126x 0; PB-3.2); the
    limits follow a model change ([radio] model = "auto").
  * Higher code = faster and less sensitive for every model, so one ladder logic serves all.
  * The rate is the only lever used: the E22-400T37S has no output power levels (every
    code is 37 dBm, manual v1.5); the T33D and the SX126x do have power codes, but a power
    change would alter both ends' regulatory envelope and is left to the operator (TXPWR).

Input (rxstats.ReceiverStats.lm_input()): the RSSI and noise statistics, frames received and
lost over the evaluation window W = max(600 s, 10 x the longest expected frame period) - a
fixed 10 minutes holds fewer than the 10 frames needed at the slow SX126x profiles (F-P1-1).

Policy (evaluated every `period_s`, at most one change per `holdoff_s`):
  sensitivity S  = the worst case (link.sensitivity_worst(), the datasheet "min") where the
                   datasheet gives one, else the typical value (D-76)
  margin_p10     = RSSI p10 over W - S (fade-aware), with the D-76 noise-floor rule
                   (rxstats.margin_assessment) applied to the p10 and the window's noise mean:
                   3-10 dB above the noise the noise is removed; < 3 dB the value is only an
                   upper bound
  * LOW   (margin_p10 < target - 3 dB or FER > 10 %): step the rate code DOWN
  * HIGH  (margin_p10 > target + 9 dB and FER < 1 %): step the rate code UP if the margin
          predicted at the new code (sensitivity difference) stays >= target + 3 dB - and
          only when the p10 RSSI is >= 10 dB above the window's noise mean (D-76): near the
          noise, or without a noise reading to check against, the RSSI cannot show that the
          margin is high, so a faster rate is never advised there
Modes: OFF | ADVISE (recommendation shown on displays) | AUTO (executed through the
coordinated LINKRATE procedure; needs the station radio control path - the GDS reports in
linkmgr.executable / why_not whether it has one, F-P1-6).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import link
from .rxstats import SIGNAL_ABOVE_NOISE_DB, margin_assessment

MIN_FRAMES = 10                        # frames in the window before the manager advises anything


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

    def _sens(self, code: int) -> tuple[float, str]:
        """(sensitivity, basis) the margins use: the worst case where one exists (D-76)."""
        worst, _ = link.sensitivity_worst(self.model, code, self.measured)
        if worst is not None:
            return worst, "worst case"
        return link.sensitivity(self.model, code, self.measured, self.rx_boost)[0], "typical"

    def evaluate(self, now: float, rx: Mapping[str, Any], rate_code: int, power_code: int | None) -> dict | None:
        """rx = rxstats.ReceiverStats.lm_input(): {"window_s", "rssi": stats, "noise": stats,
        "frames", "lost", "fer"}. Returns the recommendation when AUTO mode should act now,
        else None (the recommendation is kept for the displays)."""
        if self.mode == "OFF" or now - self.last_eval < self.period_s:
            return None
        self.last_eval = now
        window_s = rx.get("window_s")
        if not isinstance(rate_code, int) or not 0 <= rate_code <= 7:
            self.recommendation = {"action": "NONE", "why": f"rate code {rate_code!r} unknown", "window_s": window_s}
            return None
        rs = rx.get("rssi") or {}
        if rs.get("n", 0) < MIN_FRAMES:
            span = f"{window_s:.0f} s" if isinstance(window_s, (int, float)) else "the window"
            self.recommendation = {"action": "NONE", "window_s": window_s,
                                   "why": f"insufficient data (< {MIN_FRAMES} frames in {span})"}
            return None
        sens, basis = self._sens(rate_code)
        noise = (rx.get("noise") or {}).get("mean") if (rx.get("noise") or {}).get("n") else None
        a = margin_assessment(rs["p10"], noise, sens, None)
        state = a["state"]
        # at the noise floor the margin is only an upper bound: it can still show "low"
        margin_p10 = a["margin_db"] if a["margin_db"] is not None else a["max_db"]
        fer = rx.get("fer") or 0.0
        rec: dict[str, Any] = {"action": "NONE", "margin_p10_db": round(margin_p10, 1), "fer": fer,
                               "target_db": self.target, "rate_code": rate_code, "power_code": power_code,
                               "model": self.model.name, "window_s": window_s, "frames": rx.get("frames"),
                               "lost": rx.get("lost"), "rssi_p10_dbm": rs["p10"], "noise_mean_dbm": noise,
                               "noise_state": state, "sensitivity_dbm": sens, "sensitivity_basis": basis}
        if margin_p10 < self.target - 3 or fer > 0.10:
            if rate_code > self.min_rate:
                rec.update(action="LINKRATE", value=rate_code - 1, why="margin low: slower, more sensitive rate")
            else:
                rec.update(why=f"margin low at the slowest usable rate (code {self.min_rate}): improve the "
                               "antennas, height or feeders")
        elif margin_p10 > self.target + 9 and fer < 0.01:
            new = rate_code + 1
            if state != "SIGNAL":
                rec.update(why=("no faster rate: the p10 RSSI is less than "
                                f"{SIGNAL_ABOVE_NOISE_DB:.0f} dB above the noise, so it cannot show a high margin"
                                if noise is not None else
                                "no faster rate: no noise reading in the window to check the RSSI against"))
            elif new <= self.max_rate:
                predicted = margin_p10 - (self._sens(new)[0] - sens)
                if predicted >= self.target + 3:
                    rec.update(action="LINKRATE", value=new,
                               why=f"margin high: faster rate (predicted {predicted:.1f} dB)")
        self.recommendation = rec
        if self.mode == "AUTO" and rec["action"] != "NONE" and now - self.last_action > self.holdoff_s:
            self.last_action = now
            return rec
        return None

    def state(self, why_not: str | None = None) -> dict:
        """Snapshot linkmgr. why_not: why the GDS cannot execute a LINKRATE (no radio
        control path, F-P1-6); None when it can."""
        return {"mode": self.mode, "model": self.model.name, "target_margin_db": self.target,
                "recommendation": self.recommendation, "holdoff_s": self.holdoff_s,
                "rate_limits": [self.min_rate, self.max_rate], "executable": why_not is None, "why_not": why_not}
