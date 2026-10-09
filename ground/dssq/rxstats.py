"""Ground receiver statistics engine (feeds the RECEIVER page of the web UI).

Every number here is either MEASURED (from the E22 RSSI/noise registers, the
byte stream, the frame synchronizer, the RS decoder and frame/packet counters)
or an ESTIMATE derived from measurements with stated assumptions (keys ending
in "_est"). Nothing is invented: if an input is missing the value is None and
the UI shows "—".

Windows: 60 s, 600 s and "pass" (since AOS).
"""
from __future__ import annotations

import math
import statistics
import time
from collections import deque

from .link import AIR_RATE_BPS, SENSITIVITY_EST_DBM, dbm_to_mw

C_KM_S = 299_792.458
# Nominal E22-400T37S output power per power code (EBYTE 3-dB-step convention,
# code 0 = rated 37 dBm). Verify against your module's manual; used for path-loss
# estimates only.
TX_POWER_DBM = {0: 37.0, 1: 34.0, 2: 31.0, 3: 28.0}


class Window:
    """Time-windowed sample store with summary statistics."""

    def __init__(self, horizon_s: float):
        self.h = horizon_s
        self.d: deque[tuple[float, float]] = deque()

    def add(self, t: float, v: float | None):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return
        self.d.append((t, float(v)))
        self._trim(t)

    def _trim(self, now: float):
        while self.d and now - self.d[0][0] > self.h:
            self.d.popleft()

    def stats(self, now: float | None = None) -> dict:
        if now is not None:
            self._trim(now)
        v = [x for _, x in self.d]
        if not v:
            return {"n": 0}
        s = sorted(v)

        def pct(p):
            k = (len(s) - 1) * p
            f, c = math.floor(k), math.ceil(k)
            return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)

        return {"n": len(v), "mean": statistics.fmean(v), "min": s[0], "max": s[-1],
                "std": statistics.pstdev(v) if len(v) > 1 else 0.0,
                "p10": pct(0.10), "p50": pct(0.50), "p90": pct(0.90), "last": v[-1],
                "fade_depth": pct(0.90) - pct(0.10)}


class Hist:
    def __init__(self, lo: float, hi: float, step: float):
        self.lo, self.hi, self.step = lo, hi, step
        self.bins = [0] * (int((hi - lo) / step) + 1)

    def add(self, v: float | None):
        if v is None:
            return
        i = int((min(max(v, self.lo), self.hi) - self.lo) / self.step)
        self.bins[min(i, len(self.bins) - 1)] += 1

    def as_dict(self):
        return {"lo": self.lo, "step": self.step, "bins": self.bins}


