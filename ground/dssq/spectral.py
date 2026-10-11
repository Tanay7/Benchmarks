"""AS7265X ground spectral analytics: packet decoding, radiometry, colorimetry, instrument science.

Implements the ground half of contract sections 8 and 13 (13.3 logarithmic spectral code,
13.4 SPEC v3.1 field set, 13.7 ground analytics), the SPECBURST packet of contract 28 /
Phase B PB-1.12 (raw or CCSDS 121.0-B-3 "Rice" payload) and the AS7265X cross-check
decoding rules of contract 26 (PB-1.16) for the DSS-Q ground data system. Pure Python
(standard library only); the only I/O is the JSON persistence of the white reference and
of the spectral library.

Governing documents
  ams AS7265x datasheet v1-04 (2018-07-09): 18 channels 410..940 nm, 20 nm FWHM, centre
    wavelength accuracy +/-10 nm, calibrated counts/(uW/cm2) accurate to +/-12 %, dark counts
    <= 5 (GAIN 64x, 165 ms, 25 degC), average field of view +/-20.5 deg, internal temperature
    accuracy +/-8.5 degC; integration time (N + 1) x 2.78 ms (Figure 30); GAIN 1/3.7/16/64x
    (Figure 29).
  C. Wyman, P.-P. Sloan, P. Shirley, "Simple Analytic Approximations to the CIE XYZ Color
    Matching Functions", JCGT 2(2), 2013: multi-lobe piecewise-Gaussian fit of the CIE 1931
    2 deg observer (lobe constants in contract section 8).
  CIE 015:2018 Colorimetry: XYZ, chromaticity xy, CIE 1960 UCS (u, v), CCT and Duv.
  C. S. McCamy, Color Res. Appl. 17(2), 1992: explicit CCT(x, y), used as a cross-check only.
  Vegetation indices: NDVI (Rouse 1974), GNDVI (Gitelson 1996), NDRE (Barnes 2000), WBI
    (Penuelas 1993: R900/R970) and PRI (Gamon 1992: R531/R570). WBI and PRI are evaluated on
    the nearest AS7265X bands and are therefore reported as "proxy".

Units
  Spectral values are the chip's calibrated output in uW/cm2 (band irradiance of one 20 nm
  channel); uncertainties are 1-sigma in the same unit. Radiometric results: W/m2,
  umol m-2 s-1 (PPFD) and lux. Angles: degrees. Times: t = UNIX seconds (SCET when the GDS
  knows it, else ERT); burst sample offsets in milliseconds.

The single radiometric assumption (contract 13.7)
  Each calibrated channel value is the band irradiance through a 20 nm FWHM filter, so the
  spectral irradiance at the channel centre is value / 20 nm. Integrals use the trapezoid rule
  over the channel centres, continued at constant level for half a FWHM (10 nm) beyond the
  outermost centre. Everything derived from it is labelled "approximate (18-band)".

Conventions
  Channel index i is wavelength order (0 = 410 nm ... 17 = 940 nm). None means "not
  available". Every spectral value or uncertainty entering from outside (packet, file) is
  range-checked to |v| <= VALUE_ABS_MAX_UW and quantised to 0 below the code floor 1e-4, so
  no derived quantity can overflow; NaN and infinities never leave this module and every
  output passes json.dumps(..., allow_nan=False). Every loop is bounded by a channel count,
  a named capacity or the length of an input that was range-checked first. Heuristic
  thresholds are named constants.

Thread safety: none of its own. The GDS calls SpectralState while holding its state lock.
"""
from __future__ import annotations

import cmath
import itertools
import json
import math
import os
import re
import statistics
import struct
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .ccsds import rice

# =============================================================================
# Instrument constants (AS7265x datasheet v1-04)
# =============================================================================
WAVELENGTHS_NM: tuple[int, ...] = (410, 435, 460, 485, 510, 535, 560, 585, 610, 645, 680, 705,
                                   730, 760, 810, 860, 900, 940)
N_CH = len(WAVELENGTHS_NM)
CHANNEL_LETTERS = ("A", "B", "C", "D", "E", "F", "G", "H", "R", "I", "S", "J", "T", "U", "V",
                   "W", "K", "L")
N_VIS = 12                                  # 410..705 nm: the bands used for colorimetry
# Wavelength-order channels of each device: the flight kSpecSlotChannel table (SparkFun
# AS7265X mapping; the datasheet's per-register captions are inconsistent and not used).
DEVICE_CHANNELS: tuple[tuple[int, ...], ...] = ((8, 10, 12, 13, 14, 15),   # AS72651 NIR
                                                (6, 7, 9, 11, 16, 17),     # AS72652 VIS
                                                (0, 1, 2, 3, 4, 5))        # AS72653 UV
DEVICE_NAMES = ("AS72651 (NIR)", "AS72652 (VIS)", "AS72653 (UV)")
GAIN_X = (1.0, 3.7, 16.0, 64.0)             # Figure 29, GAIN codes 0..3
CYCLE_MS = 2.78                             # Figure 30: integration = (N + 1) x 2.78 ms
LED_DRV_MA = (12.5, 25.0, 50.0, 100.0)      # LED_DRV current codes 0..3
FWHM_NM = 20.0
WAVELENGTH_ACC_NM = 10.0
CAL_ACCURACY_REL = 0.12
FOV_HALF_DEG = 20.5
DARK_COUNTS_MAX = 5
TEMP_ACCURACY_C = 8.5
DATASHEET_NOTES = (
    ("calibrated counts/(uW/cm2) accurate to +/-12 % (systematic per channel; cancels in "
     "reflectance against a white reference taken with the same set-up)"),
    "centre wavelength accuracy +/-10 nm, 20 nm FWHM per channel",
    "average field of view +/-20.5 deg; calibration made with diffused light",
    "dark counts <= 5 at GAIN 64x, 165 ms, 25 degC",
    "die temperature accuracy +/-8.5 degC",
    ("uncertainties shown are the packet's 1-sigma statistical values (counting/dark model "
     "or lock-in standard error); the systematic +/-12 % is not included"),
)

MODE_NAMES = {0: "SURVEY", 1: "HDR", 2: "LOCKIN", 3: "FLUOR", 4: "BURST"}
MODE_HDR = 1
MODE_FLUOR = 3
MODE_BURST = 4
# HDR packets (flight spectral.h, contract 26 AS-DP-04): gain_code / int_cycles are the AGC
# centre of the bracket, and each channel's raw count comes from the exposure chosen for that
# channel, so raw / full_scale(int_cycles) is not a meaningful fraction.
HDR_EXPOSURE_NOTE = ("bracket centre, not necessarily an exposure taken; raw[] from the frame "
                     "chosen per channel")
CLASS_NAMES = {0: "UNKNOWN", 1: "DARK", 2: "DAYLIGHT", 3: "INCANDESCENT/HALOGEN", 4: "WHITE LED",
               5: "FLUORESCENT", 6: "NARROWBAND", 7: "OWN LAMP"}
CLASS_UNKNOWN, CLASS_DARK, CLASS_DAYLIGHT, CLASS_INCANDESCENT = 0, 1, 2, 3
CLASS_WHITE_LED, CLASS_FLUORESCENT, CLASS_NARROWBAND, CLASS_OWN_LAMP = 4, 5, 6, 7

# SPEC v3.1 flag bits (contract 13.4)
F_VALID, F_WHITE, F_IR, F_UV, F_AGC, F_ACTIVE, F_SAT, F_ATTITUDE = (1 << b for b in range(8))
LAMP_NAMES = ("white", "IR", "UV")          # flags b1..b3 / SPECCFG lamps b0..b2

# Highest LED_DRV current code each board lamp may be driven at, by device = lamp bit
# (flight spectral.h kSpecLedMaxCode; vendor limits from SparkFun AS7265x 1.0.5
# Example3_Settings: white 120 mA max -> all codes, IR 65 mA -> <= 50 mA, UV 30 mA ->
# 12.5 mA only; contract 26). SPECCFG led_cur: b1:0 white, b3:2 IR, b5:4 UV.
LED_MAX_CODE = (3, 2, 0)


def led_cur_allowed(led_cur: int) -> bool:
    """True when every lamp code of led_cur respects LED_MAX_CODE, i.e. the flight's
    spec_led_cur_applied(v) == v. The allowed set is exactly 0..11."""
    if isinstance(led_cur, bool) or not isinstance(led_cur, int) or not 0 <= led_cur <= 63:
        return False
    return all((led_cur >> (2 * d)) & 3 <= LED_MAX_CODE[d] for d in range(3))


LED_CUR_ALLOWED: tuple[int, ...] = tuple(v for v in range(64) if led_cur_allowed(v))

APPROX_LABEL = "approximate (18-band)"
HEURISTIC_LABEL = "heuristic"

# Ground directives handled here (the GDS routes them; texts merge into GROUND_DIRECTIVES).
DIRECTIVE_HELP = {
    "SPECREF": "SPECREF [CLEAR] - capture the latest spectrum as white reference / drop it",
    "SPECLIB": "SPECLIB SAVE <name> | DEL <name> | LIST - named spectral library "
               "(best spectral-angle match shown live)",
}
# Onboard spectrometer EVRs 0x0701..0x0707 (PB-2.6): spectral event, light-source change,
# SPECDIAG line, burst start/end, soft-reset recovery, identification, driver state.
EVR_SPECTRAL_IDS = range(0x0701, 0x0708)


# =============================================================================
# Small numeric helpers (input validation: every external value passes through one)
# =============================================================================
def _num(x: Any) -> float | None:
    """Finite float, else None. bool is not accepted as a number."""
    if x is None or isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        f = float(x)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _int(x: Any, lo: int, hi: int) -> int | None:
    """Integer in [lo, hi] (an integral float is accepted), else None."""
    f = _num(x)
    if f is None or f != math.floor(f) or not lo <= f <= hi:
        return None
    return int(f)


def _sig(x: float | None, digits: int = 5) -> float | None:
    """Rounds to `digits` significant digits (compact JSON for the history rows)."""
    if x is None:
        return None
    if x == 0.0:
        return 0.0
    return round(x, digits - 1 - math.floor(math.log10(abs(x))))


def full_scale(int_cycles: int) -> int:
    """Maximum ADC value for integration value N: min(65535, 1024 (N + 1) - 1) (Figure 30)."""
    return min(65535, 1024 * (int_cycles + 1) - 1)


def integration_ms(int_cycles: int) -> float:
    return (int_cycles + 1) * CYCLE_MS


# =============================================================================
# Logarithmic spectral code (contract 13.3), identical to flight spec_log_encode/decode
# =============================================================================
LOG_FLOOR_UW = 1e-4          # |v| below this encodes as m = 0 (value 0)
LOG_STEPS_PER_OCTAVE = 1024  # code step 2^(1/1024) - 1 = 0.0677 %
LOG_M_MAX = 32766            # largest magnitude code: 1e-4 x 2^(32765/1024) = 4.29e5 uW/cm2
LOG_CODE_NAN = 0xFFFF
_F32 = struct.Struct("<f")


