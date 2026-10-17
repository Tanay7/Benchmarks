"""Ground products of the attitude sensors (dssq/sensors.py): BHI260AP 6-DOF from ATT v4
(D-75) and the sensor-bus state from the flight's bus EVRs (D-73).

Every expected value below is computed by hand in the comment next to it; the ATT values go
through the real packet path (decom.encode -> Space Packet -> Decommutator) so the
not-available sentinels (0x7FFF, 0xFFFF) arrive as the GDS sees them.
"""
import math

import pytest

from dssq import dictionary as dd
from dssq import sensors as sn
from dssq.ccsds.packets import SpacePacket, build_packet
from dssq.ccsds.timecode import CucTime
from dssq.decom import Decommutator, encode

STILL = (0.5, 10.0)                     # [science] still_rate_dps, still_vib_mg defaults
H = math.sqrt(0.5)


def _q(axis: str, deg: float) -> tuple[float, float, float, float]:
    """Unit quaternion of a rotation by deg about the body axis x / y / z."""
    c, s = math.cos(math.radians(deg) / 2), math.sin(math.radians(deg) / 2)
    return (c, s if axis == "x" else 0.0, s if axis == "y" else 0.0, s if axis == "z" else 0.0)


def _att(**over) -> dict:
    """Decoded ATT v4 values through the real packet path. Defaults: every ATT flag, a level
    body (identity quaternions), acc (0, 0, 1) g, IMU flags GRV | STATS | PAIRED | GRV_PRESENT,
    8 windows."""
    v = {"flags": 0x1F, "acc_x": 0.0, "acc_y": 0.0, "acc_z": 1.0, "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0,
         "mag_x": 20.0, "mag_y": 0.0, "mag_z": 40.0, "q_fus_acc": 0.035,
         "imu_flags": 0x47, "imu_nwin": 8, "vib_rms": 2.1, "vib_peak": 7, "rate_mean_x": 0.03,
         "rate_mean_y": 0.04, "rate_mean_z": 0.0, "rate_peak": 0.3}
    for i, a in enumerate("wxyz"):
        v[f"q_fus_{a}"] = 1.0 if i == 0 else 0.0
        v[f"q_triad_{a}"] = 1.0 if i == 0 else 0.0
        v[f"q_grv_{a}"] = 1.0 if i == 0 else 0.0
    for k, x in over.items():
        if k in ("q_fus", "q_grv"):
            for a, c in zip("wxyz", x, strict=True):
                v[f"{k}_{a}"] = None if c is None else round(c * 16384) / 16384
        else:
            v[k] = x
    pkt = SpacePacket.parse(build_packet(dd.APID_ATT, 1, encode(dd.APID_ATT, v), CucTime.from_seconds(10.0)))
    return Decommutator().decode(pkt)


def test_level_body_and_stationary_window():
    p = sn.imu_products(_att(), *STILL)
    assert p["available"] and p["grv_valid"] and p["paired"] and p["grv_present"]
    assert p["up"] == pytest.approx([0, 0, 1]) and p["gravity_g"] == pytest.approx([0, 0, 1])
    assert (p["tilt_deg"], p["roll_deg"], p["pitch_deg"], p["heading_rel_deg"]) == pytest.approx((0, 0, 0, 0))
    assert p["heading_offset_deg"] == pytest.approx(0) and p["tilt_disagreement_deg"] == pytest.approx(0, abs=1e-3)
    # |rate| = sqrt(0.03^2 + 0.04^2) = 0.05 deg/s: below 10 x 0.07 sqrt(3) = 1.212 deg/s -> no spin axis
    assert p["rate_abs_dps"] == pytest.approx(0.05) and p["spin_axis"] is None
    assert p["na"]["spin"] == "|omega| 0.05 deg/s is not above 1.21 deg/s, 10 x the estimated 0.12 deg/s noise floor"
    # rate_peak 0.3 < 0.5 deg/s and vib_peak 7 < 10 mg -> stationary
    assert p["motion"] == "stationary" and p["vib_rms_mg"] == pytest.approx(2.1) and p["vib_peak_mg"] == 7
    assert p["grv_accuracy"] is None and "Table 89" in p["na"]["grv_accuracy"]
    assert p["vib_floor_mg"] == pytest.approx(2.078, abs=1e-3)                  # 1.2 sqrt(3)
    assert p["spin_axis_min_dps"] == pytest.approx(1.212, abs=1e-3)             # 10 x 0.07 sqrt(3)
    assert p["bandwidth_hz"] == 12.5 and p["acc_offset_mg"] == 50.0
    assert set(p["na"]) == {"grv_accuracy", "spin"}


