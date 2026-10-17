"""Ground products of the spacecraft's attitude sensors: BHI260AP 6-DOF (D-75) and the
sensor-bus state (D-73). Pure Python, standard library only.

1. 6-DOF products from ATT v4 (imu_products). Per ATT period the flight sends the latest
   BHI260AP game rotation vector q_grv (accelerometer + gyroscope, no magnetometer) and the
   statistics of the Nicla ARU's 1 s windows of 25 Hz samples (vibration RMS and peak, mean
   angular-rate vector, peak |rate|): "measured on board". Everything else is derived here,
   "derived on the ground".
   Conventions: q_fus and q_grv are device -> world, scalar first as sent, world Z = up
   (Android convention, the dashboard's fusToNed); the Nicla's device frame is taken as the
   body frame. The game rotation vector's world has arbitrary horizontal axes (fixed when
   the BHI260AP starts) that drift with the gyro bias. For a unit q = (w, x, y, z):
   * up vector (body axes) u = R(q)^T e_z = (2(xz - wy), 2(yz + wx), w^2 - x^2 - y^2 + z^2);
     at rest the accelerometer reads +1 g along u, so the gravity reading is 1 g x u (the
     sign of the accelerometer and of Android's gravity sensor).
   * tilt = acos(u_z) (angle of body +Z from the vertical), roll = atan2(u_y, u_z) (about
     body X), pitch = atan2(-u_x, sqrt(u_y^2 + u_z^2)) (about body Y): Z-up body frame,
     level = body +Z up, roll = pitch = 0; equal to the ZYX Euler roll and pitch of q.
   * relative heading = atan2(2(wz + xy), 1 - 2(y^2 + z^2)): the body X axis in the
     horizontal plane, counter-clockwise seen from above, from the game rotation vector's
     arbitrary reference - NOT a compass heading, and it drifts (gyro bias stability 5 deg/h,
     BHI260AP datasheet Table 119).
   * 9-DOF vs 6-DOF, only when IMU_F_PAIRED (both quaternions from one ARU rebuild):
     dq = q_fus (x) conj(q_grv) maps the game-rotation world to the rotation-vector world;
     heading offset psi = atan2(2(dw dz + dx dy), 1 - 2(dy^2 + dz^2)); tilt disagreement
     delta = acos(1 - 2(dx^2 + dy^2)) = the angle between the two up vectors. A change of psi
     over time = game-rotation drift and / or a magnetic disturbance of the 9-DOF heading.
   * linear acceleration (estimate) = acc - 1 g x u. ATT v4 carries no window mean of the
     acceleration (D-75 packet choice: 59 octets, no stretching at 2.4 kbit/s), so `acc` is
     the ATT acceleration: one corrected-accelerometer sample of page 0, used only when
     IMU_F_PAIRED (the same ARU rebuild as q_grv) and ATT b1 ACCEL. Not a spacecraft
     acceleration: the accelerometer's zero-g offset is up to +/-50 mg over life and its
     sensitivity error up to +/-2 % (Table 117).
   * rate: |omega| of the mean rate vector; spin axis = rate / |omega| only when |omega| >
     SPIN_AXIS_FACTOR x RATE_FLOOR_DPS.
   * motion state over the ATT period: "stationary" when imu_nwin >= 1 and rate_peak <
     [science] still_rate_dps and vib_peak < still_vib_mg, "moving" otherwise, "unknown"
     without a window. While the body turns, the change of the gravity projection inside a
     1 s window counts in vib_rms / vib_peak (the ARU removes the window mean only).
   * the game rotation vector has no accuracy estimate (the BHI260AP reports 0 for it,
     datasheet Table 89): no accuracy is produced, the snapshot says why.
   Floors (estimates from the datasheet values at ODR 200 Hz; at the ARU's 25 Hz they are
   not specified, VERIFY T-SC-14): accelerometer output noise 1.2 mg rms per axis (Table
   117) -> vibration floor ~2.1 mg; gyroscope output noise 0.07 deg/s rms per axis (Table
   119) -> |omega| floor ~0.12 deg/s. The residual gyro offset after the BHI260AP's own
   calibration is not specified (zero-rate offset +/-1 deg/s with its fast offset
   compensation off, Table 119).
   Every value is None when it cannot be produced; `na` names the reason per group.

2. Sensor-bus state (SensorBusState). The flight names the bus of every instrument in its
   event reports (dictionary.SENSOR_BUS_EVRS): acquisition 0x060B, AS7265X identity 0x0706,
   the wrong-bus WARN and the "not separated" WARN 0x060D, losses 0x0606 / 0x060E. The ground
   keeps the latest report per instrument and decides whether the Nicla Sense ME and the
   RM3100s are on separate buses (the D-73 separation rule) as the flight does: only
   instruments whose latest report is an acquisition count, and an instrument the newest HK
   reports MISSING (sensor_health) does not. A reboot re-reports every instrument it finds
   (before its BOOT EVR), so the latest report per instrument is current; an instrument
   absent after a reboot keeps its old report, flagged by HK. Recorder playback (VC2) never
   replaces a real-time report: it fills an instrument without one (a newer playback copy
   replaces an older one). A ground started after the spacecraft booted may never see these
   reports: then everything stays unknown.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from . import dictionary as dd

Quat = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]

# BHI260AP datasheet BST-BHI260AP-DS000 (Tables 117 accelerometer, 119 gyroscope, 89 quaternion).
ACC_NOISE_MG = 1.2                   # output noise per axis, mg rms (8 g, 80 Hz filter, ODR 200 Hz)
ACC_OFFSET_LIFE_MG = 50.0            # zero-g offset over life (8 g range), mg
ACC_SENS_ERR_PCT = 2.0               # sensitivity error, max, %
GYRO_NOISE_DPS = 0.07                # output noise per axis, deg/s rms (74.6 Hz, ODR 200 Hz)
GYRO_BIAS_STABILITY_DPH = 5.0        # vibration-free, 25 C, deg/h
VIB_FLOOR_MG = ACC_NOISE_MG * math.sqrt(3.0)       # ~2.08 mg est.: vib_rms adds the three axes' variances
RATE_FLOOR_DPS = GYRO_NOISE_DPS * math.sqrt(3.0)   # ~0.121 deg/s est.: |omega| of three noisy axes
SPIN_AXIS_FACTOR = 10.0              # spin axis only when |omega| > 10 x the floor (D-75)
ARU_ODR_HZ = 25.0                    # ARU page-2 window samples (D-75): bandwidth <= ODR / 2
QUAT_NORM2 = (0.81, 1.21)            # the flight's quaternion norm gate (|q|^2), ARU decoders
G_MG = 1000.0                        # 1 g in mg (the accelerometer's unit)

NA_GRV_ACCURACY = "the BHI260AP reports none for the game rotation vector: always 0, datasheet Table 89"
MOTION_STATES = ("stationary", "moving", "unknown")


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _components(values: Mapping[str, Any], prefix: str, axes: str) -> list[float] | None:
    out: list[float] = []
    for a in axes:
        c = _num(values.get(f"{prefix}_{a}"))
        if c is None:
            return None
        out.append(c)
    return out


def unit_quat(values: Mapping[str, Any], prefix: str) -> Quat | None:
    """Unit quaternion (w, x, y, z) of the ATT fields <prefix>_w.._z, or None when a component
    is missing (sentinel) or the norm is outside the flight's gate."""
    q = _components(values, prefix, "wxyz")
    if q is None:
        return None
    n2 = sum(c * c for c in q)
    if not QUAT_NORM2[0] < n2 < QUAT_NORM2[1]:
        return None
    n = math.sqrt(n2)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def _vec(values: Mapping[str, Any], prefix: str) -> Vec3 | None:
    v = _components(values, prefix, "xyz")
    return None if v is None else (v[0], v[1], v[2])