def to_f32(x: float) -> float:
    """Nearest IEEE binary32 value (round half to even); +/-inf beyond the binary32 range."""
    try:
        return _F32.unpack(_F32.pack(x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


_FLOOR_F32 = to_f32(LOG_FLOOR_UW)           # the flight's 1e-4f = 9.99999975e-05


def log_encode(v: float | None) -> int:
    """u16 code of a signed value in uW/cm2, bit-identical to the flight spec_log_encode():
    the value is rounded to binary32 and every step is evaluated in binary32 exactly as
    there (a / 1e-4f, log2f, 1 + 1024 x, floorf(x + 0.5f); half-up, not Python's half-even
    round()), assuming a correctly rounded log2f. None / NaN / non-numbers -> 0xFFFF;
    |v| < 1e-4f -> sign bit only (m = 0); +/-inf and |v| >= 4.29e5 saturate at m = 32766."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return LOG_CODE_NAN
    try:
        f = to_f32(float(v))
    except OverflowError:                    # an int beyond the binary64 range
        f = math.inf if v > 0 else -math.inf
    if math.isnan(f):
        return LOG_CODE_NAN
    s = 0x8000 if f < 0.0 else 0
    a = abs(f)
    if not a >= _FLOOR_F32:
        return s
    x = to_f32(1.0 + LOG_STEPS_PER_OCTAVE * to_f32(math.log2(to_f32(a / _FLOOR_F32))))
    if not x < LOG_M_MAX:                    # also +/-inf
        return s | LOG_M_MAX
    return s | math.floor(to_f32(x + 0.5))


def log_decode(code: int) -> float:
    """Value in uW/cm2 of a u16 code: 0xFFFF -> NaN, m = 0 -> 0.0 (either sign, like the
    flight), else (s ? -1 : 1) x 1e-4 x 2^((m - 1)/1024). Exact (FP64) form of the
    contract formula; raises ValueError for anything that is not an integer 0..0xFFFF."""
    if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 0xFFFF:
        raise ValueError(f"log code {code!r} is not a u16")
    if code == LOG_CODE_NAN:
        return math.nan
    m = code & 0x7FFF
    if m == 0:
        return 0.0
    a = LOG_FLOOR_UW * 2.0 ** ((m - 1) / LOG_STEPS_PER_OCTAVE)
    return -a if code & 0x8000 else a


def log_decode_f32(code: int) -> float:
    """The flight decoder's binary32 result, 1e-4f x exp2f((m - 1)/1024.f), assuming a
    correctly rounded exp2f (cross-check only; the ground uses the exact log_decode). glibc
    2.39's exp2f misrounds a near-tie argument for the 32 codes with (m - 1) mod 1024 = 88,
    where the flight value differs by <= 2 ulp (the exact value is always within 2 ulp)."""
    v = log_decode(code)
    if math.isnan(v) or v == 0.0:
        return v
    m = code & 0x7FFF
    e = to_f32(2.0 ** ((m - 1) / LOG_STEPS_PER_OCTAVE))
    a = to_f32(_FLOOR_F32 * e)               # binary32 x binary32 is exact in binary64
    return -a if code & 0x8000 else a


# Range check for spectral values and uncertainties from outside (packets, files): far above
# any physical band irradiance and 2.3x the largest code value (4.29e5); anything larger is
# corrupt. Together with the code floor it bounds every derived quantity (no overflow).
VALUE_ABS_MAX_UW = 1.0e6


def _value(x: Any, nonneg: bool = False) -> float | None:
    """A validated spectral value / uncertainty: finite, |x| <= VALUE_ABS_MAX_UW (and >= 0
    when nonneg), else None; |x| < 1e-4 -> 0.0, the code floor (nothing smaller can arrive
    over the link)."""
    f = _num(x)
    if f is None or abs(f) > VALUE_ABS_MAX_UW or (nonneg and f < 0.0):
        return None
    return 0.0 if abs(f) < LOG_FLOOR_UW else f


# =============================================================================
# Packet field sets: SPEC v3.1 (APID 0x022, 129 octets) and SPECBURST (APID 0x024)
# =============================================================================
APID_SPEC = 0x022
APID_SPECBURST = 0x024
SPEC_V31_LEN = 129
# Unavailable sentinels per struct code (identical to dictionary.NA).
_NA = {"h": 0x7FFF, "H": 0xFFFF, "i": 0x7FFFFFFF, "I": 0xFFFFFFFF, "b": -128}
_RANGE = {"B": (0, 0xFF), "b": (-128, 127), "H": (0, 0xFFFF), "h": (-32768, 32767),
          "I": (0, 0xFFFFFFFF), "i": (-(1 << 31), (1 << 31) - 1)}


def round_half_away(x: float) -> int:
    """Nearest integer, halves away from zero (C lroundf, which the flight packers use)."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def azimuth_from_cdeg(raw: int) -> float:
    """bore_az decoder: the flight sends 0.01 deg wrapped to (-180, 180] (0..360 deg does not
    fit an i16); the ground reports degrees from north toward east, 0 <= az < 360."""
    return (raw * 0.01) % 360.0


def azimuth_to_cdeg(deg: float) -> int:
    """Inverse of azimuth_from_cdeg: degrees -> 0.01 deg wrapped to (-180, 180]."""
    f = deg % 360.0
    f = f - 360.0 if f > 180.0 else f
    return round_half_away(f * 100.0)


# Dictionary-format field tuples with a 7th element: an optional decoder applied to the raw
# integer after the unavailable-sentinel check (scale is then None). This list is the single
# ground definition of SPEC v3.1; dictionary.py splices it into PACKETS[0x022].
FieldDef = tuple[str, str, float | None, str, str, tuple | None, Callable[[int], Any] | None]
SPEC_V31_FIELDS: list[FieldDef] = [
    ("spec_mode", "B", None, "", "Acquisition mode (0 SURVEY, 1 HDR, 2 LOCKIN, 3 FLUOR, 4 BURST)",
     None, None),
    ("flags", "B", None, "", ("b0 valid b1 white b2 IR b3 UV lamp b4 AGC b5 active (on-off) "
                              "b6 saturated b7 attitude valid"), None, None),
    ("gain_code", "B", None, "", "Gain code (0 1x, 1 3.7x, 2 16x, 3 64x)", None, None),
    ("int_cycles", "B", None, "", "Base integration value N: (N + 1) x 2.78 ms", None, None),
    ("led_cur", "B", None, "", "LED_DRV codes b1:0 white b3:2 IR b5:4 UV (12.5/25/50/100 mA)",
     None, None),
    ("n_frames", "B", None, "", "Exposures merged / on-off pairs (1 for SURVEY)", None, None),
    ("sat_mask", "I", None, "", "Saturated channels, bit i = channel i (wavelength order)",
     None, None),
    *[(f"spec_{nm}nm", "H", None, "uW/cm2", f"Spectrum {nm} nm (log code, decoded)", None,
       log_decode) for nm in WAVELENGTHS_NM],
    *[(f"unc_{nm}nm", "H", None, "uW/cm2", f"1-sigma uncertainty {nm} nm (log code, decoded)",
       None, log_decode) for nm in WAVELENGTHS_NM],
    *[(f"raw_{nm}nm", "H", None, "cts", f"Raw counts {nm} nm (0xFFFF = not converted)", None,
       None) for nm in WAVELENGTHS_NM],
    *[(f"temp_{d}", "b", 1.0, "degC", f"Die temperature {DEVICE_NAMES[d]}", None, None)
      for d in range(3)],
    ("bore_zen", "h", 0.01, "deg", "Boresight zenith angle (from local vertical up)", None, None),
    ("bore_az", "h", None, "deg", "Boresight azimuth from north, 0..360 (sent as (-180, 180])",
     None, azimuth_from_cdeg),
    ("spec_class", "B", None, "", "Onboard light-source class (heuristic)", None, None),
    ("spec_conf", "B", None, "%", "Onboard class confidence", None, None),
    ("spec_change", "H", 0.01, "deg", "Spectral angle to the onboard EWMA baseline", None, None),
]
_SPEC_V31 = struct.Struct(">" + "".join(f[1] for f in SPEC_V31_FIELDS))
if _SPEC_V31.size != SPEC_V31_LEN:          # import-time consistency check of the table
    raise RuntimeError(f"SPEC v3.1 field table is {_SPEC_V31.size} octets, not {SPEC_V31_LEN}")


def decode_fields(field_defs: Sequence[FieldDef], raw: Sequence[int]) -> dict[str, Any]:
    """The decom rule with the decoder hook: sentinel -> None, else decoder(raw), else raw
    (scale None) or raw x scale."""
    out: dict[str, Any] = {}
    for (name, code, scale, _unit, _desc, _lim, dec), r in zip(field_defs, raw, strict=True):
        if code in _NA and r == _NA[code]:
            out[name] = None
        elif dec is not None:
            out[name] = dec(r)
        elif scale is None:
            out[name] = r
        else:
            out[name] = r * scale
    return out


def decode_spec_v31(data: bytes) -> dict[str, Any]:
    """SPEC v3.1 user data (secondary header removed) -> engineering values, exactly what
    the dictionary-driven decommutator produces. Raises ValueError if short."""
    if len(data) < SPEC_V31_LEN:
        raise ValueError(f"SPEC v3.1: {len(data)} octets < {SPEC_V31_LEN}")
    return decode_fields(SPEC_V31_FIELDS, _SPEC_V31.unpack_from(data))


def _encode_field(code: str, scale: float | None, dec: Callable[[int], Any] | None,
                  v: Any) -> int:
    lo, hi = _RANGE[code]
    if dec is log_decode:
        return log_encode(v)
    f = _num(v)
    if f is None:
        return _NA.get(code, 0)
    r = azimuth_to_cdeg(f) if dec is azimuth_from_cdeg else round_half_away(f / scale if scale else f)
    return min(hi, max(lo, r))


def pack_spec_v31(values: Mapping[str, Any]) -> bytes:
    """Inverse of decode_spec_v31 (used by the simulator and the cross-check): missing / None
    fields go out as their unavailable sentinel. pack(decode(x)) == x for every packet except
    that the log code 0x8000 ("negative, below 1e-4") decodes to 0.0 and re-encodes as 0x0000,
    as in the flight."""
    return _SPEC_V31.pack(*[_encode_field(code, scale, dec, values.get(name))
                            for name, code, scale, _u, _d, _l, dec in SPEC_V31_FIELDS])


# SPECBURST (APID 0x024, PB-1.12 = contract 28): t0_ms u32 | rate_dhz u16 | gain_code u8 |
# int_cycles u8 | n u8 (1..3) | encoding u8 | dt_ms u16[n] | payload.
#   encoding 0: n x 18 u16 big-endian, TIME-MAJOR (sample 0 channels 0..17, then sample 1 ...)
#   encoding 1: CCSDS 121.0-B-3 (rice.MISSION: n = 16, J = 16, r = 128, Basic, preprocessor
#               on) over the CHANNEL-MAJOR samples cm[ch * n + t] = s[t].raw[ch], 1..36 n - 1
#               octets (the flight sends Rice only when it is strictly shorter than raw).
# Raw codes (contract 26 AS-DP-07): 0xFFFF = channel not converted in the BANK mode (None);
# a real count of 65535 is sent as 0xFFFE (flight spec_burst_raw), so a count is saturated
# when it is >= full_scale(int_cycles) - 1, which includes 0xFFFE.
_BURST_HDR = struct.Struct(">IHBBBB")       # t0_ms, rate_dhz, gain_code, int_cycles, n, encoding
BURST_HDR_LEN = _BURST_HDR.size             # 10 octets (flight kSpecBurstHdrLen)
BURST_PER_PACKET_MAX = 3
BURST_RAW_PER_SAMPLE = 2 * N_CH             # 36 octets
BURST_MAX_LEN = BURST_HDR_LEN + (2 + BURST_RAW_PER_SAMPLE) * BURST_PER_PACKET_MAX   # 124
BURST_RAW, BURST_RICE = 0, 1
BURST_ENCODINGS = {BURST_RAW: "raw", BURST_RICE: "CCSDS 121.0-B-3"}
BURST_NOT_CONVERTED = 0xFFFF
BURST_COUNT_MAX = 0xFFFE                    # code of any real count >= 65534


def _burst_code(r: int | None) -> int:
    """Wire code of one burst value: None = not converted; a count of 65535 -> 0xFFFE."""
    if r is None:
        return BURST_NOT_CONVERTED
    if isinstance(r, bool) or not isinstance(r, int) or not 0 <= r <= 0xFFFF:
        raise ValueError(f"SPECBURST raw value {r!r} is not a count 0..65535 or None")
    return min(r, BURST_COUNT_MAX)


def decode_specburst(data: bytes) -> dict[str, Any]:
    """SPECBURST user data -> {t0_ms, rate_dhz, rate_hz, gain_code, int_cycles, n, encoding,
    encoding_name, payload_len, compression_ratio, samples: [{dt_ms, raw: [18 x int | None],
    saturated: [18 x bool]}]}. compression_ratio = 36 n / payload_len (1.0 for raw).
    Raises ValueError for a malformed packet (bad n, unknown encoding, wrong payload length)
    and rice.RiceError (a ValueError) for an undecodable CCSDS 121.0 payload."""
    if len(data) < BURST_HDR_LEN:
        raise ValueError(f"SPECBURST: {len(data)} octets < {BURST_HDR_LEN}")
    if len(data) > BURST_MAX_LEN:
        raise ValueError(f"SPECBURST: {len(data)} octets > {BURST_MAX_LEN}")
    t0, rate_dhz, gain, cycles, n, encoding = _BURST_HDR.unpack_from(data)
    if not 1 <= n <= BURST_PER_PACKET_MAX:
        raise ValueError(f"SPECBURST: sample count {n} outside 1..{BURST_PER_PACKET_MAX}")
    if encoding not in BURST_ENCODINGS:
        raise ValueError(f"SPECBURST: unknown encoding {encoding}")
    hdr_end = BURST_HDR_LEN + 2 * n
    if len(data) < hdr_end:
        raise ValueError(f"SPECBURST: {len(data)} octets < {hdr_end} for {n} time offsets")
    dts = struct.unpack_from(f">{n}H", data, BURST_HDR_LEN)
    payload = data[hdr_end:]
    raw_len = BURST_RAW_PER_SAMPLE * n
    if encoding == BURST_RAW:
        if len(payload) != raw_len:
            raise ValueError(f"SPECBURST raw: payload {len(payload)} octets, expected {raw_len}")
        flat = struct.unpack(f">{N_CH * n}H", payload)
        rows = [list(flat[t * N_CH:(t + 1) * N_CH]) for t in range(n)]
    else:
        if not 1 <= len(payload) < raw_len:
            raise ValueError(f"SPECBURST Rice: payload {len(payload)} octets outside "
                             f"1..{raw_len - 1}")
        rows = rice.time_major(rice.decode(payload, N_CH * n), N_CH)
    fs = full_scale(cycles)
    samples = []
    for t in range(n):
        raw = [None if r == BURST_NOT_CONVERTED else r for r in rows[t]]
        samples.append({"dt_ms": dts[t], "raw": raw,
                        "saturated": [r is not None and r >= fs - 1 for r in raw]})
    return {"t0_ms": t0, "rate_dhz": rate_dhz, "rate_hz": rate_dhz / 10.0, "gain_code": gain,
            "int_cycles": cycles, "n": n, "encoding": encoding,
            "encoding_name": BURST_ENCODINGS[encoding], "payload_len": len(payload),
            "compression_ratio": raw_len / len(payload), "samples": samples}


def _u(x: Any, bits: int, what: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int) or not 0 <= x < 1 << bits:
        raise ValueError(f"SPECBURST {what} {x!r} is not a u{bits}")
    return x


def pack_specburst(t0_ms: int, rate_dhz: int, gain_code: int, int_cycles: int,
                   samples: Sequence[tuple[int, Sequence[int | None]]], *,
                   encoding: int | None = None) -> bytes:
    """Reference SPECBURST encoder (simulator, tests): (dt_ms, raw[18]) tuples, 1..3 per
    packet; raw None = not converted, a count of 65535 goes out as 0xFFFE. encoding None
    chooses like the flight (CCSDS 121.0 when strictly shorter than raw, else raw); 0 / 1
    force one (ValueError if a forced Rice stream is not shorter than raw). Every argument
    is range-checked (ValueError)."""
    if not 1 <= len(samples) <= BURST_PER_PACKET_MAX:
        raise ValueError(f"SPECBURST carries 1..{BURST_PER_PACKET_MAX} samples")
    if encoding is not None and (isinstance(encoding, bool) or encoding not in BURST_ENCODINGS):
        raise ValueError(f"SPECBURST encoding {encoding!r} is not 0 (raw) or 1 (Rice)")
    n = len(samples)
    dts: list[int] = []
    rows: list[list[int]] = []
    for dt, raw in samples:
        if len(raw) != N_CH:
            raise ValueError("SPECBURST sample needs 18 raw values")
        dts.append(_u(dt, 16, "dt_ms"))
        rows.append([_burst_code(r) for r in raw])
    raw_payload = struct.pack(f">{N_CH * n}H", *[v for row in rows for v in row])
    payload, enc = raw_payload, BURST_RAW
    if encoding != BURST_RAW:
        coded = rice.encode(rice.channel_major(rows))
        if len(coded) < len(raw_payload):
            payload, enc = coded, BURST_RICE
        elif encoding == BURST_RICE:
            raise ValueError(f"Rice stream {len(coded)} octets is not shorter than raw "
                             f"{len(raw_payload)}: the flight sends raw")
    return (_BURST_HDR.pack(_u(t0_ms, 32, "t0_ms"), _u(rate_dhz, 16, "rate_dhz"),
                            _u(gain_code, 8, "gain_code"), _u(int_cycles, 8, "int_cycles"), n,
                            enc)
            + struct.pack(f">{n}H", *dts) + payload)


# =============================================================================
# Colour-matching functions (Wyman-Sloan-Shirley 2013) and Planck's law
# =============================================================================
# (weight, mu nm, sigma left nm, sigma right nm) per lobe; contract section 8.
_WYMAN_X = ((1.056, 599.8, 37.9, 31.0), (0.362, 442.0, 16.0, 26.7), (-0.065, 501.1, 20.4, 26.2))
_WYMAN_Y = ((0.821, 568.8, 46.9, 40.5), (0.286, 530.9, 16.3, 31.1))
_WYMAN_Z = ((1.217, 437.0, 11.8, 36.0), (0.681, 459.0, 26.0, 13.8))


def _lobes(nm: float, lobes: Sequence[tuple[float, float, float, float]]) -> float:
    s = 0.0
    for w, mu, sl, sr in lobes:
        t = (nm - mu) / (sl if nm < mu else sr)
        s += w * math.exp(-0.5 * t * t)
    return s


def cmf_xbar(nm: float) -> float:
    return _lobes(nm, _WYMAN_X)


def cmf_ybar(nm: float) -> float:
    return _lobes(nm, _WYMAN_Y)


def cmf_zbar(nm: float) -> float:
    return _lobes(nm, _WYMAN_Z)


C2_M_K = 1.4388e-2           # second radiation constant hc/k (contract value)
H_J_S = 6.62607015e-34       # Planck constant (exact, SI 2019)
C_M_S = 299_792_458.0        # speed of light (exact)
N_A = 6.02214076e23          # Avogadro constant (exact, SI 2019)
K_M_LM_W = 683.0             # maximum luminous efficacy (SI candela definition)


def planck_rel(nm: float, temp_k: float) -> float:
    """Blackbody spectral exitance in arbitrary units (lambda in um: um^-5 / (e^x - 1))."""
    lam_um = nm * 1e-3
    x = C2_M_K * 1e6 / (lam_um * temp_k)
    if x > 700.0:                            # e^x would overflow; the term is 0 to FP64
        return 0.0
    return lam_um ** -5 / math.expm1(x)


# Synthetic band model: a Gaussian passband of 20 nm FWHM sampled every 1 nm out to 4 sigma.
_BAND_SIGMA_NM = FWHM_NM / (2.0 * math.sqrt(2.0 * math.log(2.0)))
_BAND_HALF = math.ceil(4.0 * _BAND_SIGMA_NM)
_BAND_KERNEL = tuple(math.exp(-0.5 * (k / _BAND_SIGMA_NM) ** 2)
                     for k in range(-_BAND_HALF, _BAND_HALF + 1))
_BAND_KERNEL_SUM = math.fsum(_BAND_KERNEL)


def band_values(spd: Callable[[float], float],
                channels: Iterable[int] = range(N_CH)) -> list[float]:
    """Channel values (uW/cm2) of a spectral irradiance spd(nm) in uW/cm2/nm: FWHM x the
    Gaussian-weighted mean of spd over a 20 nm FWHM passband. Consistent with the radiometric
    assumption: a flat spd of E gives exactly 20 E in every channel. For synthetic tests,
    the simulator and the Planckian locus."""
    out = []
    for i in channels:
        c = WAVELENGTHS_NM[i]
        acc = math.fsum(w * spd(float(c + k))
                        for k, w in zip(range(-_BAND_HALF, _BAND_HALF + 1), _BAND_KERNEL,
                                        strict=True))
        out.append(FWHM_NM * acc / _BAND_KERNEL_SUM)
    return out


# =============================================================================
# Integration weights: the single radiometric assumption, as linear functionals
# =============================================================================
UW_CM2_TO_W_M2 = 0.01                       # 1 uW/cm2 = 1e-6 W / 1e-4 m2
EXTEND_NM = FWHM_NM / 2.0                   # constant continuation beyond the outer centres
_E_LAMBDA = UW_CM2_TO_W_M2 / FWHM_NM        # channel value -> spectral irradiance, W/m2/nm
PAR_NM = (400.0, 700.0)
VIS_NM = (400.0, 700.0)
NIR_NM = (700.0, WAVELENGTHS_NM[-1] + EXTEND_NM)
TOTAL_NM = (WAVELENGTHS_NM[0] - EXTEND_NM, WAVELENGTHS_NM[-1] + EXTEND_NM)
COLOR_NM = (WAVELENGTHS_NM[0] - EXTEND_NM, WAVELENGTHS_NM[N_VIS - 1] + EXTEND_NM)


def trapz_weights(channels: Sequence[int], lo: float, hi: float,
                  g: Callable[[float], float]) -> list[float]:
    """Weights w (18 entries) with  integral_lo^hi f(lambda) dlambda = sum_i w_i v_i,  where
    f is the trapezoid interpolant through (lambda_c, v_c x g(lambda_c) x 0.01/20) over the
    given channel centres, continued at constant channel value for EXTEND_NM beyond the
    outermost ones. Linear in v, so 1-sigma values propagate as sqrt(sum (w_i s_i)^2)."""
    nodes = [(WAVELENGTHS_NM[channels[0]] - EXTEND_NM, channels[0])]
    nodes += [(float(WAVELENGTHS_NM[c]), c) for c in channels]
    nodes.append((WAVELENGTHS_NM[channels[-1]] + EXTEND_NM, channels[-1]))
    w = [0.0] * N_CH
    for (la, ca), (lb, cb) in itertools.pairwise(nodes):
        a, b = max(la, lo), min(lb, hi)
        if b <= a:
            continue
        ta, tb = (a - la) / (lb - la), (b - la) / (lb - la)
        w[ca] += (b - a) * ((1.0 - ta) + (1.0 - tb)) / 2.0 * g(la) * _E_LAMBDA
        w[cb] += (b - a) * (ta + tb) / 2.0 * g(lb) * _E_LAMBDA
    return w


_ALL = tuple(range(N_CH))
_VIS = tuple(range(N_VIS))
_PHOTON_UMOL = 1e-9 / (H_J_S * C_M_S * N_A) * 1e6     # (W/m2/nm x nm) -> umol m-2 s-1
W_TOTAL = trapz_weights(_ALL, *TOTAL_NM, lambda nm: 1.0)
W_VIS = trapz_weights(_ALL, *VIS_NM, lambda nm: 1.0)
W_NIR = trapz_weights(_ALL, *NIR_NM, lambda nm: 1.0)
W_PPFD = trapz_weights(_ALL, *PAR_NM, lambda nm: nm * _PHOTON_UMOL)
W_X = trapz_weights(_VIS, *COLOR_NM, lambda nm: K_M_LM_W * cmf_xbar(nm))
W_Y = trapz_weights(_VIS, *COLOR_NM, lambda nm: K_M_LM_W * cmf_ybar(nm))   # Y = illuminance, lx
W_Z = trapz_weights(_VIS, *COLOR_NM, lambda nm: K_M_LM_W * cmf_zbar(nm))


def apply_weights(w: Sequence[float], spec: Sequence[float | None],
                  unc: Sequence[float | None] | None = None) -> tuple[float | None, float | None]:
    """(sum w_i v_i, sqrt(sum (w_i s_i)^2)); value None if a weighted channel is missing,
    sigma None if a weighted channel has no uncertainty."""
    val, var, have_sig = 0.0, 0.0, True
    for i in range(N_CH):
        if w[i] == 0.0:
            continue
        v = spec[i]
        if v is None:
            return None, None
        val += w[i] * v
        s = unc[i] if unc is not None else None
        if s is None:
            have_sig = False
        else:
            var += (w[i] * s) ** 2
    return val, (math.sqrt(var) if have_sig else None)


# =============================================================================
# Spectrum features, reflectance, indices
# =============================================================================
MIN_ANGLE_CHANNELS = 6       # spectral angles need at least this many common channels


def spectral_angle_deg(a: Sequence[float | None], b: Sequence[float | None],
                       mask: Sequence[bool] | None = None) -> float | None:
    """Angle (deg, 0..90) between two spectra as vectors over the channels both have (and
    `mask` allows); negatives clipped to 0 as onboard; 2 atan2(|u - v|, |u + v|) is accurate
    at small and large angles. None if < MIN_ANGLE_CHANNELS common channels or no signal."""
    pa: list[float] = []
    pb: list[float] = []
    for i in range(min(len(a), len(b), N_CH)):
        x, y = a[i], b[i]
        if (mask is not None and not mask[i]) or x is None or y is None:
            continue
        pa.append(max(x, 0.0))
        pb.append(max(y, 0.0))
    if len(pa) < MIN_ANGLE_CHANNELS:
        return None
    na, nb = math.sqrt(math.fsum(x * x for x in pa)), math.sqrt(math.fsum(y * y for y in pb))
    if not (na > 0.0 and nb > 0.0):
        return None
    dm = math.fsum((x / na - y / nb) ** 2 for x, y in zip(pa, pb, strict=True))
    dp = math.fsum((x / na + y / nb) ** 2 for x, y in zip(pa, pb, strict=True))
    return math.degrees(2.0 * math.atan2(math.sqrt(dm), math.sqrt(dp)))


def _parabolic_peak(x: Sequence[float], y: Sequence[float]) -> float:
    """Vertex of the parabola through three (non-uniformly spaced) points, clamped to them."""
    (x0, x1, x2), (y0, y1, y2) = x, y
    den = (x0 - x1) * (x0 - x2) * (x1 - x2)
    a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / den
    b = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) + x0 * x0 * (y1 - y2)) / den
    if not a < 0.0:
        return x1
    return min(x2, max(x0, -b / (2.0 * a)))