def test_tilt_roll_pitch_and_relative_heading():
    # 90 deg about body X: u = (2(xz - wy), 2(yz + wx), w^2 - x^2 - y^2 + z^2) = (0, 2 * 0.5, 0) = (0, 1, 0)
    p = sn.imu_products(_att(q_grv=_q("x", 90)), *STILL)
    assert p["up"] == pytest.approx([0, 1, 0], abs=1e-4)
    assert (p["tilt_deg"], p["roll_deg"], p["pitch_deg"]) == pytest.approx((90, 90, 0), abs=0.01)
    # 30 deg about body Y: u = (-2 wy, 0, w^2 - y^2) = (-sin 30, 0, cos 30); pitch = atan2(0.5, 0.866) = 30
    p = sn.imu_products(_att(q_grv=_q("y", 30)), *STILL)
    assert p["up"] == pytest.approx([-0.5, 0, math.sqrt(3) / 2], abs=1e-4)
    assert (p["tilt_deg"], p["roll_deg"], p["pitch_deg"]) == pytest.approx((30, 0, 30), abs=0.01)
    # 30 deg about Z: heading = atan2(2wz, 1 - 2z^2) = atan2(sin 30, cos 30) = 30; up stays (0, 0, 1)
    p = sn.imu_products(_att(q_grv=_q("z", 30)), *STILL)
    assert p["heading_rel_deg"] == pytest.approx(30, abs=0.01) and p["tilt_deg"] == pytest.approx(0, abs=0.01)
    # ZYX composition: yaw 40, pitch 20, roll -10 -> the same angles back
    q = sn.qmul(sn.qmul(_q("z", 40), _q("y", 20)), _q("x", -10))
    p = sn.imu_products(_att(q_grv=q), *STILL)
    assert (p["heading_rel_deg"], p["pitch_deg"], p["roll_deg"]) == pytest.approx((40, 20, -10), abs=0.01)


def test_nine_vs_six_dof_offset_and_tilt_disagreement():
    q_grv = sn.qmul(_q("z", 10), _q("x", 20))
    # q_fus = Rz(40) (x) q_grv: dq = q_fus (x) conj(q_grv) = Rz(40) -> psi 40, delta 0
    p = sn.imu_products(_att(q_grv=q_grv, q_fus=sn.qmul(_q("z", 40), q_grv)), *STILL)
    assert p["heading_offset_deg"] == pytest.approx(40, abs=0.01)
    assert p["tilt_disagreement_deg"] == pytest.approx(0, abs=0.05)
    # q_fus = Rx(5) (x) q_grv: dq = Rx(5), dx = sin 2.5 -> delta = acos(1 - 2 sin^2 2.5) = 5, psi 0
    p = sn.imu_products(_att(q_grv=q_grv, q_fus=sn.qmul(_q("x", 5), q_grv)), *STILL)
    assert p["tilt_disagreement_deg"] == pytest.approx(5, abs=0.02)
    assert p["heading_offset_deg"] == pytest.approx(0, abs=0.01)
    # not from one ARU rebuild (IMU_F_PAIRED clear): no comparison, no linear acceleration
    p = sn.imu_products(_att(imu_flags=0x43), *STILL)
    assert p["heading_offset_deg"] is None and p["lin_acc_mg"] is None and p["tilt_deg"] == pytest.approx(0)
    assert "IMU_F_PAIRED clear" in p["na"]["pair"] and "IMU_F_PAIRED clear" in p["na"]["lin_acc"]
    # paired but the 9-DOF quaternion not valid (ATT b0 clear): no comparison, linear acceleration kept
    p = sn.imu_products(_att(flags=0x1E, q_fus=(None, None, None, None)), *STILL)
    assert p["heading_offset_deg"] is None and "QUAT clear" in p["na"]["pair"] and p["lin_acc_mg"] is not None


