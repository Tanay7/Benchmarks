"""Link-quality metrics computed by the ground station.

Measured directly
  * Packet RSSI (dBm)     — E22 appended RSSI octet, dBm = -(256 - value)
  * Noise floor (dBm)     — E22 ambient-noise register 0x00 (C0 C1 C2 C3 query)
  * Frame accounting      — MCFC continuity, RS corrections, FECF, ASM errors

Estimated (clearly labelled "est." on every display)
  * SNR_est   = RSSI - noise floor (dB). LoRa demodulates *below* the noise floor
                (required SNR is negative), so SNR_est near 0 dB is still a usable
                link; the RSSI register saturates near the floor in that regime.
  * Pr        = 10log10(10^(RSSI/10) - 10^(N/10)) (signal power with noise removed)
  * C/N0      = Pr - (N - 10log10(B))   dB-Hz, B = assumed receiver noise bandwidth
  * Eb/N0     = C/N0 - 10log10(Rb)      Rb = air data rate
  * Margin    = RSSI - S_est            S_est = sensitivity estimate for the air rate
These mirror the DSN's Pc/N0, Pd/N0 and Eb/N0 monitor quantities, but the E22
does not expose carrier/symbol-loop SNRs, so they are estimates, not measurements.
"""
from __future__ import annotations

import math
import time
from collections import deque

# LoRa-physics sensitivity ESTIMATES per E22 air-rate code (BW 125 kHz assumed,
# S = -174 + 10log10(BW) + NF(6 dB) + SNR_req(SF)). EBYTE does not publish the
# SF/BW behind each air-rate code; measure your own (docs/08 test T-RF-03).
SENSITIVITY_EST_DBM = {0: -137.0, 1: -132.0, 2: -129.0, 3: -126.0, 4: -123.0,
                       5: -120.0, 6: -117.0, 7: -114.0}
AIR_RATE_BPS = {0: 300, 1: 1200, 2: 2400, 3: 4800, 4: 9600, 5: 19200, 6: 38400, 7: 62500}


def dbm_to_mw(d):
    return 10 ** (d / 10.0)


