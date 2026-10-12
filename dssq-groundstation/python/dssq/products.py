"""Standard ground data products: CCSDS Tracking Data Message and XTCE mission database.

Contract v3 sections 24.7 (TDM) and 24.9 (XTCE). Pure Python, standard library only.

1. Tracking Data Message (TDM), CCSDS 503.0-B-2 incl. Technical Corrigendum 1 (Blue Book,
   June 2020 / October 2021), KVN syntax (section 4). Section numbers below refer to it.
   * Structure (3.1): Header + one or more Segments; Segment = Metadata + Data Section with
     at least one Tracking Data Record.
   * Header (3.2, Table 3-2): CCSDS_TDM_VERS = 2.0, COMMENT, CREATION_DATE (UTC), ORIGINATOR,
     MESSAGE_ID - in this order.
   * Metadata (3.3, Table 3-3): keyword order fixed by the table; TIME_SYSTEM mandatory, at
     least one PARTICIPANT_n (n = 1..5). One-way data (3.3.2): MODE = SEQUENTIAL and the
     spacecraft's participant number first in PATH. Participant numbering follows the
     Blue Book one-way examples (Annex E, Figures E-1 and E-2): PARTICIPANT_1 = the ground
     station, PARTICIPANT_2 = the spacecraft, PATH = 2,1, RECEIVE_FREQ_1 is the frequency
     received at the station and TRANSMIT_FREQ_2 the spacecraft's nominal beacon frequency,
     as 3.3.2 recommends for one-way receive-frequency data.
   * Data (3.4, 3.5; units Table 3-5): CARRIER_POWER [dBW] (3.5.2.1), DOPPLER_INSTANTANEOUS
     [km/s] = instantaneous range rate, positive receding (3.5.2.2), PC_N0 [dBHz] (3.5.2.5),
     RECEIVE_FREQ_n [Hz] (3.5.2.8; FREQ_OFFSET [Hz] "must be added to every RECEIVE_FREQ to
     reconstruct it", Table 3-3), TRANSMIT_FREQ_n [Hz] (3.5.2.9).
   * KVN (4.2, 4.3): printable ASCII, <= 254 characters per line, no TAB; fixed-point
     numbers with <= 16 digits; NaN/Inf/-0 not supported; timetags
     YYYY-MM-DDThh:mm:ss[.d..d][Z] or YYYY-DDDThh:mm:ss[.d..d][Z] (4.3.9); comments only at the start
     of the Header, of a Metadata Section and of a Data Section (4.5).
   * Within a Data Section the data of each keyword are in chronological order and every
     keyword/timetag pair is unique (3.4).
   Mission conventions (the "ICD" items of Annex D, stated in the file's COMMENT lines):
   TRANSMIT_BAND = RECEIVE_BAND = UHF meaning 300 MHz..3 GHz (ITU-R V.431 band 9); timetags
   are UTC at the end of reception at the station receiver; CARRIER_POWER is the calibrated
   receiver RSSI (station.toml [radio] rssi_cal_offset_db applied) at the receiver input;
   PC_N0 = SNR + 10 log10(noise bandwidth); RECEIVE_FREQ_1 = nominal channel frequency
   (FREQ_OFFSET) + station AFC correction + measured residual, against the station's own
   frequency reference; DOPPLER_INSTANTANEOUS is only produced when the static TX-RX crystal
   offset [radio] freq_offset_cal_hz has been calibrated. The E22 UART modules report RSSI
   only, so their TDMs carry CARRIER_POWER only.
   Producer: build_tdm() / TdmRecorder, which re-read their own output with the strict
   reader before returning it. Reader: parse_tdm(text, strict=True) validates everything
   above; strict=False accepts the formatting latitude of third-party files (TAB as blank,
   any float spelling, free keyword order) but still rejects unknown keywords, malformed
   records, bad timetags, non-numeric values and inconsistent structure.

2. XTCE, CCSDS 660.0-B-2 (February 2020) = OMG XML Telemetric and Command Exchange 1.2
   (formal/18-10-04), namespace http://www.omg.org/spec/XTCE/20180204, normative schema
   SpaceSystem.xsd. build_xtce() turns dssq/dictionary.py into one SpaceSystem
   "VGQ-1": the CCSDS Space Packet primary header (CCSDS 133.0-B-2) and the CUC secondary
   header (CCSDS 301.0-B-4, implicit P-field 0x2E) in an abstract container, one child
   SpaceSystem per telemetry APID whose container inherits it with a RestrictionCriteria on
   the APID, integer/IEEE-754 encodings big-endian, polynomial calibrators for the scale
   factors, units, alarm ranges from the GDS limits; and the telecommands as an abstract
   MetaCommand for the TC packet (APID 0x0C0, no secondary header, opcode) inherited by one
   MetaCommand per opcode (opcode and packet length as ArgumentAssignments, argument ranges
   as ValidRangeSets, hazardous commands with DefaultSignificance "critical"). The generator
   never imports a schema validator; the tests validate against SpaceSystem.xsd when one is
   available.

Command line
    python -m dssq.products xtce [-o vgq1.xtce.xml] [--version V] [--date D]
    python -m dssq.products tdm validate FILE [--lenient]
    python -m dssq.products tdm build RECORDS.csv --station DSS-Q1 [-o FILE | --archive DIR]
                [--freq-offset-cal-hz HZ] [--radio-model NAME] [--originator NAME]
  RECORDS.csv columns (header row required, empty cell = not available):
    t_utc,rx_power_dbm,snr_db,freq_offset_hz,afc_correction_hz,nominal_freq_hz,noise_bw_hz[,integration_s]
  t_utc is UNIX seconds or an ISO 8601 UTC time.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import re
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from . import dictionary as dd
from . import spectral
from .link import C_M_S

# =============================================================================
# 1. Tracking Data Message
# =============================================================================
TDM_VERSION = "2.0"                    # this issue (Annex E examples, 5.3 version="2.0"; Table 3-2)
KVN_MAX_LINE = 254                     # 4.2
KVN_MAX_DIGITS = 16                    # 4.3: fixed-point digits / floating-point mantissa
INT32_MIN, INT32_MAX = -2 ** 31, 2 ** 31 - 1          # 4.3: KVN integer range
MAX_TDM_OCTETS = 64 * 1024 * 1024      # reader guard: a TDM is text, refuse anything larger
UHF_RANGE_HZ = (300e6, 3e9)            # ITU-R V.431 band 9 (our ICD meaning of "UHF")

HEADER_KEYS = ("CCSDS_TDM_VERS", "CREATION_DATE", "ORIGINATOR", "MESSAGE_ID")
_HEADER_MANDATORY = ("CREATION_DATE", "ORIGINATOR")

# Table 3-3 rows in their normative order; "*_n" rows are indexed 1..5 and CORRECTION_* is
# one row. The row index is the order key used by the strict reader.
_META_ROWS: tuple[tuple[str, ...], ...] = (
    ("TRACK_ID",), ("DATA_TYPES",), ("TIME_SYSTEM",), ("START_TIME",), ("STOP_TIME",),
    tuple(f"PARTICIPANT_{n}" for n in range(1, 6)), ("MODE",), ("PATH", "PATH_1", "PATH_2"),
    tuple(f"EPHEMERIS_NAME_{n}" for n in range(1, 6)), ("TRANSMIT_BAND",), ("RECEIVE_BAND",),
    ("TURNAROUND_NUMERATOR",), ("TURNAROUND_DENOMINATOR",), ("TIMETAG_REF",),
    ("INTEGRATION_INTERVAL",), ("INTEGRATION_REF",), ("FREQ_OFFSET",), ("RANGE_MODE",),
    ("RANGE_MODULUS",), ("RANGE_UNITS",), ("ANGLE_TYPE",), ("REFERENCE_FRAME",),
    ("INTERPOLATION",), ("INTERPOLATION_DEGREE",), ("DOPPLER_COUNT_BIAS",),
    ("DOPPLER_COUNT_SCALE",), ("DOPPLER_COUNT_ROLLOVER",),
    tuple(f"TRANSMIT_DELAY_{n}" for n in range(1, 6)), tuple(f"RECEIVE_DELAY_{n}" for n in range(1, 6)),
    ("DATA_QUALITY",),
    ("CORRECTION_ANGLE_1", "CORRECTION_ANGLE_2", "CORRECTION_DOPPLER", "CORRECTION_MAG",
     "CORRECTION_RANGE", "CORRECTION_RCS", "CORRECTION_RECEIVE", "CORRECTION_TRANSMIT",
     "CORRECTION_ABERRATION_YEARLY", "CORRECTION_ABERRATION_DIURNAL"),
    ("CORRECTIONS_APPLIED",),
)
META_ORDER: dict[str, int] = {k: i for i, row in enumerate(_META_ROWS) for k in row}

# Normative value sets (Table 3-3 rows marked "N"); text values are case-insensitive (4.3).
_META_ENUMS: dict[str, frozenset[str]] = {
    "MODE": frozenset({"SEQUENTIAL", "SINGLE_DIFF"}),
    "TIMETAG_REF": frozenset({"TRANSMIT", "RECEIVE"}),
    "INTEGRATION_REF": frozenset({"START", "MIDDLE", "END"}),
    "RANGE_MODE": frozenset({"COHERENT", "CONSTANT", "ONE_WAY"}),
    "RANGE_UNITS": frozenset({"KM", "S", "RU"}),
    "ANGLE_TYPE": frozenset({"AZEL", "RADEC", "XEYN", "XSYE"}),
    "DOPPLER_COUNT_ROLLOVER": frozenset({"YES", "NO"}),
    "DATA_QUALITY": frozenset({"RAW", "VALIDATED", "DEGRADED"}),
    "CORRECTIONS_APPLIED": frozenset({"YES", "NO"}),
}
_META_INTEGERS = frozenset({"TURNAROUND_NUMERATOR", "TURNAROUND_DENOMINATOR", "INTERPOLATION_DEGREE",
                            "DOPPLER_COUNT_SCALE"})
_META_DOUBLES = frozenset({"INTEGRATION_INTERVAL", "FREQ_OFFSET", "RANGE_MODULUS", "DOPPLER_COUNT_BIAS",
                           *_META_ROWS[-2], *_META_ROWS[-4], *_META_ROWS[-5]})
_META_TIMES = frozenset({"START_TIME", "STOP_TIME"})
# Lower bounds stated in Table 3-3: (bound, inclusive).
_META_MIN: dict[str, tuple[float, bool]] = {
    "INTEGRATION_INTERVAL": (0.0, False), "RANGE_MODULUS": (0.0, True), "DOPPLER_COUNT_BIAS": (0.0, False),
    "DOPPLER_COUNT_SCALE": (0.0, False),
    **{f"TRANSMIT_DELAY_{n}": (0.0, True) for n in range(1, 6)},
    **{f"RECEIVE_DELAY_{n}": (0.0, True) for n in range(1, 6)},
}

# Table 3-5 data keywords (DATA_START / DATA_STOP / COMMENT are structural).
_DATA_PLAIN = frozenset({"ANGLE_1", "ANGLE_2", "CARRIER_POWER", "CLOCK_BIAS", "CLOCK_DRIFT", "DOPPLER_COUNT",
                         "DOPPLER_INSTANTANEOUS", "DOPPLER_INTEGRATED", "DOR", "MAG", "PC_N0", "PR_N0",
                         "PRESSURE", "RANGE", "RCS", "RECEIVE_FREQ", "RHUMIDITY", "STEC", "TEMPERATURE",
                         "TROPO_DRY", "TROPO_WET", "VLBI_DELAY"})
_DATA_INDEXED = ("RECEIVE_FREQ_", "RECEIVE_PHASE_CT_", "TRANSMIT_FREQ_", "TRANSMIT_FREQ_RATE_",
                 "TRANSMIT_PHASE_CT_")
DATA_KEYWORDS = frozenset(_DATA_PLAIN | {p + str(n) for p in _DATA_INDEXED for n in range(1, 6)})
_PHASE_PREFIXES = ("RECEIVE_PHASE_CT_", "TRANSMIT_PHASE_CT_")
# Value domains stated in 3.5 (strict mode): (low, low inclusive, high, high inclusive).
_DATA_RANGE: dict[str, tuple[float | None, bool, float | None, bool]] = {
    "ANGLE_1": (-180.0, True, 360.0, False), "ANGLE_2": (-180.0, True, 360.0, False),   # 3.5.4.2/3
    "RHUMIDITY": (0.0, True, 100.0, True),                                               # 3.5.8.2
    "TEMPERATURE": (0.0, False, None, False),                                            # 3.5.8.3
    "TROPO_DRY": (0.0, True, None, False), "TROPO_WET": (0.0, True, None, False),        # 3.5.7.2/3
    "STEC": (0.0, False, None, False), "RCS": (0.0, False, None, False),                 # 3.5.7.1, 3.5.5.2
    **{f"TRANSMIT_FREQ_{n}": (0.0, False, None, False) for n in range(1, 6)},            # 3.5.2.9
}

_KEYWORD_RE = re.compile(r"[A-Z][A-Z0-9_]*$")
_VERSION_RE = re.compile(r"\d+\.\d+$")
_PATH_RE = re.compile(r"[1-5](,[1-5])+$")
_INT_RE = re.compile(r"[+-]?\d+$")
_FIXED_RE = re.compile(r"[+-]?(\d+)\.(\d+)$")
_FLOAT_RE = re.compile(r"[+-]?(\d)\.(\d+)[Ee][+-]?\d+$")
_PHASE_RE = re.compile(r"[+-]?\d+(\.\d+)?$")
_CAL_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?Z?$")
_DOY_RE = re.compile(r"(\d{4})-(\d{3})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?Z?$")


class TdmError(ValueError):
    """A TDM violates CCSDS 503.0-B-2 (message carries the line number and the rule)."""


@dataclass(frozen=True)
class TdmObservation:
    keyword: str
    epoch: str                         # timetag exactly as written in the file
    value: float                       # measurement (phase counts: nearest double)
    text: str                          # measurement exactly as written (full precision)
    line: int


@dataclass
class TdmSegment:
    comments: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)       # keyword -> value, file order
    data_comments: list[str] = field(default_factory=list)
    observations: list[TdmObservation] = field(default_factory=list)

    def values(self, keyword: str) -> list[float]:
        return [o.value for o in self.observations if o.keyword == keyword]


@dataclass
class Tdm:
    version: str
    comments: list[str]
    creation_date: str
    originator: str
    message_id: str | None
    segments: list[TdmSegment]


def parse_epoch(text: str) -> tuple[int, Decimal]:
    """KVN timetag (4.3.9) -> sort key (proleptic Gregorian day ordinal, seconds of day as an
    exact Decimal). Leap second 23:59:60 is accepted (UTC). Raises ValueError."""
    m = _CAL_RE.match(text)
    if m:
        y, mo, d, hh, mi, ss, frac = m.groups()
        day = date(int(y), int(mo), int(d))                       # validates month and day
    else:
        m = _DOY_RE.match(text)
        if not m:
            raise ValueError(f"timetag {text!r} is not YYYY-MM-DDThh:mm:ss[.d][Z] or YYYY-DDDThh:mm:ss[.d][Z]")
        y, doy, hh, mi, ss, frac = m.groups()
        first = date(int(y), 1, 1)
        n_days = (date(int(y) + 1, 1, 1) - first).days
        if not 1 <= int(doy) <= n_days:
            raise ValueError(f"timetag {text!r}: day of year outside 001..{n_days:03d}")
        day = first + timedelta(days=int(doy) - 1)
    h, mnt, s = int(hh), int(mi), int(ss)
    if h > 23 or mnt > 59 or s > 60 or (s == 60 and (h, mnt) != (23, 59)):
        raise ValueError(f"timetag {text!r}: time of day out of range")
    return day.toordinal(), Decimal(h * 3600 + mnt * 60 + s) + (Decimal(frac) if frac else Decimal(0))


def _number(text: str, strict: bool, what: str) -> float:
    """KVN numeric value (4.3). strict: integer, fixed-point (<= 16 digits) or
    floating-point d.ddd[Ee]x (<= 16 mantissa digits); NaN/Inf/-0 rejected in both modes."""
    if strict:
        mf, mx = _FIXED_RE.match(text), _FLOAT_RE.match(text)
        ok = bool(_INT_RE.match(text)) or (mf is not None and len(mf[1]) + len(mf[2]) <= KVN_MAX_DIGITS) or \
            (mx is not None and 1 + len(mx[2]) <= KVN_MAX_DIGITS)
        if not ok:
            raise ValueError(f"{what}: {text!r} is not a KVN integer, fixed-point or floating-point value")
    try:
        v = float(text)
    except ValueError:
        raise ValueError(f"{what}: {text!r} is not a number") from None
    if not math.isfinite(v):
        raise ValueError(f"{what}: {text!r} - NaN and infinities are not supported (4.3)")
    if strict and v == 0.0 and text.lstrip().startswith("-"):
        raise ValueError(f"{what}: {text!r} - '-0' is not supported (4.3)")
    return v


def _check_bound(v: float, lo: float | None, lo_inc: bool, hi: float | None, hi_inc: bool, what: str) -> None:
    if lo is not None and (v < lo or (v == lo and not lo_inc)):
        raise ValueError(f"{what} = {v!r} below its allowed range")
    if hi is not None and (v > hi or (v == hi and not hi_inc)):
        raise ValueError(f"{what} = {v!r} above its allowed range")


def _participant_index(keyword: str) -> int | None:
    tail = keyword.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() and keyword[:-len(tail)] in _DATA_INDEXED else None


def _validate_metadata(seg: TdmSegment, strict: bool, line: int) -> None:
    md = seg.metadata
    where = f"metadata section ending at line {line}"
    if "TIME_SYSTEM" not in md:
        raise TdmError(f"{where}: TIME_SYSTEM is mandatory (Table 3-3)")
    participants = {int(k[-1]) for k in md if k.startswith("PARTICIPANT_")}
    if not participants:
        raise TdmError(f"{where}: at least one PARTICIPANT_n is mandatory (Table 3-3)")
    for k, v in md.items():
        what = f"{where}: {k}"
        try:
            if k in _META_ENUMS and v.upper() not in _META_ENUMS[k]:
                raise ValueError(f"{what} = {v!r}; allowed {sorted(_META_ENUMS[k])}")
            if k in _META_INTEGERS and (not _INT_RE.match(v) or not INT32_MIN <= int(v) <= INT32_MAX):
                raise ValueError(f"{what} = {v!r} is not a 32-bit integer")
            if k in _META_DOUBLES or k in _META_INTEGERS:
                x = _number(v, strict, what)
                if k in _META_MIN:
                    _check_bound(x, _META_MIN[k][0], _META_MIN[k][1], None, False, what)
            if k in _META_TIMES:
                parse_epoch(v)
            if k == "DATA_TYPES":
                bad = [t for t in v.split(",") if t.strip() not in DATA_KEYWORDS]
                if bad or (strict and " " in v):
                    raise ValueError(f"{what}: not a comma-separated list of data keywords: {bad or v!r}")
            if k.startswith("PARTICIPANT_") and not v.strip():
                raise ValueError(f"{what} is empty")
        except ValueError as e:
            raise TdmError(str(e)) from None
    mode = md.get("MODE", "").upper()
    paths = {k: md[k] for k in ("PATH", "PATH_1", "PATH_2") if k in md}
    for k, v in paths.items():
        if not _PATH_RE.match(v):
            raise TdmError(f"{where}: {k} = {v!r} must list participant indices separated by commas, "
                           "no blanks (Table 3-3)")
        missing = {int(c) for c in v.split(",")} - participants
        if missing:
            raise TdmError(f"{where}: {k} = {v} names undefined participant(s) {sorted(missing)}")
    if "PATH" in paths and mode != "SEQUENTIAL":
        raise TdmError(f"{where}: PATH is used with MODE = SEQUENTIAL only (Table 3-3)")
    if ("PATH_1" in paths or "PATH_2" in paths) and mode != "SINGLE_DIFF":
        raise TdmError(f"{where}: PATH_1/PATH_2 are used with MODE = SINGLE_DIFF only (Table 3-3)")
    if mode == "SEQUENTIAL" and "PATH" not in paths:
        raise TdmError(f"{where}: MODE = SEQUENTIAL needs PATH (3.3.2, Annex I)")
    if mode == "SINGLE_DIFF" and not ("PATH_1" in paths and "PATH_2" in paths):
        raise TdmError(f"{where}: MODE = SINGLE_DIFF needs PATH_1 and PATH_2 (3.3.2, differenced modes)")
    if any(k.startswith("CORRECTION_") for k in md) and "CORRECTIONS_APPLIED" not in md:
        raise TdmError(f"{where}: CORRECTIONS_APPLIED is required with CORRECTION_* (Table 3-3)")
    if strict and "INTERPOLATION" in md and "INTERPOLATION_DEGREE" not in md:
        raise TdmError(f"{where}: INTERPOLATION needs INTERPOLATION_DEGREE (Table 3-3)")
    if "START_TIME" in md and "STOP_TIME" in md and parse_epoch(md["START_TIME"]) > parse_epoch(md["STOP_TIME"]):
        raise TdmError(f"{where}: START_TIME is after STOP_TIME")


def _validate_data(seg: TdmSegment, strict: bool) -> None:
    md = seg.metadata
    participants = {int(k[-1]) for k in md if k.startswith("PARTICIPANT_")}
    if not seg.observations:
        raise TdmError("a Data Section needs at least one Tracking Data Record (3.1)")
    span = None
    if strict and md.get("TIME_SYSTEM", "").upper() == "UTC" and "START_TIME" in md and "STOP_TIME" in md:
        span = (parse_epoch(md["START_TIME"]), parse_epoch(md["STOP_TIME"]))
    last: dict[str, tuple[int, Decimal]] = {}
    for o in seg.observations:
        key = parse_epoch(o.epoch)
        prev = last.get(o.keyword)
        if prev is not None and key < prev:
            raise TdmError(f"line {o.line}: {o.keyword} data not in chronological order (3.4)")
        if prev is not None and key == prev:
            raise TdmError(f"line {o.line}: {o.keyword} = {o.epoch} repeats a keyword/timetag pair (3.4)")
        last[o.keyword] = key
        if strict:
            n = _participant_index(o.keyword)
            if n is not None and n not in participants:
                raise TdmError(f"line {o.line}: {o.keyword} refers to undefined PARTICIPANT_{n}")
            if span is not None and not span[0] <= key <= span[1]:
                raise TdmError(f"line {o.line}: timetag {o.epoch} outside START_TIME..STOP_TIME")
            rng = _DATA_RANGE.get(o.keyword)
            if rng is not None:
                try:
                    _check_bound(o.value, *rng, what=f"line {o.line}: {o.keyword}")
                except ValueError as e:
                    raise TdmError(str(e)) from None


def _observation(keyword: str, value: str, line: int, strict: bool) -> TdmObservation:
    parts = value.split()
    if len(parts) != 2:
        raise TdmError(f"line {line}: {keyword}: value must be '<timetag> <measurement>' (3.4, 4.2), got {value!r}")
    epoch, meas = parts
    try:
        parse_epoch(epoch)
        if keyword.startswith(_PHASE_PREFIXES):
            if not _PHASE_RE.match(meas):
                raise ValueError(f"{keyword}: phase count {meas!r} must be a real number string (4.3)")
            v = float(meas)
        elif keyword == "DOPPLER_COUNT":
            if not _INT_RE.match(meas) or (strict and not INT32_MIN <= int(meas) <= INT32_MAX):
                raise ValueError(f"DOPPLER_COUNT {meas!r} must be an integer (3.5.2.4)")
            v = float(meas)
        else:
            v = _number(meas, strict, keyword)
    except ValueError as e:
        raise TdmError(f"line {line}: {e}") from None
    return TdmObservation(keyword, epoch, v, meas, line)


def parse_tdm(text: str, strict: bool = True) -> Tdm:
    """Parses and validates a KVN TDM (CCSDS 503.0-B-2). Raises TdmError naming the line and
    the rule. strict=True applies every KVN syntax and value rule (used on our own output);
    strict=False tolerates TAB characters, non-ASCII text (e.g. typographic quotes in
    comments), number spellings that float() accepts, free metadata order, values outside
    the stated domains and data/participant cross-references (third-party files), but not
    unknown keywords, malformed records or broken structure."""
    if not isinstance(text, str):
        raise TdmError("TDM text must be str")
    if len(text) > MAX_TDM_OCTETS:
        raise TdmError(f"TDM larger than {MAX_TDM_OCTETS} characters")
    header: dict[str, str] = {}
    head_comments: list[str] = []
    segments: list[TdmSegment] = []
    seg: TdmSegment | None = None
    state = "START"                    # START HEADER META DATA BETWEEN
    lines = text.replace("\r\n", "\n").replace("\n\r", "\n").replace("\r", "\n").split("\n")
    for no, raw in enumerate(lines, start=1):
        if strict:
            if len(raw.rstrip(" ")) > KVN_MAX_LINE:
                raise TdmError(f"line {no}: longer than {KVN_MAX_LINE} characters (4.2)")
            if any(not 0x20 <= ord(c) <= 0x7E for c in raw):
                raise TdmError(f"line {no}: only printable ASCII and blanks are allowed (4.2)")
        elif any((ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F for c in raw):
            raise TdmError(f"line {no}: control character")
        line = raw.replace("\t", " ").strip()
        if not line:
            continue
        word = line.split(" ", 1)[0]
        if word == "COMMENT":
            body = line[len("COMMENT"):].lstrip(" ")
            if state == "HEADER" and not set(header) - {"CCSDS_TDM_VERS"}:
                head_comments.append(body)
            elif state == "META" and seg is not None and not seg.metadata:
                seg.comments.append(body)
            elif state == "DATA" and seg is not None and not seg.observations:
                seg.data_comments.append(body)
            else:
                raise TdmError(f"line {no}: COMMENT only at the start of the header, a metadata or a data "
                               "section (4.5)")
            continue
        if line in ("META_START", "META_STOP", "DATA_START", "DATA_STOP"):
            if line == "META_START":
                if state not in ("HEADER", "BETWEEN"):
                    raise TdmError(f"line {no}: unexpected META_START")
                if state == "HEADER":
                    missing = [k for k in _HEADER_MANDATORY if k not in header]
                    if missing:
                        raise TdmError(f"line {no}: header lacks {missing} (Table 3-2)")
                seg = TdmSegment()
                segments.append(seg)
                state = "META"
            elif line == "META_STOP":
                if state != "META" or seg is None:
                    raise TdmError(f"line {no}: unexpected META_STOP")
                _validate_metadata(seg, strict, no)
                state = "META_DONE"
            elif line == "DATA_START":
                if state != "META_DONE":
                    raise TdmError(f"line {no}: DATA_START must follow META_STOP")
                state = "DATA"
            else:
                if state != "DATA" or seg is None:
                    raise TdmError(f"line {no}: unexpected DATA_STOP")
                _validate_data(seg, strict)
                state = "BETWEEN"
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not _KEYWORD_RE.match(key):
            raise TdmError(f"line {no}: expected 'KEYWORD = value', got {line[:60]!r}")
        if not value:
            raise TdmError(f"line {no}: {key} has an empty value (4.3)")
        if state == "START":
            if key != "CCSDS_TDM_VERS":
                raise TdmError(f"line {no}: the first line must be CCSDS_TDM_VERS (3.2)")
            if not _VERSION_RE.match(value):
                raise TdmError(f"line {no}: CCSDS_TDM_VERS = {value!r} is not 'x.y' (3.2)")
            header[key] = value
            state = "HEADER"
        elif state == "HEADER":
            if key not in HEADER_KEYS or key in header:
                raise TdmError(f"line {no}: {key} is not allowed here in the header (Table 3-2)")
            if HEADER_KEYS.index(key) < max(HEADER_KEYS.index(k) for k in header):
                raise TdmError(f"line {no}: {key} out of the Table 3-2 order")
            if key == "CREATION_DATE":
                try:
                    parse_epoch(value)
                except ValueError as e:
                    raise TdmError(f"line {no}: CREATION_DATE: {e}") from None
            header[key] = value
        elif state == "META" and seg is not None:
            if key not in META_ORDER:
                raise TdmError(f"line {no}: {key} is not a TDM metadata keyword (Table 3-3)")
            if key in seg.metadata:
                raise TdmError(f"line {no}: {key} repeated in one metadata section")
            if strict and seg.metadata and META_ORDER[key] < max(META_ORDER[k] for k in seg.metadata):
                raise TdmError(f"line {no}: {key} out of the Table 3-3 order (3.3)")
            seg.metadata[key] = value
        elif state == "DATA" and seg is not None:
            if key not in DATA_KEYWORDS:
                raise TdmError(f"line {no}: {key} is not a TDM data keyword (Table 3-5)")
            seg.observations.append(_observation(key, value, no, strict))
        else:
            raise TdmError(f"line {no}: {key} outside any section")
    if state != "BETWEEN":
        raise TdmError("TDM incomplete: it must end with DATA_STOP after at least one segment")
    return Tdm(version=header["CCSDS_TDM_VERS"], comments=head_comments, creation_date=header["CREATION_DATE"],
               originator=header["ORIGINATOR"], message_id=header.get("MESSAGE_ID"), segments=segments)


# ------------------------------------------------------------------ producer
@dataclass(frozen=True)
class TdmRecord:
    """One received frame/packet as seen by the station (plain values, no GDS objects).

    t_utc              UNIX seconds (UTC) at the end of reception at the station receiver
                       (GDS ERT minus [radio] radio_latency_s)
    rx_power_dbm       calibrated received power, dBm ([radio] rssi_cal_offset_db applied,
                       as gds.py does before rxstats); None = not available
    snr_db             per-packet SNR in noise_bw_hz, dB (SX126x SnrPkt/4); None for the E22
    freq_offset_hz     received carrier minus the frequency the receiver was tuned to, Hz,
                       positive = above (SX126x frequency error with the AFC sign applied,
                       AfcLoop.residual_hz()); None for the E22
    afc_correction_hz  station AFC correction in force for this packet, Hz (0 without AFC)
    nominal_freq_hz    nominal channel frequency of the link, Hz
    noise_bw_hz        bandwidth the SNR refers to, Hz (LoRa: the profile bandwidth)
    integration_s      averaging window of the frequency estimate, s, or None (not stated)
    """
    t_utc: float
    rx_power_dbm: float | None
    snr_db: float | None
    freq_offset_hz: float | None
    afc_correction_hz: float
    nominal_freq_hz: float
    noise_bw_hz: float
    integration_s: float | None = None

    def __post_init__(self) -> None:
        _finite(self.t_utc, "t_utc", 946684800.0, 4102444800.0)           # 2000-01-01 .. 2100-01-01
        _finite_or_none(self.rx_power_dbm, "rx_power_dbm", -200.0, 60.0)
        _finite_or_none(self.snr_db, "snr_db", -60.0, 80.0)
        _finite_or_none(self.freq_offset_hz, "freq_offset_hz", -1e6, 1e6)
        _finite(self.afc_correction_hz, "afc_correction_hz", -1e6, 1e6)
        _finite(self.nominal_freq_hz, "nominal_freq_hz", 1e6, 1e12)
        _finite(self.noise_bw_hz, "noise_bw_hz", 1.0, 1e9)
        _finite_or_none(self.integration_s, "integration_s", 1e-6, 86400.0)


def _finite(v: object, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what} must be a finite number, got {v!r}")
    if not lo <= v <= hi:
        raise ValueError(f"{what} = {v!r} outside {lo:g}..{hi:g}")
    return float(v)


def _finite_or_none(v: object, what: str, lo: float, hi: float) -> float | None:
    return None if v is None else _finite(v, what, lo, hi)


def format_epoch(t_utc: float) -> str:
    """UNIX seconds -> 'YYYY-MM-DDThh:mm:ss.fff' (UTC, millisecond resolution, 4.3.9).
    Rounded through integer milliseconds so 59.9996 s never prints as '60.000'."""
    ms = round(_finite(t_utc, "t_utc", 0.0, 253402300799.0) * 1000.0)
    dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms % 1000:03d}"


def format_value(v: float, decimals: int) -> str:
    """Finite float -> KVN fixed-point text with at most 16 digits; falls back to
    floating-point notation d.ddddE+xx; never '-0' (all 4.3)."""
    x = _finite(v, "value", -1e300, 1e300)
    s = f"{x:.{decimals}f}"
    if len(s.lstrip("+-").replace(".", "")) > KVN_MAX_DIGITS:
        s = f"{x:.{KVN_MAX_DIGITS - 1}E}"
    if float(s) == 0.0:
        s = s.lstrip("-")
    return s


def _kvn_text(s: str, what: str) -> str:
    """A text value: printable ASCII, no '=' ambiguity, collapsed blanks."""
    t = " ".join(str(s).split())
    if not t or any(not 0x20 <= ord(c) <= 0x7E for c in t):
        raise ValueError(f"{what} must be non-empty printable ASCII, got {s!r}")
    return t


def doppler_range_rate_km_s(total_offset_hz: float, freq_offset_cal_hz: float, nominal_freq_hz: float) -> float:
    """One-way range rate (km/s, positive receding) from the total received offset:
    Doppler = total offset - static crystal offset; range rate = -c x Doppler / f
    (the same relation as link.doppler(), in km/s for DOPPLER_INSTANTANEOUS, 3.5.2.2)."""
    return -C_M_S * (total_offset_hz - freq_offset_cal_hz) / nominal_freq_hz / 1000.0


_POWER_ONLY_NOTE = ("No SNR or frequency-error measurement in this segment (the E22 UART modules report "
                    "RSSI only), so it carries CARRIER_POWER only")


def build_tdm(records: Iterable[TdmRecord], *, station: str, spacecraft: str = "VGQ-1",
              originator: str | None = None, creation_utc: float | None = None,
              freq_offset_cal_hz: float | None = None, radio_model: str | None = None,
              message_id: str | None = None, band: str = "UHF", integration_ref: str = "END",
              data_quality: str = "RAW") -> str:
    """One-way downlink TDM (KVN text) from station records, re-validated with the strict
    reader before it is returned. Records are sorted by time; a record whose millisecond
    timetag repeats the previous one is dropped (3.4); records with nothing to report are
    skipped. A new segment starts whenever the nominal frequency or the integration interval
    changes (3.3). DOPPLER_INSTANTANEOUS needs freq_offset_cal_hz (None = the static
    crystal offset is not calibrated -> no range rate). Raises ValueError when no record is
    usable or an argument is invalid."""
    station_t = _kvn_text(station, "station")
    craft_t = _kvn_text(spacecraft, "spacecraft")
    origin_t = _kvn_text(originator if originator is not None else station, "originator")
    if band.upper() != "UHF":
        raise ValueError("only band 'UHF' is defined in this mission's ICD")
    if integration_ref.upper() not in _META_ENUMS["INTEGRATION_REF"]:
        raise ValueError(f"integration_ref must be one of {sorted(_META_ENUMS['INTEGRATION_REF'])}")
    if data_quality.upper() not in _META_ENUMS["DATA_QUALITY"]:
        raise ValueError(f"data_quality must be one of {sorted(_META_ENUMS['DATA_QUALITY'])}")
    cal = _finite_or_none(freq_offset_cal_hz, "freq_offset_cal_hz", -1e6, 1e6)
    recs = list(records)
    if not all(isinstance(r, TdmRecord) for r in recs):
        raise ValueError("records must be TdmRecord instances")
    recs.sort(key=lambda r: r.t_utc)
    for r in recs:
        if not UHF_RANGE_HZ[0] <= r.nominal_freq_hz <= UHF_RANGE_HZ[1]:
            raise ValueError(f"nominal frequency {r.nominal_freq_hz:g} Hz is not UHF (300 MHz..3 GHz)")

    rows: list[tuple[str, TdmRecord]] = []
    for r in recs:
        if r.rx_power_dbm is None and r.snr_db is None and r.freq_offset_hz is None:
            continue
        ep = format_epoch(r.t_utc)
        if rows and rows[-1][0] == ep:
            continue
        rows.append((ep, r))
    if not rows:
        raise ValueError("no record with a measurement to report")

    groups: list[list[tuple[str, TdmRecord]]] = []
    for ep, r in rows:
        key = (r.nominal_freq_hz, r.integration_s)
        if not groups or (groups[-1][0][1].nominal_freq_hz, groups[-1][0][1].integration_s) != key:
            groups.append([])
        groups[-1].append((ep, r))

    created = format_epoch(creation_utc if creation_utc is not None else datetime.now(UTC).timestamp())
    out = [f"CCSDS_TDM_VERS = {TDM_VERSION}",
           "COMMENT One-way downlink tracking data from the DSS-Q ground data system",
           "COMMENT UHF = 300 MHz to 3 GHz (ITU-R V.431 band 9); timetags = UTC at the end of reception at the "
           "station receiver",
           f"CREATION_DATE = {created}", f"ORIGINATOR = {origin_t}"]
    if message_id is not None:
        out.append(f"MESSAGE_ID = {_kvn_text(message_id, 'message_id')}")
    for g in groups:
        out += _segment_lines(g, station_t, craft_t, cal, radio_model, band.upper(), integration_ref.upper(),
                              data_quality.upper())
    text = "\n".join(out) + "\n"
    parse_tdm(text, strict=True)                   # never hand out a non-conforming TDM
    return text


def _segment_lines(group: list[tuple[str, TdmRecord]], station: str, craft: str, cal: float | None,
                   radio_model: str | None, band: str, integration_ref: str, quality: str) -> list[str]:
    first = group[0][1]
    has_freq = any(r.freq_offset_hz is not None for _, r in group)
    has_snr = any(r.snr_db is not None for _, r in group)
    has_pwr = any(r.rx_power_dbm is not None for _, r in group)
    has_dop = has_freq and cal is not None
    types = (["TRANSMIT_FREQ_2", "RECEIVE_FREQ_1"] if has_freq else []) + \
        (["DOPPLER_INSTANTANEOUS"] if has_dop else []) + (["CARRIER_POWER"] if has_pwr else []) + \
        (["PC_N0"] if has_snr else [])
    comments = []
    if radio_model:
        comments.append(f"Station receiver: {_kvn_text(radio_model, 'radio_model')}")
    if not has_freq and not has_snr:
        comments.append(_POWER_ONLY_NOTE)
    if has_pwr:
        comments.append("CARRIER_POWER: calibrated receiver RSSI at the receiver input (rssi_cal_offset_db "
                        "applied), not corrected for antenna gain or line loss")
    if has_snr:
        comments.append(f"PC_N0 = SNR + 10 log10(noise bandwidth {first.noise_bw_hz:.0f} Hz)")
    if has_freq:
        comments.append("RECEIVE_FREQ_1 + FREQ_OFFSET = nominal + AFC correction + measured residual, "
                        "against the station frequency reference")
        comments.append("DOPPLER_INSTANTANEOUS = -c (total offset - static crystal offset "
                        f"{cal:.1f} Hz) / f" if has_dop else
                        "No DOPPLER_INSTANTANEOUS: the static TX-RX crystal offset is not calibrated")
    md = [*(f"COMMENT {c}" for c in comments),
          f"DATA_TYPES = {','.join(types)}", "TIME_SYSTEM = UTC",
          f"START_TIME = {group[0][0]}", f"STOP_TIME = {group[-1][0]}",
          f"PARTICIPANT_1 = {station}", f"PARTICIPANT_2 = {craft}", "MODE = SEQUENTIAL", "PATH = 2,1",
          f"TRANSMIT_BAND = {band}", f"RECEIVE_BAND = {band}"]
    if has_freq and first.integration_s is not None:
        md += [f"INTEGRATION_INTERVAL = {format_value(first.integration_s, 6)}",
               f"INTEGRATION_REF = {integration_ref}"]
    if has_freq:
        md.append(f"FREQ_OFFSET = {format_value(first.nominal_freq_hz, 1)}")
    md.append(f"DATA_QUALITY = {quality}")
    data: list[str] = []
    beacon_done = False                # TRANSMIT_FREQ_2 once: constant within the segment
    for ep, r in group:
        if r.freq_offset_hz is not None:
            if not beacon_done:
                data.append(f"TRANSMIT_FREQ_2 = {ep} {format_value(r.nominal_freq_hz, 1)}")
                beacon_done = True
            total = r.afc_correction_hz + r.freq_offset_hz
            data.append(f"RECEIVE_FREQ_1 = {ep} {format_value(total, 1)}")
            if cal is not None:
                rr = doppler_range_rate_km_s(total, cal, r.nominal_freq_hz)
                data.append(f"DOPPLER_INSTANTANEOUS = {ep} {format_value(rr, 6)}")
        if r.rx_power_dbm is not None:
            data.append(f"CARRIER_POWER = {ep} {format_value(r.rx_power_dbm - 30.0, 2)}")
        if r.snr_db is not None:
            data.append(f"PC_N0 = {ep} {format_value(r.snr_db + 10.0 * math.log10(r.noise_bw_hz), 2)}")
    return ["META_START", *md, "META_STOP", "DATA_START", *data, "DATA_STOP"]


def tdm_filename(spacecraft: str, station: str, first_utc: float) -> str:
    """'VGQ1_<station>_<UTC>.kvn' (contract 24.7): spacecraft and station names reduced to
    letters, digits and '-', UTC of the first record as YYYYMMDDThhmmssZ."""
    sc = re.sub(r"[^A-Za-z0-9]", "", spacecraft) or "SC"
    st = re.sub(r"[^A-Za-z0-9-]", "", station) or "STATION"
    stamp = datetime.fromtimestamp(_finite(first_utc, "first_utc", 0.0, 4102444800.0), UTC)
    return f"{sc}_{st}_{stamp.strftime('%Y%m%dT%H%M%SZ')}.kvn"


def write_text_atomic(path: Path, text: str) -> None:
    """Writes text through a temporary file in the same directory and renames it, so a
    reader never sees a partial TDM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class TdmRecorder:
    """Bounded per-pass collector. GDS use: clear() at AOS, add() per received frame, write()
    at LOS (then clear()) and on demand from the web UI (same file name, rewritten)."""
    MAX_RECORDS = 100_000

    def __init__(self, station: str, spacecraft: str = "VGQ-1", max_records: int = MAX_RECORDS):
        self.station = _kvn_text(station, "station")
        self.spacecraft = _kvn_text(spacecraft, "spacecraft")
        if isinstance(max_records, bool) or not isinstance(max_records, int) or not 1 <= max_records <= 10_000_000:
            raise ValueError("max_records must be an integer 1..10000000")
        self.max_records = max_records
        self.records: list[TdmRecord] = []
        self.dropped = 0

    def __len__(self) -> int:
        return len(self.records)

    def add(self, rec: TdmRecord) -> bool:
        """False (and counted) when the pass buffer is full or rec is not a TdmRecord."""
        if not isinstance(rec, TdmRecord) or len(self.records) >= self.max_records:
            self.dropped += 1
            return False
        self.records.append(rec)
        return True

    def clear(self) -> None:
        self.records.clear()
        self.dropped = 0

    def write(self, archive_dir: str | Path, *, freq_offset_cal_hz: float | None = None,
              radio_model: str | None = None, creation_utc: float | None = None) -> Path | None:
        """Writes <archive_dir>/tdm/<tdm_filename> and returns its path; None when the pass
        holds no reportable record."""
        if not any(r.rx_power_dbm is not None or r.snr_db is not None or r.freq_offset_hz is not None
                   for r in self.records):
            return None
        first = min(r.t_utc for r in self.records)
        name = tdm_filename(self.spacecraft, self.station, first)
        text = build_tdm(self.records, station=self.station, spacecraft=self.spacecraft,
                         freq_offset_cal_hz=freq_offset_cal_hz, radio_model=radio_model,
                         creation_utc=creation_utc, message_id=name[:-4])
        path = Path(archive_dir) / "tdm" / name
        write_text_atomic(path, text)
        return path