def test_linear_acceleration_is_acc_minus_gravity():
    # level: g = (0, 0, 1) g; acc (0.010, -0.020, 1.030) g -> (10, -20, 30) mg, |.| = sqrt(1400) = 37.417 mg
    p = sn.imu_products(_att(acc_x=0.010, acc_y=-0.020, acc_z=1.030), *STILL)
    assert p["lin_acc_mg"] == pytest.approx([10, -20, 30], abs=1e-6)
    assert p["lin_acc_abs_mg"] == pytest.approx(math.sqrt(1400), abs=1e-6)
    # 90 deg about X: g = (0, 1, 0) g; acc (0, 1.005, 0.002) g -> (0, 5, 2) mg (Q14 rounding of the quaternion
    # moves the up vector by < 1e-4, i.e. < 0.1 mg)
    p = sn.imu_products(_att(q_grv=_q("x", 90), acc_x=0.0, acc_y=1.005, acc_z=0.002), *STILL)
    assert p["lin_acc_mg"] == pytest.approx([0, 5, 2], abs=0.1)
    # no valid acceleration (ATT b1 clear, sentinel values)
    p = sn.imu_products(_att(flags=0x1D, acc_x=None, acc_y=None, acc_z=None), *STILL)
    assert p["lin_acc_mg"] is None and "ACCEL clear" in p["na"]["lin_acc"]


def test_rates_spin_axis_and_motion_state():
    # rate_mean (3, 4, 0) deg/s: |omega| = 5 > 1.212 -> spin axis (0.6, 0.8, 0); rate_peak 5.5 -> moving
    p = sn.imu_products(_att(rate_mean_x=3.0, rate_mean_y=4.0, rate_mean_z=0.0, rate_peak=5.5), *STILL)
    assert p["rate_mean_dps"] == pytest.approx([3, 4, 0]) and p["rate_abs_dps"] == pytest.approx(5)
    assert p["spin_axis"] == pytest.approx([0.6, 0.8, 0]) and "spin" not in p["na"]
    assert p["motion"] == "moving" and p["rate_peak_dps"] == pytest.approx(5.5)
    # vibration peak at the threshold is not "below": moving
    assert sn.imu_products(_att(vib_peak=10), *STILL)["motion"] == "moving"
    assert sn.imu_products(_att(vib_peak=10), 0.5, 10.5)["motion"] == "stationary"
    assert sn.motion_state(3, 0.49, 9.9, *STILL) == "stationary"
    assert sn.motion_state(3, 0.5, 9.9, *STILL) == "moving" and sn.motion_state(0, 0.1, 1, *STILL) == "unknown"
    # flags pass through
    p = sn.imu_products(_att(imu_flags=0x47 | 0x08 | 0x10 | 0x20), *STILL)
    assert p["acc_sat"] and p["gyro_sat"] and p["gap"]
    assert p["flags"] == ["GRV", "STATS", "PAIRED", "ACC_SAT", "GYRO_SAT", "GAP", "GRV_PRESENT"]


def test_sentinels_give_reasons_never_numbers():
    # no ATT packet yet
    p = sn.imu_products(None, *STILL)
    assert p["available"] is False and p["motion"] == "unknown" and p["na"]["all"] == "no ATT packet yet"
    assert all(p[k] is None for k in ("q_grv", "up", "tilt_deg", "vib_rms_mg", "rate_mean_dps", "nwin"))
    # the flight without ARU page 2: flags 0, q_grv 0x7FFF, imu_nwin 0, statistics 0xFFFF / 0x7FFF
    p = sn.imu_products(_att(imu_flags=0, imu_nwin=0, q_grv=(None, None, None, None), vib_rms=None, vib_peak=None,
                             rate_mean_x=None, rate_mean_y=None, rate_mean_z=None, rate_peak=None), *STILL)
    att = _att(imu_flags=0, q_grv=(None, None, None, None))
    assert att["q_grv_w"] is None                                        # 0x7FFF decodes to None
    assert p["available"] and p["grv_present"] is False and p["q_grv"] is None and p["tilt_deg"] is None
    assert "IMU_F_GRV_PRESENT clear" in p["na"]["grv"] and p["na"]["pair"] == p["na"]["grv"]
    assert p["nwin"] == 0 and p["vib_rms_mg"] is None and p["motion"] == "unknown"
    assert "imu_nwin 0" in p["na"]["stats"] and p["na"]["motion"] == p["na"]["stats"] == p["na"]["spin"]
    # present but not valid: IMU_F_GRV clear
    p = sn.imu_products(_att(imu_flags=0x42, q_grv=(None, None, None, None)), *STILL)
    assert "IMU_F_GRV clear" in p["na"]["grv"] and p["vib_rms_mg"] == pytest.approx(2.1)
    # a quaternion outside the norm gate is not an attitude
    p = sn.imu_products(_att(q_grv=(0.5, 0.0, 0.0, 0.0)), *STILL)
    assert p["q_grv"] is None and "norm gate" in p["na"]["grv"]
    # an older ATT without the 6-DOF part
    p = sn.imu_products({"flags": 0x1F}, *STILL)
    assert p["available"] is False and "imu_flags missing" in p["na"]["all"]


