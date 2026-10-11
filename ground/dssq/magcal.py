"""Ground magnetometer calibration: hard-iron offset and soft-iron matrix from a rotation.

Contract v3 24.6 (the "MAGROL" spacecraft-roll analogue) and the MAGCAL telecommand of 24.1.
Pure Python, standard library only; every loop is bounded by the sample count or by a fixed
iteration limit.

Model
  The RM3100 reports B_raw = S^-1 B_true + b + noise. While the operator rotates the
  spacecraft in a constant ambient field of magnitude R (config [science] field_ut, e.g. the
  WMM total field at the site), B_raw lies on an ellipsoid. The general quadric

      A x^2 + B y^2 + C z^2 + 2D xy + 2E xz + 2F yz + 2G x + 2H y + 2I z = 1

  (9 parameters) is fitted by linear least squares; the normal equations are solved by
  Gaussian elimination with partial pivoting. With Q = [[A,D,E],[D,B,F],[E,F,C]] and
  v = (G,H,I): centre b = -Q^-1 v, k = 1 + b^T Q b, and (x-b)^T (Q/k) (x-b) = 1. The flight
  applies B_cal = M (B_raw - b) (autonomy.*, ATTITUDE path only) with M = R sqrtm(Q/k), the
  symmetric positive-definite square root, so |B_cal| = R on the fitted ellipsoid. Only the
  symmetric part of a soft-iron distortion is observable from field magnitudes; a pure
  rotation of the sensor frame is not (and is not wanted: it would change the attitude
  reference).
  Data are centred and scaled to unit RMS radius before fitting (numerical conditioning)
  and transformed back afterwards.

Quality gates (a fit failing any of them is reported, never encoded)
  * at least MIN_SAMPLES vectors, each component within the RM3100 field measurement range
    +/-800 uT (PNI RM3100 user manual r06, Table 3-1);
  * conditioning: condition number of the 9 x 9 normal matrix of the normalised data
    <= MAX_CONDITION (a planar or conical rotation leaves the quadric undetermined);
  * ellipsoid sanity: Q/k positive definite, axis ratio <= MAX_AXIS_RATIO;
  * sphere coverage: fraction of 26 direction bins (cube faces, edges and corners) hit by
    the calibrated unit vectors >= MIN_COVERAGE;
  * residual RMS of |B_cal| - R <= MAX_RESIDUAL_FRACTION x R;
  * the coefficients fit the MAGCAL argument ranges (24.1): offsets in 0.01 uT with
    |value| <= 30000, matrix in Q2.14 (value / 16384) with diagonal 8192..32767 (0.5..2.0)
    and |off-diagonal| <= 8192 (0.5).
  The thresholds are engineering choices of this ground segment (named below), not values
  from a datasheet.

Units: uT for fields, offsets and residuals; matrices dimensionless.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]

RM3100_RANGE_UT = 800.0              # PNI RM3100 manual r06 Table 3-1: -800..+800 uT
FIELD_UT_RANGE = (1.0, RM3100_RANGE_UT)
MIN_SAMPLES = 50                     # >> 9 unknowns so noise averages out
MAX_SAMPLES = 20_000                 # collector bound (memory and fit time)
MIN_STEP_UT = 1.0                    # collector: a sample closer than this to the previous one is a
                                     # duplicate orientation (RM3100 noise is 15-30 nT, Table 3-1)
MIN_COVERAGE = 0.5                   # >= 13 of 26 direction bins
MAX_CONDITION = 1.0e3                # normal-matrix condition number in normalised coordinates
                                     # (synthetic trials: full sphere ~15, hemisphere ~200,
                                     # 60 deg cone ~1.7e3, planar ring >= 1e4)
MAX_AXIS_RATIO = 4.0                 # MAGCAL diagonal range 0.5..2.0 spans a factor of 4
MAX_RESIDUAL_FRACTION = 0.05         # residual RMS <= 5 % of the reference field
JACOBI_MAX_SWEEPS = 64               # cyclic Jacobi converges quadratically; 64 is a hard bound

# MAGCAL argument encoding (contract 24.1)
OFFSET_LSB_UT = 0.01
OFFSET_MAX = 30_000
Q14 = 16_384
DIAG_RANGE = (8_192, 32_767)
OFFDIAG_MAX = 8_192
MAGCAL_ARGS = ("unit", "off_x", "off_y", "off_z", "m11", "m22", "m33", "m12", "m13", "m23")
UNITS = {0: "outboard", 1: "inboard"}

# 26 direction bins: every (i, j, k) in {-1, 0, 1}^3 except the origin, normalised.
_BINS: tuple[Vec3, ...] = tuple(
    (i / n, j / n, k / n)
    for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)
    if (n := math.sqrt(i * i + j * j + k * k)) > 0)


# ----------------------------------------------------------------- linear algebra
def solve(a: Sequence[Sequence[float]], b: Sequence[float]) -> list[float]:
    """Solves a x = b by Gaussian elimination with partial pivoting (n <= 16).
    Raises ValueError when a pivot vanishes relative to the matrix scale (singular)."""
    n = len(b)
    if n == 0 or n > 16 or len(a) != n or any(len(r) != n for r in a):
        raise ValueError("solve: square system of size 1..16 required")
    m = [[float(v) for v in row] + [float(rhs)] for row, rhs in zip(a, b, strict=True)]
    scale = max(abs(v) for row in m for v in row[:n]) or 1.0
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) <= 1e-13 * scale:
            raise ValueError("singular system")
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f != 0.0:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def eigh(a: Sequence[Sequence[float]]) -> tuple[list[float], list[list[float]]]:
    """Eigenvalues and eigenvectors (columns of V) of a real symmetric matrix by the cyclic
    Jacobi method, at most JACOBI_MAX_SWEEPS sweeps. Raises ValueError if not converged."""
    n = len(a)
    m = [[float(v) for v in row] for row in a]
    if n == 0 or any(len(r) != n for r in m) or any(abs(m[i][j] - m[j][i]) > 1e-9 * (abs(m[i][j]) + 1.0)
                                                    for i in range(n) for j in range(n)):
        raise ValueError("eigh: symmetric square matrix required")
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    total = sum(m[i][j] ** 2 for i in range(n) for j in range(n)) or 1.0
    for _ in range(JACOBI_MAX_SWEEPS):
        off = sum(m[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off <= 1e-30 * total:
            return [m[i][i] for i in range(n)], v
        for p in range(n - 1):
            for q in range(p + 1, n):
                if m[p][q] == 0.0:
                    continue
                theta = (m[q][q] - m[p][p]) / (2.0 * m[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):                      # A <- A J (columns p, q)
                    akp, akq = m[k][p], m[k][q]
                    m[k][p], m[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):                      # A <- J^T A (rows p, q)
                    apk, aqk = m[p][k], m[q][k]
                    m[p][k], m[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    raise ValueError("eigh: Jacobi iteration did not converge")


def _matvec(m: Sequence[Sequence[float]], x: Sequence[float]) -> Vec3:
    return (m[0][0] * x[0] + m[0][1] * x[1] + m[0][2] * x[2],
            m[1][0] * x[0] + m[1][1] * x[1] + m[1][2] * x[2],
            m[2][0] * x[0] + m[2][1] * x[1] + m[2][2] * x[2])


def _norm(x: Sequence[float]) -> float:
    return math.sqrt(x[0] * x[0] + x[1] * x[1] + x[2] * x[2])


# ------------------------------------------------------------------ validation
def _vec(v: object, what: str) -> Vec3:
    if not isinstance(v, (tuple, list)) or len(v) != 3:
        raise ValueError(f"{what} must be a 3-vector, got {v!r}")
    out = []
    for c in v:
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c):
            raise ValueError(f"{what} component {c!r} is not a finite number")
        if abs(c) > RM3100_RANGE_UT:
            raise ValueError(f"{what} component {c} uT outside the RM3100 range +/-{RM3100_RANGE_UT:g} uT")
        out.append(float(c))
    return out[0], out[1], out[2]


def _field(field_ut: object) -> float:
    if isinstance(field_ut, bool) or not isinstance(field_ut, (int, float)) or not math.isfinite(field_ut) \
            or not FIELD_UT_RANGE[0] <= field_ut <= FIELD_UT_RANGE[1]:
        raise ValueError(f"reference field must be {FIELD_UT_RANGE[0]:g}..{FIELD_UT_RANGE[1]:g} uT, got {field_ut!r}")
    return float(field_ut)


def coverage(unit_vectors: Iterable[Sequence[float]]) -> tuple[float, int]:
    """(fraction, bins hit) of the 26 direction bins hit by the given directions (each is
    assigned to the bin with the largest dot product; zero vectors are ignored)."""
    hit = [False] * len(_BINS)
    for u in unit_vectors:
        n = _norm(u)
        if n <= 0.0:
            continue
        best = max(range(len(_BINS)), key=lambda i: (_BINS[i][0] * u[0] + _BINS[i][1] * u[1]
                                                     + _BINS[i][2] * u[2]))
        hit[best] = True
    k = sum(hit)
    return k / len(_BINS), k


def sphere_centre(samples: Sequence[Vec3]) -> Vec3 | None:
    """Least-squares sphere centre (|x|^2 = 2 c.x + d, 4 unknowns): a quick hard-iron
    estimate used for the live coverage display. None when the data do not determine it."""
    if len(samples) < 4:
        return None
    ata = [[0.0] * 4 for _ in range(4)]
    atb = [0.0] * 4
    for x in samples:
        row = (2.0 * x[0], 2.0 * x[1], 2.0 * x[2], 1.0)
        rhs = x[0] * x[0] + x[1] * x[1] + x[2] * x[2]
        for i in range(4):
            atb[i] += row[i] * rhs
            for j in range(4):
                ata[i][j] += row[i] * row[j]
    try:
        c = solve(ata, atb)
    except ValueError:
        return None
    return c[0], c[1], c[2]


# ------------------------------------------------------------------ the fit
@dataclass(frozen=True)
class MagCalResult:
    ok: bool
    reasons: tuple[str, ...]             # why the fit is refused (empty when ok)
    n: int
    field_ut: float
    offset_ut: Vec3 | None               # hard-iron offset b
    matrix: Mat3 | None                  # symmetric soft-iron matrix M (B_cal = M (B_raw - b))
    residual_rms_ut: float | None        # RMS of |B_cal| - R
    residual_rms_quantised_ut: float | None   # same, with the MAGCAL-encoded coefficients
    coverage: float                      # fraction of the 26 direction bins hit
    bins_hit: int
    condition: float | None              # normal-matrix condition number (normalised coordinates)
    axis_ratio: float | None             # longest / shortest ellipsoid semi-axis
    encoded: tuple[int, ...] | None      # (off_x, off_y, off_z, m11, m22, m33, m12, m13, m23)

    def magcal_args(self, unit: int) -> dict[str, int]:
        """MAGCAL arguments (contract 24.1) in dictionary order. Raises ValueError when the
        fit was refused or the unit is not 0 (outboard) / 1 (inboard)."""
        if not self.ok or self.encoded is None:
            raise ValueError("calibration refused: " + "; ".join(self.reasons))
        if isinstance(unit, bool) or unit not in UNITS:
            raise ValueError("unit must be 0 (outboard) or 1 (inboard)")
        return dict(zip(MAGCAL_ARGS, (unit, *self.encoded), strict=True))

    def as_dict(self) -> dict:
        """JSON-ready summary for the web UI."""
        return {"ok": self.ok, "reasons": list(self.reasons), "n": self.n, "field_ut": self.field_ut,
                "offset_ut": None if self.offset_ut is None else list(self.offset_ut),
                "matrix": None if self.matrix is None else [list(r) for r in self.matrix],
                "residual_rms_ut": self.residual_rms_ut,
                "residual_rms_quantised_ut": self.residual_rms_quantised_ut,
                "coverage": self.coverage, "bins_hit": self.bins_hit, "bins": len(_BINS),
                "condition": self.condition, "axis_ratio": self.axis_ratio,
                "encoded": None if self.encoded is None else list(self.encoded)}


def encode(offset_ut: Sequence[float], matrix: Sequence[Sequence[float]]) -> tuple[int, ...]:
    """(off_x, off_y, off_z [0.01 uT], m11, m22, m33, m12, m13, m23 [Q2.14]); raises
    ValueError naming the first value outside the MAGCAL argument ranges."""
    if len(offset_ut) != 3 or len(matrix) != 3 or any(len(r) != 3 for r in matrix):
        raise ValueError("3-vector offset and 3 x 3 matrix required")
    offs = tuple(round(o / OFFSET_LSB_UT) for o in offset_ut)
    for name, v in zip(("off_x", "off_y", "off_z"), offs, strict=True):
        if abs(v) > OFFSET_MAX:
            raise ValueError(f"{name} = {v * OFFSET_LSB_UT:.2f} uT exceeds +/-{OFFSET_MAX * OFFSET_LSB_UT:g} uT")
    diag = tuple(round(matrix[i][i] * Q14) for i in range(3))
    off = tuple(round(matrix[i][j] * Q14) for i, j in ((0, 1), (0, 2), (1, 2)))
    for name, v in zip(("m11", "m22", "m33"), diag, strict=True):
        if not DIAG_RANGE[0] <= v <= DIAG_RANGE[1]:
            raise ValueError(f"{name} = {v / Q14:.4f} outside 0.5..2.0 (Q2.14 {DIAG_RANGE[0]}..{DIAG_RANGE[1]})")
    for name, v in zip(("m12", "m13", "m23"), off, strict=True):
        if abs(v) > OFFDIAG_MAX:
            raise ValueError(f"{name} = {v / Q14:.4f} outside +/-0.5 (Q2.14 +/-{OFFDIAG_MAX})")
    return (*offs, *diag, *off)


def decode(encoded: Sequence[int]) -> tuple[Vec3, Mat3]:
    """Inverse of encode(): what the flight software will apply."""
    if len(encoded) != 9:
        raise ValueError("9 MAGCAL coefficients expected")
    ox, oy, oz, m11, m22, m33, m12, m13, m23 = (int(v) for v in encoded)
    off = (ox * OFFSET_LSB_UT, oy * OFFSET_LSB_UT, oz * OFFSET_LSB_UT)
    m = ((m11 / Q14, m12 / Q14, m13 / Q14), (m12 / Q14, m22 / Q14, m23 / Q14), (m13 / Q14, m23 / Q14, m33 / Q14))
    return off, m


def clear_args(unit: int) -> dict[str, int]:
    """MAGCAL arguments that clear the onboard calibration: identity matrix, zero offsets."""
    if isinstance(unit, bool) or unit not in UNITS:
        raise ValueError("unit must be 0 (outboard) or 1 (inboard)")
    return dict(zip(MAGCAL_ARGS, (unit, 0, 0, 0, Q14, Q14, Q14, 0, 0, 0), strict=True))


def magcal_command(args: dict[str, int]) -> str:
    """Operator command text 'MAGCAL <unit> <off_x> ... <m23>' for the dictionary encoder."""
    return "MAGCAL " + " ".join(str(int(args[k])) for k in MAGCAL_ARGS)


def _residual_rms(samples: Sequence[Vec3], b: Sequence[float], m: Sequence[Sequence[float]], r: float) -> float:
    acc = 0.0
    for x in samples:
        acc += (_norm(_matvec(m, (x[0] - b[0], x[1] - b[1], x[2] - b[2]))) - r) ** 2
    return math.sqrt(acc / len(samples))


def _refused(reasons: list[str], n: int, r: float, **kw: object) -> MagCalResult:
    base: dict[str, object] = {"offset_ut": None, "matrix": None, "residual_rms_ut": None,
                               "residual_rms_quantised_ut": None, "coverage": 0.0, "bins_hit": 0,
                               "condition": None, "axis_ratio": None, "encoded": None}
    base.update(kw)
    return MagCalResult(ok=False, reasons=tuple(reasons), n=n, field_ut=r, **base)   # type: ignore[arg-type]


def fit(samples: Iterable[Sequence[float]], field_ut: float) -> MagCalResult:
    """Ellipsoid fit of raw vectors (uT) to a reference field magnitude (uT). Raises
    ValueError only for malformed input (non-finite or out-of-range values, more than
    MAX_SAMPLES vectors); a geometrically insufficient data set returns a refused result
    whose reasons say why. The reference field only scales M, so a wrong field_ut cannot be
    detected here: it must be the site's WMM total field at the time of the rotation."""
    r = _field(field_ut)
    pts = [_vec(s, f"sample {i}") for i, s in enumerate(samples)]
    n = len(pts)
    if n > MAX_SAMPLES:
        raise ValueError(f"more than {MAX_SAMPLES} samples")
    if n < MIN_SAMPLES:
        return _refused([f"{n} samples, at least {MIN_SAMPLES} needed"], n, r)
    c0 = tuple(sum(p[k] for p in pts) / n for k in range(3))
    s = math.sqrt(sum((p[0] - c0[0]) ** 2 + (p[1] - c0[1]) ** 2 + (p[2] - c0[2]) ** 2 for p in pts) / n)
    if s < 1e-3:
        return _refused(["samples do not move (spread below 1 nT)"], n, r)
    ata = [[0.0] * 9 for _ in range(9)]
    atb = [0.0] * 9
    for p in pts:
        x, y, z = ((p[k] - c0[k]) / s for k in range(3))
        row = (x * x, y * y, z * z, 2 * x * y, 2 * x * z, 2 * y * z, 2 * x, 2 * y, 2 * z)
        for i in range(9):
            atb[i] += row[i]
            ri = row[i]
            for j in range(i, 9):
                ata[i][j] += ri * row[j]
    for i in range(9):
        for j in range(i):
            ata[i][j] = ata[j][i]
    # Mean normal matrix in the normalised coordinates: its columns are O(1) for any data set,
    # so its condition number measures the geometry (how well the rotation pins down all nine
    # quadric terms), not the units.
    nm = [[ata[i][j] / n for j in range(9)] for i in range(9)]
    try:
        lam, _ = eigh(nm)
        cond = math.inf if min(lam) <= 0.0 else max(lam) / min(lam)
        if cond > MAX_CONDITION:
            return _refused([f"ill-conditioned fit (condition {cond:.3g} > {MAX_CONDITION:.3g}): "
                             "rotate through more orientations"], n, r, condition=cond)
        p9 = solve(nm, [atb[i] / n for i in range(9)])
    except ValueError as e:
        return _refused([f"normal equations not solvable ({e}): rotate through more orientations"], n, r)
    qm = [[p9[0], p9[3], p9[4]], [p9[3], p9[1], p9[5]], [p9[4], p9[5], p9[2]]]
    v = [p9[6], p9[7], p9[8]]
    try:
        bn = [-c for c in solve(qm, v)]
    except ValueError:
        return _refused(["fitted quadric has no centre (not an ellipsoid)"], n, r, condition=cond)
    k = 1.0 + sum(bn[i] * qm[i][j] * bn[j] for i in range(3) for j in range(3))
    if k <= 0.0:
        return _refused(["fitted quadric is not an ellipsoid"], n, r, condition=cond)
    try:
        lam3, vec3 = eigh([[qm[i][j] / k for j in range(3)] for i in range(3)])
    except ValueError:
        return _refused(["fitted quadric has no eigen-decomposition (not an ellipsoid)"], n, r, condition=cond)
    if min(lam3) <= 0.0:
        return _refused(["fitted quadric is not an ellipsoid (non-positive axis)"], n, r, condition=cond)
    axes = [1.0 / math.sqrt(x) for x in lam3]
    ratio = max(axes) / min(axes)
    # Physical units: x = c0 + s x'  ->  b = c0 + s b',  M = (R / s) V diag(sqrt(lambda)) V^T.
    b = (c0[0] + s * bn[0], c0[1] + s * bn[1], c0[2] + s * bn[2])
    sq = [math.sqrt(x) for x in lam3]
    m = tuple(tuple(r / s * sum(vec3[i][t] * sq[t] * vec3[j][t] for t in range(3)) for j in range(3))
              for i in range(3))
    mat: Mat3 = (m[0], m[1], m[2])                                  # type: ignore[assignment]
    rms = _residual_rms(pts, b, mat, r)
    cov, hits = coverage(_matvec(mat, (pt[0] - b[0], pt[1] - b[1], pt[2] - b[2])) for pt in pts)
    reasons = []
    if ratio > MAX_AXIS_RATIO:
        reasons.append(f"axis ratio {ratio:.2f} > {MAX_AXIS_RATIO:g}: implausible soft-iron distortion")
    if cov < MIN_COVERAGE:
        reasons.append(f"sphere coverage {hits}/{len(_BINS)} bins < {MIN_COVERAGE:.0%}: rotate through more "
                       "orientations")
    if rms > MAX_RESIDUAL_FRACTION * r:
        reasons.append(f"residual RMS {rms:.3f} uT > {MAX_RESIDUAL_FRACTION:.0%} of the reference field "
                       "(data not on one ellipsoid: moving field source, changing ambient field or bad samples)")
    encoded = None
    rms_q = None
    try:
        encoded = encode(b, mat)
        qb, qm3 = decode(encoded)
        rms_q = _residual_rms(pts, qb, qm3, r)
    except ValueError as e:
        reasons.append(f"not representable as MAGCAL arguments: {e}")
    return MagCalResult(ok=not reasons, reasons=tuple(reasons), n=n, field_ut=r, offset_ut=b, matrix=mat,
                        residual_rms_ut=rms, residual_rms_quantised_ut=rms_q, coverage=cov, bins_hit=hits,
                        condition=cond, axis_ratio=ratio, encoded=None if reasons else encoded)


