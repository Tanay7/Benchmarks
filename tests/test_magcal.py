"""Ground magnetometer calibration (contract v3 24.6) and MAGCAL argument encoding (24.1).

Synthetic rotations with a known hard-iron offset and symmetric soft-iron matrix: raw =
S^-1 (R u) + b + noise for unit directions u, so the expected calibration is exactly
(b, S). Noise levels bracket the RM3100 figure (15-30 nT, PNI manual r06 Table 3-1) by an
order of magnitude and more. Deterministic seeds; no numpy.
"""
import math
import random

import pytest

from dssq import magcal as mc

R = 48.0                                         # uT, a mid-latitude total field
B_TRUE = (12.5, -7.3, 20.1)                      # uT
S_TRUE = ((1.10, 0.03, -0.02), (0.03, 0.95, 0.05), (-0.02, 0.05, 1.02))


def _inv3(m):
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def rotation(n, keep=lambda u: True, noise=0.05, seed=1, b=B_TRUE, s=S_TRUE, r=R):
    rng = random.Random(seed)
    si = _inv3(s)
    out = []
    for _ in range(100 * n):                     # bounded rejection sampling
        if len(out) == n:
            break
        u = [rng.gauss(0.0, 1.0) for _ in range(3)]
        k = math.sqrt(sum(x * x for x in u))
        u = [x / k for x in u]
        if not keep(u):
            continue
        out.append(tuple(sum(si[i][j] * r * u[j] for j in range(3)) + b[i] + rng.gauss(0.0, noise)
                         for i in range(3)))
    assert len(out) == n
    return out


def _errors(res, b=B_TRUE, s=S_TRUE):
    eb = max(abs(res.offset_ut[i] - b[i]) for i in range(3))
    em = max(abs(res.matrix[i][j] - s[i][j]) for i in range(3) for j in range(3))
    return eb, em


# ---------------------------------------------------------------- linear algebra
def test_solve_and_eigh_against_hand_values():
    assert mc.solve([[2.0, 1.0], [1.0, 3.0]], [3.0, 5.0]) == pytest.approx([0.8, 1.4])
    assert mc.solve([[0.0, 1.0], [1.0, 0.0]], [2.0, 3.0]) == pytest.approx([3.0, 2.0])      # needs pivoting
    with pytest.raises(ValueError):
        mc.solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])
    lam, v = mc.eigh([[2.0, 1.0], [1.0, 2.0]])                                             # eigenvalues 1 and 3
    assert sorted(lam) == pytest.approx([1.0, 3.0])
    a = [[4.0, 1.0, -2.0], [1.0, 2.0, 0.5], [-2.0, 0.5, 3.0]]
    lam, v = mc.eigh(a)
    for k in range(3):                                                                     # A v = lambda v
        for i in range(3):
            assert sum(a[i][j] * v[j][k] for j in range(3)) == pytest.approx(lam[k] * v[i][k], abs=1e-12)
    assert sum(lam) == pytest.approx(9.0)                                                  # trace
    with pytest.raises(ValueError):
        mc.eigh([[1.0, 2.0], [0.0, 1.0]])


# ---------------------------------------------------------------- recovery
def test_full_rotation_recovers_offset_and_matrix():
    res = mc.fit(rotation(400, noise=0.05), R)
    assert res.ok and not res.reasons
    eb, em = _errors(res)
    assert eb < 0.05 and em < 0.002
    assert res.bins_hit == 26 and res.coverage == 1.0
    assert res.residual_rms_ut == pytest.approx(0.05, rel=0.3)
    assert res.residual_rms_quantised_ut < 0.1
    assert res.condition < 100 and 1.0 < res.axis_ratio < 1.3


def test_partial_coverage_with_heavy_noise_still_recovers():
    # Directions with z > -0.3 only (about two thirds of the sphere), 0.3 uT noise.
    res = mc.fit(rotation(400, keep=lambda u: u[2] > -0.3, noise=0.3, seed=3), R)
    assert res.ok, res.reasons
    eb, em = _errors(res)
    assert eb < 0.3 and em < 0.01
    assert 0.5 <= res.coverage < 1.0


def test_hemisphere_recovers_within_a_looser_tolerance():
    res = mc.fit(rotation(400, keep=lambda u: u[2] > 0.0, noise=0.05, seed=4), R)
    assert res.ok, res.reasons
    eb, em = _errors(res)
    assert eb < 0.1 and em < 0.005


@pytest.mark.parametrize("keep", [lambda u: abs(u[2]) < 0.05,                         # planar spin
                                  lambda u: u[0] > math.cos(math.radians(30)),        # 30 deg cone
                                  lambda u: u[0] > math.cos(math.radians(60))])       # 60 deg cone
def test_poor_coverage_is_refused(keep):
    res = mc.fit(rotation(300, keep=keep, noise=0.05, seed=5), R)
    assert not res.ok and res.encoded is None
    assert any("orientations" in r for r in res.reasons)
    with pytest.raises(ValueError):
        res.magcal_args(0)


def test_degenerate_and_insufficient_data_are_refused():
    assert not mc.fit(rotation(mc.MIN_SAMPLES - 1), R).ok
    assert "samples" in mc.fit([(10.0, 20.0, 30.0)] * 60, R).reasons[0]            # no motion
    rng = random.Random(9)
    cloud = [tuple(rng.uniform(-50, 50) for _ in range(3)) for _ in range(300)]    # not an ellipsoid
    assert not mc.fit(cloud, R).ok