def qmul(a: Sequence[float], b: Sequence[float]) -> Quat:
    """Hamilton product a (x) b, scalar first."""
    return (a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
            a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
            a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
            a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0])


def up_vector(q: Sequence[float]) -> Vec3:
    """World up (Z) in body axes, R(q)^T e_z, for a unit device -> world quaternion."""
    w, x, y, z = q
    return (2.0 * (x * z - w * y), 2.0 * (y * z + w * x), w * w - x * x - y * y + z * z)


def tilt_roll_pitch_deg(u: Sequence[float]) -> tuple[float, float, float]:
    """(tilt, roll, pitch) in degrees from the up vector (module docstring convention)."""
    ux, uy, uz = u
    return (math.degrees(math.acos(max(-1.0, min(1.0, uz)))), math.degrees(math.atan2(uy, uz)),
            math.degrees(math.atan2(-ux, math.hypot(uy, uz))))


def heading_deg(q: Sequence[float]) -> float:
    """Angle of the body X axis in the world's horizontal plane, counter-clockwise from world X."""
    w, x, y, z = q
    return math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def nine_vs_six_deg(q_fus: Sequence[float], q_grv: Sequence[float]) -> tuple[float, float]:
    """(heading offset psi, tilt disagreement delta) in degrees between the 9-DOF rotation
    vector and the 6-DOF game rotation vector of one instant (module docstring)."""
    dw, dx, dy, dz = qmul(q_fus, (q_grv[0], -q_grv[1], -q_grv[2], -q_grv[3]))
    psi = math.degrees(math.atan2(2.0 * (dw * dz + dx * dy), 1.0 - 2.0 * (dy * dy + dz * dz)))
    delta = math.degrees(math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * (dx * dx + dy * dy)))))
    return psi, delta