def records_from_csv(text: str) -> list[TdmRecord]:
    """CSV (header row; empty cell = None) -> records; see the module docstring."""
    rdr = csv.DictReader(io.StringIO(text))
    need = ("t_utc", "rx_power_dbm", "snr_db", "freq_offset_hz", "afc_correction_hz", "nominal_freq_hz",
            "noise_bw_hz")
    missing = [c for c in need if c not in (rdr.fieldnames or [])]
    if missing:
        raise ValueError(f"CSV lacks column(s) {missing}")

    def opt(row: Mapping[str, str | None], k: str) -> float | None:
        v = (row.get(k) or "").strip()
        return None if v == "" else float(v)

    out = []
    for i, row in enumerate(rdr, start=2):
        try:
            t_txt = (row["t_utc"] or "").strip()
            if re.match(r"\d{4}-", t_txt):
                dt = datetime.fromisoformat(t_txt.replace("Z", "+00:00"))
                t = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()
            else:
                t = float(t_txt)
            out.append(TdmRecord(t, opt(row, "rx_power_dbm"), opt(row, "snr_db"), opt(row, "freq_offset_hz"),
                                 opt(row, "afc_correction_hz") or 0.0, float(row["nominal_freq_hz"] or ""),
                                 float(row["noise_bw_hz"] or ""), opt(row, "integration_s")))
        except (ValueError, TypeError) as e:
            raise ValueError(f"CSV line {i}: {e}") from None
    return out