# ------------------------------------------------------------------ sensor-bus state
BOOT = [(0x060B, "instrument acquired: Nicla Sense ME on A4/A5"),
        (0x060B, "instrument acquired: RM3100 outboard on Qwiic"),
        (0x060B, "instrument acquired: RM3100 inboard on Qwiic"),
        (0x0706, "AS7265X HW 40/41 FW 12.0.0 devices NIR+VIS+UV on Qwiic")]
ALL_OK = 0x07FF


def _bus(events, health=ALL_OK, playback=False):
    s = sn.SensorBusState()
    for i, (eid, text) in enumerate(events):
        s.on_evr(eid, text, 100.0 + i, playback=playback)
    return s, s.snapshot(health)


def test_unknown_until_the_acquisition_events_arrive():
    s, snap = _bus([])
    assert snap["separated"] is None and snap["separation"] == sn.UNKNOWN_NO_EVENTS
    assert snap["separation"] == "unknown (no acquisition events received since the ground started)"
    assert all(e["bus"] is None and e["state"] is None for e in snap["instruments"].values())
    assert s.on_evr(0x0001, "BOOT VGQ-1 FSW 3.0 rst=0 POWER-ON", 1.0) is False      # not a bus report
    _, snap = _bus(BOOT[:1])
    assert snap["separated"] is None and "RM3100" in snap["separation"]


def test_recommended_layout_is_separated():
    _, snap = _bus(BOOT)
    inst = snap["instruments"]
    assert (inst["nicla"]["bus"], inst["rm3100_ob"]["bus"], inst["rm3100_ib"]["bus"], inst["as7265x"]["bus"]) == \
        ("A4/A5", "Qwiic", "Qwiic", "Qwiic")
    assert inst["as7265x"]["recommended"] is None and inst["as7265x"]["wrong_bus"] is None   # no preferred bus
    assert not any(e["wrong_bus"] for e in inst.values()) and snap["lost"] == []
    assert snap["separated"] is True and snap["shared_bus"] is None and snap["reports"] == 4
    assert snap["separation"] == "yes: Nicla Sense ME on A4/A5, RM3100 outboard on Qwiic, RM3100 inboard on Qwiic"


def test_not_separated_from_the_flight_warning():
    sep = "Nicla Sense ME and RM3100 outboard both on A4/A5: not separated (docs/03)"
    events = [(0x060B, "instrument acquired: Nicla Sense ME on A4/A5"),
              (0x060B, "instrument acquired: RM3100 outboard on A4/A5"),
              (0x060D, "RM3100 outboard found on A4/A5; the recommended bus is Qwiic (docs/03)"), (0x060D, sep)]
    s, snap = _bus(events)
    assert snap["separated"] is False and snap["shared_bus"] == "A4/A5"
    assert snap["separation"] == "NOT SEPARATED: Nicla Sense ME and RM3100 outboard both on A4/A5"
    assert snap["last_not_separated"]["text"] == sep
    ob = snap["instruments"]["rm3100_ob"]
    assert ob["wrong_bus"] is True and ob["warning"].startswith("RM3100 outboard found on A4/A5")
    assert s.separated(ALL_OK) is False
    # the RM3100 MISSING in the newest HK does not count (flight: only acquired instruments)
    assert s.separated(ALL_OK & ~(1 << dd.SENSOR_BITS.index("RM3100_OB"))) is None
    # moved to the Qwiic socket and acquired again (no WARN): separated, the old warning no longer shown
    s.on_evr(0x060B, "instrument acquired: RM3100 outboard on Qwiic", 200.0)
    snap = s.snapshot(ALL_OK)
    assert snap["separated"] is True and snap["instruments"]["rm3100_ob"]["warning"] is None