def motion_state(nwin: int | None, rate_peak_dps: float | None, vib_peak_mg: float | None,
                 still_rate_dps: float, still_vib_mg: float) -> str:
    """"stationary" / "moving" / "unknown" over one ATT period (module docstring)."""
    if not nwin or rate_peak_dps is None or vib_peak_mg is None:
        return "unknown"
    return "stationary" if rate_peak_dps < still_rate_dps and vib_peak_mg < still_vib_mg else "moving"


def imu_products(att: Mapping[str, Any] | None, still_rate_dps: float, still_vib_mg: float) -> dict[str, Any]:
    """The `imu` block of the snapshot from the newest decoded ATT values (None = no ATT yet).
    Every value is None when it cannot be produced; `na` gives the reason per group:
    all, grv, pair, lin_acc, stats, spin, motion, grv_accuracy."""
    v: Mapping[str, Any] = att or {}
    flags = _int(v.get("imu_flags"))
    names = dd.decode_bits(flags, dd.IMU_FLAG_BITS) if flags is not None and 0 <= flags <= 0xFF else []
    att_raw = _int(v.get("flags"))
    att_flags = dd.decode_bits(att_raw, dd.ATT_FLAG_BITS) if att_raw is not None and 0 <= att_raw <= 0xFF else []
    na: dict[str, str] = {"grv_accuracy": NA_GRV_ACCURACY}
    known = flags is not None and 0 <= flags <= 0xFF
    out: dict[str, Any] = {
        "available": known, "flags": names,
        "grv_present": "GRV_PRESENT" in names if known else None, "grv_valid": "GRV" in names if known else None,
        "paired": "PAIRED" in names if known else None,
        "q_grv": None, "grv_accuracy": None, "up": None, "gravity_g": None, "tilt_deg": None, "roll_deg": None,
        "pitch_deg": None, "heading_rel_deg": None, "heading_offset_deg": None, "tilt_disagreement_deg": None,
        "lin_acc_mg": None, "lin_acc_abs_mg": None,
        "nwin": None, "gap": None, "acc_sat": None, "gyro_sat": None,
        "vib_rms_mg": None, "vib_peak_mg": None, "rate_mean_dps": None, "rate_abs_dps": None, "spin_axis": None,
        "rate_peak_dps": None, "motion": "unknown",
        "still_rate_dps": still_rate_dps, "still_vib_mg": still_vib_mg,
        "vib_floor_mg": VIB_FLOOR_MG, "rate_floor_dps": RATE_FLOOR_DPS,
        "spin_axis_min_dps": SPIN_AXIS_FACTOR * RATE_FLOOR_DPS, "bandwidth_hz": ARU_ODR_HZ / 2.0,
        "acc_offset_mg": ACC_OFFSET_LIFE_MG, "acc_sens_err_pct": ACC_SENS_ERR_PCT,
        "gyro_bias_stability_dph": GYRO_BIAS_STABILITY_DPH, "na": na}
    if not known:
        why = "no ATT packet yet" if att is None else "imu_flags missing: no 6-DOF part in this ATT"
        for group in ("all", "grv", "pair", "lin_acc", "stats", "spin", "motion"):
            na[group] = why
        return out
    # --- game rotation vector (on board) and what follows from it (ground) ---
    q_grv = unit_quat(v, "q_grv") if "GRV" in names else None
    if q_grv is None:
        if "GRV_PRESENT" not in names:
            na["grv"] = ("IMU_F_GRV_PRESENT clear: ARU page 2 not read, i.e. no Nicla Sense ME or an ARU "
                         "firmware without page 2, or no game rotation vector in the BHI260AP firmware")
        elif "GRV" not in names:
            na["grv"] = "IMU_F_GRV clear: game rotation vector stale, absent or outside the norm gate"
        else:
            na["grv"] = "q_grv missing or outside the quaternion norm gate"
    else:
        u = up_vector(q_grv)
        tilt, roll, pitch = tilt_roll_pitch_deg(u)
        out.update(q_grv=list(q_grv), up=list(u), gravity_g=list(u), tilt_deg=tilt, roll_deg=roll, pitch_deg=pitch,
                   heading_rel_deg=heading_deg(q_grv))
    # --- 9-DOF vs 6-DOF and the linear acceleration: one ARU rebuild only ---
    q_fus = unit_quat(v, "q_fus") if "QUAT" in att_flags else None
    acc = _vec(v, "acc") if "ACCEL" in att_flags else None
    if q_grv is None:
        na["pair"] = na["lin_acc"] = na["grv"]
    elif "PAIRED" not in names:
        na["pair"] = "IMU_F_PAIRED clear: q_fus and q_grv not from one ARU rebuild"
        na["lin_acc"] = "IMU_F_PAIRED clear: acc and q_grv not from one ARU rebuild"
    else:
        if q_fus is None:
            na["pair"] = "ATT b0 QUAT clear: no valid 9-DOF rotation vector"
        else:
            psi, delta = nine_vs_six_deg(q_fus, q_grv)
            out.update(heading_offset_deg=psi, tilt_disagreement_deg=delta)
        if acc is None:
            na["lin_acc"] = "ATT b1 ACCEL clear: no valid acceleration"
        else:
            u = up_vector(q_grv)
            lin = [G_MG * (acc[i] - u[i]) for i in range(3)]
            out.update(lin_acc_mg=lin, lin_acc_abs_mg=math.sqrt(sum(c * c for c in lin)))
    # --- window statistics (on board) and what follows from them (ground) ---
    nwin = _int(v.get("imu_nwin"))
    out.update(nwin=nwin, gap="GAP" in names, acc_sat="ACC_SAT" in names, gyro_sat="GYRO_SAT" in names)
    vib_rms, vib_peak = _num(v.get("vib_rms")), _num(v.get("vib_peak"))
    rate, rate_peak = _vec(v, "rate_mean"), _num(v.get("rate_peak"))
    if not nwin or "STATS" not in names:
        na["stats"] = na["spin"] = na["motion"] = "imu_nwin 0: no complete 1 s IMU window in this ATT period"
        return out
    out.update(vib_rms_mg=vib_rms, vib_peak_mg=vib_peak, rate_peak_dps=rate_peak)
    if rate is None:
        na["spin"] = "rate_mean not available in this ATT"
    else:
        mag = math.sqrt(sum(c * c for c in rate))
        out.update(rate_mean_dps=list(rate), rate_abs_dps=mag)
        if mag > SPIN_AXIS_FACTOR * RATE_FLOOR_DPS:
            out["spin_axis"] = [c / mag for c in rate]
        else:
            na["spin"] = (f"|omega| {mag:.2f} deg/s is not above {SPIN_AXIS_FACTOR * RATE_FLOOR_DPS:.2f} deg/s, "
                          f"{SPIN_AXIS_FACTOR:.0f} x the estimated {RATE_FLOOR_DPS:.2f} deg/s noise floor")
    out["motion"] = motion_state(nwin, rate_peak, vib_peak, still_rate_dps, still_vib_mg)
    if out["motion"] == "unknown":
        na["motion"] = "rate_peak or vib_peak not available in this ATT"
    return out