# ------------------------------------------------------------------ collector
class MagCalCollector:
    """Bounded sample store for one magnetometer unit during an operator rotation. add()
    validates every vector and drops repeats of the previous orientation (MIN_STEP_UT), so a
    spacecraft at rest cannot dominate the fit."""

    def __init__(self, unit: int, field_ut: float, max_samples: int = MAX_SAMPLES):
        if isinstance(unit, bool) or unit not in UNITS:
            raise ValueError("unit must be 0 (outboard) or 1 (inboard)")
        if isinstance(max_samples, bool) or not isinstance(max_samples, int) or \
                not MIN_SAMPLES <= max_samples <= MAX_SAMPLES:
            raise ValueError(f"max_samples must be {MIN_SAMPLES}..{MAX_SAMPLES}")
        self.unit = unit
        self.field_ut = _field(field_ut)
        self.max_samples = max_samples
        self.samples: list[Vec3] = []
        self.rejected = 0
        self.duplicates = 0

    def add(self, b_ut: Sequence[float]) -> bool:
        """True when stored; False for an invalid vector, a duplicate or a full buffer."""
        try:
            v = _vec(b_ut, "sample")
        except ValueError:
            self.rejected += 1
            return False
        if self.samples:
            p = self.samples[-1]
            if _norm((v[0] - p[0], v[1] - p[1], v[2] - p[2])) < MIN_STEP_UT:
                self.duplicates += 1
                return False
        if len(self.samples) >= self.max_samples:
            self.rejected += 1
            return False
        self.samples.append(v)
        return True

    def reset(self) -> None:
        self.samples.clear()
        self.rejected = self.duplicates = 0

    def live_coverage(self) -> dict:
        """Coverage estimate before a fit: directions from a least-squares sphere centre."""
        c = sphere_centre(self.samples)
        frac, hits = (0.0, 0) if c is None else coverage(
            (x[0] - c[0], x[1] - c[1], x[2] - c[2]) for x in self.samples)
        return {"unit": self.unit, "n": len(self.samples), "coverage": frac, "bins_hit": hits,
                "bins": len(_BINS), "centre_ut": None if c is None else list(c),
                "rejected": self.rejected, "duplicates": self.duplicates}

    def fit(self) -> MagCalResult:
        return fit(self.samples, self.field_ut)