def test_lost_instruments_and_recovery():
    s, _ = _bus([*BOOT, (0x060E, "RM3100 ob stopped responding (5 failed samples on Qwiic)")])
    snap = s.snapshot(ALL_OK & ~(1 << 8))
    assert snap["instruments"]["rm3100_ob"]["state"] == "lost" and snap["lost"] == ["RM3100 outboard (Qwiic)"]
    assert snap["instruments"]["rm3100_ob"]["hk_ok"] is False and snap["separated"] is True     # the inboard counts
    s.on_evr(0x0606, "Nicla Sense ME stopped responding on A4/A5", 300.0)
    snap = s.snapshot(ALL_OK)
    assert snap["separated"] is None and "Nicla Sense ME (A4/A5)" in snap["lost"]
    s.on_evr(0x060B, "instrument acquired: Nicla Sense ME on A4/A5", 400.0)                  # re-acquired
    assert s.snapshot(ALL_OK)["instruments"]["nicla"]["state"] == "acquired"


def test_playback_never_overrides_a_real_time_report():
    s, _ = _bus(BOOT)
    s.on_evr(0x060B, "instrument acquired: Nicla Sense ME on Qwiic", 500.0, playback=True)
    assert s.snapshot(ALL_OK)["instruments"]["nicla"]["bus"] == "A4/A5"
    _, snap = _bus(BOOT, playback=True)                                    # only playback: used, marked
    assert snap["instruments"]["nicla"]["playback"] is True and snap["separated"] is True
    s, _ = _bus(BOOT, playback=True)
    s.on_evr(0x060E, "RM3100 ob stopped responding (5 failed samples on Qwiic)", 600.0, playback=True)
    assert s.snapshot(ALL_OK)["instruments"]["rm3100_ob"]["state"] == "lost"    # a newer copy replaces a copy
    s.on_evr(0x060B, "instrument acquired: RM3100 outboard on Qwiic", 700.0)
    s.on_evr(0x060E, "RM3100 ob stopped responding (5 failed samples on Qwiic)", 800.0, playback=True)
    assert s.snapshot(ALL_OK)["instruments"]["rm3100_ob"]["state"] == "acquired"


def test_untrusted_texts_are_refused():
    s = sn.SensorBusState()
    for eid, text in [(0x060B, "instrument acquired: Nicla Sense ME on SDA"),
                      (0x060B, "instrument acquired: X on Qwiic"),
                      (0x060D, "Foo and Bar both on A4/A5: not separated (docs/03)"), (0x060E, "RM3100 zz stopped")]:
        assert s.on_evr(eid, text, 1.0) is False
    assert s.snapshot(None)["separated"] is None and s.used == 0


# ------------------------------------------------------------------ simulator (D-75 model)
def _rotate(q, v):
    """R(q) v for a unit quaternion (scalar first)."""
    p = sn.qmul(sn.qmul(q, (0.0, *v)), (q[0], -q[1], -q[2], -q[3]))
    return p[1:]


def _sim_att(t_s: float, seed: int = 3):
    """(simulator, ATT values) of one ATT generated at SCLK t_s (values as encoded)."""
    from dssq.config import load_config
    from dssq.sim import SimSpacecraft

    clock = [1_800_000_000.0]
    cfg = load_config()
    cfg["science"]["field_ut"] = 50.0
    sim = SimSpacecraft(cfg, clock=lambda: clock[0], seed=seed)
    got = []
    sim._emit_values = lambda apid, v, vc, now: got.append((apid, dict(v)))
    clock[0] += t_s
    sim._att(clock[0])
    assert got and got[-1][0] == dd.APID_ATT
    return sim, got[-1][1]


def test_simulated_body_rates_are_the_rotation_kinematics():
    from dssq.sim import _body_rates, _rot

    # R(t + dt) = R(t) (I + [omega]x dt): omega_body from the skew part of R^T (R(t + dt) - R(t)) / dt
    dt = 1e-5
    for t in (0.0, 7.3, 41.0, 333.3):
        r0, r1 = _rot(t), _rot(t + dt)
        d = [[sum(r0[k][i] * (r1[k][j] - r0[k][j]) for k in range(3)) / dt for j in range(3)] for i in range(3)]
        omega = ((d[2][1] - d[1][2]) / 2, (d[0][2] - d[2][0]) / 2, (d[1][0] - d[0][1]) / 2)
        assert _body_rates(t) == pytest.approx(omega, abs=1e-5)