def spectrum_features(spec: Sequence[float | None]) -> dict[str, Any]:
    """Peak wavelength (parabolic interpolation), centroid, VIS (410-705 nm) and NIR
    (730-940 nm) channel sums and their ratio; negatives clipped to 0 as onboard."""
    out: dict[str, Any] = {"n_valid": 0, "peak_nm": None, "peak_value": None, "centroid_nm": None,
                           "vis_sum": None, "nir_sum": None, "nir_vis": None}
    c = {i: max(v, 0.0) for i, v in enumerate(spec[:N_CH]) if v is not None}
    out["n_valid"] = len(c)
    if not c:
        return out
    k = max(c, key=c.__getitem__)
    out["peak_value"] = spec[k]
    out["peak_nm"] = float(WAVELENGTHS_NM[k])
    if k - 1 in c and k + 1 in c and c[k] > 0.0:
        out["peak_nm"] = _parabolic_peak([WAVELENGTHS_NM[j] for j in (k - 1, k, k + 1)],
                                         [c[j] for j in (k - 1, k, k + 1)])
    tot = math.fsum(c.values())
    if tot > 0.0:
        out["centroid_nm"] = math.fsum(WAVELENGTHS_NM[i] * v for i, v in c.items()) / tot
    vis = [v for i, v in c.items() if i < N_VIS]
    nir = [v for i, v in c.items() if i >= N_VIS]
    vis_sum, nir_sum = math.fsum(vis), math.fsum(nir)
    out["vis_sum"] = vis_sum if vis else None
    out["nir_sum"] = nir_sum if nir else None
    if vis and nir and vis_sum > 0.0:
        out["nir_vis"] = nir_sum / vis_sum
    return out


REF_MIN_SNR = 3.0            # white-reference channels below 3 sigma give no reflectance


def reflectance(spec: Sequence[float | None], unc: Sequence[float | None],
                ref: Sequence[float | None], ref_unc: Sequence[float | None]
                ) -> tuple[list[float | None], list[float | None]]:
    """R_i = S_i / W_i with first-order 1-sigma sqrt((s_S/W)^2 + (S s_W / W^2)^2)."""
    r: list[float | None] = [None] * N_CH
    rs: list[float | None] = [None] * N_CH
    for i in range(N_CH):
        s, w = spec[i], ref[i]
        sw = ref_unc[i] if i < len(ref_unc) else None
        if s is None or w is None or not w > 0.0 or (sw is not None and w < REF_MIN_SNR * sw):
            continue
        r[i] = s / w
        ss = unc[i]
        if ss is not None:
            rs[i] = math.sqrt((ss / w) ** 2 + ((s * sw / (w * w)) ** 2 if sw is not None else 0.0))
    return r, rs


def _nd(a: float | None, sa: float | None, b: float | None, sb: float | None
        ) -> tuple[float | None, float | None]:
    """(a - b)/(a + b) and its first-order sigma 2 sqrt((b sa)^2 + (a sb)^2) / (a + b)^2."""
    if a is None or b is None or not a + b > 0.0:
        return None, None
    d = a + b
    sig = None
    if sa is not None and sb is not None:
        sig = 2.0 * math.sqrt((b * sa) ** 2 + (a * sb) ** 2) / (d * d)
    return (a - b) / d, sig


def _ratio(a: float | None, sa: float | None, b: float | None, sb: float | None
           ) -> tuple[float | None, float | None]:
    """a / b and its first-order sigma sqrt((sa/b)^2 + (a sb / b^2)^2)."""
    if a is None or b is None or not b > 0.0:
        return None, None
    sig = None
    if sa is not None and sb is not None:
        sig = math.sqrt((sa / b) ** 2 + (a * sb / (b * b)) ** 2)
    return a / b, sig


