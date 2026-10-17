#!/usr/bin/env python3
"""Generates docs/05_Telemetry_and_Command_Dictionary.md from the code itself.

Sources (the single sources of truth):
  dssq-groundstation/python/dssq/dictionary.py   packets, commands, directives, enumerations,
                                                 bit maps
  dssq-groundstation/python/dssq/cmdparse.py     the stored-sequence policy (seq_forbidden_opcodes)
  vgq1-spacecraft/sketch/src/fsw/flight.cpp, datalink.cpp
                                                 event reports (literal ids in evr(sev, 0x....
                                                 calls) and the per-mode cadence table
                                                 (kModeCadence, v2 name kCadence)

The flight half of every packet and command is checked against the same dictionary by
tests/test_cross_check.py, so the generated document cannot drift from the code.
Field tuples may carry a 7th element (a decoder, e.g. the SPEC v3.1 log code): the
document then names the decoder instead of a scale.

Usage:  python3 tools/gen_dictionary_doc.py [-o FILE]     (default: docs/05_...md)
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dssq-groundstation" / "python"))   # the dssq package
from dssq import dictionary as dd  # noqa: E402
from dssq.cmdparse import SEQ_CMD_MAX, seq_forbidden_opcodes  # noqa: E402
from dssq.spectral import BURST_HDR_LEN, BURST_MAX_LEN, CLASS_NAMES, MODE_NAMES  # noqa: E402

FSW = ROOT / "vgq1-spacecraft" / "sketch" / "src" / "fsw"
OUT = ROOT / "docs" / "05_Telemetry_and_Command_Dictionary.md"
CODE = {"B": "u8", "b": "i8", "H": "u16", "h": "i16", "I": "u32", "i": "i32", "f": "f32"}
SEV = ["DIAG", "INFO", "WARN", "ERROR", "FATAL"]
_EVR_RE = re.compile(r'evr\((\d),\s*(0x[0-9A-Fa-f]+)(\s*\+\s*[A-Za-z_]+)?\s*,\s*"([^"]*)"')
_ROW_RE = re.compile(r"/\*\s*(\w+)\s*\*/\s*\{([^}]*)\}")


def _c(text: object) -> str:
    """Escapes "|" so free text cannot split a Markdown table cell."""
    return str(text).replace("|", "\\|")


def _flight_sources() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in (FSW / "flight.cpp", FSW / "datalink.cpp") if p.is_file()]


def _na_text(apid: int, fld: tuple) -> str:
    na = dd.field_na(apid, fld)
    if na is None:
        return "reading" if fld[1] in dd.NA else "-"
    return f"0x{na & 0xFFFFFFFF:X}" if na >= 0 else str(na)


def packets() -> list[str]:
    out: list[str] = []
    for apid, spec in sorted(dd.PACKETS.items()):
        fields = spec["fields"]
        size = struct.calcsize(">" + "".join(f[1] for f in fields))
        out += [f"### APID 0x{apid:03X} - {spec['name']}: {_c(spec['desc'])} ({size} octets of user data, "
                f"VC{dd.APID_VC[apid]})", "",
                "| # | Offset | Field | Type | Scale / decoder | Unit | N/A | Limits (red low / yellow low / "
                "yellow high / red high) | Description |",
                "|---|---|---|---|---|---|---|---|---|"]
        off = 0
        for i, fld in enumerate(fields):
            name, code, scale, unit, desc, lim = fld[:6]
            dec = fld[6] if len(fld) > 6 else None
            lim_s = " / ".join("-" if x is None else f"{x:g}" for x in lim) if lim else ""
            sc = getattr(dec, "__name__", "decoder") if callable(dec) else ("raw" if scale is None else f"{scale:g}")
            out.append(f"| {i} | {off} | `{name}` | {CODE[code]} | {sc} | {_c(unit)} | {_na_text(apid, fld)} | "
                       f"{lim_s} | {_c(desc)} |")
            off += struct.calcsize(">" + code)
        out.append("")
    return out


def variable_packets() -> list[str]:
    return [
        "| APID | Name | VC | Layout |", "|---|---|---|---|",
        f"| 0x{dd.APID_EVR:03X} | EVR | 0 | `severity u8 \\| event_id u16 \\| ASCII text (<= {dd.EVR_TEXT_MAX} "
        "characters)`; the ground shows octets outside 0x20..0x7E as U+FFFD |",
        f"| 0x{dd.APID_RFSCAN:03X} | RFSCAN | 0 | `first_ch u8 \\| count u8 (1..{dd.RFSCAN_MAX_CHANNELS}) \\| "
        "duration_ms u16 \\| noise_dbm i8 x count` (-128 = no reading) |",
        f"| 0x{dd.APID_SPECBURST:03X} | SPECBURST | 1 | `t0_ms u32 \\| rate_dhz u16 \\| gain_code u8 \\| "
        "int_cycles u8 \\| n u8 (1..3) \\| encoding u8 \\| dt_ms u16[n] \\| payload`; encoding 0: n x 18 u16 "
        "counts, time-major; 1: CCSDS 121.0-B-3 (16-bit samples, J = 16, r = 128, Basic, unit-delay "
        f"preprocessor) of the channel-major counts, strictly shorter than raw. Header {BURST_HDR_LEN} "
        f"octets, at most {BURST_MAX_LEN}. Count 0xFFFF = not converted, 0xFFFE = saturated (>= 65534) |",
        f"| 0x{dd.APID_CFDP:03X} | CFDP | 3 | one CCSDS 727.0-B-5 class-1 PDU of {dd.CFDP_PDU_MIN}.."
        f"{dd.CFDP_PDU_MAX} octets (Metadata, File Data, EOF); never recorded in the SSR |",
    ]


def cadence() -> list[str]:
    for text in _flight_sources():
        for name in ("kModeCadence", "kCadence"):
            i = text.find(name)
            if i < 0:
                continue
            block = text[i:]
            block = block[:block.find("};")]
            rows = _ROW_RE.findall(block)
            if not rows:
                continue
            out = ["| Mode | HK | RF | TIMECORR | MAG | ATT | SPEC | ENV |", "|---|---|---|---|---|---|---|---|"]
            for mode, vals in rows:
                cells = [v.strip() for v in vals.split(",") if v.strip()]
                out.append(f"| {mode} | " + " | ".join("off" if v == "0" else f"{v} s" for v in cells) + " |")
            return out
    return ["(the cadence table was not found in the flight sources)"]


def evrs() -> list[str]:
    rows: dict[str, tuple[str, str]] = {}
    for text in _flight_sources():
        for m in _EVR_RE.finditer(text):
            sev, eid, plus, fmt = int(m.group(1)), int(m.group(2), 16), m.group(3), m.group(4)
            key = f"0x{eid:04X}" + ("+n" if plus else "")
            rows.setdefault(key, (SEV[sev] if sev < len(SEV) else str(sev), fmt))
    if not rows:
        return ["(no literal evr(severity, 0x...) calls found in the flight sources)"]
    out = ["| Event id | Severity | Text (printf format) |", "|---|---|---|"]
    for k in sorted(rows):
        sev_name, fmt = rows[k]
        out.append(f"| {k} | {sev_name} | `{_c(fmt)}` |")
    return out


def _bits(title: str, names: Iterable[str]) -> list[str]:
    return [f"### {title}", "", "| Bit | Name |", "|---|---|", *[f"| {i} | {n} |" for i, n in enumerate(names)], ""]


def _enum(title: str, table: Mapping[int, str]) -> list[str]:
    return [f"### {title}", "", "| Code | Meaning |", "|---|---|",
            *[f"| {k} | {_c(v)} |" for k, v in sorted(table.items())], ""]


def commands() -> list[str]:
    forbidden = seq_forbidden_opcodes()
    out = ["| Mnemonic | Opcode | Arguments (name: type, range) | Rule | Hazardous | Storable | Description |",
           "|---|---|---|---|---|---|---|"]
    for c in sorted(dd.COMMANDS.values(), key=lambda c: c.opcode):
        args = ", ".join(f"{n}: {CODE[t]} {lo}..{hi}" + (f" ({', '.join(f'{k}={v}' for k, v in e.items())})"
                                                         if e else "")
                         for n, t, lo, hi, e in c.args) or "-"
        storable = "no" if c.opcode in forbidden or 1 + c.arg_octets > SEQ_CMD_MAX else "yes"
        desc = c.desc + (" (built from a sequence file, not typed)" if c.builder_only else "")
        out.append(f"| `{c.mnemonic}` | 0x{c.opcode:02X} | {_c(args)} | {_c(c.check_doc)} | "
                   f"{'YES' if c.hazardous else ''} | {storable} | {_c(desc)} |")
    return out


def document() -> list[str]:
    instruments = [n for i, n in enumerate(dd.SENSOR_BITS) if dd.SENSOR_INSTRUMENT_MASK >> i & 1]
    return [
        "# 05 - Telemetry & Command Dictionary",
        "",
        "> GENERATED by `tools/gen_dictionary_doc.py` from `dssq-groundstation/python/dssq/dictionary.py` "
        "and the flight source. Do not edit by hand; re-run the script. The flight packers and the command "
        "parser are verified against the same dictionary by `tests/test_cross_check.py` (bit-exact).",
        "",
        "Conventions: all fields big-endian (CCSDS network order). Engineering value = raw x scale, or "
        "the named decoder applied to the raw code. Not-available sentinels (column N/A, decoded as "
        "`None` and shown as `-`): i16 0x7FFF, u16 0xFFFF, i32 0x7FFFFFFF, u32 0xFFFFFFFF, i8 -128; "
        "`reading` marks saturating counters, bit masks and time fields, where every raw value is a "
        "reading. Every TM packet carries a 6-octet CUC secondary header (SCLK, CCSDS 301.0-B-4, 4 + 2 "
        "octets) before the user data listed here.",
        "",
        "## 1 Telemetry packets", "",
        "### Variable-length packets", "", *variable_packets(), "",
        *packets(),
        "## 2 Packet generation cadence per flight-software mode", "",
        "Seconds between packets (overridable per packet with `PKTRATE`). The spacecraft stretches every "
        "period at run time so that the periodic load stays within 75 % of the measured downlink capacity "
        "(EVR 0x0215 reports the factor). EVR, CMDVER, RFSCAN, SPECBURST and CFDP are event driven.", "",
        *cadence(), "",
        "## 3 Bit maps and enumerations", "",
        *_bits("HK.fdir_flags", dd.FDIR_BITS),
        *_bits(f"HK.sensor_health (SENSORS enable mask: {', '.join(instruments)} = "
               f"0x{dd.SENSOR_INSTRUMENT_MASK:04X})", dd.SENSOR_BITS),
        *_bits("MAG.flags", dd.MAG_FLAG_BITS),
        *_bits("ATT.flags", dd.ATT_FLAG_BITS),
        *_bits("ATT.imu_flags (ATT v4, BHI260AP 6-DOF)", dd.IMU_FLAG_BITS),
        *_bits("ENV.flags", dd.ENV_FLAG_BITS),
        *_bits("RF.flags (rf_flags)", dd.RF_FLAG_BITS),
        "### HK.mag_bist", "",
        "b0..2 outboard RM3100 X/Y/Z pass, b3 outboard tested, b4..6 inboard X/Y/Z pass, b7 inboard tested "
        "(an untested unit has no result).", "",
        *_enum("HK.radio_fault (1..4 E22-400T37S abnormal status, 16..19 SX126x)", dd.RADIO_FAULTS),
        *_enum("HK.reset_cause", dd.RESET_CAUSES),
        *_enum("HK.fsw_mode", dd.MODES),
        *_enum("HK.sdls_tc_service / sdls_tm_service", dd.SDLS_SERVICES),
        *_enum("HK.sdls_tm_state", dd.SDLS_TM_STATES),
        *_enum("HK.cfdp_state", dd.CFDP_STATES),
        *_enum("HK.seq_state", dd.SEQ_STATES),
        *_enum("HK.beacon_level", dd.BEACON_LEVELS),
        *_enum("RF.radio_model", dd.RADIO_MODELS),
        *_enum("RF.framing", dd.FRAMINGS),
        *_enum("SPEC.spec_mode", MODE_NAMES),
        *_enum("SPEC.spec_class (onboard heuristic)", CLASS_NAMES),
        *_enum("CMDVER.error_code / HK.cmd_last_status", dd.CMD_ERRORS),
        *_enum("CMDVER.cmd_source (stage octet b7..6)", dd.CMD_SOURCES),
        "CMDVER.stage octet: b1..0 the stage, b7..6 the command source; tc_seq is the N(S) of the "
        "carrying AD frame and is significant only for source AD.", "",
        "## 4 Telecommands (uplinked; APID 0x0C0, user data = opcode + arguments)", "",
        "Typed identically on the web console and on the station CardKB. Hazardous commands need CONFIRM "
        "(and a second operator's AUTH when the two-person rule is enabled). Prefix any command with `BD` "
        "to send it as a COP-1 bypass (Type-BD) frame. Rule = the cross-argument check applied identically "
        "by the flight parser and the ground. Storable = may be stored by SEQLOAD (not hazardous, not "
        f"REBOOT/SEQLOAD/SEQCTRL, at most {SEQ_CMD_MAX} octets).", "",
        *commands(), "",
        "### COP-1 control directives (Type-BC frames)", "", "| Directive | Meaning |", "|---|---|",
        *[f"| `{k}` | {_c(v)} |" for k, v in dd.DIRECTIVES.items()], "",
        "### Ground-station directives (executed by the GDS, never uplinked as such)", "",
        "| Directive | Meaning |", "|---|---|",
        *[f"| `{k}` | {_c(v)} |" for k, v in dd.GROUND_DIRECTIVES.items()], "",
        "## 5 Event reports (EVR, APID 0x012)", "",
        "Extracted from the flight sources. `+n` = base id plus a code (e.g. the TC rejection reason).", "",
        *evrs(), "",
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="generate the telemetry and command dictionary document")
    ap.add_argument("-o", "--output", default=str(OUT), help=f"output file (default {OUT.relative_to(ROOT)})")
    a = ap.parse_args(argv)
    lines = document()
    out = Path(a.output)
    try:
        out.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
