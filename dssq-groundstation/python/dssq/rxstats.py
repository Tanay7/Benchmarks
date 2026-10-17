"""Ground receiver statistics engine (feeds the RECEIVER page of the web UI, Phase B PB-5.3.2).

Every number here is either MEASURED (radio RSSI / SNR / frequency error / noise readings,
the byte stream, the frame synchronizer, the Reed-Solomon decoder, frame and packet
counters), DERIVED from measurements with a stated assumption (keys ending in "_est", or a
*_source label), or CONFIGURED (station.toml). Nothing is invented: a missing input gives
None and the UI shows "-".

Radio models (dssq.link): the E22 UART modules report an integer packet RSSI and an
integer ambient-noise register (1 dB steps); the SX126x reports per packet RssiPkt in
0.5 dB steps, the SNR (SnrPkt / 4, 0.25 dB steps) and a LoRa frequency-error estimate, and
its noise floor is the median of 8 instantaneous RSSI samples (rf_noise). No single reading
is accurate to 0.0001 dB: window statistics carry n and the standard error of the mean, and
the absolute accuracy is set by [radio] rssi_cal_offset_db (docs/08 T-RF-02).

Windows: "1m" (60 s), "10m" (600 s) and "pass" (since AOS; cleared at every AOS and
capped at PASS_WINDOW_MAX samples, `truncated` once older samples were discarded - the v2
"pass" window was never trimmed nor reset). Every window is also capped in length. The
AOS frame's own reception / codeblock samples (added before the frame is recognised as
the AOS) are kept in the new pass, and the inter-pass gap is never an inter-arrival time.

Frame loss (D-76): the MCFC jump of the AOS frame (frames sent while out of view, or a
spacecraft reboot; modulo 256, so it is not even a count) is NOT a frame lost: it is
`frames.aos_gap_frames`, and the per-VC loss baselines restart at AOS. `frames.lost` /
`frames.fer` count the frames lost inside the passes since the GDS started; `frames.fer_10m`
is the same ratio over the last 10 minutes of reception. Every FER comes with its exact
(Clopper-Pearson) 95 % bounds from the frames received and lost (fer_block(): two-sided
[lo, hi] and the one-sided upper bound; with no frame lost in n the one-sided bound is
1 - 0.05^(1/n), the two-sided upper 1 - 0.025^(1/n), e.g. 0 of 178: 1.67 % and 2.05 %): a FER
from a few frames is not a precise number.

RSSI readings (D-76): an E22 RSSI octet at either end of its range (0x00 reads -256 dBm,
0xFF reads -1 dBm; record 2026-10-13-A: both, alternating, with the two radios on one bench)
is an OVERLOAD - the received signal is too strong to measure (top of the scale), not a
power of -256 dBm, and nothing says how far above the scale it is; a reading below
RSSI_FLOOR_DBM is INVALID. Neither is a received power: they are counted (rf.rssi_overloads,
rf.rssi_invalid) and kept out of every window, histogram, series, margin, path loss, SNR
estimate and TDM record; rf.rssi_state says which. The E22 ambient-noise register is the same
kind of octet: such a noise reading is counted in rf.noise_rejected and not used.

Received power in watts (D-76): the "rssi" and "noise" windows (POWER_METRICS) also carry
mean_mw, the linear mean of the per-reading powers, (1/n) sum 10^(x_i / 10) mW, over the same
accepted readings as their dB statistics (an overload or invalid reading is in neither). It is
not 10^(mean / 10): the dB mean is the mean of the logarithms, i.e. the geometric mean of the
power, which is lower whenever the power fluctuates (-100 and -110 dBm: linear mean 5.5e-11 mW,
10^(-105 / 10) = 3.2e-11 mW). min, max and last are readings and convert to watts directly;
p10 / p50 / p90 are interpolated in dB between two neighbouring readings, so their watt value
(the page converts it) lies between those readings' powers. SEM, std and fade_depth exist in
dB only. Every watt value follows the calibrated dBm reading ([radio] rssi_cal_offset_db): it
is as accurate as the radio's RSSI and no more.

Margin at the noise floor (D-76): the RSSI the radios report is the power in the receiver
bandwidth, signal plus noise, and LoRa decodes below the noise, so near the sensitivity
limit RSSI - sensitivity measures the noise, not the signal (margin_assessment(); R = RSSI,
N = the noise reading of the same pass, both calibrated):
  R - N >= 10 dB   SIGNAL         S >= 9 N: the noise adds <= 10 log10(10/9) = 0.46 dB,
                                  below the E22's 1 dB step - the RSSI is the signal
  3 <= R - N < 10  NEAR_NOISE     the noise reading is removed: signal power est.
                                  S = 10 log10(10^(R/10) - 10^(N/10)) (pr_est_dbm)
  R - N < 3 dB     NOISE_FLOOR    S < N: the margin is not measurable; it is at most
                                  N - sensitivity (an upper bound, never shown as a margin)
  no N in the pass NOISE_UNKNOWN  the RSSI, not checked against the noise floor
Margins are given against the worst-case sensitivity (link.sensitivity_worst(), the
datasheet "min") and the typical one; the worst case is the one the displays colour.

Windows that depend on the frame period (set_frame_period(), from the GDS's longest
expected frame period P): the frame / information rates span max(60 s, 5 P), the link
manager's window max(600 s, 10 P) - at the slow SX126x profiles a fixed minute holds 0 or 1
frame and ten minutes fewer than the 10 frames the link manager needs.

Data logger (contract 39): interval_row() is the 1 Hz rx_stats row the GDS archives - the
receptions of the last interval (count; mean RSSI, SNR and frequency error of those
receptions, None without one), the current noise floor while it is fresh, the link margin of
that mean RSSI against the typical sensitivity (None where the noise-floor rule above says it
is not measurable), the 10-minute FER, the counter increments of the interval (RS symbols
corrected, uncorrectable codeblocks, frames received / lost) and the AFC / Doppler state.

Free-space path loss (exact constant): FSPL = 20 log10(4 pi d f / c), d in m, f in Hz;
20 log10(4 pi 1e9 / c) = 32.44778 dB for d in km and f in MHz (not 32.44).
"""
from __future__ import annotations

import functools
import math
import statistics
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

from . import link
from .ccsds.tm import FrameLossTracker