_CH = {nm: i for i, nm in enumerate(WAVELENGTHS_NM)}
# (key, kind, band a nm, band b nm, formula, note)
INDEX_DEFS = (
    ("ndvi", "nd", 860, 680, "(R860 - R680) / (R860 + R680)", "NDVI"),
    ("gndvi", "nd", 860, 560, "(R860 - R560) / (R860 + R560)", "green NDVI"),
    ("ndre", "nd", 860, 705, "(R860 - R705) / (R860 + R705)", "red-edge NDVI (705 nm band)"),
    ("wbi_proxy", "ratio", 900, 940, "R900 / R940",
     "WBI proxy: the standard WBI is R900/R970; 970 nm is outside the AS7265X range"),
    ("pri_proxy", "nd", 535, 560, "(R535 - R560) / (R535 + R560)",
     "PRI proxy: the standard PRI is (R531 - R570)/(R531 + R570); nearest bands 535/560 nm"),
)


def vegetation_indices(src: Sequence[float | None], src_unc: Sequence[float | None],
                       basis: str, saturated: Sequence[bool]) -> dict[str, Any]:
    """Indices with propagated 1-sigma. basis 'reflectance' or 'spectrum' (the label says
    which: on a raw spectrum they describe the light, not a reflecting surface)."""
    out: dict[str, Any] = {
        "basis": basis,
        "label": ("computed on reflectance (white reference)" if basis == "reflectance" else
                  "computed on the raw spectrum (no compatible white reference): not surface "
                  "reflectance indices"),
    }
    for key, kind, nma, nmb, formula, note in INDEX_DEFS:
        ia, ib = _CH[nma], _CH[nmb]
        fn = _nd if kind == "nd" else _ratio
        val, sig = fn(src[ia], src_unc[ia], src[ib], src_unc[ib])
        out[key] = {"value": _num(val), "sigma": _num(sig), "formula": formula,
                    "bands_nm": [nma, nmb], "note": note,
                    "saturated": bool(saturated[ia] or saturated[ib])}
    return out


def radiometry(spec: Sequence[float | None], unc: Sequence[float | None]) -> dict[str, Any]:
    """Band-integrated irradiance, PPFD and illuminance under the single stated assumption."""
    def item(w: Sequence[float], unit: str, band: tuple[float, float]) -> dict[str, Any]:
        v, s = apply_weights(w, spec, unc)
        return {"value": _num(v), "sigma": _num(s), "unit": unit, "band_nm": list(band)}

    return {
        "label": APPROX_LABEL,
        "assumption": "each channel = band irradiance through a 20 nm FWHM filter; spectral "
                      "irradiance = value / 20 nm; trapezoid over channel centres, constant "
                      "for 10 nm beyond the outermost centre",
        "irradiance_total": item(W_TOTAL, "W/m2", TOTAL_NM),
        "irradiance_vis": item(W_VIS, "W/m2", VIS_NM),
        "irradiance_nir": item(W_NIR, "W/m2", NIR_NM),
        "ppfd": item(W_PPFD, "umol m-2 s-1", PAR_NM),
        "illuminance": item(W_Y, "lx", COLOR_NM),
    }


# =============================================================================
# Colorimetry: XYZ, xy, CIE 1960 uv, CCT / Duv (Planckian locus search), McCamy
# =============================================================================
CCT_MIN_K = 1000.0
CCT_MAX_K = 25000.0
_MIRED_STEP = 2.0
DUV_VALID_MAX = 0.05         # CCT is not meaningful farther than this from the locus (CIE)
MCCAMY_RANGE_K = (2000.0, 12500.0)
_GOLDEN_ITERS = 40           # 4-mired bracket shrinks to 4 x 0.618^40 = 1.8e-8 mired


def xyz_to_uv(x_: float, y_: float, z_: float) -> tuple[float, float] | None:
    d = x_ + 15.0 * y_ + 3.0 * z_
    if not d > 0.0:
        return None
    return 4.0 * x_ / d, 6.0 * y_ / d


class _PlanckLocus:
    """Blackbody chromaticities in CIE 1960 uv computed through the SAME 12 visible bands,
    band model and XYZ weights as a measured spectrum, so a blackbody spectrum is recovered
    at Duv = 0 (self-consistent; contract 13.7). Built lazily once (~0.1 s)."""

    def __init__(self) -> None:
        lo = WAVELENGTHS_NM[0] - _BAND_HALF
        hi = WAVELENGTHS_NM[N_VIS - 1] + _BAND_HALF
        self.lam = [float(nm) for nm in range(lo, hi + 1)]
        # Collapse band filter and XYZ weights into one per-wavelength kernel per tristimulus.
        self.k = ([0.0] * len(self.lam), [0.0] * len(self.lam), [0.0] * len(self.lam))
        for i in range(N_VIS):
            base = WAVELENGTHS_NM[i] - _BAND_HALF - lo
            for j, gk in enumerate(_BAND_KERNEL):
                f = FWHM_NM * gk / _BAND_KERNEL_SUM
                for kk, w in zip(self.k, (W_X, W_Y, W_Z), strict=True):
                    kk[base + j] += w[i] * f
        n = round((1e6 / CCT_MIN_K - 1e6 / CCT_MAX_K) / _MIRED_STEP)
        self.mired = [1e6 / CCT_MAX_K + j * _MIRED_STEP for j in range(n + 1)]
        self.uv = [self.uv_at(1e6 / m) for m in self.mired]

    def uv_at(self, temp_k: float) -> tuple[float, float]:
        p = [planck_rel(nm, temp_k) for nm in self.lam]
        x_, y_, z_ = (math.fsum(a * b for a, b in zip(kk, p, strict=True)) for kk in self.k)
        uv = xyz_to_uv(x_, y_, z_)
        if uv is None:                       # unreachable for 1000..25000 K
            raise ArithmeticError("degenerate blackbody tristimulus")
        return uv


_LOCUS: _PlanckLocus | None = None


def planck_locus() -> _PlanckLocus:
    global _LOCUS
    if _LOCUS is None:
        _LOCUS = _PlanckLocus()
    return _LOCUS


def cct_duv(u: float, v: float) -> tuple[float | None, float | None, str]:
    """CCT (K) and Duv of a CIE 1960 (u, v) point by nearest-point search on the band-sampled
    Planckian locus: coarse 2-mired grid, then golden-section refinement in mired. Duv > 0
    above the locus (greenish). Returns (None, None, reason) outside 1000..25000 K."""
    loc = planck_locus()
    d2 = [(u - lu) ** 2 + (v - lv) ** 2 for lu, lv in loc.uv]
    k = min(range(len(d2)), key=d2.__getitem__)
    if k == 0 or k == len(d2) - 1:
        return None, None, f"outside the {CCT_MIN_K:.0f}-{CCT_MAX_K:.0f} K locus search range"
    a, b = loc.mired[k - 1], loc.mired[k + 1]

    def dist2(m: float) -> float:
        lu, lv = loc.uv_at(1e6 / m)
        return (u - lu) ** 2 + (v - lv) ** 2

    g = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = dist2(c), dist2(d)
    for _ in range(_GOLDEN_ITERS):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = dist2(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = dist2(d)
    m = (a + b) / 2.0
    temp = 1e6 / m
    lu, lv = loc.uv_at(temp)
    duv = math.copysign(math.sqrt((u - lu) ** 2 + (v - lv) ** 2), v - lv)
    return temp, duv, "ok"


def mccamy_cct(x: float, y: float) -> float | None:
    """McCamy (1992): n = (x - 0.3320)/(0.1858 - y); CCT = 449 n^3 + 3525 n^2 + 6823.3 n +
    5520.33 K (stated validity ~2000-12500 K)."""
    den = 0.1858 - y
    if den == 0.0:
        return None
    n = (x - 0.3320) / den
    return 449.0 * n ** 3 + 3525.0 * n ** 2 + 6823.3 * n + 5520.33


def colorimetry(spec: Sequence[float | None], unc: Sequence[float | None]) -> dict[str, Any]:
    """XYZ (Y = illuminance, lx), xy with first-order 1-sigma, CIE 1960 uv, CCT/Duv by
    locus search, McCamy cross-check. Uses only the 12 visible bands."""
    out: dict[str, Any] = {"label": APPROX_LABEL, "available": False, "reason": "",
                           "X": None, "Y": None, "Z": None, "x": None, "y": None,
                           "x_sigma": None, "y_sigma": None, "u": None, "v": None,
                           "cct_k": None, "duv": None, "cct_valid": False,
                           "cct_mccamy_k": None, "mccamy_in_range": False,
                           "cct_mccamy_diff_pct": None,
                           "method": "CIE 1931 2 deg via Wyman 2013 CMF fit on 12 bands; CCT/Duv "
                                     "by Planckian-locus search in CIE 1960 uv (blackbody "
                                     "through the same bands); McCamy 1992 cross-check"}
    x_, y_, z_ = (apply_weights(w, spec, unc)[0] for w in (W_X, W_Y, W_Z))
    if x_ is None or y_ is None or z_ is None:
        out["reason"] = "a visible channel is missing"
        return out
    s = x_ + y_ + z_
    out.update(X=_num(x_), Y=_num(y_), Z=_num(z_))
    if not (s > 0.0 and y_ > 0.0 and x_ >= 0.0 and z_ >= 0.0):
        out["reason"] = "no positive visible signal"
        return out
    x, y = x_ / s, y_ / s
    out.update(available=True, x=x, y=y)
    # First-order sigma of x, y: dx/dv_i = (wX_i S - X wS_i) / S^2 (channels independent).
    vx = vy = 0.0
    have = True
    for i in range(N_VIS):
        si = unc[i]
        if si is None:
            have = False
            break
        ws = W_X[i] + W_Y[i] + W_Z[i]
        vx += ((W_X[i] * s - x_ * ws) / (s * s) * si) ** 2
        vy += ((W_Y[i] * s - y_ * ws) / (s * s) * si) ** 2
    if have:
        out.update(x_sigma=math.sqrt(vx), y_sigma=math.sqrt(vy))
    uv = xyz_to_uv(x_, y_, z_)
    if uv is None:
        out["reason"] = "degenerate chromaticity"
        return out
    out.update(u=uv[0], v=uv[1])
    cct, duv, why = cct_duv(*uv)
    out.update(cct_k=_num(cct), duv=_num(duv))
    out["cct_valid"] = cct is not None and duv is not None and abs(duv) <= DUV_VALID_MAX
    if cct is None:
        out["reason"] = why
    elif not out["cct_valid"]:
        out["reason"] = f"|Duv| > {DUV_VALID_MAX}: far from the Planckian locus, CCT not meaningful"
    mc = mccamy_cct(x, y)
    out["cct_mccamy_k"] = _num(mc)
    out["mccamy_in_range"] = mc is not None and MCCAMY_RANGE_K[0] <= mc <= MCCAMY_RANGE_K[1]
    if cct is not None and mc is not None:
        out["cct_mccamy_diff_pct"] = _num(100.0 * (mc - cct) / cct)
    return out


# =============================================================================
# Light-source classification (HEURISTIC, explainable; mirrors the onboard chain)
# =============================================================================
@dataclass(frozen=True)
class ClassThresholds:
    """The onboard kSpecClassThresholds (fsw/spectral.cpp) - same values, same decision chain,
    so ground and spacecraft agree except within FP32 rounding of a threshold. HEURISTIC
    values from synthetic spectra; tune against real sources (docs/08). The last three are
    ground-only consistency checks: they add reasons, never change the class."""
    dark_total_uw: float = 0.5
    dark_snr: float = 5.0
    narrow_peak_mean: float = 6.0
    low_nir_vis: float = 0.15
    fluor_line: float = 1.5
    thermal_nir_vis: float = 0.70
    thermal_blue_max: float = 0.70
    day_blue_min: float = 0.70
    day_flatness_min: float = 0.60
    conf_full_ln: float = math.log(2.0)
    planckian_duv_max: float = 0.01          # HEURISTIC: thermal sources lie near the locus
    incandescent_cct_min_k: float = 2000.0   # HEURISTIC plausibility window
    incandescent_cct_max_k: float = 3600.0
    daylight_cct_min_k: float = 4000.0
    daylight_cct_max_k: float = 10000.0

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any] | None) -> ClassThresholds:
        """Overrides from station.toml [spectral.classifier]; unknown keys or non-positive
        values raise ValueError (a typo must not silently keep a default)."""
        if not m:
            return cls()
        names = {f.name for f in fields(cls)}
        kw: dict[str, float] = {}
        for k, v in m.items():
            f = _num(v)
            if k not in names or f is None or not f > 0.0:
                raise ValueError(f"[spectral.classifier] {k} = {v!r} is not a known positive value")
            kw[k] = f
        return cls(**kw)


DEFAULT_THRESHOLDS = ClassThresholds()


def classifier_features(spec: Sequence[float | None], unc: Sequence[float | None]
                        ) -> tuple[dict[str, float], bool]:
    """Onboard features (spec_features) in FP64: total, snr, nir_vis, blue (460/560),
    flatness (geometric/arithmetic mean), peak_mean, line (min(435/460, 610/645)).
    Returns (features, complete); incomplete spectra only carry total and snr."""
    c = [0.0] * N_CH
    total = vis = nir = mx = var = 0.0
    complete = True
    for i in range(N_CH):
        v = spec[i]
        if v is None:
            complete = False
            continue
        c[i] = max(v, 0.0)
        total += c[i]
        if i < N_VIS:
            vis += c[i]
        else:
            nir += c[i]
        mx = max(mx, c[i])
        u = unc[i]
        if u is not None:
            var += u * u
    snr = total / math.sqrt(var) if var > 0.0 else (1e6 if total > 0.0 else 0.0)
    f = {"total": total, "snr": snr}
    if not complete or not total > 0.0:
        return f, False
    mean = total / N_CH
    eps = 1e-6 * mx
    f["nir_vis"] = nir / vis if vis > 0.0 else 1e6
    f["blue"] = c[2] / c[6] if c[6] > 0.0 else 1e6
    f["flatness"] = math.exp(math.fsum(math.log(x + eps) for x in c) / N_CH) / mean
    f["peak_mean"] = mx / mean
    l1 = c[1] / c[2] if c[2] > 0.0 else 1e6
    l2 = c[8] / c[9] if c[9] > 0.0 else 1e6
    f["line"] = min(l1, l2)
    return f, True