def test_simulated_att_is_physically_consistent():
    from dssq.sim import GRV_DRIFT_DPH, GRV_YAW0_DEG, MAG_FIELD_DIR, _body_rates

    t = 100.0
    sim, v = _sim_att(t)
    q_fus = sn.unit_quat(v, "q_fus")
    q_triad = sn.unit_quat(v, "q_triad")
    # accelerometer = 1 g along the up vector of the fusion quaternion (noise 1.2 mg per axis)
    assert [v["acc_x"], v["acc_y"], v["acc_z"]] == pytest.approx(list(sn.up_vector(q_fus)), abs=0.006)
    # BMM150 = the NED world field in body axes (q_triad is NED -> body), noise 0.3 uT per axis
    n = math.sqrt(sum(c * c for c in MAG_FIELD_DIR))
    b_ned = [50.0 * c / n for c in MAG_FIELD_DIR]
    assert [v["mag_x"], v["mag_y"], v["mag_z"]] == pytest.approx(list(_rotate(q_triad, b_ned)), abs=1.5)
    # gyroscope = body rates (not Euler-angle rates), noise 0.07 deg/s, 0.1 deg/s steps
    assert [v["gyro_x"], v["gyro_y"], v["gyro_z"]] == pytest.approx(
        [math.degrees(c) for c in _body_rates(t)], abs=0.4)
    # q_grv = q_fus turned about the vertical: heading offset = 25 deg + 2 deg/h x 100 s, no tilt difference
    psi, delta = sn.nine_vs_six_deg(q_fus, sn.unit_quat(v, "q_grv"))
    assert psi == pytest.approx(GRV_YAW0_DEG + GRV_DRIFT_DPH * t / 3600, abs=0.02) and delta < 0.02
    # window statistics: 100 whole 1 s windows since boot; rate mean = mean body rate over them
    assert v["imu_flags"] == 0x47 and v["imu_nwin"] == 100
    mean = [sum(math.degrees(_body_rates(k + (i + 0.5) / 25)[a]) for k in range(100) for i in range(25)) / 2500
            for a in range(3)]
    assert [v["rate_mean_x"], v["rate_mean_y"], v["rate_mean_z"]] == pytest.approx(mean, abs=0.02)
    assert v["rate_peak"] >= max(abs(c) for c in mean)
    # vibration: the 2.1 mg noise plus the turn of gravity inside each 1 s window
    assert 2.0 < v["vib_rms"] < 60 and v["vib_peak"] >= v["vib_rms"]
    # the next ATT aggregates only the windows since this one
    sim._emit_values = lambda apid, vals, vc, now: v.update(vals)
    sim._att(sim.t0 + t + 8.4)
    assert v["imu_nwin"] == 8


def test_simulator_boot_reports_the_recommended_bus_layout():
    sim, _ = _sim_att(1.0)
    evrs = [(eid, text) for _sev, eid, text in sim.evr_q]
    assert evrs[:4] == BOOT and evrs[4][0] == 0x0001                     # flight order: buses, then BOOT
    _, snap = _bus(BOOT)
    assert snap["separated"] is True


# ------------------------------------------------------------------ GDS: snapshot and alarm reason
def test_gds_imu_and_sensor_bus_end_to_end(monkeypatch, tmp_path):
    from test_end_to_end import World

    w = World(monkeypatch, tmp_path)
    s = w.gs.snapshot()
    assert s["imu"]["na"]["all"] == "no ATT packet yet" and s["sensor_bus"]["separation"] == sn.UNKNOWN_NO_EVENTS
    w.run(40)
    assert w.command("MODE TEST") == "EXECUTED"
    w.run(30)
    s = w.gs.snapshot()
    imu, bus = s["imu"], s["sensor_bus"]
    assert imu["paired"] and imu["motion"] == "moving" and imu["spin_axis"] is not None and imu["nwin"] >= 7
    assert imu["heading_offset_deg"] == pytest.approx(25.0, abs=0.2) and imu["tilt_disagreement_deg"] < 0.1
    assert imu["lin_acc_abs_mg"] < 8                                     # the simulated noise, no offset
    assert bus["separated"] is True and sn.NOT_SEPARATED_REASON not in s["alarm_reasons"]
    assert "ME_GRV" in s["decoded"]["sensors_ok"]
    # the flight's WARN for a shared bus -> the YELLOW alarm reason (data still used)
    w.sim.evr_q.append((2, 0x060D, "Nicla Sense ME and RM3100 outboard both on A4/A5: not separated (docs/03)"))
    w.run(20)
    s = w.gs.snapshot()
    assert s["sensor_bus"]["separated"] is False and s["sensor_bus"]["shared_bus"] == "A4/A5"
    assert sn.NOT_SEPARATED_REASON in s["alarm_reasons"] and s["alarm"] in ("YELLOW", "RED")