C_M_S = link.C_M_S
C_KM_S = C_M_S / 1000.0
PASS_WINDOW_MAX = 20_000            # >= 8 h at the fastest E22 frame period of 1.5 s
TIME_WINDOW_MAX = 20_000            # length cap of the 1-min / 10-min windows (safety bound)
STALE_PAIR_DEFAULT_S = 90.0         # RSSI / noise pairing limit before the first tick ([gds] los_timeout_s)
FER_WINDOW_S = 600.0                # the "10 min" FER window (frames.fer_10m)
RATE_WINDOW_MIN_S = 60.0            # frame / information rates: max(60 s, 5 expected frame periods)
RATE_WINDOW_PERIODS = 5.0
LM_WINDOW_MIN_S = 600.0             # link-manager window: max(600 s, 10 expected frame periods)
LM_WINDOW_PERIODS = 10.0
# E22 RSSI octet x reads -(256 - x) dBm: 0x00 and 0xFF are the ends of its range (V-70, L-45).
E22_RSSI_OCTET_ENDS_DBM = (-256.0, -1.0)
RSSI_FLOOR_DBM = link.MEASURED_RANGE_DBM[0]       # below -160 dBm no receiver here reports a signal
SIGNAL_ABOVE_NOISE_DB = 10.0        # noise adds <= 10 log10(10/9) = 0.46 dB: below the E22's 1 dB step
NOISE_FLOOR_ABOVE_NOISE_DB = 3.0    # RSSI - noise < 3 dB: the signal is below the noise reading
OVERLOAD_RECENT_S = 600.0           # an overload within this long keeps the YELLOW alarm
# The operator text of an OVERLOAD (dashboard, event log, console, alarm). The +10 dBm is the
# module's maximum receiver input ("blocking power" 10 dBm, T37S manual v1.5 2.1: "do not
# communicate at close range with high-power radio frequency"); an overload says only that
# the input is above the top of the RSSI scale, not that it exceeds that limit.
OVERLOAD_TEXT = ("received signal too strong to measure: increase distance or add attenuation "
                 "(receiver damage limit +10 dBm)")
FER_CONFIDENCE = 0.95               # exact (Clopper-Pearson) bounds of every FER shown
MARGIN_STATES = ("SIGNAL", "NEAR_NOISE", "NOISE_FLOOR", "NOISE_UNKNOWN")
WINDOWS = (("1m", 60.0), ("10m", 600.0), ("pass", None))
METRICS = ("rssi", "noise", "snr_est", "snr_meas", "freq_err", "iat", "rs", "asm")
POWER_METRICS = ("rssi", "noise")   # received powers in dBm: their windows also carry mean_mw (D-76)
SERIES_LEN = 300
EVENT_LOG_LEN = 20
FREQ_STATE_SAMPLES = 3              # freq_offset_state = median |error| of the last 3 packets
RS_PARITY_SYMBOLS_PER_CODEWORD = 232   # symbols per shortened codeword (SER denominator, v2)
CADU_BITS = link.CADU_OCTETS * 8
E22_RSSI_RESOLUTION_DB = 1.0        # E22 manuals: dBm = -(256 - value)
SX126X_RSSI_RESOLUTION_DB = 0.5     # SX1261/2 DS GetPacketStatus: RssiPkt / 2
FREQ_HIST_DEFAULT_HZ = 5000.0       # +-5 kHz when no FERR_L applies
FREQ_HIST_STEP_HZ = 100.0
# Counters whose increment per logger interval goes into rx_stats (row key -> attribute).
ROW_COUNTERS = {"n_rx": "receptions", "rs_symbols": "sym_corrected", "cb_uncorrectable": "cb_uncorrectable",
                "frames_ok": "frames_ok", "frames_lost": "frames_lost"}


def at_resolution(v: float | None, step: float) -> str | None:
    """A received-power reading (or a difference of two) as text at the radio's resolution, no
    finer (D-70): whole dB for the E22, 0.5 / 0.1 dB steps for the SX126x. None stays None."""
    if v is None or not math.isfinite(v) or not step > 0.0:
        return None
    q = math.floor(v / step + 0.5) * step + 0.0        # half up, as the dashboard's Math.round; never "-0"
    return f"{q:.0f}" if step >= 1.0 else f"{q:.1f}"


def linear_mean_mw(dbm: list[float]) -> float | None:
    """The linear mean of received powers given in dBm, in mW: (1/n) sum 10^(x_i / 10) (D-76,
    module docstring). It is not 10^(mean(x) / 10), the geometric mean, which is lower whenever
    the readings differ. None for no reading, or a sum beyond the float range (no radio here
    reports such a power)."""
    if not dbm:
        return None
    try:
        total = math.fsum([10.0 ** (x / 10.0) for x in dbm])        # link.dbm_to_mw, inlined (snapshot path)
    except OverflowError:
        return None
    return total / len(dbm) if math.isfinite(total) else None


def fspl_db(distance_m: float, freq_hz: float) -> float | None:
    """Free-space path loss 20 log10(4 pi d f / c); None for d <= 0."""
    if not distance_m > 0 or not freq_hz > 0:
        return None
    return 20.0 * math.log10(4.0 * math.pi * distance_m * freq_hz / C_M_S)


def range_for_loss_m(loss_db: float, freq_hz: float) -> float:
    """Inverse of fspl_db: the distance at which free space costs `loss_db`."""
    return C_M_S / (4.0 * math.pi * freq_hz) * 10.0 ** (loss_db / 20.0)


def classify_rssi(raw_dbm: float | None, octet_radio: bool) -> str | None:
    """"OK", "OVERLOAD" (an E22 octet at either end of its range) or "INVALID" (below
    RSSI_FLOOR_DBM, or not a finite number) for one raw reading; None without a reading.
    octet_radio: the reading came from an E22 RSSI octet (module docstring)."""
    if raw_dbm is None:
        return None
    if not isinstance(raw_dbm, (int, float)) or isinstance(raw_dbm, bool) or not math.isfinite(raw_dbm):
        return "INVALID"
    if octet_radio and float(raw_dbm) in E22_RSSI_OCTET_ENDS_DBM:
        return "OVERLOAD"
    return "INVALID" if raw_dbm < RSSI_FLOOR_DBM else "OK"


def signal_power_dbm(rssi: float | None, noise: float | None) -> float | None:
    """Signal power without the noise (both calibrated), dBm: 10 log10(10^(R/10) - 10^(N/10));
    None when the RSSI is not above the noise."""
    if rssi is None or noise is None:
        return None
    d = link.dbm_to_mw(rssi) - link.dbm_to_mw(noise)
    return 10 * math.log10(d) if d > 0 else None


def margin_assessment(rssi: float | None, noise: float | None, sens: float | None,
                      sens_worst: float | None) -> dict[str, Any]:
    """The D-76 noise-floor rule (module docstring) for one received power `rssi` (calibrated,
    None = no valid reading) and the noise reading of the same pass (None = none). Returns
    state, basis, the power the margins use (power_dbm), the margins against the typical
    and the worst-case sensitivity (None when not measurable) and, at the noise floor, their
    upper bounds noise - sensitivity (max_db, worst_max_db)."""
    out: dict[str, Any] = {"state": None, "basis": None, "power_dbm": None, "margin_db": None,
                           "worst_db": None, "max_db": None, "worst_max_db": None}
    if rssi is None:
        return out
    if noise is None:
        out.update(state="NOISE_UNKNOWN", basis="RSSI, not checked against the noise floor (no noise reading in "
                                                "this pass)", power_dbm=rssi)
    elif rssi - noise >= SIGNAL_ABOVE_NOISE_DB:
        out.update(state="SIGNAL", basis="RSSI (>= 10 dB above the noise reading)", power_dbm=rssi)
    elif rssi - noise >= NOISE_FLOOR_ABOVE_NOISE_DB:
        out.update(state="NEAR_NOISE", basis="signal power est.: RSSI with the noise reading removed (RSSI 3-10 dB "
                                             "above the noise)", power_dbm=signal_power_dbm(rssi, noise))
    else:
        out.update(state="NOISE_FLOOR", basis="not measurable: RSSI < 3 dB above the noise reading (the signal is "
                                              "below the noise); upper bound noise - sensitivity",
                   max_db=_sub(noise, sens), worst_max_db=_sub(noise, sens_worst))
        return out
    p = out["power_dbm"]
    out.update(margin_db=_sub(p, sens), worst_db=_sub(p, sens_worst))
    return out