def _margin(x: float, thr: float) -> float:
    return abs(math.log(max(x, 1e-30) / thr))


def classify(spec: Sequence[float | None], unc: Sequence[float | None], *, active: bool,
             thresholds: ClassThresholds = DEFAULT_THRESHOLDS,
             colour: Mapping[str, Any] | None = None, onboard_class: int | None = None,
             onboard_conf: int | None = None) -> dict[str, Any]:
    """Class, confidence 0..100 (smallest log-margin on the decision path, full at ln 2),
    the features, one reason per comparison made and ground-only consistency notes."""
    t = thresholds
    f, ok = classifier_features(spec, unc)
    reasons: list[str] = []
    cls, m = CLASS_UNKNOWN, 0.0
    if active:
        cls, m = CLASS_OWN_LAMP, t.conf_full_ln
        reasons.append("active (lamp-on minus lamp-off) spectrum: the light is the own lamp")
    elif f["total"] < t.dark_total_uw or f["snr"] < t.dark_snr:
        cls = CLASS_DARK
        m = max(_margin(f["total"], t.dark_total_uw) if f["total"] < t.dark_total_uw else 0.0,
                _margin(f["snr"], t.dark_snr) if f["snr"] < t.dark_snr else 0.0)
        reasons.append(f"total {f['total']:.4g} uW/cm2 (dark below {t.dark_total_uw:g}) / "
                       f"SNR {f['snr']:.3g} (dark below {t.dark_snr:g})")
    elif not ok:
        reasons.append("incomplete spectrum: features undefined")
    else:
        m = min(_margin(f["total"], t.dark_total_uw), _margin(f["snr"], t.dark_snr),
                _margin(f["peak_mean"], t.narrow_peak_mean))
        reasons.append(f"total {f['total']:.4g} uW/cm2, SNR {f['snr']:.3g}: not dark")
        if f["peak_mean"] >= t.narrow_peak_mean:
            cls = CLASS_NARROWBAND
            reasons.append(f"peak/mean {f['peak_mean']:.3g} >= {t.narrow_peak_mean:g}: one "
                           "dominant band")
        else:
            reasons.append(f"peak/mean {f['peak_mean']:.3g} < {t.narrow_peak_mean:g}: broadband")
            m = min(m, _margin(f["nir_vis"], t.low_nir_vis))
            if f["nir_vis"] < t.low_nir_vis:
                m = min(m, _margin(f["line"], t.fluor_line))
                reasons.append(f"NIR/VIS {f['nir_vis']:.3g} < {t.low_nir_vis:g}: no NIR (LED or "
                               "discharge lamp)")
                if f["line"] >= t.fluor_line:
                    cls = CLASS_FLUORESCENT
                    reasons.append(f"line index {f['line']:.3g} >= {t.fluor_line:g}: Hg 436 / "
                                   "Eu 611 nm emission lines")
                else:
                    cls = CLASS_WHITE_LED
                    reasons.append(f"line index {f['line']:.3g} < {t.fluor_line:g}: smooth "
                                   "phosphor continuum")
            else:
                m = min(m, _margin(f["nir_vis"], t.thermal_nir_vis))
                if f["nir_vis"] >= t.thermal_nir_vis:
                    reasons.append(f"NIR/VIS {f['nir_vis']:.3g} >= {t.thermal_nir_vis:g}: "
                                   "NIR-rich (thermal emitter)")
                    if f["blue"] < t.thermal_blue_max:
                        m = min(m, _margin(f["blue"], t.thermal_blue_max))
                        cls = CLASS_INCANDESCENT
                        reasons.append(f"460/560 {f['blue']:.3g} < {t.thermal_blue_max:g}: "
                                       "little blue")
                    else:
                        reasons.append(f"460/560 {f['blue']:.3g} >= {t.thermal_blue_max:g}: "
                                       "too blue for a thermal lamp")
                elif f["blue"] >= t.day_blue_min and f["flatness"] >= t.day_flatness_min:
                    m = min(m, _margin(f["blue"], t.day_blue_min),
                            _margin(f["flatness"], t.day_flatness_min))
                    cls = CLASS_DAYLIGHT
                    reasons.append(f"460/560 {f['blue']:.3g} >= {t.day_blue_min:g} and flatness "
                                   f"{f['flatness']:.3g} >= {t.day_flatness_min:g}: broad, blue-"
                                   "rich continuum")
                else:
                    reasons.append(f"460/560 {f['blue']:.3g}, flatness {f['flatness']:.3g}: no "
                                   "class matches")
    conf = 0 if cls == CLASS_UNKNOWN else math.floor(100.0 * min(m / t.conf_full_ln, 1.0) + 0.5)
    consistency: list[str] = []
    cct = colour.get("cct_k") if colour else None
    duv = colour.get("duv") if colour else None
    if cct is not None and duv is not None and cls in (CLASS_INCANDESCENT, CLASS_DAYLIGHT):
        near = abs(duv) <= t.planckian_duv_max
        consistency.append(f"Duv {duv:+.4f}: " + ("near the Planckian locus (supports)" if near else
                                                  "off the Planckian locus (does not support)"))
        lo, hi = ((t.incandescent_cct_min_k, t.incandescent_cct_max_k) if cls == CLASS_INCANDESCENT
                  else (t.daylight_cct_min_k, t.daylight_cct_max_k))
        consistency.append(f"CCT {cct:.0f} K " + ("inside" if lo <= cct <= hi else "outside") +
                           f" {lo:.0f}-{hi:.0f} K")
    elif cct is not None and duv is not None and cls != CLASS_OWN_LAMP:
        consistency.append(f"CCT {cct:.0f} K, Duv {duv:+.4f} (information)")
    onboard = None
    if onboard_class is not None:
        onboard = {"class_id": onboard_class, "class": CLASS_NAMES.get(onboard_class, "?"),
                   "confidence": onboard_conf}
    return {"label": HEURISTIC_LABEL, "class_id": cls, "class": CLASS_NAMES[cls],
            "confidence": conf, "features": {k: _num(v) for k, v in f.items()},
            "reasons": reasons, "consistency": consistency, "onboard": onboard,
            "agrees": None if onboard_class is None else onboard_class == cls}


# =============================================================================
# Fluorescence (FLUOR mode: UV LED excitation, lamp-on minus lamp-off)
# =============================================================================
FLUOR_EXCITATION_MAX_NM = 460     # channels at or below are the excitation band (masked)
_W_F_BLUE_GREEN = [1.0 if nm in (485, 510) else 0.0 for nm in WAVELENGTHS_NM]
_W_F_RED = [1.0 if nm in (680, 730) else 0.0 for nm in WAVELENGTHS_NM]


def fluorescence_metrics(spec: Sequence[float | None], unc: Sequence[float | None]
                         ) -> dict[str, Any]:
    """F680/F730 red/far-red ratio (chlorophyll-fluorescence proxy: the emission peaks near
    685/740 nm), blue-green index (F485 + F510)/(F680 + F730) (cf. F440/F690 of UV-induced
    fluorescence; 440 nm lies inside the masked excitation band here), emission total and
    peak over the unmasked channels. All HEURISTIC proxies."""
    masked = [nm <= FLUOR_EXCITATION_MAX_NM for nm in WAVELENGTHS_NM]
    r, rs = _ratio(spec[_CH[680]], unc[_CH[680]], spec[_CH[730]], unc[_CH[730]])
    bg, bgs = _ratio(*apply_weights(_W_F_BLUE_GREEN, spec, unc),
                     *apply_weights(_W_F_RED, spec, unc))
    em = [(v, i) for i, v in enumerate(spec[:N_CH]) if v is not None and not masked[i]]
    peak = max(em) if em else None
    return {"label": HEURISTIC_LABEL,
            "masked": masked, "excitation_max_nm": FLUOR_EXCITATION_MAX_NM,
            "f680_f730": {"value": _num(r), "sigma": _num(rs),
                          "note": "red/far-red chlorophyll-fluorescence ratio proxy"},
            "blue_green_index": {"value": _num(bg), "sigma": _num(bgs),
                                 "note": "(F485 + F510) / (F680 + F730)"},
            "emission_total": math.fsum(v for v, _ in em) if em else None,
            "emission_peak_nm": WAVELENGTHS_NM[peak[1]] if peak else None}


# =============================================================================
# White reference and spectral library (persistent, JSON)
# =============================================================================
MIN_REF_CHANNELS = 12          # usable (unsaturated, positive) channels a reference needs
LIBRARY_MAX = 64
LIBRARY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
LIBRARY_TOP_N = 3


def _vec18(x: Any, nonneg: bool = False) -> list[float | None]:
    """18 range-checked values from a JSON file (out-of-range entries become None)."""
    if not isinstance(x, list) or len(x) != N_CH:
        raise ValueError("expected a list of 18 values")
    return [_value(v, nonneg) for v in x]


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, allow_nan=False))
    os.replace(tmp, path)


@dataclass
class WhiteReference:
    """A captured reference spectrum (uW/cm2) with the set-up it was taken with."""
    t: float | None
    mode: int
    lamps: int
    active: bool
    gain_code: int | None
    int_cycles: int | None
    spectrum: list[float | None]
    uncertainty: list[float | None]

    def compatible(self, mode: int | None, lamps: int, active: bool) -> tuple[bool, str]:
        """Reflectance needs the same illumination: same active flag, same lamps, and FLUOR
        only against FLUOR."""
        if active != self.active:
            return False, "white reference is " + ("active" if self.active else "ambient") + \
                          ", spectrum is " + ("active" if active else "ambient")
        if lamps != self.lamps:
            return False, f"lamps differ (reference {self.lamps:#x}, spectrum {lamps:#x})"
        if (mode == MODE_FLUOR) != (self.mode == MODE_FLUOR):
            return False, "fluorescence and reflectance spectra are not comparable"
        return True, "same illumination set-up"

    def to_json(self) -> dict[str, Any]:
        return {"version": 1, "t": self.t, "mode": self.mode, "lamps": self.lamps,
                "active": self.active, "gain_code": self.gain_code, "int_cycles": self.int_cycles,
                "spectrum": self.spectrum, "uncertainty": self.uncertainty}

    @classmethod
    def from_json(cls, d: Any) -> WhiteReference:
        if not isinstance(d, dict) or d.get("version") != 1:
            raise ValueError("not a version-1 white reference")
        mode, lamps = _int(d.get("mode"), 0, 4), _int(d.get("lamps"), 0, 7)
        if mode is None or lamps is None or not isinstance(d.get("active"), bool):
            raise ValueError("white reference mode / lamps / active invalid")
        return cls(_num(d.get("t")), mode, lamps, d["active"], _int(d.get("gain_code"), 0, 3),
                   _int(d.get("int_cycles"), 0, 255), _vec18(d.get("spectrum")),
                   _vec18(d.get("uncertainty"), nonneg=True))

    def info(self) -> dict[str, Any]:
        return {"t": self.t, "mode": self.mode, "mode_name": MODE_NAMES.get(self.mode),
                "lamps": [LAMP_NAMES[b] for b in range(3) if self.lamps >> b & 1],
                "active": self.active, "gain_code": self.gain_code, "int_cycles": self.int_cycles,
                "n_channels": sum(v is not None for v in self.spectrum),
                "spectrum": self.spectrum}


@dataclass
class LibraryEntry:
    name: str
    t: float | None
    mode: int
    lamps: int
    active: bool
    spectrum: list[float | None]             # normalised to its maximum
    reflectance: list[float | None] | None

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "t": self.t, "mode": self.mode, "lamps": self.lamps,
                "active": self.active, "spectrum": self.spectrum, "reflectance": self.reflectance}

    @classmethod
    def from_json(cls, d: Any) -> LibraryEntry:
        if not isinstance(d, dict) or not isinstance(d.get("name"), str) \
                or not LIBRARY_NAME_RE.match(d["name"]):
            raise ValueError("library entry name invalid")
        mode, lamps = _int(d.get("mode"), 0, 4), _int(d.get("lamps"), 0, 7)
        if mode is None or lamps is None or not isinstance(d.get("active"), bool):
            raise ValueError(f"library entry {d['name']}: mode / lamps / active invalid")
        refl = d.get("reflectance")
        return cls(d["name"], _num(d.get("t")), mode, lamps, d["active"], _vec18(d.get("spectrum")),
                   None if refl is None else _vec18(refl))


def _normalised(spec: Sequence[float | None]) -> list[float | None]:
    mx = max((v for v in spec if v is not None), default=None)
    if mx is None or not mx > 0.0:
        return [None] * N_CH
    return [None if v is None else v / mx for v in spec]