# =============================================================================
# 2. XTCE 1.2 (CCSDS 660.0-B-2)
# =============================================================================
XTCE_NS = "http://www.omg.org/spec/XTCE/20180204"
SPACE_SYSTEM = "VGQ-1"
TM_BASE = "CCSDS_TM_Packet"
TC_BASE = "VGQ1_TC"
_NAME_RE = re.compile(r"[^./:\[\] ]+$")          # SpaceSystem.xsd NameType
APID_TC = dd.APID_TC
EVR_TEXT_MAX = dd.EVR_TEXT_MAX                  # flight pack_evr(): text <= 80 characters
RFSCAN_MAX_CHANNELS = dd.RFSCAN_MAX_CHANNELS    # flight pack_rfscan(): up to 84 channels
# Struct code -> (bits, signed, is_float); big-endian everywhere (dictionary.py).
_CODES: dict[str, tuple[int, bool, bool]] = {
    "B": (8, False, False), "b": (8, True, False), "H": (16, False, False), "h": (16, True, False),
    "I": (32, False, False), "i": (32, True, False), "Q": (64, False, False), "q": (64, True, False),
    "f": (32, True, True), "d": (64, True, True)}
# Enumerated telemetry fields (packet name, field) -> labels, from dictionary.py's tables. A
# field that also has GDS limits stays an integer type (alarm ranges) and lists the labels in
# its LongDescription instead.
_TM_ENUMS: dict[tuple[str, str], Mapping[int, str]] = {
    ("HK", "fsw_mode"): dd.MODES, ("HK", "reset_cause"): dd.RESET_CAUSES, ("HK", "farm_state"): dd.FARM_STATES,
    ("HK", "cmd_last_status"): dd.CMD_ERRORS, ("HK", "radio_fault"): dd.RADIO_FAULTS,
    ("HK", "sdls_tc_service"): dd.SDLS_SERVICES, ("HK", "sdls_tm_service"): dd.SDLS_SERVICES,
    ("HK", "sdls_tm_state"): dd.SDLS_TM_STATES, ("HK", "cfdp_state"): dd.CFDP_STATES,
    ("HK", "seq_state"): dd.SEQ_STATES, ("HK", "beacon_level"): dd.BEACON_LEVELS,
    ("RF", "radio_model"): dd.RADIO_MODELS, ("RF", "framing"): dd.FRAMINGS,
    ("CMDVER", "stage"): dd.CMD_STAGES, ("CMDVER", "cmd_source"): dd.CMD_SOURCES,
    ("CMDVER", "error_code"): dd.CMD_ERRORS,
    ("SPEC", "spec_mode"): spectral.MODE_NAMES, ("SPEC", "spec_class"): spectral.CLASS_NAMES}