def margin_colour_db(a: Mapping[str, Any]) -> float | None:
    """The margin the displays colour (D-76): against the worst-case sensitivity where the
    datasheet gives one, else the typical one; None when not measurable."""
    return a["worst_db"] if a["worst_db"] is not None else a["margin_db"]


# ------------------------------------------------- binomial confidence interval
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz; W. H. Press et
    al., Numerical Recipes, 3rd ed., 6.4)."""
    tiny, eps = 1e-300, 1e-15
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 10_000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b), a, b > 0, 0 <= x <= 1 (pure Python)."""
    if not (a > 0 and b > 0):
        raise ValueError("a and b must be positive")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def beta_ppf(p: float, a: float, b: float) -> float:
    """Quantile of the Beta(a, b) distribution: the x with I_x(a, b) = p, by bisection
    (I_x is monotonic in x) to below 1e-15."""
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in 0..1")
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-15:
            break
    return 0.5 * (lo + hi)


@functools.lru_cache(maxsize=512)
def clopper_pearson(x: int, n: int, confidence: float = FER_CONFIDENCE) -> tuple[float, float, float]:
    """Exact (Clopper-Pearson) bounds of a binomial proportion x / n, 0 <= x <= n, n >= 1:
    (two-sided lower, two-sided upper, one-sided upper) at `confidence` c, alpha = 1 - c:
      two-sided  lo = Beta(alpha/2; x, n-x+1)   hi = Beta(1-alpha/2; x+1, n-x)
      one-sided  hi = Beta(1-alpha; x+1, n-x)   (the rate is below it with confidence c)
    with the closed forms at the ends: x = 0 gives lo = 0, hi = 1 - (alpha/2)^(1/n) and the
    one-sided 1 - alpha^(1/n) (about 3/n at 95 %, the "rule of three"); x = n gives
    lo = (alpha/2)^(1/n), hi = 1."""
    if isinstance(x, bool) or isinstance(n, bool) or not 0 <= x <= n or n < 1 or not 0.0 < confidence < 1.0:
        raise ValueError("need integers 0 <= x <= n, n >= 1 and a confidence in (0, 1)")
    alpha = 1.0 - confidence
    if x == 0:
        return 0.0, 1.0 - (alpha / 2.0) ** (1.0 / n), 1.0 - alpha ** (1.0 / n)
    if x == n:
        return (alpha / 2.0) ** (1.0 / n), 1.0, 1.0
    return (beta_ppf(alpha / 2.0, x, n - x + 1), beta_ppf(1.0 - alpha / 2.0, x + 1, n - x),
            beta_ppf(1.0 - alpha, x + 1, n - x))


def fer_block(lost: int, received: int, confidence: float = FER_CONFIDENCE) -> dict[str, Any] | None:
    """A frame error rate with its exact bounds (module docstring): lost / (lost + received)
    and clopper_pearson() of it, every bound labelled one- or two-sided; None without a
    frame. Keys: lost, received, fer, confidence, two_sided_lo, two_sided_hi, one_sided_hi."""
    x, r = int(lost), int(received)
    if x < 0 or r < 0:
        raise ValueError("lost and received must be counts >= 0")
    n = x + r
    if n == 0:
        return None
    lo, hi, up = clopper_pearson(x, n, confidence)
    return {"lost": x, "received": r, "fer": x / n, "confidence": confidence,
            "two_sided_lo": lo, "two_sided_hi": hi, "one_sided_hi": up}


class Window:
    """Sample store with summary statistics. horizon_s = None: a pass window (no time
    trimming, capped at maxlen, `truncated` reported). power = the samples are received
    powers in dBm: stats() adds mean_mw, their linear mean (module docstring, D-76)."""

    def __init__(self, horizon_s: float | None, maxlen: int, power: bool = False):
        self.h = horizon_s
        self.d: deque[tuple[float, float]] = deque(maxlen=maxlen)
        self.discarded = 0
        self.power = power

    def add(self, t: float, v: float | None) -> None:
        if v is None or isinstance(v, bool) or not math.isfinite(v):
            return
        if len(self.d) == self.d.maxlen:
            self.discarded += 1
        self.d.append((t, float(v)))
        self._trim(t)

    def clear(self) -> None:
        self.d.clear()
        self.discarded = 0

    def clear_before(self, t: float) -> None:
        """Drops the samples older than t (a new pass keeps the AOS frame's own samples)."""
        kept = [x for x in self.d if x[0] >= t]
        self.clear()
        self.d.extend(kept)

    def _trim(self, now: float) -> None:
        if self.h is None:
            return
        while self.d and now - self.d[0][0] > self.h:      # bounded by maxlen
            self.d.popleft()

    def values(self, now: float | None = None) -> list[float]:
        if now is not None:
            self._trim(now)
        return [x for _, x in self.d]

    def stats(self, now: float | None = None) -> dict[str, Any]:
        v = self.values(now)
        out: dict[str, Any] = {"n": len(v)}
        if self.h is None:
            out["truncated"] = self.discarded > 0
        if not v:
            return out
        s = sorted(v)

        def pct(p: float) -> float:
            k = (len(s) - 1) * p
            f, c = math.floor(k), math.ceil(k)
            return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)

        n = len(v)
        mean = math.fsum(v) / n                                     # = statistics.fmean
        # Two-pass sum of squares with exactly rounded sums: the statistics module's exact
        # rational arithmetic gives the same 4 dp at a fraction of the cost (snapshot path).
        ss = math.fsum((x - mean) * (x - mean) for x in v) if n > 1 else 0.0
        sd = math.sqrt(ss / (n - 1)) if n > 1 else None             # sample standard deviation
        out.update({"mean": mean, "min": s[0], "max": s[-1],
                    "std": math.sqrt(ss / n) if n > 1 else 0.0,     # population standard deviation
                    "sem": None if sd is None else sd / math.sqrt(n),   # standard error of the mean
                    "p10": pct(0.10), "p50": pct(0.50), "p90": pct(0.90), "last": v[-1],
                    "fade_depth": pct(0.90) - pct(0.10)})
        if self.power:
            out["mean_mw"] = linear_mean_mw(v)
        return out

    def brief(self, now: float | None = None) -> dict[str, Any]:
        """{mean, sem, min, max, n} (contract 10 rf.snr_db / rf.freq_err_hz)."""
        st = self.stats(now)
        return {k: st.get(k) for k in ("mean", "sem", "min", "max")} | {"n": st["n"]}


class Hist:
    def __init__(self, lo: float, hi: float, step: float):
        self.lo, self.hi, self.step = lo, hi, step
        self.bins = [0] * (round((hi - lo) / step) + 1)

    def add(self, v: float | None) -> None:
        if v is None or not math.isfinite(v):
            return
        i = int((min(max(v, self.lo), self.hi) - self.lo) / self.step)
        self.bins[min(i, len(self.bins) - 1)] += 1

    def as_dict(self) -> dict[str, Any]:
        return {"lo": self.lo, "step": self.step, "bins": list(self.bins)}