class SpectralLibrary:
    """Named reference spectra; match() ranks them by spectral angle (reflectance against
    reflectance when both exist, else normalised spectrum against normalised spectrum)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.entries: dict[str, LibraryEntry] = {}
        self.load_error: str | None = None
        if path is not None and path.exists():
            try:
                doc = json.loads(path.read_text())
                if not isinstance(doc, dict) or doc.get("version") != 1 \
                        or not isinstance(doc.get("entries"), list):
                    raise ValueError("not a version-1 spectral library")
                for d in doc["entries"][:LIBRARY_MAX]:
                    e = LibraryEntry.from_json(d)
                    self.entries[e.name] = e
            except (OSError, ValueError) as exc:   # json.JSONDecodeError is a ValueError
                self.entries.clear()
                self.load_error = f"spectral library {path} ignored: {exc}"

    def persist(self) -> str | None:
        """Writes the library; returns an error text instead of raising (state stays valid)."""
        if self.path is None:
            return None
        try:
            _atomic_write_json(self.path, {"version": 1, "entries": [e.to_json() for e in
                                                                     self.entries.values()]})
        except OSError as exc:
            return f"spectral library not saved: {exc}"
        return None

    def match(self, spec: Sequence[float | None], refl: Sequence[float | None] | None,
              mask: Sequence[bool] | None = None) -> dict[str, Any] | None:
        found: list[tuple[float, str, str]] = []
        for e in self.entries.values():
            if refl is not None and e.reflectance is not None:
                ang, basis = spectral_angle_deg(refl, e.reflectance, mask), "reflectance"
            else:
                ang, basis = spectral_angle_deg(spec, e.spectrum, mask), "spectrum"
            if ang is not None:
                found.append((ang, e.name, basis))
        if not found:
            return None
        ranked = [{"name": n, "angle_deg": a, "basis": b} for a, n, b in sorted(found)]
        return {"best": ranked[0], "ranked": ranked[:LIBRARY_TOP_N]}

    def snapshot(self) -> dict[str, Any]:
        return {"max_entries": LIBRARY_MAX, "file": str(self.path) if self.path else None,
                "entries": [{"name": e.name, "t": e.t, "mode": e.mode,
                             "mode_name": MODE_NAMES.get(e.mode), "active": e.active,
                             "has_reflectance": e.reflectance is not None,
                             "spectrum": e.spectrum, "reflectance": e.reflectance}
                            for e in sorted(self.entries.values(), key=lambda x: x.name)]}


# =============================================================================
# The per-packet analysis
# =============================================================================
def _meta(values: Mapping[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Range-checked packet header fields (spacecraft data is external input)."""
    def get(name: str, lo: int, hi: int, alt: str | None = None) -> int | None:
        raw = values.get(name, values.get(alt) if alt else None)
        v = _int(raw, lo, hi)
        if v is None and raw is not None:
            warnings.append(f"{name} = {str(raw)[:24]} out of range {lo}..{hi}")
        return v

    flags = get("flags", 0, 255) or 0
    gain = get("gain_code", 0, 3)
    cyc = get("int_cycles", 0, 255)
    led = get("led_cur", 0, 63)
    zen = _num(values.get("bore_zen"))
    az = _num(values.get("bore_az"))
    if zen is not None and not 0.0 <= zen <= 180.0:
        warnings.append(f"bore_zen {zen} outside 0..180 deg")
        zen = None
    if az is not None:
        az %= 360.0
    temps = [_int(values.get(f"temp_{d}"), -127, 127) for d in range(3)]
    change = _num(values.get("spec_change"))
    return {
        "mode": get("spec_mode", 0, 4, alt="mode"), "flags": flags, "gain_code": gain,
        "int_cycles": cyc, "led_cur": led, "n_frames": get("n_frames", 0, 255),
        "sat_mask": (get("sat_mask", 0, 0xFFFFFFFF) or 0) & ((1 << N_CH) - 1),
        "temps": temps, "zen": zen, "az": az,
        "class_id": get("spec_class", 0, 7), "class_conf": get("spec_conf", 0, 100),
        "change": change if change is not None and change >= 0.0 else None,
    }


def _channels(values: Mapping[str, Any], meta: Mapping[str, Any], warnings: list[str]
              ) -> tuple[list[float | None], list[float | None], list[int | None]]:
    """Range-checked spectrum, uncertainty (>= 0) and raw counts, wavelength order."""
    spec: list[float | None] = []
    unc: list[float | None] = []
    rejected: list[str] = []
    for nm in WAVELENGTHS_NM:
        for key, dst, nonneg in ((f"spec_{nm}nm", spec, False), (f"unc_{nm}nm", unc, True)):
            x = values.get(key)
            v = _value(x, nonneg)
            if v is None and x is not None:
                rejected.append(key)
            dst.append(v)
    if rejected:
        warnings.append("out of range, ignored: " + ", ".join(rejected))
    raw = [_int(values.get(f"raw_{nm}nm"), 0, 0xFFFF) for nm in WAVELENGTHS_NM]
    # 0xFFFF is both the "not converted" sentinel and a saturated count: whenever a channel's
    # saturation bit is set, 0xFFFF is the saturated count, whatever int_cycles says (the HDR
    # int_cycles is the bracket centre, not the exposure the count came from; contract 26
    # AS-DP-04).
    raw = [0xFFFF if r is None and meta["sat_mask"] >> i & 1 else r for i, r in enumerate(raw)]
    return spec, unc, raw