class ReceiverStats:
    def __init__(self, cfg: dict):
        r, st, sc = cfg["radio"], cfg["station"], cfg["spacecraft"]
        self.air_rate_code = int(r.get("air_rate", 2))
        self.noise_bw_hz = float(r.get("noise_bw_hz", 125e3))
        self.gr_dbi = float(st.get("antenna_gain_dbi", 2.15))
        self.rx_loss_db = float(st.get("rx_line_loss_db", 0.0))
        self.gt_dbi = float(sc.get("antenna_gain_dbi", 2.15))
        self.tx_loss_db = float(sc.get("tx_line_loss_db", 0.5))
        self.distance_km = float(sc.get("distance_km", 0.0))
        self.freq_mhz = 410.125 + int(r.get("channel", 23))
        self.t0 = time.time()
        # windows
        self.w = {name: {"rssi": Window(h), "noise": Window(h), "snr": Window(h),
                         "iat": Window(h), "rs": Window(h), "asm": Window(h)}
                  for name, h in (("1m", 60), ("10m", 600), ("pass", 10 ** 9))}
        self.rssi_hist = Hist(-150, -20, 5)
        self.snr_hist = Hist(-20, 60, 2)
        self.rs_hist = [0] * 17
        self.asm_hist = [0] * 5
        self.series = {k: deque(maxlen=600) for k in ("t", "rssi", "noise", "snr", "margin", "rs")}
        # counters
        self.noise_dbm: float | None = None
        self.noise_t = 0.0
        self.noise_queries = 0
        self.noise_replies = 0
        self.serial_octets = 0
        self.serial_rate = Window(10)
        self.last_frame_t: float | None = None
        self.aos_t: float | None = None
        self.los = True
        self.passes = 0
        self.lock_time_s = 0.0
        self._last_tick: float | None = None
        self.codeblocks = 0
        self.cb_clean = 0
        self.cb_corrected = 0
        self.cb_uncorrectable = 0
        self.sym_corrected = 0
        self.max_corr = 0
        self.fecf_fail = 0
        self.foreign = 0
        self.frames_ok = 0
        self.frames_lost = 0
        self.mcfc_prev: int | None = None
        self.oid_frames = 0
        self.data_octets = 0           # non-idle packet octets delivered
        self.field_octets = 0          # total data-field octets received
        self.info_rate = Window(60)
        self.frame_rate = Window(60)
        self.uplink = {"cltus": 0, "octets": 0, "last_t": None, "windows": 0}
        self.sc_rf: dict = {}          # latest spacecraft RF packet (two-way table)
        self.events: list[tuple[float, str]] = []

    # ------------------------------------------------------------------ inputs
    def on_serial(self, n: int, t: float):
        self.serial_octets += n
        self.serial_rate.add(t, n)

    def on_noise(self, dbm: float, t: float):
        self.noise_dbm, self.noise_t = dbm, t
        self.noise_replies += 1
        for w in self.w.values():
            w["noise"].add(t, dbm)

    def on_codeblock(self, t: float, rs_corr: int, asm_err: int, ok: bool, reason: str):
        self.codeblocks += 1
        self.asm_hist[min(asm_err, 4)] += 1
        for w in self.w.values():
            w["asm"].add(t, asm_err)
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
        for w in self.w.values():
            w["rs"].add(t, rs_corr)
        if not ok:
            if "FECF" in reason:
                self.fecf_fail += 1
            else:
                self.foreign += 1

    def on_frame(self, t: float, rssi: int | None, mcfc: int, oid: bool, data_octets: int):
        if self.los:
            self.los = False
            self.aos_t = t
            self.passes += 1
            self.events.append((t, "AOS"))
        if self.last_frame_t is not None:
            iat = t - self.last_frame_t
            for w in self.w.values():
                w["iat"].add(t, iat)
        self.last_frame_t = t
        if self.mcfc_prev is not None:
            self.frames_lost += (mcfc - self.mcfc_prev - 1) & 0xFF
        self.mcfc_prev = mcfc
        self.frames_ok += 1
        self.oid_frames += int(oid)
        self.field_octets += 188
        self.data_octets += data_octets
        self.info_rate.add(t, data_octets * 8)
        self.frame_rate.add(t, 1)
        snr = None if rssi is None or self.noise_dbm is None else rssi - self.noise_dbm
        for w in self.w.values():
            w["rssi"].add(t, rssi)
            w["snr"].add(t, snr)
        self.rssi_hist.add(rssi)
        self.snr_hist.add(snr)
        s = self.series
        s["t"].append(t)
        s["rssi"].append(rssi)
        s["noise"].append(self.noise_dbm)
        s["snr"].append(snr)
        s["margin"].append(None if rssi is None else rssi - self.sensitivity_est)
        s["rs"].append(None)

    def on_uplink(self, n_octets: int, t: float):
        self.uplink["cltus"] += 1
        self.uplink["octets"] += n_octets
        self.uplink["last_t"] = t

    def tick(self, now: float, los_timeout_s: float):
        if self._last_tick is not None and not self.los:
            self.lock_time_s += now - self._last_tick
        self._last_tick = now
        if not self.los and self.last_frame_t and now - self.last_frame_t > los_timeout_s:
            self.los = True
            self.events.append((now, "LOS"))

    # ------------------------------------------------------------- estimators
    @property
    def sensitivity_est(self) -> float:
        return SENSITIVITY_EST_DBM.get(self.air_rate_code, -129.0)

    def _pr(self, rssi):
        if rssi is None or self.noise_dbm is None:
            return None
        d = dbm_to_mw(rssi) - dbm_to_mw(self.noise_dbm)
        return 10 * math.log10(d) if d > 0 else None

    def fspl_db(self, d_km: float) -> float | None:
        if d_km <= 0:
            return None
        return 32.44 + 20 * math.log10(self.freq_mhz) + 20 * math.log10(d_km)

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        rssi = self.series["rssi"][-1] if self.series["rssi"] else None
        pr = self._pr(rssi)
        n0 = None if self.noise_dbm is None else self.noise_dbm - 10 * math.log10(self.noise_bw_hz)
        cn0 = None if pr is None or n0 is None else pr - n0
        rb = AIR_RATE_BPS.get(self.air_rate_code, 2400)
        ebn0 = None if cn0 is None else cn0 - 10 * math.log10(rb)
        pwr_code = self.sc_rf.get("tx_power_code")
        ptx = TX_POWER_DBM.get(pwr_code) if pwr_code is not None else None
        eirp = None if ptx is None else ptx + self.gt_dbi - self.tx_loss_db
        meas_pl = None if eirp is None or rssi is None else eirp + self.gr_dbi - self.rx_loss_db - rssi
        fspl = self.fspl_db(self.distance_km)
        excess = None if meas_pl is None or fspl is None else meas_pl - fspl
        # range at which RSSI would hit the sensitivity estimate (free space + current excess)
        max_range = None
        if eirp is not None:
            budget = eirp + self.gr_dbi - self.rx_loss_db - self.sensitivity_est - (excess or 0.0)
            max_range = 10 ** ((budget - 32.44 - 20 * math.log10(self.freq_mhz)) / 20)
        total = self.frames_ok + self.frames_lost
        cw = self.codeblocks - self.cb_uncorrectable
        ser = (self.sym_corrected / (cw * 232)) if cw else None
        ws = {k: {m: w[m].stats(now) for m in w} for k, w in self.w.items()}
        span = max(1.0, min(60.0, now - self.t0))
        return {
            "now": now,
            "state": {"los": self.los, "aos_t": self.aos_t, "passes": self.passes,
                      "pass_duration_s": (now - self.aos_t) if self.aos_t and not self.los else None,
                      "since_last_frame_s": None if self.last_frame_t is None else now - self.last_frame_t,
                      "time_in_lock_pct": 100 * self.lock_time_s / max(1e-9, now - self.t0)},
            "rf": {"freq_mhz": self.freq_mhz, "air_rate_bps": rb, "air_rate_code": self.air_rate_code,
                   "rssi_dbm": rssi, "noise_dbm": self.noise_dbm,
                   "noise_age_s": None if not self.noise_t else now - self.noise_t,
                   "snr_est_db": None if rssi is None or self.noise_dbm is None else rssi - self.noise_dbm,
                   "pr_est_dbm": pr, "n0_est_dbm_hz": n0, "cn0_est_dbhz": cn0, "ebn0_est_db": ebn0,
                   "sensitivity_est_dbm": self.sensitivity_est,
                   "margin_est_db": None if rssi is None else rssi - self.sensitivity_est,
                   "noise_bw_assumed_hz": self.noise_bw_hz},
            "budget": {"sc_tx_power_dbm_nominal": ptx, "sc_eirp_dbm_est": eirp,
                       "ground_gain_dbi": self.gr_dbi, "ground_line_loss_db": self.rx_loss_db,
                       "measured_path_loss_db_est": meas_pl, "distance_km": self.distance_km or None,
                       "owlt_s": (self.distance_km / C_KM_S) if self.distance_km else None,
                       "fspl_db": fspl, "excess_loss_db_est": excess,
                       "range_at_sensitivity_km_est": max_range},
            "windows": ws,
            "hist": {"rssi": self.rssi_hist.as_dict(), "snr": self.snr_hist.as_dict(),
                     "rs_corrections": self.rs_hist, "asm_bit_errors": self.asm_hist},
            "decoder": {"codeblocks": self.codeblocks, "clean": self.cb_clean,
                        "corrected": self.cb_corrected, "uncorrectable": self.cb_uncorrectable,
                        "symbols_corrected": self.sym_corrected, "max_corrections": self.max_corr,
                        "symbol_error_rate_est": ser,
                        "bit_error_rate_est_range": None if ser is None else [ser / 8, ser],
                        "fecf_fail_after_rs": self.fecf_fail, "foreign_or_bad_header": self.foreign},
            "frames": {"ok": self.frames_ok, "lost_mcfc": self.frames_lost,
                       "fer": (self.frames_lost / total) if total else None,
                       "oid_frames": self.oid_frames,
                       "idle_frame_pct": 100 * self.oid_frames / self.frames_ok if self.frames_ok else None,
                       "channel_efficiency_pct": 100 * self.data_octets / self.field_octets if self.field_octets else None,
                       "frames_per_min": sum(v for _, v in self.frame_rate.d) * 60 / span,
                       "info_rate_bps": sum(v for _, v in self.info_rate.d) / span,
                       "cadu_rate_bps": sum(v for _, v in self.frame_rate.d) * 236 * 8 / span},
            "serial": {"octets": self.serial_octets,
                       "octets_per_s": sum(v for _, v in self.serial_rate.d) / 10.0,
                       "noise_queries": self.noise_queries, "noise_replies": self.noise_replies},
            "uplink": dict(self.uplink),
            "two_way": self.sc_rf,
            "series": {k: list(v)[-300:] for k, v in self.series.items()},
            "events": self.events[-20:],
        }