def _sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _number(v: object) -> float | None:
    """A telemetry value as a float; None for anything else (bool, None, text)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class ReceiverStats:
    """Receiver statistics for one station. All inputs come from the GDS radio thread
    under the GDS lock; snapshot() is called under the same lock."""

    def __init__(self, cfg: Mapping[str, Any]):
        r, st, sc = cfg["radio"], cfg["station"], cfg["spacecraft"]
        self.cfg_noise_bw_hz = float(r["noise_bw_hz"])
        self.rssi_cal_db = float(r["rssi_cal_offset_db"])
        self.offset_cal_hz = float(r["freq_offset_cal_hz"])
        self.offset_calibrated = bool(r["freq_offset_calibrated"])
        self.gr_dbi = float(st["antenna_gain_dbi"])
        self.rx_loss_db = float(st["rx_line_loss_db"])
        self.gt_dbi = float(sc["antenna_gain_dbi"])
        self.tx_loss_db = float(sc["tx_line_loss_db"])
        self.distance_km = float(sc["distance_km"])
        self.t0 = time.time()
        self.w = {name: {m: Window(h, TIME_WINDOW_MAX if h is not None else PASS_WINDOW_MAX, m in POWER_METRICS)
                         for m in METRICS}
                  for name, h in WINDOWS}
        self.rssi_hist = Hist(-150.0, -20.0, 5.0)
        self.snr_hist = Hist(-30.0, 60.0, 2.0)
        self.rs_hist = [0] * 17
        self.asm_hist = [0] * 5
        self.series: dict[str, deque] = {k: deque(maxlen=SERIES_LEN)
                                         for k in ("t", "rssi", "noise", "snr_est", "snr_meas", "freq_err",
                                                   "margin", "rs")}
        # last reception (CADU or packet); rssi_dbm is None unless the reading is a received
        # power (rssi_state "OK"), rssi_raw_dbm is the reading as the radio reported it
        self.last_rx: dict[str, Any] = {"rssi_dbm": None, "rssi_raw_dbm": None, "rssi_state": None, "snr_db": None,
                                        "freq_err_hz": None, "crc_ok": None, "t": None}
        self.rssi_overloads = 0
        self.rssi_invalid = 0
        self.overload_last_t: float | None = None
        self.overload_recent = Window(OVERLOAD_RECENT_S, 10_000)      # (t, 1) per overload reading
        self.noise_rejected = 0        # noise-register octets at the end of their range / below the floor
        self._ferr_recent: deque[float] = deque(maxlen=FREQ_STATE_SAMPLES)
        self.receptions = 0            # radio units received (on_reception), for the logger's n_rx
        self._row_prev: dict[str, int] = dict.fromkeys(ROW_COUNTERS, 0)
        self.packets_ok = 0
        self.packets_crc_fail = 0
        # noise
        self.noise_dbm: float | None = None
        self.noise_t = 0.0
        self.noise_queries = 0
        self.noise_replies = 0
        self.noise_reply_t: float | None = None       # last noise reply, used or rejected
        # serial link (E22)
        self.serial_octets = 0
        self.serial_rate = Window(10.0, 10_000)
        # pass state
        self.last_frame_t: float | None = None
        self.aos_t: float | None = None
        self.los = True
        self.passes = 0
        self.lock_time_s = 0.0
        self.los_timeout_s: float | None = None
        self.hibernating = False
        self._last_tick: float | None = None
        # decoder
        self.codeblocks = 0
        self.cb_clean = 0
        self.cb_corrected = 0
        self.cb_uncorrectable = 0
        self.sym_corrected = 0
        self.max_corr = 0
        self.fecf_fail = 0
        self.foreign = 0
        self.rs_rescued = 0
        self.rs_rescue_failed = 0
        self.rice_errors = 0
        self.decom_errors = 0
        self.sdls_rejected: dict[str, int] = dict.fromkeys(("BAD_MAC", "BAD_IV", "BAD_SPI", "BAD_LENGTH",
                                                            "REPLAY"), 0)
        # frames
        self.loss = FrameLossTracker()
        self.vc_loss: dict[int, int] = {}
        self.frames_ok = 0
        self.frames_lost = 0           # lost INSIDE passes (an AOS MCFC jump is aos_gap_frames)
        self.aos_gap_frames = 0        # MCFC jumps at AOS: sent while out of view (mod 256), or a reboot
        self.fer_lost = Window(FER_WINDOW_S, TIME_WINDOW_MAX)     # (t, frames lost before this frame)
        self.oid_frames = 0
        self.by_format = {"TM": 0, "USLP": 0}
        self.per_vc: dict[int, dict[str, int]] = {}
        self.data_octets = 0           # non-idle packet octets delivered
        self.field_octets = 0          # data-field octets of the data-VC frames received
        self.frame_period_s: float | None = None                  # longest expected (set_frame_period)
        self.info_rate = Window(RATE_WINDOW_MIN_S, 10_000)
        self.frame_rate = Window(RATE_WINDOW_MIN_S, 10_000)
        # link-manager window (F-P1-1): RSSI, noise and frames lost over max(600 s, 10 P)
        self.lm_w = {k: Window(LM_WINDOW_MIN_S, TIME_WINDOW_MAX) for k in ("rssi", "noise", "lost")}
        # uplink
        self.uplink: dict[str, Any] = {"cltus": 0, "octets": 0, "last_t": None, "windows": 0,
                                       "rejected_too_long": 0, "framing": str(cfg["link"]["tc_framing"]).upper()}
        self.sc_rf: dict[str, Any] = {}          # latest real-time spacecraft RF packet
        self.pending_events: list[tuple[float, str]] = []
        self.event_log: deque[dict[str, Any]] = deque(maxlen=EVENT_LOG_LEN)
        self.settings = link.RadioSettings.from_config(r)
        self.freq_hist = Hist(-FREQ_HIST_DEFAULT_HZ, FREQ_HIST_DEFAULT_HZ, FREQ_HIST_STEP_HZ)
        self.set_settings(self.settings)

    # ------------------------------------------------------------------ settings
    def set_settings(self, s: link.RadioSettings) -> None:
        """Station radio configuration (changes with LINKRATE / LINKCHAN)."""
        self.settings = s
        lim = s.freq_offset_limit_hz or FREQ_HIST_DEFAULT_HZ
        if (self.freq_hist.lo, self.freq_hist.hi) != (-lim, lim):
            self.freq_hist = Hist(-lim, lim, FREQ_HIST_STEP_HZ)

    @property
    def packet_mode(self) -> bool:
        return self.settings.model.packet_mode

    @property
    def profile(self) -> link.LoRaProfile | None:
        return link.SX126X_PROFILES[self.settings.rate_code] if self.packet_mode else None

    @property
    def noise_bw(self) -> tuple[float, str]:
        p = self.profile
        return (p.bw_hz, "profile BW") if p is not None else (self.cfg_noise_bw_hz, "assumed")

    def calibrated(self, raw_dbm: float | None) -> float | None:
        return None if raw_dbm is None else raw_dbm + self.rssi_cal_db

    def received_power(self, raw_dbm: float | None) -> float | None:
        """The calibrated received power of a raw reading, None unless classify_rssi() says
        "OK" (an overload or invalid octet is not a power: TDM, statistics)."""
        return self.calibrated(raw_dbm) if classify_rssi(raw_dbm, not self.packet_mode) == "OK" else None

    @property
    def rate_window_s(self) -> float:
        return float(self.frame_rate.h or RATE_WINDOW_MIN_S)

    @property
    def lm_window_s(self) -> float:
        return float(self.lm_w["rssi"].h or LM_WINDOW_MIN_S)

    def set_frame_period(self, period_s: float) -> None:
        """The longest expected spacecraft frame period P (GDS): rate windows max(60 s, 5 P),
        link-manager window max(600 s, 10 P) (module docstring)."""
        if not isinstance(period_s, (int, float)) or not math.isfinite(period_s) or period_s <= 0:
            return
        self.frame_period_s = float(period_s)
        rate = max(RATE_WINDOW_MIN_S, RATE_WINDOW_PERIODS * period_s)
        self.info_rate.h = self.frame_rate.h = rate
        for w in self.lm_w.values():
            w.h = max(LM_WINDOW_MIN_S, LM_WINDOW_PERIODS * period_s)

    @property
    def pair_s(self) -> float:
        """Longest time between an RSSI and a noise reading that still pair (one pass)."""
        return self.los_timeout_s or STALE_PAIR_DEFAULT_S

    def noise_for(self, t: float) -> float | None:
        """The noise reading that pairs with an event at t (F-P1-5): only one taken within
        pair_s of it; an older one belongs to another pass."""
        if self.noise_dbm is None or not self.noise_t or abs(self.noise_t - t) > self.pair_s:
            return None
        return self.noise_dbm

    def _add(self, metric: str, t: float, v: float | None) -> None:
        for w in self.w.values():
            w[metric].add(t, v)

    # ------------------------------------------------------------------ inputs
    def on_serial(self, n: int, t: float) -> None:
        self.serial_octets += n
        self.serial_rate.add(t, n)

    def on_noise_query(self) -> None:
        self.noise_queries += 1

    def on_noise(self, dbm_cal: float, t: float) -> None:
        """A calibrated noise-floor reading (rssi_cal_offset_db already applied)."""
        self.noise_dbm, self.noise_t = dbm_cal, t
        self.noise_replies += 1
        self.noise_reply_t = t
        self._add("noise", t, dbm_cal)
        self.lm_w["noise"].add(t, dbm_cal)

    def on_noise_octet(self, raw_dbm: float, t: float) -> None:
        """An E22 ambient-noise reply (raw register value in dBm): calibrated and used like
        on_noise(), except an octet at the end of its range or below the floor (module
        docstring), which counts as a reply (the module answered) but is no noise power."""
        if classify_rssi(raw_dbm, True) != "OK":
            self.noise_rejected += 1
            self.noise_replies += 1
            self.noise_reply_t = t
            return
        self.on_noise(raw_dbm + self.rssi_cal_db, t)

    def on_reception(self, t: float, rssi_raw: float | None, snr_db: float | None = None,
                     freq_err_hz: float | None = None, crc_ok: bool | None = None) -> None:
        """One received radio unit (E22 CADU or SX126x packet), before decoding. crc_ok: the
        radio's LoRa CRC verdict where the radio reports one (SX126x); None for the E22, which
        delivers only CRC-passed packets and reports no CRC (F-P0-2, docs/20 L-20)."""
        state = classify_rssi(rssi_raw, not self.packet_mode)
        rssi = self.calibrated(rssi_raw) if state == "OK" else None
        if state == "OVERLOAD":
            self.rssi_overloads += 1
            self.overload_last_t = t
            self.overload_recent.add(t, 1)
        elif state == "INVALID":
            self.rssi_invalid += 1
        self.receptions += 1
        self.last_rx = {"rssi_dbm": rssi, "rssi_raw_dbm": rssi_raw, "rssi_state": state, "snr_db": snr_db,
                        "freq_err_hz": freq_err_hz, "crc_ok": crc_ok, "t": t}
        noise = self.noise_for(t)
        self._add("rssi", t, rssi)
        self.lm_w["rssi"].add(t, rssi)
        self._add("snr_est", t, _sub(rssi, noise))
        self._add("snr_meas", t, snr_db)
        self._add("freq_err", t, freq_err_hz)
        self.rssi_hist.add(rssi)
        self.snr_hist.add(snr_db if snr_db is not None else _sub(rssi, noise))
        self.freq_hist.add(freq_err_hz)
        if freq_err_hz is not None:
            self._ferr_recent.append(freq_err_hz)
        if self.packet_mode:
            if crc_ok is False:
                self.packets_crc_fail += 1
            elif crc_ok is True:
                self.packets_ok += 1

    def overloads_recent(self, now: float) -> int:
        """Overload readings within the last OVERLOAD_RECENT_S (the YELLOW alarm,
        gds._compute_alarm; a wall clock stepped back before them counts them too)."""
        return len(self.overload_recent.values(now))

    def last_rx_assessment(self) -> dict[str, Any]:
        """margin_assessment() of the newest reception with the noise reading of its pass."""
        t = self.last_rx["t"]
        s = self.settings
        return margin_assessment(self.last_rx["rssi_dbm"], None if t is None else self.noise_for(t),
                                 s.sensitivity[0], s.sensitivity_worst[0])

    def on_codeblock(self, t: float, rs_corr: int, asm_err: int, ok: bool, reason: str,
                     rescue: bool = False) -> None:
        """One RS codeblock. rescue = the radio flagged the packet's LoRa CRC as failed
        (SX126x, contract 20): a frame that still decodes is counted as RS-rescued."""
        self.codeblocks += 1
        self.asm_hist[min(max(asm_err, 0), 4)] += 1
        self._add("asm", t, asm_err)
        if rescue:
            if ok:
                self.rs_rescued += 1
            else:
                self.rs_rescue_failed += 1
        if rs_corr < 0:
            self.cb_uncorrectable += 1
            return
        self.rs_hist[min(rs_corr, 16)] += 1
        self.max_corr = max(self.max_corr, rs_corr)
        if rs_corr == 0:
            self.cb_clean += 1
        else:
            self.cb_corrected += 1
            self.sym_corrected += rs_corr
        self._add("rs", t, rs_corr)
        if not ok:
            if "FECF" in reason:
                self.fecf_fail += 1
            else:
                self.foreign += 1

    def on_frame(self, t: float, frame: Any, rs: int, data_octets: int, field_octets: int) -> None:
        """One frame that passed RS and the FECF (a dssq.ccsds.tm.TransferFrame)."""
        aos = self.los
        if aos:
            self.los = False
            self.aos_t = t
            self.passes += 1
            # A new pass starts empty, except for this frame's own reception / codeblock
            # samples: the GDS adds them (on_reception, on_codeblock, same ERT) before this call.
            for w in self.w["pass"].values():
                w.clear_before(t)
            self._event(t, "AOS")
        elif self.last_frame_t is not None:               # the LOS gap is not an inter-arrival time
            self._add("iat", t, t - self.last_frame_t)
        self.last_frame_t = t
        lost = self.loss.update(frame)
        if aos:
            # The AOS frame's MCFC jump counts frames sent while out of view (or a spacecraft
            # reboot), modulo 256: not frames lost in a pass (F-P0-4). The per-VC baselines
            # restart with the pass for the same reason.
            self.aos_gap_frames += lost
            lost = 0
            self.vc_loss.clear()
        self.frames_lost += lost
        self.fer_lost.add(t, lost)
        self.lm_w["lost"].add(t, lost)
        vc = int(frame.vcid)
        prev = self.vc_loss.get(vc)
        vc_lost = 0 if prev is None else (frame.vcfc - prev - 1) & 0xFF
        self.vc_loss[vc] = frame.vcfc
        pv = self.per_vc.setdefault(vc, {"frames": 0, "lost": 0})
        pv["frames"] += 1
        pv["lost"] += vc_lost
        self.frames_ok += 1
        self.by_format[frame.format] = self.by_format.get(frame.format, 0) + 1
        self.oid_frames += int(frame.is_oid)
        self.field_octets += field_octets
        self.data_octets += data_octets
        self.info_rate.add(t, data_octets * 8)
        self.frame_rate.add(t, 1)
        rssi = self.last_rx["rssi_dbm"] if self.last_rx["t"] == t else None
        noise = self.noise_for(t)
        typ, worst = self.settings.sensitivity[0], self.settings.sensitivity_worst[0]
        ma = margin_assessment(rssi, noise, typ, worst)
        s = self.series
        s["t"].append(t)
        s["rssi"].append(rssi)
        s["noise"].append(noise)
        s["snr_est"].append(_sub(rssi, noise))
        s["snr_meas"].append(self.last_rx["snr_db"] if self.last_rx["t"] == t else None)
        s["freq_err"].append(self.last_rx["freq_err_hz"] if self.last_rx["t"] == t else None)
        # margin against the worst case where one exists (D-76), else the typical value
        s["margin"].append(ma["worst_db"] if worst is not None else ma["margin_db"])
        s["rs"].append(rs)

    def fer_counts(self, now: float) -> tuple[int, int]:
        """(frames lost, frames received) over the last FER_WINDOW_S."""
        v = self.fer_lost.values(now)
        return int(math.fsum(v)), len(v)

    def fer_window(self, now: float) -> float | None:
        """Frame error rate over the last FER_WINDOW_S (None without frames): lost / (lost + received)."""
        lost, n = self.fer_counts(now)
        return lost / (lost + n) if n else None

    def lm_input(self, now: float) -> dict[str, Any]:
        """The link manager's evaluation input (linkmgr.LinkManager.evaluate) over its window
        max(600 s, 10 P) (F-P1-1): RSSI and noise statistics, frames received and lost, FER."""
        v = self.lm_w["lost"].values(now)
        lost = int(math.fsum(v))
        return {"window_s": self.lm_window_s, "rssi": self.lm_w["rssi"].stats(now),
                "noise": self.lm_w["noise"].stats(now), "frames": len(v), "lost": lost,
                "fer": lost / (lost + len(v)) if v else None}

    def on_uplink(self, n_octets: int, t: float) -> None:
        self.uplink["cltus"] += 1
        self.uplink["octets"] += n_octets
        self.uplink["last_t"] = t

    def on_uplink_window(self) -> None:
        self.uplink["windows"] += 1

    def on_uplink_rejected(self) -> None:
        self.uplink["rejected_too_long"] += 1

    def on_sdls_reject(self, verdict: str) -> None:
        self.sdls_rejected[verdict] = self.sdls_rejected.get(verdict, 0) + 1

    def _event(self, t: float, name: str) -> None:
        self.pending_events.append((t, name))
        self.event_log.append({"t": t, "event": name})

    def tick(self, now: float, los_timeout_s: float, hibernating: bool = False) -> None:
        self.los_timeout_s = los_timeout_s
        self.hibernating = hibernating
        if self._last_tick is not None and not self.los:
            self.lock_time_s += now - self._last_tick
        self._last_tick = now
        if not self.los and self.last_frame_t is not None and now - self.last_frame_t > los_timeout_s:
            self.los = True
            self._event(now, "LOS")

    # ------------------------------------------------------------- estimators
    def _sc_model(self) -> link.RadioModel:
        mid = self.sc_rf.get("radio_model")
        if isinstance(mid, int) and mid in link.MODEL_IDS:
            return link.model_from_id(mid)
        return self.settings.model

    def freq_state(self) -> tuple[float | None, str | None]:
        """(median |raw frequency error| of the last FREQ_STATE_SAMPLES packets, "OK" / "WARN" /
        "ALARM" against FERR_L) - (None, None) without SX126x packets (contract 14)."""
        lim = self.settings.freq_offset_limit_hz
        if not lim or not self._ferr_recent:
            return None, None
        med = statistics.median(abs(x) for x in self._ferr_recent)
        return med, link.freq_offset_state(med, lim)

    def _doppler(self, afc: link.AfcLoop | None) -> dict[str, Any]:
        out: dict[str, Any] = {"offset_cal_hz": self.offset_cal_hz, "calibrated": self.offset_calibrated,
                               "doppler_hz": None, "range_rate_m_s": None, "source": None}
        if not self.packet_mode:
            return out
        total: float | None = None
        if afc is not None and afc.enabled and afc.estimate_hz is not None:
            total, out["source"] = afc.estimate_hz, "afc estimate"
        elif self.last_rx["freq_err_hz"] is not None:
            corr = afc.correction_hz if afc is not None else 0
            sign = afc.sign if afc is not None else 1
            total, out["source"] = corr + sign * self.last_rx["freq_err_hz"], "raw frequency error"
        if total is not None:
            d = link.doppler(total, self.offset_cal_hz, self.settings.freq_hz)
            out.update(doppler_hz=d["doppler_hz"], range_rate_m_s=d["range_rate_m_s"])
        return out

    def _interval_mean(self, metric: str, since: float, now: float) -> float | None:
        """Mean of the `metric` samples with since < t <= now (the 1-minute window holds them)."""
        vals: list[float] = []
        for t, v in reversed(self.w["1m"][metric].d):
            if t <= since:
                break
            if t <= now:
                vals.append(v)
        return math.fsum(vals) / len(vals) if vals else None

    def interval_row(self, since: float, now: float, afc: link.AfcLoop | None = None) -> dict[str, Any]:
        """The data logger's rx_stats row for the interval (since, now] (module docstring;
        keys = dssq.archive.RX_STATS_COLUMNS). Counter fields are the increments since the
        previous call, so the rows of a time range sum to its totals."""
        row: dict[str, Any] = {"t": now, "los": int(self.los), "pass_no": self.passes}
        for key, attr in ROW_COUNTERS.items():
            cur = int(getattr(self, attr))
            row[key] = max(0, cur - self._row_prev.get(key, 0))
            self._row_prev[key] = cur
        rssi = self._interval_mean("rssi", since, now)
        noise = self.noise_for(now)
        # margin_db keeps its meaning (against the typical sensitivity) under the D-76
        # noise-floor rule: None when the RSSI cannot measure it (margin_assessment())
        ma = margin_assessment(rssi, noise, self.settings.sensitivity[0], None)
        row.update(rssi_dbm=rssi, snr_db=self._interval_mean("snr_meas", since, now),
                   snr_est_db=_sub(rssi, noise), noise_dbm=noise, margin_db=ma["margin_db"],
                   freq_err_hz=self._interval_mean("freq_err", since, now), fer_10m=self.fer_window(now),
                   afc_hz=None, doppler_hz=None, range_rate_m_s=None)
        if self.packet_mode:
            if afc is not None and afc.enabled:
                row["afc_hz"] = float(afc.correction_hz)
            if not self.los:                              # a Doppler of the last pass is not news
                d = self._doppler(afc)
                row["doppler_hz"], row["range_rate_m_s"] = d["doppler_hz"], d["range_rate_m_s"]
        return row

    def _rates(self, now: float) -> dict[str, Any]:
        """Frame / information / CADU rates over the rate window max(60 s, 5 P) (F-P1-2), or
        the time since the GDS started when that is shorter."""
        win = self.rate_window_s
        span = max(1.0, min(win, now - self.t0))
        frames = sum(v for ts, v in self.frame_rate.d if now - ts <= win)
        bits = sum(v for ts, v in self.info_rate.d if now - ts <= win)
        return {"frames_per_min": frames * 60 / span, "info_rate_bps": bits / span,
                "cadu_rate_bps": frames * CADU_BITS / span, "rate_window_s": win}

    def snapshot(self, now: float | None = None, afc: link.AfcLoop | None = None,
                 afc_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        s = self.settings
        m = s.model
        prof = self.profile
        rssi = self.last_rx["rssi_dbm"]
        snr_meas = self.last_rx["snr_db"]
        rx_t = self.last_rx["t"]
        # RSSI - noise only when both readings belong to the same pass (noise_for): the last
        # packet's RSSI minus a noise reading taken hours later was never a measured SNR.
        noise_pair = None if rx_t is None else self.noise_for(rx_t)
        pr = signal_power_dbm(rssi, noise_pair)
        bw, bw_src = self.noise_bw
        n0 = None if self.noise_dbm is None else self.noise_dbm - 10 * math.log10(bw)
        if m.packet_mode:
            cn0 = None if snr_meas is None else snr_meas + 10 * math.log10(bw)
            cn0_src = "measured SNR + 10 log10(profile BW)"
        else:
            cn0 = _sub(pr, _sub(noise_pair, 10 * math.log10(bw)))
            cn0_src = "estimate: (RSSI - noise) / assumed noise bandwidth"
        rb = link.rate_bps(m, s.rate_code)
        ebn0 = None if cn0 is None else cn0 - 10 * math.log10(rb)
        sens, sens_src = s.sensitivity
        worst, worst_src = s.sensitivity_worst
        ma = margin_assessment(rssi, noise_pair, sens, worst)
        required = link.LORA_SNR_MIN_DB[prof.sf] if prof is not None else None
        ferr_med, ferr_state = self.freq_state()
        pkts = self.packets_ok + self.packets_crc_fail
        freq_mhz = link.channel_freq_mhz(s.channel)
        rf: dict[str, Any] = {
            "freq_mhz": freq_mhz, "freq_hz": s.freq_hz, "model": m.name, "air_rate_code": s.rate_code,
            "rate_label": link.rate_label(m, s.rate_code), "air_rate_bps": rb,
            "rssi_dbm": rssi, "rssi_raw_dbm": self.last_rx["rssi_raw_dbm"], "rssi_state": self.last_rx["rssi_state"],
            "rssi_overloads": self.rssi_overloads, "rssi_overload_last_t": self.overload_last_t,
            "rssi_invalid": self.rssi_invalid,
            "rssi_resolution_db": SX126X_RSSI_RESOLUTION_DB if m.packet_mode else E22_RSSI_RESOLUTION_DB,
            "rssi_cal_offset_db": self.rssi_cal_db, "rssi_mw": None if rssi is None else link.dbm_to_mw(rssi),
            "noise_dbm": self.noise_dbm,
            "noise_source": "SX126x GetRssiInst median of 8" if m.packet_mode else "E22 noise register",
            "noise_age_s": None if not self.noise_t else now - self.noise_t,
            "noise_rejected": self.noise_rejected,
            "snr_est_db": _sub(rssi, noise_pair), "pr_est_dbm": pr, "n0_est_dbm_hz": n0,
            "cn0_est_dbhz": cn0, "cn0_source": cn0_src, "ebn0_est_db": ebn0,
            "noise_bw_hz": bw, "noise_bw_source": bw_src,
            "sensitivity_dbm": sens, "sensitivity_source": sens_src,
            "sensitivity_worst_dbm": worst, "sensitivity_worst_source": worst_src,
            "margin_state": ma["state"], "margin_basis": ma["basis"],
            "margin_est_db": ma["margin_db"], "margin_worst_est_db": ma["worst_db"],
            "margin_max_db": ma["max_db"], "margin_worst_max_db": ma["worst_max_db"],
            "required_snr_db": required, "snr_margin_db": _sub(snr_meas, required),
            "freq_offset_limit_hz": s.freq_offset_limit_hz, "freq_offset_abs_median_hz": ferr_med,
            "freq_offset_state": ferr_state,
            "crc_fail_packets": self.packets_crc_fail if m.packet_mode else None,
            "packet_error_rate": (self.packets_crc_fail / pkts) if m.packet_mode and pkts else None,
            "packet": {"rssi_dbm": rssi, "snr_db": snr_meas, "freq_err_hz": self.last_rx["freq_err_hz"],
                       "crc_ok": self.last_rx["crc_ok"], "t": rx_t},
            "snr_db": self.w["10m"]["snr_meas"].brief(now),
            "freq_err_hz": self.w["10m"]["freq_err"].brief(now),
            "doppler": self._doppler(afc),
            "afc": None if afc is None else {**afc.snapshot(), "events": list(afc_events or [])[-20:]},
        }
        budget = self._budget(ma, bw, now)
        cw = self.codeblocks - self.cb_uncorrectable
        ser = (self.sym_corrected / (cw * RS_PARITY_SYMBOLS_PER_CODEWORD)) if cw else None
        fer_all = fer_block(self.frames_lost, self.frames_ok)
        lost10, n10 = self.fer_counts(now)
        fer10 = fer_block(lost10, n10)
        return {
            "now": now,
            "state": {"los": self.los, "aos_t": self.aos_t, "passes": self.passes,
                      "pass_duration_s": (now - self.aos_t) if self.aos_t and not self.los else None,
                      "since_last_frame_s": None if self.last_frame_t is None else now - self.last_frame_t,
                      "time_in_lock_pct": 100 * self.lock_time_s / max(1e-9, now - self.t0),
                      "los_timeout_s": self.los_timeout_s, "hibernating": self.hibernating},
            "rf": rf,
            "budget": budget,
            "windows": {k: {mname: w[mname].stats(now) for mname in METRICS} for k, w in self.w.items()},
            "hist": {"rssi": self.rssi_hist.as_dict(), "snr": self.snr_hist.as_dict(),
                     "freq_err": self.freq_hist.as_dict(), "rs_corrections": list(self.rs_hist),
                     "asm_bit_errors": list(self.asm_hist)},
            "decoder": {"codeblocks": self.codeblocks, "clean": self.cb_clean,
                        "corrected": self.cb_corrected, "uncorrectable": self.cb_uncorrectable,
                        "symbols_corrected": self.sym_corrected, "max_corrections": self.max_corr,
                        "symbol_error_rate_est": ser,
                        "bit_error_rate_est_range": None if ser is None else [ser / 8, ser],
                        "codeword_error_rate": (self.cb_uncorrectable / self.codeblocks) if self.codeblocks
                        else None,
                        "fecf_fail_after_rs": self.fecf_fail, "foreign_or_bad_header": self.foreign,
                        "rs_rescued": self.rs_rescued, "rs_rescue_failed": self.rs_rescue_failed,
                        "rice_errors": self.rice_errors, "decom_errors": self.decom_errors,
                        "sdls_rejected": dict(self.sdls_rejected)},
            "frames": {"ok": self.frames_ok, "lost": self.frames_lost, "aos_gap_frames": self.aos_gap_frames,
                       "fer": None if fer_all is None else fer_all["fer"], "fer_ci": fer_all,
                       "fer_10m": None if fer10 is None else fer10["fer"], "fer_10m_ci": fer10,
                       "oid_frames": self.oid_frames,
                       "idle_frame_pct": 100 * self.oid_frames / self.frames_ok if self.frames_ok else None,
                       "channel_efficiency_pct": 100 * self.data_octets / self.field_octets
                       if self.field_octets else None,
                       **self._rates(now),
                       "by_format": dict(self.by_format),
                       "per_vc": {str(k): dict(v) for k, v in sorted(self.per_vc.items())}},
            "serial": {"octets": self.serial_octets,
                       "octets_per_s": sum(v for ts, v in self.serial_rate.d if now - ts <= 10.0) / 10.0,
                       "noise_queries": self.noise_queries, "noise_replies": self.noise_replies},
            "uplink": dict(self.uplink),
            "two_way": {**self.sc_rf, "uplink_margin_db_est": budget["uplink"]["measured_margin_db"]},
            "series": {k: list(v) for k, v in self.series.items()},
            "events": list(self.event_log),
        }

    def _budget(self, ma: Mapping[str, Any], bw: float, now: float) -> dict[str, Any]:
        """Link budget, design (free space) vs measured, downlink and uplink. Every measured
        term uses the received power the D-76 noise-floor rule accepts (ma = margin_assessment()
        of the newest reception): none at the noise floor, after an overload or without a
        reading; the 10-minute path loss applies the same rule to the 10-minute means."""
        s = self.settings
        f_hz = s.freq_hz
        sens, sens_src = s.sensitivity
        worst, worst_src = s.sensitivity_worst
        # the range at sensitivity uses the margin the displays colour: the worst case if any
        range_sens = worst if worst is not None else sens
        sc_model = self._sc_model()
        pwr_code = self.sc_rf.get("tx_power_code")
        ptx = None
        if isinstance(pwr_code, int) and 0 <= pwr_code <= 3:
            ptx = link.power_dbm(sc_model, pwr_code)
        eirp = None if ptx is None else ptx + self.gt_dbi - self.tx_loss_db
        gain = None if eirp is None else eirp + self.gr_dbi - self.rx_loss_db     # EIRP + Gr - Lr
        meas_pl = _sub(gain, ma["power_dbm"])
        fspl = fspl_db(self.distance_km * 1000.0, f_hz)
        excess = _sub(meas_pl, fspl)
        pred_pr = _sub(gain, fspl)
        r10, n10 = self.w["10m"]["rssi"].stats(now), self.w["10m"]["noise"].stats(now)
        ma10 = margin_assessment(r10.get("mean"), n10.get("mean"), sens, worst)
        # F-P1-7: the free-space range at sensitivity is a design figure; the measured one
        # needs a measured excess loss (a configured distance and a usable reading).
        free_range = None if gain is None else range_for_loss_m(gain - range_sens, f_hz) / 1000.0
        meas_range = None if gain is None or excess is None else \
            range_for_loss_m(gain - range_sens - excess, f_hz) / 1000.0
        # Uplink: station transmitter -> spacecraft receiver.
        gs_pwr = link.power_dbm(s.model, s.power_code, s.pa_gain_db)
        gs_eirp = gs_pwr + self.gr_dbi - self.rx_loss_db
        sc_code = self.sc_rf.get("air_rate_code")
        sc_sens: float | None = None
        sc_sens_src: str | None = None
        if isinstance(sc_code, int) and 0 <= sc_code <= 7:
            rx_boost = True
            flags = self.sc_rf.get("rf_flags")
            if sc_model.packet_mode and isinstance(flags, int):
                rx_boost = bool(flags & 1)          # RF flags b0: Rx boosted gain verified
            sc_sens, sc_sens_src = link.sensitivity(sc_model, sc_code, None, rx_boost)
        up_pr = None if fspl is None else gs_eirp + self.gt_dbi - self.tx_loss_db - fspl
        # The spacecraft's uplink RSSI and noise floor are readings of the same kind (an E22
        # octet on an E22 spacecraft): the same overload and noise-floor rules apply.
        octet = not sc_model.packet_mode
        ul_raw = _number(self.sc_rf.get("uplink_rssi"))
        ul_state = classify_rssi(ul_raw, octet)
        sc_noise = _number(self.sc_rf.get("sc_noise_floor"))
        if classify_rssi(sc_noise, octet) != "OK":
            sc_noise = None
        ul_ma = margin_assessment(ul_raw if ul_state == "OK" else None, sc_noise, sc_sens, None)
        return {"sc_model": sc_model.name, "sc_tx_power_dbm_nominal": ptx,
                "power_verified": sc_model.name == link.E22_T37S,      # link.describe(): VERIFY T-RF-07
                "sc_antenna_gain_dbi": self.gt_dbi, "sc_line_loss_db": self.tx_loss_db, "sc_eirp_dbm_est": eirp,
                "distance_km": self.distance_km if self.distance_km > 0 else None,
                "owlt_s": (self.distance_km / C_KM_S) if self.distance_km > 0 else None,
                "fspl_db": fspl, "fspl_formula": "20 log10(4 pi d f / c)",
                "ground_gain_dbi": self.gr_dbi, "ground_line_loss_db": self.rx_loss_db,
                "predicted_pr_dbm": pred_pr, "predicted_margin_db": _sub(pred_pr, sens),
                "predicted_margin_worst_db": _sub(pred_pr, worst),
                "predicted_snr_db": _sub(pred_pr, self.noise_for(now)),
                "measured_power_state": ma["state"],
                "measured_path_loss_db_est": meas_pl,
                "measured_path_loss_mean10m_db": _sub(gain, ma10["power_dbm"]),
                "excess_loss_db_est": excess,
                "range_at_sensitivity_free_space_km": free_range, "range_at_sensitivity_km_est": meas_range,
                "sensitivity_dbm": sens, "sensitivity_source": sens_src,
                "sensitivity_worst_dbm": worst, "sensitivity_worst_source": worst_src,
                "thermal_noise_dbm": -174.0 + 10 * math.log10(bw),
                "noise_figure_assumed_db": link.SX126X_NF_DB if s.model.packet_mode else None,
                "uplink": {"gs_power_dbm": gs_pwr, "gs_power_verified": s.model.name == link.E22_T37S,
                           "gs_eirp_dbm_est": gs_eirp, "sc_sensitivity_dbm": sc_sens,
                           "sc_sensitivity_source": sc_sens_src, "predicted_pr_dbm": up_pr,
                           "predicted_margin_db": _sub(up_pr, sc_sens),
                           "measured_uplink_rssi_dbm": ul_raw if ul_state == "OK" else None,
                           "measured_uplink_rssi_state": ul_state, "measured_margin_state": ul_ma["state"],
                           "measured_margin_db": ul_ma["margin_db"], "measured_margin_max_db": ul_ma["max_db"]}}