class LinkMetrics:
    def __init__(self, air_rate_code: int = 2, noise_bw_hz: float = 125e3, history: int = 300):
        self.air_rate_code = air_rate_code
        self.noise_bw_hz = noise_bw_hz
        self.rssi = deque(maxlen=history)
        self.snr = deque(maxlen=history)
        self.hist_t = deque(maxlen=history)
        self.noise_dbm: float | None = None
        self.noise_t = 0.0
        self.last_frame_t: float | None = None
        self.first_frame_t: float | None = None
        self.aos_t: float | None = None
        self.los = True
        self.mcfc_prev: int | None = None
        self.frames_ok = 0
        self.frames_lost = 0
        self.rs_uncorrectable = 0
        self.fecf_fail = 0
        self.rs_corrected_symbols = 0
        self.rs_frames_with_corrections = 0
        self.foreign = 0
        self.info_bits = 0
        self.vc_frames: dict[int, int] = {}
        self.events: list[tuple[float, str]] = []
        self.rate_window = deque(maxlen=120)   # (t, info bits) for throughput

    # ------------------------------------------------------------------ inputs
    def on_noise(self, noise_dbm: float, t: float):
        self.noise_dbm = noise_dbm
        self.noise_t = t

    def on_frame(self, t: float, rssi: int | None, mcfc: int, vcid: int, rs_corr: int,
                 info_bytes: int):
        if self.los:
            self.los = False
            self.aos_t = t
            self.events.append((t, "AOS"))
        if self.first_frame_t is None:
            self.first_frame_t = t
        if self.mcfc_prev is not None:
            gap = (mcfc - self.mcfc_prev - 1) & 0xFF
            if gap:
                self.frames_lost += gap
        self.mcfc_prev = mcfc
        self.frames_ok += 1
        self.last_frame_t = t
        self.vc_frames[vcid] = self.vc_frames.get(vcid, 0) + 1
        if rs_corr > 0:
            self.rs_corrected_symbols += rs_corr
            self.rs_frames_with_corrections += 1
        self.info_bits += info_bytes * 8
        self.rate_window.append((t, info_bytes * 8))
        if rssi is not None:
            self.rssi.append(rssi)
            self.hist_t.append(t)
            snr = self.snr_est(rssi)
            self.snr.append(snr if snr is not None else float("nan"))

    def on_bad_cadu(self, reason: str):
        if "RS" in reason:
            self.rs_uncorrectable += 1
        elif "FECF" in reason:
            self.fecf_fail += 1
        else:
            self.foreign += 1

    def tick(self, now: float, los_timeout_s: float):
        if not self.los and self.last_frame_t and now - self.last_frame_t > los_timeout_s:
            self.los = True
            self.events.append((now, "LOS"))

    # ------------------------------------------------------------ estimators
    def snr_est(self, rssi: float) -> float | None:
        if self.noise_dbm is None:
            return None
        return rssi - self.noise_dbm

    def pr_dbm(self, rssi: float) -> float | None:
        if self.noise_dbm is None:
            return None
        diff = dbm_to_mw(rssi) - dbm_to_mw(self.noise_dbm)
        return 10 * math.log10(diff) if diff > 0 else None

    def cn0_dbhz(self, rssi: float) -> float | None:
        pr = self.pr_dbm(rssi)
        if pr is None:
            return None
        n0 = self.noise_dbm - 10 * math.log10(self.noise_bw_hz)
        return pr - n0

    def ebn0_db(self, rssi: float) -> float | None:
        c = self.cn0_dbhz(rssi)
        if c is None:
            return None
        return c - 10 * math.log10(AIR_RATE_BPS.get(self.air_rate_code, 2400))

    @property
    def sensitivity_est(self) -> float:
        return SENSITIVITY_EST_DBM.get(self.air_rate_code, -129.0)

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        last = self.rssi[-1] if self.rssi else None
        recent = list(self.rssi)[-20:]
        total = self.frames_ok + self.frames_lost
        win = [b for t, b in self.rate_window if now - t <= 60]
        span = 60.0 if win else 1.0
        cw_symbols = self.frames_ok * 232
        return {
            "los": self.los,
            "aos_t": self.aos_t,
            "since_last_frame_s": None if self.last_frame_t is None else now - self.last_frame_t,
            "rssi_dbm": last,
            "rssi_avg_dbm": sum(recent) / len(recent) if recent else None,
            "rssi_min_dbm": min(recent) if recent else None,
            "rssi_max_dbm": max(recent) if recent else None,
            "noise_dbm": self.noise_dbm,
            "noise_age_s": None if not self.noise_t else now - self.noise_t,
            "snr_est_db": None if last is None else self.snr_est(last),
            "pr_est_dbm": None if last is None else self.pr_dbm(last),
            "cn0_est_dbhz": None if last is None else self.cn0_dbhz(last),
            "ebn0_est_db": None if last is None else self.ebn0_db(last),
            "sensitivity_est_dbm": self.sensitivity_est,
            "margin_est_db": None if last is None else last - self.sensitivity_est,
            "frames_ok": self.frames_ok,
            "frames_lost": self.frames_lost,
            "fer": (self.frames_lost / total) if total else None,
            "rs_corrected_symbols": self.rs_corrected_symbols,
            "rs_frames_corrected": self.rs_frames_with_corrections,
            "rs_uncorrectable": self.rs_uncorrectable,
            "symbol_error_rate_est": (self.rs_corrected_symbols / cw_symbols) if cw_symbols else None,
            "fecf_fail": self.fecf_fail,
            "foreign_frames": self.foreign,
            "vc_frames": dict(self.vc_frames),
            "info_rate_bps": sum(win) / span,
            "air_rate_bps": AIR_RATE_BPS.get(self.air_rate_code),
            "history": {"t": list(self.hist_t)[-120:], "rssi": list(self.rssi)[-120:],
                        "snr": list(self.snr)[-120:]},
        }