def analyse(values: Mapping[str, Any], *, t: float | None = None,
            white_ref: WhiteReference | None = None, library: SpectralLibrary | None = None,
            thresholds: ClassThresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """Everything the ground derives from one decoded SPEC v3.1 packet (the dictionary's
    engineering values, spec_<nm>nm / unc_<nm>nm already in uW/cm2). Pure: inputs are not
    modified, the result is JSON-safe. Keys: available, t, label, valid, mode, mode_name,
    flags, exposure, wavelength_nm, spectrum, uncertainty, snr, raw, raw_fraction, saturated,
    normalized, temperature_c, boresight, onboard, features, reflectance,
    white_ref_angle_deg, indices, radiometry, colorimetry, classification, fluorescence,
    library_match, warnings, notes (derived sections None when the packet carries no valid
    spectrum, e.g. BURST mode)."""
    warnings: list[str] = []
    meta = _meta(values, warnings)
    spec, unc, raw = _channels(values, meta, warnings)
    mode, flags = meta["mode"], meta["flags"]
    active = bool(flags & F_ACTIVE)
    lamps = (flags >> 1) & 7
    valid = bool(flags & F_VALID)
    sat = [bool(meta["sat_mask"] >> i & 1) for i in range(N_CH)]
    if any(sat):
        warnings.append("saturated: " + ", ".join(f"{WAVELENGTHS_NM[i]} nm" for i in range(N_CH)
                                                  if sat[i]))
    if not valid:
        warnings.append("packet flag 'valid' is clear: spectrum not usable")
    cyc, gain, led = meta["int_cycles"], meta["gain_code"], meta["led_cur"]
    fs = full_scale(cyc) if cyc is not None else None
    hdr = mode == MODE_HDR
    has_spec = valid and mode != MODE_BURST and any(v is not None for v in spec)
    out: dict[str, Any] = {
        "available": True, "t": _num(t), "label": APPROX_LABEL, "valid": valid,
        "mode": mode, "mode_name": MODE_NAMES.get(mode) if mode is not None else None,
        "flags": {"raw": flags, "valid": valid, "agc": bool(flags & F_AGC), "active": active,
                  "saturated": bool(flags & F_SAT), "attitude": bool(flags & F_ATTITUDE),
                  "lamp_bits": lamps, "lamps": [LAMP_NAMES[b] for b in range(3) if lamps >> b & 1]},
        "exposure": {"gain_code": gain, "gain_x": GAIN_X[gain] if gain is not None else None,
                     "int_cycles": cyc,
                     "integration_ms": integration_ms(cyc) if cyc is not None else None,
                     "full_scale": fs, "n_frames": meta["n_frames"],
                     "led_ma": None if led is None else {
                         "white": LED_DRV_MA[led & 3], "IR": LED_DRV_MA[led >> 2 & 3],
                         "UV": LED_DRV_MA[led >> 4 & 3]},
                     "note": HDR_EXPOSURE_NOTE if hdr else None},
        "wavelength_nm": list(WAVELENGTHS_NM),
        "spectrum": spec, "uncertainty": unc,
        "snr": [None if v is None or s is None or not s > 0.0 else v / s
                for v, s in zip(spec, unc, strict=True)],
        "raw": raw,
        "raw_fraction": [None if r is None or not fs or hdr else r / fs for r in raw],
        "saturated": sat, "normalized": _normalised(spec),
        "temperature_c": meta["temps"],
        "boresight": (None if meta["zen"] is None else
                      {"zenith_deg": meta["zen"], "azimuth_deg": meta["az"]}),
        "onboard": {"class_id": meta["class_id"],
                    "class": CLASS_NAMES.get(meta["class_id"]),
                    "confidence": meta["class_conf"], "change_deg": meta["change"]},
        "features": spectrum_features(spec),
        "reflectance": None, "white_ref_angle_deg": None, "indices": None, "radiometry": None,
        "colorimetry": None, "classification": None, "fluorescence": None, "library_match": None,
        "warnings": warnings, "notes": list(DATASHEET_NOTES),
    }
    if not has_spec:
        return out
    refl = refl_unc = None
    if white_ref is not None:
        ok, why = white_ref.compatible(mode, lamps, active)
        out["white_ref_angle_deg"] = spectral_angle_deg(spec, white_ref.spectrum)
        if ok:
            refl, refl_unc = reflectance(spec, unc, white_ref.spectrum, white_ref.uncertainty)
        out["reflectance"] = {"compatible": ok, "reason": why, "ref_t": white_ref.t,
                              "values": refl, "uncertainty": refl_unc}
    if mode != MODE_FLUOR:
        if refl is not None and refl_unc is not None and any(v is not None for v in refl):
            out["indices"] = vegetation_indices(refl, refl_unc, "reflectance", sat)
        else:
            out["indices"] = vegetation_indices(spec, unc, "spectrum", sat)
    out["radiometry"] = radiometry(spec, unc)
    out["radiometry"]["basis"] = "active illumination (lamp-on minus lamp-off)" if active else \
        "ambient light"
    colour = colorimetry(spec, unc)
    out["colorimetry"] = colour
    out["classification"] = classify(spec, unc, active=active, thresholds=thresholds,
                                     colour=colour, onboard_class=meta["class_id"],
                                     onboard_conf=meta["class_conf"])
    mask = None
    if mode == MODE_FLUOR:
        out["fluorescence"] = fluorescence_metrics(spec, unc)
        mask = [not m for m in out["fluorescence"]["masked"]]
    if library is not None and library.entries:
        out["library_match"] = library.match(spec, refl, mask)
    return out


# =============================================================================
# BURST analysis: time series, achieved rate, FFT, transients
# =============================================================================
BURST_MAX_SAMPLES = 8192       # per burst held on the ground (60 s at > 130 Hz)
BURST_FFT_MAX = 2048           # most recent samples used for the broadband spectrum
BURST_CH_FFT_MAX = 512         # per-channel dominant-frequency estimate
BURST_PLOT_POINTS = 300        # decimated series per channel in the snapshot
BURST_IDLE_S = 30.0            # no SPECBURST for this long: the burst is complete
BURST_KEEP = 5                 # completed burst summaries kept
TRANSIENT_Z = 6.0              # |robust z| above this is a transient (HEURISTIC)
TRANSIENT_MAX = 50


def fft(x: Sequence[complex]) -> list[complex]:
    """Iterative radix-2 decimation-in-time FFT; len(x) must be a power of two."""
    n = len(x)
    if n == 0 or n & (n - 1):
        raise ValueError("FFT length must be a power of two")
    a = [complex(v) for v in x]
    j = 0
    for i in range(1, n):                    # bit-reversal permutation
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:                         # log2(n) passes
        half = size // 2
        tw = [cmath.exp(-2j * math.pi * k / size) for k in range(half)]
        for start in range(0, n, size):
            for k in range(half):
                u = a[start + k]
                v = a[start + k + half] * tw[k]
                a[start + k] = u + v
                a[start + k + half] = u - v
        size *= 2
    return a


def amplitude_spectrum(y: Sequence[float], fs_hz: float, max_n: int = BURST_FFT_MAX
                       ) -> dict[str, Any] | None:
    """One-sided amplitude spectrum of the last max_n samples: mean removed, Hann window,
    zero-padded to a power of two, scaled so a sinusoid of amplitude A shows a peak ~A.
    Bins k fs/n_fft, k = 0..n_fft/2 (Nyquist). Peak refined by parabolic interpolation."""
    y = list(y[-max_n:])
    n = len(y)
    if n < 8 or not fs_hz > 0.0:
        return None
    mean = math.fsum(y) / n
    w = [0.5 - 0.5 * math.cos(2.0 * math.pi * k / (n - 1)) for k in range(n)]
    sw = math.fsum(w)
    n_fft = 1 << (n - 1).bit_length()
    spec = fft([(y[k] - mean) * w[k] for k in range(n)] + [0.0] * (n_fft - n))
    mag = [abs(spec[k]) * (1.0 if k in (0, n_fft // 2) else 2.0) / sw
           for k in range(n_fft // 2 + 1)]
    k = max(range(1, len(mag)), key=mag.__getitem__)
    df = fs_hz / n_fft
    peak = k * df
    if 1 <= k < len(mag) - 1:
        a, b, c = mag[k - 1], mag[k], mag[k + 1]
        den = a - 2.0 * b + c
        if den < 0.0:
            peak = (k + 0.5 * (a - c) / den) * df
    return {"n": n, "n_fft": n_fft, "fs_hz": fs_hz, "df_hz": df, "nyquist_hz": fs_hz / 2.0,
            "magnitude": mag, "peak_hz": peak, "peak_amplitude": mag[k], "window": "Hann"}


def resample_uniform(t_ms: Sequence[float], y: Sequence[float]) -> tuple[float, list[float]]:
    """Linear interpolation onto n equally spaced instants spanning the samples (the bus
    timing jitters by milliseconds); returns (rate Hz, resampled values)."""
    n = len(t_ms)
    if n < 2 or not t_ms[-1] > t_ms[0]:
        return 0.0, list(y)
    step = (t_ms[-1] - t_ms[0]) / (n - 1)
    out, j = [], 0
    for k in range(n):
        tk = t_ms[0] + k * step
        while j < n - 2 and t_ms[j + 1] < tk:   # j only advances: bounded by n overall
            j += 1
        t0, t1 = t_ms[j], t_ms[j + 1]
        f = 0.0 if t1 == t0 else min(1.0, max(0.0, (tk - t0) / (t1 - t0)))
        out.append(y[j] + f * (y[j + 1] - y[j]))
    return 1000.0 / step, out


def detect_transients(t_ms: Sequence[float], y: Sequence[float], z_thr: float = TRANSIENT_Z
                      ) -> list[dict[str, Any]]:
    """Runs of samples whose robust z-score (median / 1.4826 MAD; population std if the MAD
    is 0) exceeds z_thr, grouped by sign; at most TRANSIENT_MAX events."""
    if len(y) < 8:
        return []
    med = statistics.median(y)
    scale = 1.4826 * statistics.median([abs(v - med) for v in y])
    if not scale > 0.0:
        scale = statistics.pstdev(y)
    if not scale > 0.0:
        return []
    events: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for tk, v in zip(t_ms, y, strict=True):
        z = (v - med) / scale
        sign = 1 if z > 0 else -1
        if abs(z) > z_thr and (cur is None or cur["sign"] == sign):
            if cur is None:
                cur = {"t_ms": tk, "end_ms": tk, "sign": sign, "peak_z": z, "peak_value": v, "n": 0}
            cur["end_ms"] = tk
            cur["n"] += 1
            if abs(z) > abs(cur["peak_z"]):
                cur["peak_z"], cur["peak_value"] = z, v
            continue
        if cur is not None:
            events.append(cur)
            cur = None
            if len(events) >= TRANSIENT_MAX:
                break
            if abs(z) > z_thr:               # sign flip: a new run starts here
                cur = {"t_ms": tk, "end_ms": tk, "sign": sign, "peak_z": z, "peak_value": v, "n": 1}
    if cur is not None and len(events) < TRANSIENT_MAX:
        events.append(cur)
    for e in events:
        e["duration_ms"] = e.pop("end_ms") - e["t_ms"]
    return events


def analyse_burst(samples: Sequence[tuple[int, Sequence[int | None]]], *,
                  int_cycles: int | None = None, rate_hz_reported: float | None = None,
                  series_points: int = BURST_PLOT_POINTS) -> dict[str, Any]:
    """BURST analysis from (dt_ms, raw[18]) samples (any order, duplicates by dt_ms dropped):
    measured rate, per-channel statistics and dominant frequency, broadband (sum of the
    channels present in every sample) amplitude spectrum and transients, decimated series."""
    by_dt: dict[int, Sequence[int | None]] = {}
    for dt, raw in samples:
        by_dt.setdefault(int(dt), raw)
    dts = sorted(by_dt)
    n = len(dts)
    out: dict[str, Any] = {"n_samples": n, "duration_s": None, "rate_hz_measured": None,
                           "rate_hz_reported": _num(rate_hz_reported), "channels": [],
                           "broadband": None, "fft": None, "transients": [], "series": None}
    if n == 0:
        return out
    dur = (dts[-1] - dts[0]) / 1000.0
    out["duration_s"] = dur
    if n >= 2 and dur > 0.0:
        out["rate_hz_measured"] = (n - 1) / dur
    # Per-channel series; a channel is "present" when every sample converted it.
    cols: list[list[float] | None] = []
    for i in range(N_CH):
        ys = [float(v) for v in (by_dt[d][i] for d in dts) if v is not None]
        cols.append(ys if len(ys) == n else None)
    present = [i for i in range(N_CH) if cols[i] is not None]
    fs_counts = full_scale(int_cycles) if int_cycles is not None else None
    t_ms = [float(d) for d in dts]
    rate = out["rate_hz_measured"]
    for i, y in enumerate(cols):
        if y is None:
            out["channels"].append({"nm": WAVELENGTHS_NM[i], "present": False})
            continue
        mean = math.fsum(y) / n
        ch: dict[str, Any] = {
            "nm": WAVELENGTHS_NM[i], "present": True, "mean": mean,
            "std": statistics.pstdev(y) if n > 1 else 0.0, "min": min(y), "max": max(y),
            "saturated_samples": (sum(v >= fs_counts - 1 for v in y) if fs_counts else None),
            "peak_hz": None, "peak_amplitude": None}
        if rate:
            fs_hz, yu = resample_uniform(t_ms, y)
            sp = amplitude_spectrum(yu, fs_hz, BURST_CH_FFT_MAX)
            if sp:
                ch["peak_hz"], ch["peak_amplitude"] = sp["peak_hz"], sp["peak_amplitude"]
        out["channels"].append(ch)
    full = [y for y in cols if y is not None]
    if full:
        bb = [math.fsum(y[k] for y in full) for k in range(n)]
        out["broadband"] = {"channels_nm": [WAVELENGTHS_NM[i] for i in present],
                            "mean": math.fsum(bb) / n,
                            "std": statistics.pstdev(bb) if n > 1 else 0.0}
        if rate:
            fs_hz, bbu = resample_uniform(t_ms, bb)
            out["fft"] = amplitude_spectrum(bbu, fs_hz, BURST_FFT_MAX)
        out["transients"] = detect_transients(t_ms, bb)
    step = max(1, -(-n // max(1, series_points)))
    pick = dts[::step]
    out["series"] = {"decimation": step, "t_s": [d / 1000.0 for d in pick],
                     "raw": [None if y is None else [int(v) for v in y[::step]] for y in cols]}
    return out


class _Burst:
    """Samples of one burst (key: SCLK t0_ms of its first sample)."""

    def __init__(self, t0_ms: int, gain_code: int, int_cycles: int, t: float) -> None:
        self.t0_ms, self.gain_code, self.int_cycles = t0_ms, gain_code, int_cycles
        self.rate_hz_reported: float | None = None
        self.samples: dict[int, list[int | None]] = {}
        self.t_first = self.t_last = t
        self.duplicates = self.dropped = 0

    def add(self, values: Mapping[str, Any], t: float) -> None:
        self.t_last = t
        self.rate_hz_reported = _num(values.get("rate_hz"))
        samples = values.get("samples")
        if not isinstance(samples, (list, tuple)):
            self.dropped += 1
            return
        for s in samples[:BURST_PER_PACKET_MAX]:
            dt = _int(s.get("dt_ms") if isinstance(s, Mapping) else None, 0, 0xFFFF)
            raw = s.get("raw") if isinstance(s, Mapping) else None
            if dt is None or not isinstance(raw, Sequence) or len(raw) != N_CH:
                self.dropped += 1
                continue
            if dt in self.samples:
                self.duplicates += 1          # SSR playback re-sends what real time delivered
            elif len(self.samples) >= BURST_MAX_SAMPLES:
                self.dropped += 1
            else:
                self.samples[dt] = [_int(r, 0, 0xFFFF) for r in raw]

    def analyse(self, series_points: int) -> dict[str, Any]:
        a = analyse_burst(list(self.samples.items()), int_cycles=self.int_cycles,
                          rate_hz_reported=self.rate_hz_reported, series_points=series_points)
        a.update(t0_ms=self.t0_ms, gain_code=self.gain_code, int_cycles=self.int_cycles,
                 t_first=self.t_first, t_last=self.t_last, duplicates=self.duplicates,
                 dropped=self.dropped)
        return a


# =============================================================================
# Sky map and die-temperature drift
# =============================================================================
SKY_ZEN_STEP_DEG = 15.0
SKY_AZ_STEP_DEG = 30.0
_SKY_NZ = int(180.0 / SKY_ZEN_STEP_DEG)
_SKY_NA = int(360.0 / SKY_AZ_STEP_DEG)


class SkyMap:
    """Mean illuminance and PPFD in polar bins of boresight zenith/azimuth (passive spectra
    with a valid attitude only). An undefined azimuth (boresight on the vertical) goes to
    sector 0 of its ring."""

    def __init__(self) -> None:
        self.bins: dict[tuple[int, int], list[float]] = {}   # (iz, ia) -> n, Sl, Sl2, Sp, Sp2, np
        self.n_total = 0

    def add(self, zen: float, az: float | None, lux: float | None, ppfd: float | None) -> bool:
        if lux is None or not 0.0 <= zen <= 180.0:
            return False
        iz = min(int(zen // SKY_ZEN_STEP_DEG), _SKY_NZ - 1)
        ia = 0 if az is None else min(int((az % 360.0) // SKY_AZ_STEP_DEG), _SKY_NA - 1)
        b = self.bins.setdefault((iz, ia), [0.0] * 6)
        b[0] += 1
        b[1] += lux
        b[2] += lux * lux
        if ppfd is not None:
            b[3] += ppfd
            b[4] += ppfd * ppfd
            b[5] += 1
        self.n_total += 1
        return True

    def snapshot(self) -> dict[str, Any]:
        def mean_std(s: float, s2: float, n: float) -> tuple[float | None, float | None]:
            if n <= 0:
                return None, None
            m = s / n
            return m, math.sqrt(max(0.0, s2 / n - m * m))

        out = []
        for (iz, ia), b in sorted(self.bins.items()):
            lm, ls = mean_std(b[1], b[2], b[0])
            pm, ps = mean_std(b[3], b[4], b[5])
            out.append({"zen_lo": iz * SKY_ZEN_STEP_DEG, "zen_hi": (iz + 1) * SKY_ZEN_STEP_DEG,
                        "az_lo": ia * SKY_AZ_STEP_DEG, "az_hi": (ia + 1) * SKY_AZ_STEP_DEG,
                        "n": int(b[0]), "lux_mean": lm, "lux_std": ls, "ppfd_mean": pm,
                        "ppfd_std": ps})
        return {"label": APPROX_LABEL, "zen_step_deg": SKY_ZEN_STEP_DEG,
                "az_step_deg": SKY_AZ_STEP_DEG, "n_total": self.n_total, "bins": out}


DRIFT_MAX_POINTS = 500
DRIFT_MIN_POINTS = 10
DRIFT_MIN_SPAN_C = 2.0          # the die temperature is reported in whole degC


class TemperatureDrift:
    """Per device: least-squares slope of its 6-channel ambient signal against its die
    temperature. INFORMATIONAL - real changes of the scene light are confounded with it."""

    def __init__(self) -> None:
        self.points: list[deque[tuple[float, float]]] = [deque(maxlen=DRIFT_MAX_POINTS)
                                                         for _ in range(3)]

    def add(self, temps: Sequence[int | None], spec: Sequence[float | None],
            sat: Sequence[bool]) -> None:
        for d in range(3):
            temp = temps[d]
            vals = [spec[i] for i in DEVICE_CHANNELS[d] if not sat[i]]
            ok = [v for v in vals if v is not None]
            if temp is not None and len(ok) == len(DEVICE_CHANNELS[d]):
                self.points[d].append((float(temp), math.fsum(ok)))

    def snapshot(self) -> list[dict[str, Any]]:
        out = []
        for d in range(3):
            pts = list(self.points[d])
            n = len(pts)
            item: dict[str, Any] = {"device": d, "name": DEVICE_NAMES[d], "n": n,
                                    "temp_span_c": None, "slope_uw_per_c": None,
                                    "slope_pct_per_c": None, "r": None, "label": "informational"}
            ts = [p[0] for p in pts]
            if ts:
                item["temp_span_c"] = max(ts) - min(ts)
            if n >= DRIFT_MIN_POINTS and item["temp_span_c"] >= DRIFT_MIN_SPAN_C:
                mt = math.fsum(ts) / n
                ms = math.fsum(p[1] for p in pts) / n
                sxx = math.fsum((p[0] - mt) ** 2 for p in pts)
                sxy = math.fsum((p[0] - mt) * (p[1] - ms) for p in pts)
                syy = math.fsum((p[1] - ms) ** 2 for p in pts)
                slope = sxy / sxx
                item["slope_uw_per_c"] = slope
                item["slope_pct_per_c"] = 100.0 * slope / ms if ms != 0.0 else None
                item["r"] = sxy / math.sqrt(sxx * syy) if syy > 0.0 else None
            out.append(item)
        return out


# =============================================================================
# SpectralState: what the GDS holds
# =============================================================================
HISTORY_LEN = 300
EVENT_LOG_LEN = 200
_DEDUP_LEN = 1024


class SpectralState:
    """Ground spectral state: white reference, library, history ring, burst buffers, sky
    map, temperature drift and the spectral event log.

    Call order (all under the GDS lock): on_spec() for every decoded SPEC packet,
    on_specburst() for every decoded SPECBURST packet, on_evr() for every EVR, directive()
    for SPECREF / SPECLIB, drain_events() after each of those to log ground events, and
    snapshot() for the displays."""

    def __init__(self, store_dir: str | Path | None = None, *,
                 white_ref_file: str = "spec_white_ref.json",
                 library_file: str = "spec_library.json",
                 thresholds: ClassThresholds = DEFAULT_THRESHOLDS,
                 history_len: int = HISTORY_LEN) -> None:
        store = Path(store_dir) if store_dir is not None else None
        self.ref_path = store / white_ref_file if store is not None else None
        self.thresholds = thresholds
        self.history: deque[dict[str, Any]] = deque(maxlen=max(1, min(history_len, 10_000)))
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_LOG_LEN)
        self._pending: list[dict[str, Any]] = []
        self._seen: deque[tuple[int | None, float]] = deque(maxlen=_DEDUP_LEN)
        self.latest: dict[str, Any] | None = None
        self.latest_values: dict[str, Any] | None = None
        self.burst: _Burst | None = None
        self.burst_done: deque[dict[str, Any]] = deque(maxlen=BURST_KEEP)
        self._burst_cache: dict[str, Any] | None = None
        self._burst_dirty = False
        self.skymap = SkyMap()
        self.drift = TemperatureDrift()
        self._agree: bool | None = None
        self.counters = {"spec": 0, "duplicates": 0, "burst_packets": 0, "bursts": 0}
        # SPECBURST payload statistics (contract 28): raw octets = 36 n per packet.
        self.compression: dict[str, Any] = {"packets_raw": 0, "packets_rice": 0, "octets_raw": 0,
                                            "octets_sent": 0, "ratio_mean": None, "last_ratio": None}
        self.white_ref: WhiteReference | None = None
        if self.ref_path is not None and self.ref_path.exists():
            try:
                self.white_ref = WhiteReference.from_json(json.loads(self.ref_path.read_text()))
            except (OSError, ValueError) as exc:
                self._event("WARN", f"white reference {self.ref_path} ignored: {exc}")
        self.library = SpectralLibrary(store / library_file if store is not None else None)
        if self.library.load_error:
            self._event("WARN", self.library.load_error)

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], archive: bool = True) -> SpectralState:
        """station.toml: [spectral] white_ref_file, library_file (relative to [gds]
        archive_dir), [spectral.classifier] threshold overrides. archive=False keeps the
        reference and the library in memory only."""
        sp = cfg.get("spectral", {}) or {}
        store = (cfg.get("gds", {}) or {}).get("archive_dir", "archive") if archive else None
        return cls(store, white_ref_file=str(sp.get("white_ref_file", "spec_white_ref.json")),
                   library_file=str(sp.get("library_file", "spec_library.json")),
                   thresholds=ClassThresholds.from_mapping(sp.get("classifier")))

    # ------------------------------------------------------------------ events
    def _event(self, level: str, text: str, t: float | None = None) -> None:
        e = {"t": _num(t), "level": level, "source": "SPECTRAL", "text": text}
        self.events.append(e)
        self._pending.append(e)

    def drain_events(self) -> list[dict[str, Any]]:
        """Ground events raised since the last call: {t, level, source, text}."""
        out, self._pending = self._pending, []
        return out

    def on_evr(self, event_id: int, severity: str, text: str, t: float | None) -> None:
        """Copies onboard spectrometer EVRs (0x0701..0x0707) into the spectral event log
        (the GDS logs EVRs itself, so nothing is queued for drain_events)."""
        if isinstance(event_id, int) and event_id in EVR_SPECTRAL_IDS:
            self.events.append({"t": _num(t), "level": str(severity), "source": "S/C",
                                "event_id": event_id, "text": str(text)[:200]})

    # ------------------------------------------------------------------ packets
    def _reanalyse_latest(self) -> None:
        if self.latest_values is not None and self.latest is not None:
            self.latest = analyse(self.latest_values, t=self.latest.get("t"),
                                  white_ref=self.white_ref, library=self.library,
                                  thresholds=self.thresholds)

    def on_spec(self, values: Mapping[str, Any], t: float, *, sclk: float | None = None,
                partition: int | None = None) -> dict[str, Any] | None:
        """Analyses one decoded SPEC v3.1 packet. t = SCET (or ERT if unknown), UNIX s;
        (partition, sclk) identifies the packet, so the SSR-playback copy of one already
        received is counted and dropped (returns None). The newest spectrum by t becomes
        science.spectral."""
        sclk = _num(sclk)
        if sclk is not None:
            key = (_int(partition, 0, 0xFFFF), sclk)    # SCLK restarts at every MCU boot
            if key in self._seen:
                self.counters["duplicates"] += 1
                return None
            self._seen.append(key)
        a = analyse(values, t=t, white_ref=self.white_ref, library=self.library,
                    thresholds=self.thresholds)
        self.counters["spec"] += 1
        if self.latest is None or (a["t"] or 0.0) >= (self.latest.get("t") or 0.0):
            self.latest, self.latest_values = a, dict(values)
            cls = a["classification"]
            if cls is not None and cls["agrees"] is not None and cls["agrees"] != self._agree:
                if self._agree is not None or not cls["agrees"]:
                    verdict = "agree again" if cls["agrees"] else "DISAGREE (near a threshold?)"
                    self._event("INFO" if cls["agrees"] else "WARN",
                                f"light-source class ground {cls['class']} / onboard "
                                f"{cls['onboard']['class']}: {verdict}", t)
                self._agree = cls["agrees"]
        rad = a["radiometry"]
        if rad is None:                      # no valid spectrum (e.g. BURST mode): no row
            return a
        lux = rad["illuminance"]["value"]
        col = a["colorimetry"]
        self.history.append({"t": a["t"], "sclk": sclk, "mode": a["mode"],
                             "values": [_sig(v) for v in a["spectrum"]], "lux": _sig(lux),
                             "cct_k": _sig(col["cct_k"]) if col else None,
                             "class_id": (a["classification"] or {}).get("class_id")})
        passive = not a["flags"]["active"] and a["mode"] in (0, 1)        # SURVEY / HDR
        if passive and a["flags"]["attitude"] and a["boresight"] is not None:
            self.skymap.add(a["boresight"]["zenith_deg"], a["boresight"]["azimuth_deg"], lux,
                            rad["ppfd"]["value"])
        if passive:
            self.drift.add(a["temperature_c"], a["spectrum"], a["saturated"])
        return a

    def _count_payload(self, values: Mapping[str, Any]) -> None:
        enc = _int(values.get("encoding"), 0, 1)
        n = _int(values.get("n"), 1, BURST_PER_PACKET_MAX)
        plen = _int(values.get("payload_len"), 1, BURST_RAW_PER_SAMPLE * BURST_PER_PACKET_MAX)
        if enc is None or n is None or plen is None:
            return
        c = self.compression
        c["packets_rice" if enc == BURST_RICE else "packets_raw"] += 1
        c["octets_raw"] += BURST_RAW_PER_SAMPLE * n
        c["octets_sent"] += plen
        c["last_ratio"] = BURST_RAW_PER_SAMPLE * n / plen
        c["ratio_mean"] = c["octets_raw"] / c["octets_sent"]

    def on_specburst(self, values: Mapping[str, Any], t: float) -> None:
        """Adds one decoded SPECBURST packet (t = ERT or SCET, UNIX s); a new t0_ms closes
        the previous burst. Payload statistics: compression.ratio_mean = all raw octets /
        all payload octets sent (raw packets count 1:1), last_ratio = 36 n / payload_len."""
        t0 = _int(values.get("t0_ms"), 0, 0xFFFFFFFF)
        gain = _int(values.get("gain_code"), 0, 3)
        cyc = _int(values.get("int_cycles"), 0, 255)
        tv = _num(t)
        if t0 is None or gain is None or cyc is None or tv is None:
            self._event("WARN", "SPECBURST packet with invalid header or time ignored", tv)
            return
        t = tv
        self.counters["burst_packets"] += 1
        self._count_payload(values)
        if self.burst is not None and self.burst.t0_ms != t0:
            self._close_burst(t)
        if self.burst is None:
            self.burst = _Burst(t0, gain, cyc, t)
            self.counters["bursts"] += 1
            self._event("INFO", f"burst t0 = {t0} ms: receiving (gain code {gain}, N = {cyc})", t)
        self.burst.add(values, t)
        self._burst_dirty = True

    def _close_burst(self, t: float | None) -> None:
        b = self.burst
        if b is None:
            return
        a = b.analyse(BURST_PLOT_POINTS)
        self.burst_done.append({k: a[k] for k in ("t0_ms", "n_samples", "duration_s",
                                                  "rate_hz_measured", "rate_hz_reported",
                                                  "t_first", "t_last")} |
                               {"peak_hz": (a["fft"] or {}).get("peak_hz"),
                                "transients": len(a["transients"])})
        text = f"burst t0 = {b.t0_ms} ms complete: {a['n_samples']} samples"
        if a["duration_s"]:
            text += f", {a['duration_s']:.1f} s"
        if a["rate_hz_measured"]:
            text += f", {a['rate_hz_measured']:.1f} Hz measured"
        if b.rate_hz_reported:
            text += f" ({b.rate_hz_reported:.1f} Hz reported)"
        self._event("INFO", text, t)
        self._burst_cache, self._burst_dirty = a, False
        self.burst = None

    # --------------------------------------------------------------- directives
    def directive(self, tokens: Sequence[str], t: float | None = None) -> dict[str, Any]:
        """SPECREF [CLEAR] | SPECLIB SAVE <name> | SPECLIB DEL <name> | SPECLIB LIST.
        Returns {"status": "OK" | "REJECTED", "message": str[, "entries": [...]]}."""
        toks = [str(x) for x in tokens][:4]
        head = toks[0].upper() if toks else ""
        if head == "SPECREF":
            if len(toks) == 1:
                return self._ref_capture(t)
            if len(toks) == 2 and toks[1].upper() == "CLEAR":
                return self._ref_clear(t)
            return {"status": "REJECTED", "message": "usage: " + DIRECTIVE_HELP["SPECREF"]}
        if head == "SPECLIB":
            sub = toks[1].upper() if len(toks) > 1 else ""
            if sub == "LIST" and len(toks) == 2:
                names = sorted(self.library.entries)
                return {"status": "OK", "entries": names,
                        "message": f"{len(names)} entries: " + (", ".join(names) or "(empty)")}
            if sub in ("SAVE", "DEL") and len(toks) == 3:
                if not LIBRARY_NAME_RE.match(toks[2]):
                    return {"status": "REJECTED",
                            "message": "name: 1-32 of A-Z a-z 0-9 _ . - (starting alphanumeric)"}
                return self._lib_save(toks[2], t) if sub == "SAVE" else self._lib_del(toks[2], t)
            return {"status": "REJECTED", "message": "usage: " + DIRECTIVE_HELP["SPECLIB"]}
        return {"status": "REJECTED", "message": f"not a spectral directive: {head or '(empty)'}"}

    def _usable_latest(self) -> tuple[dict[str, Any] | None, str]:
        a = self.latest
        if a is None:
            return None, "no SPEC packet received yet"
        if not a["valid"] or a["mode"] == MODE_BURST or a["features"]["n_valid"] == 0:
            return None, "latest SPEC packet carries no valid spectrum"
        return a, ""

    def _ref_capture(self, t: float | None) -> dict[str, Any]:
        a, why = self._usable_latest()
        if a is None:
            return {"status": "REJECTED", "message": why}
        spec = [None if v is None or a["saturated"][i] or not v > 0.0 else v
                for i, v in enumerate(a["spectrum"])]
        n = sum(v is not None for v in spec)
        if n < MIN_REF_CHANNELS:
            return {"status": "REJECTED", "message": f"only {n} usable (positive, unsaturated) "
                    f"channels; a reference needs {MIN_REF_CHANNELS}"}
        fl = a["flags"]
        self.white_ref = WhiteReference(a["t"], a["mode"], fl["lamp_bits"], fl["active"],
                                        a["exposure"]["gain_code"], a["exposure"]["int_cycles"],
                                        spec, [None if spec[i] is None else a["uncertainty"][i]
                                               for i in range(N_CH)])
        light = "active illumination" if fl["active"] else "ambient light"
        msg = f"white reference captured ({a['mode_name']}, {light}, {n} channels)"
        level = "INFO"
        if self.ref_path is not None:
            try:
                _atomic_write_json(self.ref_path, self.white_ref.to_json())
            except OSError as exc:
                msg, level = msg + f" - NOT saved to disk: {exc}", "WARN"
        self._event(level, msg, t)
        self._reanalyse_latest()
        return {"status": "OK", "message": msg}

    def _ref_clear(self, t: float | None) -> dict[str, Any]:
        if self.white_ref is None:
            return {"status": "OK", "message": "no white reference to clear"}
        self.white_ref = None
        msg = "white reference cleared"
        if self.ref_path is not None:
            try:
                self.ref_path.unlink(missing_ok=True)
            except OSError as exc:
                msg += f" - file not removed: {exc}"
        self._event("INFO", msg, t)
        self._reanalyse_latest()
        return {"status": "OK", "message": msg}

    def _lib_save(self, name: str, t: float | None) -> dict[str, Any]:
        a, why = self._usable_latest()
        if a is None:
            return {"status": "REJECTED", "message": why}
        if name not in self.library.entries and len(self.library.entries) >= LIBRARY_MAX:
            return {"status": "REJECTED", "message": f"library full ({LIBRARY_MAX} entries)"}
        r = a["reflectance"]
        refl = r["values"] if r and r["compatible"] else None
        replaced = name in self.library.entries
        self.library.entries[name] = LibraryEntry(name, a["t"], a["mode"], a["flags"]["lamp_bits"],
                                                  a["flags"]["active"], _normalised(a["spectrum"]),
                                                  refl)
        msg = f"library entry '{name}' {'replaced' if replaced else 'saved'}" + \
              (" (with reflectance)" if refl else " (spectrum only)")
        err = self.library.persist()
        if err:
            msg += f" - {err}"
        self._event("WARN" if err else "INFO", msg, t)
        self._reanalyse_latest()
        return {"status": "OK", "message": msg}

    def _lib_del(self, name: str, t: float | None) -> dict[str, Any]:
        if name not in self.library.entries:
            return {"status": "REJECTED", "message": f"no library entry '{name}'"}
        del self.library.entries[name]
        msg = f"library entry '{name}' deleted"
        err = self.library.persist()
        if err:
            msg += f" - {err}"
        self._event("WARN" if err else "INFO", msg, t)
        self._reanalyse_latest()
        return {"status": "OK", "message": msg}

    # ----------------------------------------------------------------- snapshot
    def snapshot(self, now: float | None = None, *, history_rows: int = HISTORY_LEN
                 ) -> dict[str, Any]:
        """The five display keys (contract 10 / 13.7), all JSON-safe:
        spectral, spectral_history, spectral_burst, spectral_skymap, spectral_library.
        `now` (UNIX s) closes a burst idle for BURST_IDLE_S."""
        now = _num(now)
        if self.burst is not None and now is not None and now - self.burst.t_last > BURST_IDLE_S:
            self._close_burst(now)
        if self.burst is not None and self._burst_dirty:
            self._burst_cache = self.burst.analyse(BURST_PLOT_POINTS)
            self._burst_dirty = False
        spectral: dict[str, Any] = dict(self.latest) if self.latest else {"available": False}
        spectral.update(white_ref=self.white_ref.info() if self.white_ref else None,
                        temperature_drift=self.drift.snapshot(), events=list(self.events)[-50:],
                        counters=dict(self.counters), directives=dict(DIRECTIVE_HELP))
        rows = sorted(self.history, key=lambda r: r["t"] or 0.0)
        rows = rows[max(0, len(rows) - max(0, history_rows)):]
        history = {"wavelength_nm": list(WAVELENGTHS_NM), "max_rows": self.history.maxlen,
                   "t": [r["t"] for r in rows], "sclk": [r["sclk"] for r in rows],
                   "mode": [r["mode"] for r in rows], "class_id": [r["class_id"] for r in rows],
                   "lux": [r["lux"] for r in rows], "cct_k": [r["cct_k"] for r in rows],
                   "values": [r["values"] for r in rows]}
        burst = None
        if self._burst_cache is not None:
            burst = dict(self._burst_cache)
            burst["active"] = self.burst is not None
            burst["previous"] = list(self.burst_done)
        elif self.burst_done:
            burst = {"active": False, "previous": list(self.burst_done)}
        if burst is not None:
            burst["compression"] = dict(self.compression)
        return {"spectral": spectral, "spectral_history": history, "spectral_burst": burst,
                "spectral_skymap": self.skymap.snapshot(),
                "spectral_library": self.library.snapshot()}