# =============================================================================
# Sensor-bus state (D-73)
# =============================================================================
# key: (name in the EVRs, recommended bus or None = no preferred bus, HK sensor_health bit name)
INSTRUMENTS: dict[str, tuple[str, str | None, str]] = {
    "nicla": ("Nicla Sense ME", "A4/A5", "NICLA_ME"),
    "rm3100_ob": ("RM3100 outboard", "Qwiic", "RM3100_OB"),
    "rm3100_ib": ("RM3100 inboard", "Qwiic", "RM3100_IB"),
    "as7265x": ("AS7265X", None, "AS7265X"),
}
_BY_NAME = {name: key for key, (name, _rec, _bit) in INSTRUMENTS.items()}
_RM3100 = ("rm3100_ob", "rm3100_ib")
NOT_SEPARATED_REASON = "sensor wiring: RM3100 and Nicla not on separate buses"
UNKNOWN_NO_EVENTS = "unknown (no acquisition events received since the ground started)"


class SensorBusState:
    """Latest bus report per instrument from the flight's sensor-bus EVRs (module docstring)."""

    def __init__(self) -> None:
        self.reports: dict[str, dict[str, Any]] = {}
        self.warnings: dict[str, dict[str, Any]] = {}       # latest wrong-bus WARN per instrument
        self.last_not_separated: dict[str, Any] | None = None
        self.used = 0                                        # sensor-bus EVRs used

    @staticmethod
    def _replaces(old: Mapping[str, Any] | None, playback: bool) -> bool:
        """A recorder copy never replaces a real-time report (it may replace an older copy)."""
        return not playback or old is None or bool(old["playback"])

    def _put(self, key: str, bus: str, state: str, event_id: int, t: float, playback: bool) -> None:
        if self._replaces(self.reports.get(key), playback):
            self.reports[key] = {"bus": bus, "state": state, "event_id": event_id, "t": t, "playback": playback}

    def on_evr(self, event_id: int, text: str, t: float, playback: bool = False) -> bool:
        """Feed one EVR; True when it was a sensor-bus report this state used."""
        pair = dd.evr_not_separated(event_id, text)
        if pair is not None:
            a, b, bus = pair
            keys = [_BY_NAME[n] for n in (a, b) if n in _BY_NAME]
            if not keys:
                return False
            for key in keys:
                self._put(key, bus, "acquired", event_id, t, playback)
            if self._replaces(self.last_not_separated, playback):
                self.last_not_separated = {"text": text, "t": t, "playback": playback}
            self.used += 1
            return True
        lost = dd.evr_instrument_lost(event_id, text)
        if lost is not None and lost[0] in _BY_NAME:
            self._put(_BY_NAME[lost[0]], lost[1], "lost", event_id, t, playback)
            self.used += 1
            return True
        found = dd.evr_instrument_bus(event_id, text)
        if found is not None and found[0] in _BY_NAME:
            key = _BY_NAME[found[0]]
            self._put(key, found[1], "acquired", event_id, t, playback)
            if event_id == dd.EVR_WRONG_BUS and self._replaces(self.warnings.get(key), playback):
                self.warnings[key] = {"text": text, "t": t, "playback": playback}
            self.used += 1
            return True
        return False

    def separated(self, sensor_health: int | None) -> bool | None:
        """The separation verdict alone (alarm logic)."""
        return self.snapshot(sensor_health)["separated"]

    def snapshot(self, sensor_health: int | None) -> dict[str, Any]:
        """The `sensor_bus` block: per instrument its bus, recommended bus, wrong-bus flag and
        WARN text, state, HK health, report time; the separation verdict with its text, the
        shared bus, the lost instruments and the latest "not separated" WARN."""
        inst: dict[str, Any] = {}
        for key, (name, rec, bit) in INSTRUMENTS.items():
            r = self.reports.get(key)
            hk_ok = None if sensor_health is None else bool(sensor_health >> dd.SENSOR_BITS.index(bit) & 1)
            bus = r["bus"] if r else None
            w = self.warnings.get(key)
            inst[key] = {"name": name, "bus": bus, "recommended": rec,
                         "wrong_bus": None if bus is None or rec is None else bus != rec,
                         "warning": w["text"] if w and bus is not None and rec is not None and bus != rec else None,
                         "state": r["state"] if r else None, "hk_ok": hk_ok,
                         "event_id": r["event_id"] if r else None, "t": r["t"] if r else None,
                         "playback": r["playback"] if r else None}
        separated, shared, text = self.separation(inst, bool(self.reports))
        lost = [f"{e['name']} ({e['bus']})" for e in inst.values() if e["state"] == "lost"]
        return {"instruments": inst, "separated": separated, "separation": text, "shared_bus": shared,
                "lost": lost, "last_not_separated": self.last_not_separated, "reports": self.used}

    @staticmethod
    def separation(inst: Mapping[str, Mapping[str, Any]], any_report: bool = True
                   ) -> tuple[bool | None, str | None, str]:
        """(separated, shared bus, text). Counted like the flight's check: an instrument whose
        latest report is an acquisition and that the newest HK does not report MISSING."""
        def counted(key: str) -> str | None:
            e = inst[key]
            return e["bus"] if e["bus"] is not None and e["state"] == "acquired" and e["hk_ok"] is not False else None

        if not any_report:
            return None, None, UNKNOWN_NO_EVENTS
        nicla = counted("nicla")
        rms = [(inst[k]["name"], b) for k in _RM3100 if (b := counted(k)) is not None]
        if nicla is None:
            return None, None, "unknown (no current acquisition report of the Nicla Sense ME)"
        if not rms:
            return None, None, "unknown (no current acquisition report of an RM3100)"
        shared = [name for name, b in rms if b == nicla]
        if shared:
            return False, nicla, f"NOT SEPARATED: Nicla Sense ME and {' and '.join(shared)} both on {nicla}"
        return True, None, "yes: Nicla Sense ME on " + nicla + ", " + ", ".join(f"{n} on {b}" for n, b in rms)


__all__ = ["ACC_OFFSET_LIFE_MG", "INSTRUMENTS", "MOTION_STATES", "NA_GRV_ACCURACY", "NOT_SEPARATED_REASON",
           "RATE_FLOOR_DPS", "SPIN_AXIS_FACTOR", "UNKNOWN_NO_EVENTS", "VIB_FLOOR_MG", "SensorBusState",
           "heading_deg", "imu_products", "motion_state", "nine_vs_six_deg", "qmul", "tilt_roll_pitch_deg",
           "unit_quat", "up_vector"]