def _q(tag: str) -> str:
    return f"{{{XTCE_NS}}}{tag}"


def _el(parent: ET.Element, tag: str, text: str | None = None, **attrs: str | None) -> ET.Element:
    e = ET.SubElement(parent, _q(tag), {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        e.text = text
    return e


def _name(s: str) -> str:
    if not _NAME_RE.match(s):
        raise ValueError(f"{s!r} is not a valid XTCE name (no '.', '/', ':', '[', ']' or blanks)")
    return s


def _describe(e: ET.Element, desc: str, notes: Sequence[str] = ()) -> None:
    """shortDescription when <= 80 characters (schema recommendation), LongDescription for
    the rest and for notes. LongDescription must be the first child (DescriptionType)."""
    long_parts = list(notes)
    if desc and len(desc) <= 80:
        e.set("shortDescription", desc)
    elif desc:
        long_parts.insert(0, desc)
    if long_parts:
        _el(e, "LongDescription", " ".join(long_parts))


def _unitset(e: ET.Element, unit: str) -> None:
    if unit:
        _el(_el(e, "UnitSet"), "Unit", unit)


def _int_encoding(e: ET.Element, bits: int, signed: bool, scale: float | None = None) -> ET.Element:
    enc = _el(e, "IntegerDataEncoding", sizeInBits=str(bits), encoding="twosComplement" if signed else "unsigned",
              byteOrder="mostSignificantByteFirst")
    if scale is not None:
        _polynomial(enc, scale)
    return enc


def _polynomial(enc: ET.Element, scale: float) -> None:
    pc = _el(_el(enc, "DefaultCalibrator"), "PolynomialCalibrator")
    _el(pc, "Term", coefficient=repr(float(scale)), exponent="1")


def _alarm(e: ET.Element, limits: Sequence[float | None] | None) -> None:
    """GDS limits (red_low, yellow_low, yellow_high, red_high): a value equal to a limit is
    out of limits (dictionary.checked_limits), so the in-limits ranges are exclusive."""
    if not limits:
        return
    rl, yl, yh, rh = limits
    if all(v is None for v in limits):
        return
    ranges = _el(_el(e, "DefaultAlarm"), "StaticAlarmRanges")
    for tag, lo, hi in (("WarningRange", yl, yh), ("CriticalRange", rl, rh)):
        if lo is None and hi is None:
            continue
        _el(ranges, tag, minExclusive=None if lo is None else repr(float(lo)),
            maxExclusive=None if hi is None else repr(float(hi)))


def _na_note(apid: int, fld: Sequence[object]) -> list[str]:
    """The not-available sentinel of one field (dictionary.field_na: saturating counters,
    bit masks and time fields have none, every raw value is a reading)."""
    code = str(fld[1])
    na = dd.field_na(apid, tuple(fld))
    if na is None:
        return [] if code not in dd.NA else ["Every raw value is a reading (no not-available sentinel)."]
    bits, signed, _ = _CODES[code]
    raw = na & ((1 << bits) - 1) if signed else na
    return [f"Raw value {na} (0x{raw:0{bits // 4}X}) = not available."]


def _tm_param_type(pts: ET.Element, packet: str, fld: Sequence[object], apid: int = -1) -> str:
    name, code, scale, unit, desc, limits = (fld[0], fld[1], fld[2], fld[3], fld[4], fld[5])
    transform = fld[6] if len(fld) > 6 else None
    if not isinstance(name, str) or not isinstance(code, str) or code not in _CODES:
        raise ValueError(f"{packet}: unsupported field {fld!r}")
    bits, signed, is_float = _CODES[code]
    tname = _name(f"{name}_Type")
    notes = _na_note(apid, fld)
    enum = _TM_ENUMS.get((packet, name))
    if enum is not None and not (not is_float and scale is None and limits is None):
        notes.append("Values: " + ", ".join(f"{k} = {v}" for k, v in sorted(enum.items())) + ".")
    if callable(transform):
        notes.append(f"Raw code; engineering value = {getattr(transform, '__name__', 'custom decoder')}(raw) "
                     "in the DSS-Q GDS (not expressible as a polynomial).")
        t = _el(pts, "IntegerParameterType", name=tname, signed=str(signed).lower(), sizeInBits=str(bits))
        _describe(t, str(desc), notes)
        _unitset(t, "")
        _int_encoding(t, bits, signed)
    elif enum is not None and not is_float and scale is None and limits is None:
        t = _el(pts, "EnumeratedParameterType", name=tname)
        _describe(t, str(desc), notes)
        _int_encoding(t, bits, signed)
        el = _el(t, "EnumerationList")
        for value, label in sorted(enum.items()):
            _el(el, "Enumeration", value=str(value), label=str(label))
    elif is_float:
        t = _el(pts, "FloatParameterType", name=tname, sizeInBits=str(bits))
        _describe(t, str(desc), notes)
        _unitset(t, str(unit))
        enc = _el(t, "FloatDataEncoding", sizeInBits=str(bits), encoding="IEEE754_1985",
                  byteOrder="mostSignificantByteFirst")
        if scale not in (None, 1, 1.0):
            _polynomial(enc, float(scale))                                  # type: ignore[arg-type]
        _alarm(t, limits)                                                   # type: ignore[arg-type]
    elif scale in (None, 1, 1.0):
        t = _el(pts, "IntegerParameterType", name=tname, signed=str(signed).lower(), sizeInBits=str(bits))
        _describe(t, str(desc), notes)
        _unitset(t, str(unit))
        _int_encoding(t, bits, signed)
        _alarm(t, limits)                                                   # type: ignore[arg-type]
    else:
        t = _el(pts, "FloatParameterType", name=tname, sizeInBits="64")
        _describe(t, str(desc), notes)
        _unitset(t, str(unit))
        _int_encoding(t, bits, signed, float(scale))                        # type: ignore[arg-type]
        _alarm(t, limits)                                                   # type: ignore[arg-type]
    return tname


def _tm_bitfield(pts: ET.Element, ps: ET.Element, packet: str, apid: int, fld: Sequence[object],
                 subs: Sequence[tuple[str, int, int]]) -> list[str]:
    """A raw field that packs several values (dictionary.BITFIELDS, e.g. the CMDVER stage
    octet, I1): one parameter per sub-field, most significant first as on the wire, and a
    reserved parameter for every gap, exactly as dssq.decom splits it. Returns the names."""
    code = str(fld[1])
    bits, signed, is_float = _CODES[code]
    if signed or is_float or fld[2] is not None or fld[5] is not None:
        raise ValueError(f"{packet}.{fld[0]}: only an unscaled unsigned integer can carry bit fields")
    out: list[str] = []
    top = bits
    for name, lsb, width in [*sorted(subs, key=lambda x: -x[1]), ("", 0, 0)]:
        if lsb + width < top:                                     # reserved bits above this sub-field
            rname = _name(f"{fld[0]}_reserved_b{top - 1}_{lsb + width}")
            t = _el(pts, "IntegerParameterType", name=f"{rname}_Type", signed="false", sizeInBits="8")
            t.set("shortDescription", f"{packet} {fld[0]} octet bits {top - 1}..{lsb + width}: reserved")
            _int_encoding(t, top - lsb - width, False)
            _el(ps, "Parameter", name=rname, parameterTypeRef=f"{rname}_Type")
            out.append(rname)
        if not width:
            break
        sname = _name(name)
        enum = _TM_ENUMS.get((packet, name))
        desc = f"{packet} {fld[0]} octet bits {lsb + width - 1}..{lsb}: {name}"
        if enum is not None:
            t = _el(pts, "EnumeratedParameterType", name=f"{sname}_Type")
            _describe(t, desc)
            _int_encoding(t, width, False)
            el = _el(t, "EnumerationList")
            for value, label in sorted(enum.items()):
                _el(el, "Enumeration", value=str(value), label=str(label))
        else:
            t = _el(pts, "IntegerParameterType", name=f"{sname}_Type", signed="false", sizeInBits="8")
            _describe(t, desc)
            _int_encoding(t, width, False)
        _el(ps, "Parameter", name=sname, parameterTypeRef=f"{sname}_Type")
        out.append(sname)
        top = lsb
    return out


def _tm_header(root: ET.Element) -> None:
    """Root TelemetryMetaData: CCSDS 133.0-B-2 primary header + CUC secondary header
    (CCSDS 301.0-B-4, P-field 0x2E implicit: 4 coarse + 2 fine octets) as abstract container."""
    tm = _el(root, "TelemetryMetaData")
    pts, ps = _el(tm, "ParameterTypeSet"), _el(tm, "ParameterSet")
    hdr = (("ccsds_version", 3, "Packet version number (000)", ""),
           ("ccsds_type", 1, "Packet type (0 = telemetry)", ""),
           ("ccsds_sec_hdr_flag", 1, "Secondary header flag (1 = CUC time present)", ""),
           ("ccsds_apid", 11, "Application process identifier", ""),
           ("ccsds_seq_flags", 2, "Sequence flags (11 = unsegmented)", ""),
           ("ccsds_seq_count", 14, "Packet sequence count (per APID)", ""),
           ("ccsds_packet_length", 16, "Packet data length = octets in the data field - 1", "octet"),
           ("cuc_coarse", 32, "CUC coarse time: SCLK seconds since the MCU boot (partition in HK)", "s"))
    for name, bits, desc, unit in hdr:
        t = _el(pts, "IntegerParameterType", name=f"{name}_Type", signed="false", sizeInBits=str(max(8, bits)))
        t.set("shortDescription", desc)
        _unitset(t, unit)
        _int_encoding(t, bits, False)
    t = _el(pts, "FloatParameterType", name="cuc_fine_Type", sizeInBits="64")
    t.set("shortDescription", "CUC fine time: 1/65536 s units")
    _unitset(t, "s")
    _int_encoding(t, 16, False, 1.0 / 65536.0)
    for name, *_ in hdr:
        _el(ps, "Parameter", name=name, parameterTypeRef=f"{name}_Type")
    _el(ps, "Parameter", name="cuc_fine", parameterTypeRef="cuc_fine_Type")
    cs = _el(tm, "ContainerSet")
    c = _el(cs, "SequenceContainer", name=TM_BASE, abstract="true",
            shortDescription="CCSDS Space Packet with CUC secondary header (all VGQ-1 TM APIDs)")
    entries = _el(c, "EntryList")
    for name, *_ in hdr:
        _el(entries, "ParameterRefEntry", parameterRef=name)
    _el(entries, "ParameterRefEntry", parameterRef="cuc_fine")


def _tm_packet_system(root: ET.Element, apid: int, spec: Mapping[str, object]) -> None:
    pname = _name(str(spec["name"]))
    ss = _el(root, "SpaceSystem", name=pname)
    _describe(ss, f"{spec.get('desc', pname)} (APID 0x{apid:03X})")
    tm = _el(ss, "TelemetryMetaData")
    pts, ps = _el(tm, "ParameterTypeSet"), _el(tm, "ParameterSet")
    fields = spec["fields"]
    if not isinstance(fields, list) or not fields:
        raise ValueError(f"{pname}: no fields")
    names = []
    for fld in fields:
        if not isinstance(fld, tuple) or len(fld) < 6:
            raise ValueError(f"{pname}: field tuple {fld!r} must have >= 6 elements")
        subs = dd.BITFIELDS.get(apid, {}).get(str(fld[0]))
        if subs:
            names += _tm_bitfield(pts, ps, pname, apid, fld, subs)
            continue
        tname = _tm_param_type(pts, pname, fld, apid)
        names.append(_name(str(fld[0])))
        _el(ps, "Parameter", name=names[-1], parameterTypeRef=tname)
    if len(set(names)) != len(names):
        raise ValueError(f"{pname}: duplicate field names")
    _packet_container(tm, pname, apid, [("ParameterRefEntry", n) for n in names])


def _packet_container(tm: ET.Element, pname: str, apid: int, entries: list[tuple[str, str]]) -> None:
    c = _el(_el(tm, "ContainerSet"), "SequenceContainer", name=pname)
    el = _el(c, "EntryList")
    for tag, ref in entries:
        _el(el, tag, parameterRef=ref)
    base = _el(c, "BaseContainer", containerRef=f"/{SPACE_SYSTEM}/{TM_BASE}")
    _el(_el(base, "RestrictionCriteria"), "Comparison", parameterRef=f"/{SPACE_SYSTEM}/ccsds_apid",
        value=str(apid))


def _evr_system(root: ET.Element) -> None:
    """EVR (APID 0x012, variable length): severity u8 | event id u16 | ASCII text to the end of
    the packet (<= 80 characters, flight pack_evr). Text size in bits = 8 x (packet data length
    + 1 - 6 (CUC) - 3) = 8 L - 72 + 8 = 8 L - 64."""
    ss = _el(root, "SpaceSystem", name="EVR")
    _describe(ss, f"Event report (APID 0x{dd.APID_EVR:03X}, variable length)")
    tm = _el(ss, "TelemetryMetaData")
    pts, ps = _el(tm, "ParameterTypeSet"), _el(tm, "ParameterSet")
    t = _el(pts, "EnumeratedParameterType", name="severity_Type", shortDescription="EVR severity")
    _int_encoding(t, 8, False)
    el = _el(t, "EnumerationList")
    for value, label in sorted(dd.EVR_SEVERITY.items()):
        _el(el, "Enumeration", value=str(value), label=label)
    t = _el(pts, "IntegerParameterType", name="event_id_Type", signed="false", sizeInBits="16",
            shortDescription="Event identifier")
    _int_encoding(t, 16, False)
    t = _el(pts, "StringParameterType", name="text_Type", shortDescription="Event text (ASCII)")
    enc = _el(t, "StringDataEncoding", encoding="US-ASCII")
    var = _el(enc, "Variable", maxSizeInBits=str(8 * EVR_TEXT_MAX))
    dv = _el(var, "DynamicValue")
    _el(dv, "ParameterInstanceRef", parameterRef=f"/{SPACE_SYSTEM}/ccsds_packet_length")
    _el(dv, "LinearAdjustment", slope="8", intercept="-64")
    _el(t, "SizeRangeInCharacters", minInclusive="0", maxInclusive=str(EVR_TEXT_MAX))
    for n in ("severity", "event_id", "text"):
        _el(ps, "Parameter", name=n, parameterTypeRef=f"{n}_Type")
    _packet_container(tm, "EVR", dd.APID_EVR, [("ParameterRefEntry", n) for n in ("severity", "event_id", "text")])


def _rfscan_system(root: ET.Element) -> None:
    """RFSCAN (APID 0x015): first_ch u8 | count u8 | duration_ms u16 | noise i8[count]
    (dBm, -128 = no reading; <= 84 channels, flight pack_rfscan)."""
    ss = _el(root, "SpaceSystem", name="RFSCAN")
    _describe(ss, f"Spacecraft RF survey (APID 0x{dd.APID_RFSCAN:03X}, variable length)")
    tm = _el(ss, "TelemetryMetaData")
    pts, ps = _el(tm, "ParameterTypeSet"), _el(tm, "ParameterSet")
    for name, bits, unit, desc in (("first_ch", 8, "", "First E22 channel scanned"),
                                   ("count", 8, "", "Number of channels scanned"),
                                   ("duration_ms", 16, "ms", "Scan duration")):
        t = _el(pts, "IntegerParameterType", name=f"{name}_Type", signed="false", sizeInBits=str(bits),
                shortDescription=desc)
        _unitset(t, unit)
        _int_encoding(t, bits, False)
    t = _el(pts, "IntegerParameterType", name="noise_dbm_Type", signed="true", sizeInBits="8",
            shortDescription="Ambient noise of one channel")
    _el(t, "LongDescription", "Raw value -128 (0x80) = no reading.")
    _unitset(t, "dBm")
    _int_encoding(t, 8, True)
    arr = _el(pts, "ArrayParameterType", name="noise_Type", arrayTypeRef="noise_dbm_Type",
              shortDescription=f"Noise per channel, first_ch upwards (<= {RFSCAN_MAX_CHANNELS})")
    dim = _el(_el(arr, "DimensionList"), "Dimension")
    _el(_el(dim, "StartingIndex"), "FixedValue", "0")
    dv = _el(_el(dim, "EndingIndex"), "DynamicValue")
    _el(dv, "ParameterInstanceRef", parameterRef="count")
    _el(dv, "LinearAdjustment", slope="1", intercept="-1")
    for n in ("first_ch", "count", "duration_ms", "noise"):
        _el(ps, "Parameter", name=n, parameterTypeRef=f"{n}_Type")
    _packet_container(tm, "RFSCAN", dd.APID_RFSCAN,
                      [("ParameterRefEntry", "first_ch"), ("ParameterRefEntry", "count"),
                       ("ParameterRefEntry", "duration_ms"), ("ArrayParameterRefEntry", "noise")])


def _int_type(pts: ET.Element, name: str, bits: int, signed: bool, unit: str, desc: str,
              scale: float | None = None) -> None:
    """One integer-encoded parameter type (raw integer, or a 64-bit float with a polynomial)."""
    if scale is None:
        t = _el(pts, "IntegerParameterType", name=f"{name}_Type", signed=str(signed).lower(),
                sizeInBits=str(bits), shortDescription=desc)
        _unitset(t, unit)
        _int_encoding(t, bits, signed)
    else:
        t = _el(pts, "FloatParameterType", name=f"{name}_Type", sizeInBits="64", shortDescription=desc)
        _unitset(t, unit)
        _int_encoding(t, bits, signed, scale)


def _enum_type(pts: ET.Element, name: str, desc: str, labels: Mapping[int, str]) -> None:
    t = _el(pts, "EnumeratedParameterType", name=f"{name}_Type", shortDescription=desc)
    _int_encoding(t, 8, False)
    el = _el(t, "EnumerationList")
    for value, label in sorted(labels.items()):
        _el(el, "Enumeration", value=str(value), label=label)


def _blob_type(pts: ET.Element, name: str, desc: str, notes: Sequence[str], intercept_bits: int) -> None:
    """Binary parameter running to the end of the packet: size in bits = 8 x ccsds_packet_length
    + intercept_bits (the packet data field holds ccsds_packet_length + 1 octets)."""
    t = _el(pts, "BinaryParameterType", name=f"{name}_Type")
    _describe(t, desc, notes)
    enc = _el(t, "BinaryDataEncoding")
    dv = _el(_el(enc, "SizeInBits"), "DynamicValue")
    _el(dv, "ParameterInstanceRef", parameterRef=f"/{SPACE_SYSTEM}/ccsds_packet_length")
    _el(dv, "LinearAdjustment", slope="8", intercept=str(intercept_bits))


# User-data offset of the first octet after the CUC secondary header, in bits relative to
# 8 x ccsds_packet_length: data field = L + 1 octets = 6 (CUC) + user data.
_USER_BITS_INTERCEPT = 8 * (1 - 6)


def _specburst_system(root: ET.Element) -> None:
    """SPECBURST (APID 0x024, PB-1.12 = contract 28): t0_ms u32 | rate_dhz u16 | gain_code u8
    | int_cycles u8 | n u8 | encoding u8 | dt_ms u16[n] | payload. The payload length depends
    on n and on the packet length, so the abstract container SPECBURST is specialised per
    sample count (SPECBURST_N1..N3, restriction on n) with a binary payload of exactly the
    remaining octets: 8 L - 40 - 80 - 16 n bits."""
    ss = _el(root, "SpaceSystem", name="SPECBURST")
    _describe(ss, f"AS7265X burst samples (APID 0x{dd.APID_SPECBURST:03X}, variable length)")
    tm = _el(ss, "TelemetryMetaData")
    pts, ps = _el(tm, "ParameterTypeSet"), _el(tm, "ParameterSet")
    _int_type(pts, "t0_ms", 32, False, "ms", "millis() of the first sample (SCLK ms since MCU boot, mod 2^32)")
    _int_type(pts, "rate_hz", 16, False, "Hz", "Achieved sample rate (sent in 0.1 Hz)", 0.1)
    _enum_type(pts, "gain_code", "AS7265X gain", {i: f"{g:g}x" for i, g in enumerate(spectral.GAIN_X)})
    _int_type(pts, "int_cycles", 8, False, "", "Burst integration N: (N + 1) x 2.78 ms")
    _int_type(pts, "n", 8, False, "", f"Samples in this packet, 1..{spectral.BURST_PER_PACKET_MAX}")
    _enum_type(pts, "encoding", "Payload encoding", {0: "RAW", 1: "CCSDS_121_0_B_3"})
    _int_type(pts, "dt_ms_item", 16, False, "ms", "DATA_RDY detection of one sample, ms after t0_ms")
    arr = _el(pts, "ArrayParameterType", name="dt_ms_Type", arrayTypeRef="dt_ms_item_Type",
              shortDescription="Sample time offsets, one per sample")
    dim = _el(_el(arr, "DimensionList"), "Dimension")
    _el(_el(dim, "StartingIndex"), "FixedValue", "0")
    dv = _el(_el(dim, "EndingIndex"), "DynamicValue")
    _el(dv, "ParameterInstanceRef", parameterRef="n")
    _el(dv, "LinearAdjustment", slope="1", intercept="-1")
    payload_note = (
        "Payload per encoding. RAW (0): n x 18 u16 counts big-endian, time-major (sample 0 channels "
        "410..940 nm, then sample 1 ...). CCSDS_121_0_B_3 (1): CCSDS 121.0-B-3 lossless (n = 16 bit, "
        "J = 16, r = 128, Basic, preprocessor with unit-delay predictor) of the channel-major sequence "
        "cm[ch x n + t] = sample t channel ch, zero-filled to an octet, strictly shorter than raw. "
        "Count 0xFFFF = channel not converted; 0xFFFE = count >= 65534 (saturated).")
    for k in range(1, spectral.BURST_PER_PACKET_MAX + 1):
        _blob_type(pts, f"payload_n{k}", f"Burst payload, {k} sample(s)", [payload_note],
                   _USER_BITS_INTERCEPT - 8 * spectral.BURST_HDR_LEN - 16 * k)
    for n in ("t0_ms", "rate_hz", "gain_code", "int_cycles", "n", "encoding", "dt_ms",
              *[f"payload_n{k}" for k in range(1, spectral.BURST_PER_PACKET_MAX + 1)]):
        _el(ps, "Parameter", name=n, parameterTypeRef=f"{n}_Type")
    cs = _el(tm, "ContainerSet")
    c = _el(cs, "SequenceContainer", name="SPECBURST", abstract="true",
            shortDescription="SPECBURST header and sample times (payload in SPECBURST_N1..N3)")
    el = _el(c, "EntryList")
    for n in ("t0_ms", "rate_hz", "gain_code", "int_cycles", "n", "encoding"):
        _el(el, "ParameterRefEntry", parameterRef=n)
    _el(el, "ArrayParameterRefEntry", parameterRef="dt_ms")
    base = _el(c, "BaseContainer", containerRef=f"/{SPACE_SYSTEM}/{TM_BASE}")
    _el(_el(base, "RestrictionCriteria"), "Comparison", parameterRef=f"/{SPACE_SYSTEM}/ccsds_apid",
        value=str(dd.APID_SPECBURST))
    for k in range(1, spectral.BURST_PER_PACKET_MAX + 1):
        ck = _el(cs, "SequenceContainer", name=f"SPECBURST_N{k}", shortDescription=f"SPECBURST with {k} sample(s)")
        _el(_el(ck, "EntryList"), "ParameterRefEntry", parameterRef=f"payload_n{k}")
        b = _el(ck, "BaseContainer", containerRef="SPECBURST")
        _el(_el(b, "RestrictionCriteria"), "Comparison", parameterRef="n", value=str(k))


def _cfdp_system(root: ET.Element) -> None:
    """CFDP (APID 0x030, VC3): the user data is exactly one CCSDS 727.0-B-5 class-1 PDU."""
    ss = _el(root, "SpaceSystem", name="CFDP")
    _describe(ss, f"CFDP file delivery (APID 0x{dd.APID_CFDP:03X}, VC3, variable length)")
    tm = _el(ss, "TelemetryMetaData")
    pts, ps = _el(tm, "ParameterTypeSet"), _el(tm, "ParameterSet")
    _blob_type(pts, "pdu", "One CFDP PDU (opaque to the MCS)", [
        f"Exactly one CCSDS 727.0-B-5 class-1 (unacknowledged) PDU of {dd.CFDP_PDU_MIN}..{dd.CFDP_PDU_MAX} "
        "octets: fixed header (mission profile: 10 octets), then Metadata (0x07), File Data or EOF "
        "(0x04, with the Entity-ID fault-location TLV when the condition code is not 0). Reassembled "
        "into files by the CFDP receiver of the DSS-Q GDS; not recorded in the SSR."], _USER_BITS_INTERCEPT)
    _el(ps, "Parameter", name="pdu", parameterTypeRef="pdu_Type")
    _packet_container(tm, "CFDP", dd.APID_CFDP, [("ParameterRefEntry", "pdu")])


def _arg_type(ats: ET.Element, tname: str, code: str, lo: int, hi: int, enum: Mapping[str, int] | None,
              desc: str) -> None:
    if code not in _CODES or _CODES[code][2]:
        raise ValueError(f"{tname}: unsupported argument code {code!r}")
    bits, signed, _ = _CODES[code]
    labels: dict[int, str] = {}
    aliases: list[str] = []
    for label, value in (enum or {}).items():
        if value in labels:
            aliases.append(f"{label} = {labels[value]}")
        else:
            labels[value] = label
    if enum and set(labels) == set(range(lo, hi + 1)):
        t = _el(ats, "EnumeratedArgumentType", name=tname)
        _describe(t, desc, [f"Aliases accepted by the DSS-Q command parser: {', '.join(aliases)}."] if aliases else [])
        _int_encoding(t, bits, signed)
        el = _el(t, "EnumerationList")
        for value in sorted(labels):
            _el(el, "Enumeration", value=str(value), label=labels[value])
        return
    t = _el(ats, "IntegerArgumentType", name=tname, signed=str(signed).lower(), sizeInBits=str(bits))
    notes = [f"Labels: {', '.join(f'{v} = {k}' for k, v in enum.items())}."] if enum else []
    _describe(t, desc, notes)
    _int_encoding(t, bits, signed)
    _el(_el(t, "ValidRangeSet"), "ValidRange", minInclusive=str(lo), maxInclusive=str(hi))


def _commands(root: ET.Element, commands: Mapping[str, dd.CommandDef]) -> None:
    cm = _el(root, "CommandMetaData")
    ats, mcs = _el(cm, "ArgumentTypeSet"), _el(cm, "MetaCommandSet")
    for name, bits, desc in (("opcode", 8, "Command opcode"),
                             ("ccsds_seq_count", 14, "Packet sequence count, assigned by the ground system"),
                             ("ccsds_packet_length", 16, "Packet data length = octets in the data field - 1")):
        t = _el(ats, "IntegerArgumentType", name=f"{name}_Type", signed="false", sizeInBits=str(max(8, bits)),
                shortDescription=desc)
        _int_encoding(t, bits, False)
        _el(_el(t, "ValidRangeSet"), "ValidRange", minInclusive="0", maxInclusive=str((1 << bits) - 1))
    base = _el(mcs, "MetaCommand", name=TC_BASE, abstract="true",
               shortDescription=f"VGQ-1 TC packet (APID 0x{APID_TC:03X}, no secondary header)")
    al = _el(base, "ArgumentList")
    _el(al, "Argument", name="opcode", argumentTypeRef="opcode_Type")
    _el(al, "Argument", name="ccsds_seq_count", argumentTypeRef="ccsds_seq_count_Type", initialValue="0")
    _el(al, "Argument", name="ccsds_packet_length", argumentTypeRef="ccsds_packet_length_Type")
    cc = _el(base, "CommandContainer", name=TC_BASE)
    el = _el(cc, "EntryList")
    # CCSDS 133.0-B-2 primary header as built by ccsds/packets.build_packet(tc=True):
    # version 000, type 1, secondary header 0, APID, sequence flags 11.
    _el(el, "FixedValueEntry", name="ccsds_version", binaryValue="00", sizeInBits="3")
    _el(el, "FixedValueEntry", name="ccsds_type", binaryValue="01", sizeInBits="1")
    _el(el, "FixedValueEntry", name="ccsds_sec_hdr_flag", binaryValue="00", sizeInBits="1")
    _el(el, "FixedValueEntry", name="ccsds_apid", binaryValue=f"{APID_TC:04X}", sizeInBits="11")
    _el(el, "FixedValueEntry", name="ccsds_seq_flags", binaryValue="03", sizeInBits="2")
    for n in ("ccsds_seq_count", "ccsds_packet_length", "opcode"):
        _el(el, "ArgumentRefEntry", argumentRef=n)
    seen_ops: set[int] = set()
    for mn, c in sorted(commands.items(), key=lambda kv: kv[1].opcode):
        if mn != c.mnemonic or not 0 <= c.opcode <= 255 or c.opcode in seen_ops:
            raise ValueError(f"command {mn}: inconsistent mnemonic/opcode")
        seen_ops.add(c.opcode)
        nbytes = 0
        mc = _el(mcs, "MetaCommand", name=_name(mn))
        notes = [f"Rule (checked by the flight parser and the DSS-Q GDS): {c.check_doc}."] if c.check_doc else []
        if c.builder_only:
            notes.append("Built by the DSS-Q GDS from a sequence file (one per stored command), not typed.")
        _describe(mc, c.desc + (" [HAZARDOUS]" if c.hazardous else ""), notes)
        bm = _el(mc, "BaseMetaCommand", metaCommandRef=TC_BASE)
        aal = _el(bm, "ArgumentAssignmentList")
        _el(aal, "ArgumentAssignment", argumentName="opcode", argumentValue=str(c.opcode))
        assign_len = _el(aal, "ArgumentAssignment", argumentName="ccsds_packet_length", argumentValue="0")
        arg_names = []
        if c.args:
            al = _el(mc, "ArgumentList")
            for (aname, code, lo, hi, enum) in c.args:
                tname = _name(f"{mn}_{aname}_Type")
                _arg_type(ats, tname, code, int(lo), int(hi), enum, f"{mn} {aname} ({lo}..{hi})")
                _el(al, "Argument", name=_name(aname), argumentTypeRef=tname)
                arg_names.append(aname)
                nbytes += struct.calcsize(">" + code)
        # Data field = opcode + arguments, so the length field (octets - 1) = argument octets.
        assign_len.set("argumentValue", str(nbytes))
        cc = _el(mc, "CommandContainer", name=f"{mn}_Packet")
        el = _el(cc, "EntryList")
        for aname in arg_names:
            _el(el, "ArgumentRefEntry", argumentRef=aname)
        _el(cc, "BaseContainer", containerRef=TC_BASE)
        _el(mc, "DefaultSignificance", consequenceLevel="critical" if c.hazardous else "normal",
            reasonForWarning="Hazardous: two-step operator confirmation in the DSS-Q GDS" if c.hazardous else None)


def build_xtce(packets: Mapping[int, Mapping[str, object]] | None = None,
               commands: Mapping[str, dd.CommandDef] | None = None, *, version: str = "v3",
               date_text: str | None = None) -> ET.Element:
    """XTCE 1.2 SpaceSystem 'VGQ-1' for the given (default: dictionary.py) packets and
    commands. Deterministic: identical inputs give identical XML. Raises ValueError for a
    dictionary entry XTCE cannot express (unknown struct code, invalid name)."""
    packets = dd.PACKETS if packets is None else packets
    commands = dd.COMMANDS if commands is None else commands
    root = ET.Element(_q("SpaceSystem"), {"name": SPACE_SYSTEM, "shortDescription":
                                          "VGQ-1 telemetry and telecommand dictionary (DSS-Q ground)"})
    hdr = _el(root, "Header", version=_kvn_text(version, "version"), date=date_text, validationStatus="Working")
    notes = _el(hdr, "NoteSet")
    na = ", ".join(f"'{k}' {v}" for k, v in sorted(dd.NA.items()))
    for text in (
            "Generated by dssq.products from dssq/dictionary.py, the field-for-field mirror of the "
            "flight tlm_packets.h and commands.h; do not edit by hand.",
            "CCSDS Space Packets (CCSDS 133.0-B-2); every multi-octet field big-endian, most significant bit first.",
            "TM secondary header: CUC (CCSDS 301.0-B-4), implicit P-field 0x2E, 4 coarse + 2 fine octets, SCLK "
            "since the MCU boot of the partition reported in HK; SCET comes from ground time correlation.",
            f"Not-available sentinels by struct code (raw value means 'no value'): {na}; saturating "
            "counters, bit masks and time fields have none (each parameter type says which).",
            f"TC packets: APID 0x{APID_TC:03X}, no secondary header, data field = opcode + arguments; the packet "
            "sequence count is assigned by the ground system; TC frames, COP-1 and SDLS are below this layer.",
            "Alarm ranges: WarningRange = YELLOW and CriticalRange = RED limits of the DSS-Q GDS; a value equal "
            "to a limit is out of limits."):
        _el(notes, "Note", text)
    _tm_header(root)
    _commands(root, commands)
    for apid in sorted(packets):
        _tm_packet_system(root, apid, packets[apid])
    _evr_system(root)
    _rfscan_system(root)
    _specburst_system(root)
    _cfdp_system(root)
    return root


def xtce_bytes(root: ET.Element | None = None) -> bytes:
    """UTF-8 XML document (declaration, default namespace, two-space indentation)."""
    ET.register_namespace("", XTCE_NS)
    tree = ET.ElementTree(build_xtce() if root is None else root)
    ET.indent(tree, "  ")
    buf = io.BytesIO()
    tree.write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue() + b"\n"


# =============================================================================
# Command line
# =============================================================================
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dssq.products", description="CCSDS TDM and XTCE products")
    sub = ap.add_subparsers(dest="product", required=True)
    px = sub.add_parser("xtce", help="write the XTCE 1.2 mission database")
    px.add_argument("-o", "--output", help="file (default: standard output)")
    px.add_argument("--version", default="v3", help="Header version attribute")
    px.add_argument("--date", default=None, help="Header date attribute")
    pt = sub.add_parser("tdm", help="build or validate a Tracking Data Message")
    tsub = pt.add_subparsers(dest="action", required=True)
    pv = tsub.add_parser("validate", help="validate a KVN TDM file")
    pv.add_argument("file")
    pv.add_argument("--lenient", action="store_true", help="accept third-party formatting latitude")
    pb = tsub.add_parser("build", help="build a TDM from a records CSV")
    pb.add_argument("records")
    pb.add_argument("--station", required=True)
    pb.add_argument("--spacecraft", default="VGQ-1")
    pb.add_argument("--originator", default=None)
    pb.add_argument("--freq-offset-cal-hz", type=float, default=None)
    pb.add_argument("--radio-model", default=None)
    out = pb.add_mutually_exclusive_group()
    out.add_argument("-o", "--output", help="file (default: standard output)")
    out.add_argument("--archive", help="write <archive>/tdm/<standard file name>")
    args = ap.parse_args(argv)
    try:
        if args.product == "xtce":
            data = xtce_bytes(build_xtce(version=args.version, date_text=args.date))
            if args.output:
                Path(args.output).write_bytes(data)
            else:
                sys.stdout.buffer.write(data)
            return 0
        if args.action == "validate":
            tdm = parse_tdm(Path(args.file).read_text(encoding="ascii", errors="strict"), strict=not args.lenient)
            n = sum(len(s.observations) for s in tdm.segments)
            print(f"{args.file}: valid TDM {tdm.version}, {len(tdm.segments)} segment(s), {n} record(s)")
            return 0
        recs = records_from_csv(Path(args.records).read_text(encoding="utf-8"))
        if args.archive:
            rec = TdmRecorder(args.station, args.spacecraft)
            for r in recs:
                rec.add(r)
            path = rec.write(args.archive, freq_offset_cal_hz=args.freq_offset_cal_hz, radio_model=args.radio_model)
            if path is None:
                raise ValueError("no record with a measurement to report")
            print(path)
            return 0
        text = build_tdm(recs, station=args.station, spacecraft=args.spacecraft, originator=args.originator,
                         freq_offset_cal_hz=args.freq_offset_cal_hz, radio_model=args.radio_model)
        if args.output:
            write_text_atomic(Path(args.output), text)
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, UnicodeDecodeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