def test_out_of_range_calibration_is_refused_not_clipped():
    far = mc.fit(rotation(300, b=(350.0, 0.0, 0.0), r=30.0), 30.0)                 # 350 uT > 300 uT
    assert not far.ok and any("off_x" in r for r in far.reasons)
    strong = ((2.4, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))                    # diagonal 2.4 > 2.0
    res = mc.fit(rotation(300, s=strong), R)
    assert not res.ok and any("m11" in r for r in res.reasons)


@pytest.mark.parametrize("bad", [[(float("nan"), 0.0, 0.0)], [(900.0, 0.0, 0.0)], [(1.0, 2.0)], [(1.0, 2.0, True)]])
def test_invalid_samples_raise(bad):
    with pytest.raises(ValueError):
        mc.fit(bad * 60, R)


@pytest.mark.parametrize("field", [0.5, 900.0, float("inf"), True, "48"])
def test_invalid_reference_field_raises(field):
    with pytest.raises(ValueError):
        mc.fit(rotation(60), field)


# ---------------------------------------------------------------- MAGCAL encoding
def test_magcal_arguments_are_range_checked_integers():
    res = mc.fit(rotation(400, noise=0.02), R)
    args = res.magcal_args(1)
    assert list(args) == ["unit", "off_x", "off_y", "off_z", "m11", "m22", "m33", "m12", "m13", "m23"]
    assert all(isinstance(v, int) for v in args.values())
    assert args["unit"] == 1
    assert args["off_x"] == round(12.5 / 0.01) and args["off_y"] == round(-7.3 / 0.01) \
        and abs(args["off_z"] - 2010) <= 2
    assert abs(args["m11"] - round(1.10 * 16384)) <= 30 and abs(args["m23"] - round(0.05 * 16384)) <= 30
    assert all(8192 <= args[k] <= 32767 for k in ("m11", "m22", "m33"))
    assert all(abs(args[k]) <= 8192 for k in ("m12", "m13", "m23"))
    off, m = mc.decode(res.encoded)
    assert max(abs(off[i] - res.offset_ut[i]) for i in range(3)) <= 0.005
    assert max(abs(m[i][j] - res.matrix[i][j]) for i in range(3) for j in range(3)) <= 0.5 / 16384
    assert mc.magcal_command(args).startswith("MAGCAL 1 1250 -730 ")
    for bad_unit in (2, -1, True):
        with pytest.raises(ValueError):
            res.magcal_args(bad_unit)


def test_encode_limits_and_clear():
    ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    assert mc.encode((300.0, -300.0, 0.0), ident) == (30000, -30000, 0, 16384, 16384, 16384, 0, 0, 0)
    with pytest.raises(ValueError):
        mc.encode((300.01, 0.0, 0.0), ident)
    with pytest.raises(ValueError):
        mc.encode((0.0, 0.0, 0.0), ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))     # 32768 > 32767
    with pytest.raises(ValueError):
        mc.encode((0.0, 0.0, 0.0), ((0.49, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
    with pytest.raises(ValueError):
        mc.encode((0.0, 0.0, 0.0), ((1.0, 0.51, 0.0), (0.51, 1.0, 0.0), (0.0, 0.0, 1.0)))
    assert mc.clear_args(0) == {"unit": 0, "off_x": 0, "off_y": 0, "off_z": 0, "m11": 16384, "m22": 16384,
                                "m33": 16384, "m12": 0, "m13": 0, "m23": 0}
    with pytest.raises(ValueError):
        mc.clear_args(2)


# ---------------------------------------------------------------- collector
def test_collector_drops_duplicates_bounds_and_fits():
    col = mc.MagCalCollector(0, R, max_samples=60)
    assert not col.add((float("nan"), 0.0, 0.0)) and col.rejected == 1
    pts = rotation(80, seed=11)
    assert col.add(pts[0]) and not col.add((pts[0][0] + 0.01, pts[0][1], pts[0][2]))   # same orientation
    assert col.duplicates == 1
    for p in pts[1:]:
        col.add(p)
    assert len(col.samples) == 60 and col.rejected == 1 + 20
    live = col.live_coverage()
    assert live["n"] == 60 and live["bins_hit"] >= 20
    assert max(abs(live["centre_ut"][i] - B_TRUE[i]) for i in range(3)) < 5.0      # rough hard-iron estimate
    assert col.fit().ok
    col.reset()
    assert col.samples == [] and col.live_coverage()["bins_hit"] == 0
    with pytest.raises(ValueError):
        mc.MagCalCollector(3, R)
    with pytest.raises(ValueError):
        mc.MagCalCollector(0, R, max_samples=10)


def test_live_coverage_grows_as_the_operator_rotates():
    col = mc.MagCalCollector(1, R)
    for p in rotation(200, keep=lambda u: u[2] > 0.7, seed=12):
        col.add(p)
    small = col.live_coverage()["bins_hit"]
    for p in rotation(400, seed=13):
        col.add(p)
    assert col.live_coverage()["bins_hit"] == 26 > small
    summary = col.fit().as_dict()
    assert summary["ok"] and summary["bins"] == 26 and len(summary["encoded"]) == 9
