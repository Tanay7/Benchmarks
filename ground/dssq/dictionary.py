"""VGQ-1 telemetry and telecommand dictionary (ground half), version 3.

Mirrors spacecraft/vgq1_flight/sketch/src/fsw/tlm_packets.h field for field and
spacecraft/.../fsw/commands.h opcode for opcode (Phase B specification PB-1, PB-2).
ground/tests/test_cross_check.py decodes packets and command vectors produced by the
flight code itself (tools/host_test) with this dictionary, so a divergence fails the
build; docs/05 is generated from it (tools/gen_dictionary_doc.py) and the XTCE mission
database is exported from it (dssq.products.build_xtce).

Telemetry field tuple (fixed-layout APIDs, PACKETS[apid]["fields"]):
    (name, struct_code, scale, unit, description, limits[, decoder])
  * struct_code : Python struct character; big-endian applied globally (CCSDS order)
  * scale       : engineering value = raw x scale; None keeps the raw integer
  * limits      : (red_low, yellow_low, yellow_high, red_high) or None; a value EQUAL to a
                  limit is out of limits (checked_limits)
  * decoder     : optional 7th element, a callable applied to the raw integer after the
                  not-available check (SPEC v3.1 log-coded spectrum, azimuth wrap); scale is
                  then None. Every consumer accepts 6- and 7-tuples.
Not-available sentinels (NA): i16 0x7FFF, u16 0xFFFF, i32 0x7FFFFFFF, u32 0xFFFFFFFF,
i8 -128 decode to None, EXCEPT for the fields listed in NO_NA: there every raw value is a
reading (counters and timers that the flight saturates at the type maximum, bit masks,
time fields). Reason: the flight packs saturated counters as 0xFFFF (`umin32(x, 65535)`)
and RF frame_period_ms saturates at 65535 ms, which the operator must see as a saturated
value, not as "no value" (PB-5.4); field_na() is the one place that applies the rule.

Variable-length APIDs (not in PACKETS; decom.py and spectral.py decode them):
  EVR 0x012 (severity u8 | event_id u16 | ASCII text <= 80), RFSCAN 0x015 (first_ch u8 |
  count u8 | duration_ms u16 | noise i8[count <= 84]), SPECBURST 0x024 (PB-1.12,
  spectral.decode_specburst) and CFDP 0x030 (one CCSDS 727.0-B-5 class-1 PDU per packet).

Telecommand argument tuple: (name, struct_code, lo, hi, enum | None); enum maps upper-case
labels to values and every label value lies in lo..hi (checked at import). A CommandDef
`check` adds the cross-argument rules that the flight parse_user_data() applies
identically (PB-2.3), so the ground never queues a command the spacecraft would refuse
for its arguments. Hazardous commands need the ground's two-step confirmation; the
flight sequencer refuses hazardous opcodes, REBOOT, SEQLOAD and SEQCTRL
(cmdparse.seq_forbidden_opcodes()).
"""
from __future__ import annotations

import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .ccsds.sdls import TM_STATE_NAMES
from .spectral import DIRECTIVE_HELP as _SPECTRAL_DIRECTIVES
from .spectral import SPEC_V31_FIELDS, WAVELENGTHS_NM, led_cur_allowed

# =============================================================================
# Types and sentinels
# =============================================================================
FieldDef = tuple[Any, ...]          # 6- or 7-tuple, see the module docstring
ArgDef = tuple[str, str, int, int, Mapping[str, int] | None]
CheckFn = Callable[[Mapping[str, int]], str | None]

NA: dict[str, int] = {"h": 0x7FFF, "H": 0xFFFF, "i": 0x7FFFFFFF, "I": 0xFFFFFFFF, "b": -128}

# =============================================================================
# APIDs (11-bit, flight tlm_packets.h enum Apid) and virtual channels (PB-1.2)
# =============================================================================
APID_HK = 0x010
APID_RF = 0x011
APID_EVR = 0x012
APID_CMDVER = 0x013
APID_TIMECORR = 0x014
APID_RFSCAN = 0x015
APID_MAG = 0x020
APID_ATT = 0x021
APID_SPEC = 0x022
APID_ENV = 0x023
APID_SPECBURST = 0x024
APID_CFDP = 0x030
APID_TC = 0x0C0                     # uplink TC packets (no secondary header)

VARIABLE_APIDS = frozenset({APID_EVR, APID_RFSCAN, APID_SPECBURST, APID_CFDP})
# Real-time virtual channel of each APID: 0 RT engineering, 1 RT science, 3 file delivery
# (CFDP); VC2 replays the SSR (any recorded APID), the OID channel carries idle data only.
APID_VC: dict[int, int] = {
    APID_HK: 0, APID_RF: 0, APID_EVR: 0, APID_CMDVER: 0, APID_TIMECORR: 0, APID_RFSCAN: 0,
    APID_MAG: 1, APID_ATT: 1, APID_SPEC: 1, APID_ENV: 1, APID_SPECBURST: 1, APID_CFDP: 3}
# PKTRATE may set the period of these (flight PeriodicIdx order, PB-3.3).
PERIODIC_APIDS: tuple[int, ...] = (APID_HK, APID_RF, APID_TIMECORR, APID_MAG, APID_ATT,
                                   APID_SPEC, APID_ENV)
EVR_TEXT_MAX = 80                   # flight pack_evr(): text truncated at 80 characters
RFSCAN_MAX_CHANNELS = 84            # flight pack_rfscan(): channels 0..83
CFDP_PDU_MIN, CFDP_PDU_MAX = 10, 146   # one class-1 PDU per packet (PB-1.13, kCfdpMaxPduLen)

# =============================================================================
# Enumerations and bit maps (flight tlm_packets.h / commands.h / autonomy.h)
# =============================================================================
MODES = {0: "BOOT", 1: "SAFE", 2: "CRUISE", 3: "ENCOUNTER", 4: "TEST"}
RESET_CAUSES = {0: "POWER-ON", 1: "PIN", 2: "SOFTWARE", 3: "WATCHDOG", 4: "BROWNOUT",
                255: "UNKNOWN"}
RESET_BROWNOUT = 4
FARM_STATES = {1: "OPEN", 2: "WAIT", 3: "LOCKOUT"}
EVR_SEVERITY = {0: "DIAG", 1: "INFO", 2: "WARN", 3: "ERROR", 4: "FATAL"}
CMD_STAGES = {1: "ACCEPTED", 2: "EXECUTED", 3: "FAILED"}
# CmdError (PB-2.2); 4 is retired. BUSY = resource in use or not ready yet: retry later.
CMD_ERRORS = {0: "OK", 1: "UNKNOWN_OPCODE", 2: "BAD_LENGTH", 3: "BAD_ARGUMENT",
              5: "HARDWARE", 6: "BAD_MAGIC", 7: "BUSY"}
# CMDVER `stage` octet (interface decision I1): b1..0 the stage (CMD_STAGES), b7..6 the
# command source (CMD_SOURCES), b5..2 spare (0; decoded as stage_spare so decode/encode
# stay lossless). tc_seq is the carrying AD frame's N(S) and is significant only for source
# AD: every N(S) 0..255 is a valid sequence number (no in-band sentinels).
CMD_SOURCE_AD, CMD_SOURCE_BD, CMD_SOURCE_STORED = 0, 1, 2
CMD_SOURCES = {CMD_SOURCE_AD: "AD", CMD_SOURCE_BD: "BD/HARDLINE", CMD_SOURCE_STORED: "STORED"}

RADIO_MODELS = {1: "E22-400T33D", 2: "E22-400T37S", 3: "SX126x"}   # RF radio_model
FRAMINGS = {0: "TM", 1: "USLP"}                                     # RF framing
SDLS_SERVICES = {0: "NONE", 1: "AUTH", 2: "AE"}                     # HK sdls_tc/tm_service
SDLS_TM_STATES: dict[int, str] = dict(TM_STATE_NAMES)               # HK sdls_tm_state
CFDP_STATES = {0: "IDLE", 1: "SENDING SSR", 2: "RELAYING EGSE FILE"}
SEQ_STATES = {0: "IDLE/EMPTY", 1: "ARMED", 2: "COMPLETE", 3: "STOPPED_BY_FDIR"}
BEACON_LEVELS = {0: "NOMINAL", 1: "INTERESTING", 2: "IMPORTANT", 3: "URGENT"}
# HK radio_fault: 1..4 E22-400T37S abnormal status (FF FF FF 0x), 16..19 SX126x RadioFault.
RADIO_FAULTS = {0: "NONE", 1: "UNDER-VOLTAGE", 2: "OVER-VOLTAGE", 3: "OVER-TEMPERATURE",
                4: "OVER-VOLTAGE+OVER-TEMPERATURE", 16: "SX126x BUSY STUCK",
                17: "SX126x SPI/COMMAND ERROR", 18: "SX126x REINIT FAILED",
                19: "SX126x NO IRQ"}

FDIR_BITS = ["RADIO_CFG", "AUX_TIMEOUT", "PA_OVERTEMP", "AVI_OVERTEMP", "CMD_LOSS", "I2C_BUS",
             "SENSOR_LOST", "LOOP_OVERRUN", "VC_CONGEST", "LOW_BUS_V", "FARM_LOCKOUT",
             "TX_INHIBIT", "RATE_REVERT", "SDLS_AUTH", "HIBERNATE", "RADIO_FAULT"]
FDIR_HIBERNATE = 1 << 14
FDIR_TX_INHIBIT = 1 << 11
SENSOR_BITS = ["NICLA_ME", "ME_QUAT", "ME_ACC", "ME_GYRO", "ME_MAG", "ME_BARO", "ME_BSEC",
               "AS7265X", "RM3100_OB", "RM3100_IB"]
SENSOR_INSTRUMENT_MASK = 0x0381     # SENSORS enable mask: b0 NICLA_ME, b7, b8, b9
MAG_FLAG_BITS = ["PA_KEYED", "OB", "IB", "BMM150", "LAMP", "CAL"]
ATT_FLAG_BITS = ["QUAT", "ACCEL", "GYRO", "BMM150", "TRIAD", "MAG_SCALE", "TRIAD_RM3100"]
ENV_FLAG_BITS = ["BARO", "BSEC", "TEMP_RH", "GAS"]
RF_FLAG_BITS = ["RX_BOOST", "LBT"]

AS7265X_NM = list(WAVELENGTHS_NM)


def decode_bits(value: int | None, names: list[str]) -> list[str]:
    """Names of the set bits of a 0..2^32-1 bit mask, least significant first; a set bit
    without a name is reported as 'b<i>' (never silently dropped). None -> []."""
    if value is None:
        return []
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 1 << 32:
        raise ValueError(f"bit mask {value!r} is not a 32-bit unsigned integer")
    return [names[i] if i < len(names) else f"b{i}" for i in range(32) if value >> i & 1]


def decode_mag_bist(value: int | None) -> dict[str, Any]:
    """HK mag_bist (b0..2 outboard X/Y/Z OK, b3 outboard tested, b4..6 inboard X/Y/Z OK,
    b7 inboard tested) -> {"available", "ob": {tested, x, y, z, pass}, "ib": {...}}.
    Axis results and pass are None for a unit that was not tested."""
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                              or not 0 <= value <= 0xFF):
        raise ValueError(f"mag_bist {value!r} is not a u8")

    def unit(nib: int | None) -> dict[str, Any]:
        tested = nib is not None and bool(nib & 8)
        if not tested or nib is None:
            return {"tested": False, "x": None, "y": None, "z": None, "pass": None}
        x, y, z = bool(nib & 1), bool(nib & 2), bool(nib & 4)
        return {"tested": True, "x": x, "y": y, "z": z, "pass": x and y and z}

    return {"available": value is not None,
            "ob": unit(None if value is None else value & 0x0F),
            "ib": unit(None if value is None else value >> 4)}


# =============================================================================
# Fixed-layout telemetry packets
# =============================================================================
def _vec(prefix: str, code: str, scale: float, unit: str, desc: str, n: int = 3,
         axes: str = "xyz") -> list[FieldDef]:
    return [(f"{prefix}_{axes[i]}", code, scale, unit, f"{desc} {axes[i].upper()}", None)
            for i in range(n)]


def _mag(prefix: str, desc: str) -> list[FieldDef]:
    return [*_vec(prefix + "_b", "i", 1e-3, "uT", desc + " field"),
            (prefix + "_rms", "H", 1e-3, "uT", desc + " RMS fluctuation in interval", None)]


PACKETS: dict[int, dict[str, Any]] = {
    APID_HK: {"name": "HK", "desc": "Engineering housekeeping (beacon in hibernation)", "fields": [
        ("fsw_mode", "B", None, "", "Flight software mode", None),
        ("sclk_partition", "H", None, "", "SCLK partition = EGSE boot counter = SDLS TM epoch "
                                          "(0 = none yet)", None),
        ("uptime_s", "I", None, "s", "SCLK seconds since MCU boot", None),
        ("reset_cause", "B", None, "", "Cause of the last MCU reset", None),
        ("fdir_flags", "H", None, "", "Fault protection flags (FDIR_BITS)", None),
        ("sensor_health", "H", None, "", "Instrument health bit mask (SENSOR_BITS)", None),
        ("cmd_accepted", "H", None, "", "Commands executed", None),
        ("cmd_rejected", "H", None, "", "Commands rejected", None),
        ("cmd_last_opcode", "B", None, "", "Last opcode executed", None),
        ("cmd_last_status", "B", None, "", "Last command error code (CMD_ERRORS)", None),
        ("tc_frames_ok", "H", None, "", "TC frames passing FECF and SDLS", None),
        ("tc_frames_bad", "H", None, "", "TC frames rejected", None),
        ("farm_state", "B", None, "", "FARM-1 state", None),
        ("farm_vr", "B", None, "", "FARM-1 V(R)", None),
        ("cmd_loss_timer_s", "I", None, "s", "Time since the last valid TC", None),
        ("bus_temp", "h", 0.01, "degC", "Bus temperature (Nicla Sense ME BME688)", (-20, 0, 60, 75)),
        ("radio_fault", "B", None, "", "Last radio fault (RADIO_FAULTS; 0 none)", (None, None, 0.5, 2.5)),
        ("radio_fault_reports", "H", None, "", "T37S abnormal-status reports + SX126x faults", None),
        ("loop_max_ms", "H", None, "ms", "Worst loop pass since the previous HK", (None, None, 600, 1000)),
        ("loop_overruns", "H", None, "", "Loop passes longer than 250 ms", None),
        ("i2c_errors", "H", None, "", "Sensor-bus I2C transaction errors", None),
        ("ssr_fill", "H", 0.1, "%", "Solid-state recorder fill", (None, None, 80, 95)),
        ("ssr_dropped", "H", None, "", "SSR overwrite drops + export-pause drops", None),
        ("ssr_kib", "H", None, "KiB", "SSR capacity allocated at boot", None),
        ("vc0_backlog", "H", None, "B", "VC0 queued octets", (None, None, 600, 900)),
        ("vc1_backlog", "H", None, "B", "VC1 queued octets", (None, None, 600, 900)),
        ("vc2_backlog", "H", None, "B", "VC2 queued octets", None),
        ("vc3_backlog", "H", None, "B", "VC3 (CFDP) queued octets", None),
        ("sdls_tc_service", "B", None, "", "SDLS uplink service (0 NONE, 1 AUTH, 2 AE)", None),
        ("sdls_tm_service", "B", None, "", "SDLS downlink service (0 NONE, 2 AE)", None),
        ("sdls_auth_fail", "H", None, "", "TC frames rejected by SDLS", (None, None, 1, 10)),
        ("sdls_last_sn", "I", None, "", "Last accepted SDLS TC sequence number", None),
        ("sdls_tm_epoch", "H", None, "", "SDLS TM epoch in use (0 = none yet)", None),
        ("sdls_tm_state", "B", None, "", "SDLS TM state (SDLS_TM_STATES)", (None, None, 1.5, None)),
        ("cfdp_state", "B", None, "", "CFDP sender state (CFDP_STATES)", None),
        ("cfdp_seq", "I", None, "", "Current/last CFDP transaction sequence number (0 = none)",
         None),
        ("cfdp_progress", "H", 0.1, "%", "CFDP transfer progress", None),
        ("seq_state", "B", None, "", "Stored sequence state (SEQ_STATES)", None),
        ("seq_count", "B", None, "", "Stored sequence entries pending", None),
        ("seq_next_dt_s", "H", None, "s", "Time to the next due entry (None: none or not armed)",
         None),
        ("mag_bist", "B", None, "", "RM3100 self-test: b0-2 OB XYZ ok, b3 OB tested, b4-6 IB XYZ "
                                    "ok, b7 IB tested", None),
        ("magcal_state", "B", None, "", "Onboard magnetometer calibration: b0 OB active, b1 IB "
                                        "active", None),
        ("beacon_level", "B", None, "", "Beacon urgency (BEACON_LEVELS)", (None, None, 1.5, 2.5)),
    ]},
    APID_RF: {"name": "RF", "desc": "Telecom subsystem (any radio model)", "fields": [
        ("radio_model", "B", None, "", "Radio model (1 E22-400T33D, 2 E22-400T37S, 3 SX126x)", None),
        ("air_rate_code", "B", None, "", "E22 REG0 air-rate code / SX126x LoRa profile 0..7", None),
        ("tx_power_code", "B", None, "", "TX power code 0..3 (meaning per model)", None),
        ("channel", "B", None, "", "Channel (f = 410.125 + CH MHz)", None),
        ("radio_cfg_ok", "B", None, "", "Radio configuration verified", (0, None, None, None)),
        ("framing", "B", None, "", "Downlink framing (0 TM, 1 USLP)", None),
        ("frames_sent", "I", None, "", "CADUs transmitted", None),
        ("oid_frames_sent", "I", None, "", "Idle (OID) frames transmitted", None),
        ("tx_airtime_ms_total", "I", None, "ms", "Cumulative transmitter busy time", None),
        ("tx_duty", "H", 0.1, "%", "Transmit duty cycle (60 s window)", (None, None, 55, 70)),
        ("frame_period_ms", "H", None, "ms", "Frame period in force (saturates at 65535)", None),
        ("radio_timeouts", "H", None, "", "Radio TX timeouts (all models)", None),
        ("uplink_rssi", "h", 1.0, "dBm", "RSSI of the last uplink packet", None),
        ("uplink_snr", "h", 0.01, "dB", "SNR of the last uplink packet (SX126x)", None),
        ("uplink_freq_err", "i", 1.0, "Hz", "Raw frequency error of the last uplink packet (SX126x)",
         None),
        ("sc_noise_floor", "h", 1.0, "dBm", "Spacecraft receiver noise floor", None),
        ("cltu_ok", "H", None, "", "CLTUs decoded", None),
        ("cltu_bad", "H", None, "", "CLTUs rejected", None),
        ("bch_corrected", "H", None, "", "BCH codeblocks corrected", None),
        ("rx_bytes", "I", None, "B", "Octets received from the radio", None),
        ("mcfc", "B", None, "", "Master channel frame count (TM framer)", None),
        ("last_airtime", "B", 0.1, "s", "Busy time of the last frame (saturates at 25.5 s)", None),
        ("lbt_busy", "H", None, "", "Transmissions deferred by LBT/CAD", None),
        ("rf_flags", "B", None, "", "b0 Rx boosted gain verified, b1 LBT/CAD on", None),
    ]},
    APID_CMDVER: {"name": "CMDVER", "desc": "Command verification", "fields": [
        ("tc_seq", "B", None, "", "N(S) of the carrying AD frame (significant for cmd_source AD only)",
         None),
        ("opcode", "B", None, "", "Opcode", None),
        ("stage", "B", None, "", "b1..0 verification stage (CMD_STAGES), b7..6 command source "
                                 "(CMD_SOURCES); decoded as stage + stage_spare + cmd_source (BITFIELDS)", None),
        ("error_code", "B", None, "", "Error code (CMD_ERRORS)", None),
        ("ground_tag", "I", None, "", "Echoed ground tag (PING)", None),
    ]},
    APID_TIMECORR: {"name": "TIMECORR", "desc": "SCLK correlation sample", "fields": [
        ("ref_mcfc", "B", None, "", "Reference frame: MCFC (TM) / VC0 VCF count (USLP)", None),
        ("sclk_coarse", "I", None, "s", "SCLK coarse when the PA was keyed for the reference", None),
        ("sclk_fine", "H", None, "", "SCLK fine (1/65536 s)", None),
        ("air_rate_code", "B", None, "", "Air-rate code / LoRa profile of the reference frame", None),
        ("ref_airtime_ms", "H", None, "ms", "Busy time of the reference frame", None),
    ]},
    APID_MAG: {"name": "MAG", "desc": "Magnetometer science (RM3100 boom + BMM150 body), raw",
               "fields": [
        ("nsamples", "B", None, "", "Samples averaged", None),
        ("flags", "B", None, "", ("b0 PA keyed, b1 OB ok, b2 IB ok, b3 BMM150 ok, b4 AS7265X lamp "
                                  "lit or unknown during a sample, b5 onboard MAGCAL active"), None),
        *_mag("ob", "Outboard RM3100"),
        *_mag("ib", "Inboard RM3100 (optional)"),
        *_mag("body", "Body BMM150 (Nicla Sense ME)"),
        ("rm3100_cc", "H", None, "", "RM3100 cycle count", None),
    ]},
    APID_ATT: {"name": "ATT", "desc": "Attitude determination (Nicla Sense ME + TRIAD)", "fields": [
        ("flags", "B", None, "", ("b0 fusion quaternion, b1 accel, b2 gyro, b3 BMM150, b4 TRIAD, "
                                  "b5 BMM150 scale read, b6 TRIAD used the RM3100"), None),
        *_vec("acc", "h", 1e-3, "g", "Acceleration"),
        *_vec("gyro", "h", 0.1, "deg/s", "Angular rate"),
        *_vec("mag", "h", 0.1, "uT", "BMM150 field (latest)"),
        *_vec("q_fus", "h", 1 / 16384, "", "BHI260 fusion quaternion", n=4, axes="wxyz"),
        ("q_fus_acc", "H", 1e-3, "rad", "BHI260 fusion accuracy estimate", None),
        *_vec("q_triad", "h", 1 / 16384, "", "TRIAD quaternion", n=4, axes="wxyz"),
    ]},
    APID_SPEC: {"name": "SPEC", "desc": "AS7265X spectrometer (SPEC v3.1)",
                "fields": list(SPEC_V31_FIELDS)},
    APID_ENV: {"name": "ENV", "desc": "Environment (Nicla Sense ME)", "fields": [
        ("flags", "B", None, "", "b0 BMP390 pressure, b1 BSEC, b2 temperature/humidity, b3 gas "
                                 "resistance", None),
        ("p", "I", 0.01, "hPa", "Pressure (BMP390)", None),
        ("t", "h", 0.01, "degC", "Temperature (BME688)", None),
        ("rh", "H", 0.01, "%", "Relative humidity (BME688, whole percent resolution)", None),
        ("iaq", "H", None, "", "BSEC indoor air-quality index", None),
        ("iaq_s", "H", None, "", "BSEC static IAQ", None),
        ("co2eq", "I", None, "ppm", "BSEC CO2 equivalent", None),
        ("bvoc", "H", 0.01, "ppm", "BSEC breath-VOC equivalent", None),
        ("gas", "I", None, "ohm", "BME688 gas resistance", None),
        ("accuracy", "B", None, "", "BSEC accuracy 0..3 (meaningful when flags b1 set)", None),
    ]},
}

# Raw fields that pack several values (PB-1.6 / I1): decom replaces the raw field by the
# listed sub-fields (name, lsb, width), which cover every bit; encode() packs them back.
BITFIELDS: dict[int, dict[str, tuple[tuple[str, int, int], ...]]] = {
    APID_CMDVER: {"stage": (("stage", 0, 2), ("stage_spare", 2, 4), ("cmd_source", 6, 2))},
}

# Fields whose every raw value is a reading: the NA sentinel does NOT apply (module docstring).
NO_NA: dict[int, frozenset[str]] = {
    APID_HK: frozenset({
        "uptime_s", "fdir_flags", "sensor_health", "cmd_accepted", "cmd_rejected", "tc_frames_ok",
        "tc_frames_bad", "cmd_loss_timer_s", "radio_fault_reports", "loop_max_ms",
        "loop_overruns", "i2c_errors", "ssr_fill", "ssr_dropped", "ssr_kib", "vc0_backlog",
        "vc1_backlog", "vc2_backlog", "vc3_backlog", "sdls_auth_fail", "sdls_last_sn",
        "cfdp_progress"}),
    APID_RF: frozenset({
        "frames_sent", "oid_frames_sent", "tx_airtime_ms_total", "tx_duty", "frame_period_ms",
        "radio_timeouts", "cltu_ok", "cltu_bad", "bch_corrected", "rx_bytes", "lbt_busy"}),
    APID_CMDVER: frozenset({"ground_tag"}),
    APID_TIMECORR: frozenset({"sclk_coarse", "sclk_fine", "ref_airtime_ms"}),
    APID_MAG: frozenset({"rm3100_cc"}),
}

PACKET_NAMES: dict[int, str] = {
    **{apid: str(spec["name"]) for apid, spec in PACKETS.items()},
    APID_EVR: "EVR", APID_RFSCAN: "RFSCAN", APID_SPECBURST: "SPECBURST", APID_CFDP: "CFDP"}


def field_na(apid: int, fld: FieldDef) -> int | None:
    """Raw not-available sentinel of one field, or None when the field has none."""
    if fld[0] in NO_NA.get(apid, ()):
        return None
    return NA.get(fld[1])


def packet_len(apid: int) -> int:
    """User-data octets of a fixed-layout APID (KeyError for any other)."""
    return struct.calcsize(">" + "".join(f[1] for f in PACKETS[apid]["fields"]))


# =============================================================================
# Telecommands (opcodes MUST match flight fsw/commands.h enum Opcode)
# =============================================================================
_ARG_CODES = {"B": (0, 0xFF), "b": (-0x80, 0x7F), "H": (0, 0xFFFF), "h": (-0x8000, 0x7FFF),
              "I": (0, 0xFFFFFFFF), "i": (-0x80000000, 0x7FFFFFFF)}


@dataclass
class CommandDef:
    """One telecommand. `check(values)` returns an error text or None (values = the
    range-checked arguments by name); `check_doc` is that rule in one line (XTCE, docs,
    /api/dictionary). builder_only commands are listed (XTCE, help) but cmdparse.parse()
    refuses them: SEQLOAD is built from a sequence file (cmdparse.seqload_commands)."""
    mnemonic: str
    opcode: int
    args: list[ArgDef] = field(default_factory=list)
    hazardous: bool = False
    desc: str = ""
    check: CheckFn | None = None
    check_doc: str = ""
    builder_only: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.opcode <= 0xFF:
            raise ValueError(f"{self.mnemonic}: opcode {self.opcode} is not a u8")
        names = set()
        for name, code, lo, hi, enum in self.args:
            if code not in _ARG_CODES:
                raise ValueError(f"{self.mnemonic}.{name}: unsupported struct code {code!r}")
            tmin, tmax = _ARG_CODES[code]
            if not tmin <= lo <= hi <= tmax:
                raise ValueError(f"{self.mnemonic}.{name}: range {lo}..{hi} invalid for '{code}'")
            for label, v in (enum or {}).items():
                if label != label.upper() or not lo <= v <= hi:
                    raise ValueError(f"{self.mnemonic}.{name}: label {label}={v} invalid")
            if name in names:
                raise ValueError(f"{self.mnemonic}: argument {name} given twice")
            names.add(name)
        if self.check is not None and not self.check_doc:
            raise ValueError(f"{self.mnemonic}: a check needs its check_doc")

    @property
    def arg_octets(self) -> int:
        return struct.calcsize(">" + "".join(a[1] for a in self.args))


def _check_pktrate(v: Mapping[str, int]) -> str | None:
    if v["apid"] not in PERIODIC_APIDS:
        return (f"apid 0x{v['apid']:03X} is not a periodic packet (HK 0x010, RF 0x011, "
                "TIMECORR 0x014, MAG 0x020, ATT 0x021, SPEC 0x022, ENV 0x023)")
    return None


def _check_sensors(v: Mapping[str, int]) -> str | None:
    extra = v["mask"] & ~SENSOR_INSTRUMENT_MASK & 0xFFFF
    if extra:
        return (f"mask 0x{v['mask']:04X} sets bits 0x{extra:04X} outside the instrument bits "
                f"0x{SENSOR_INSTRUMENT_MASK:04X}")
    return None


def _check_rfscan(v: Mapping[str, int]) -> str | None:
    if v["first_ch"] > v["last_ch"]:
        return f"first_ch {v['first_ch']} > last_ch {v['last_ch']}"
    return None


SPECCFG_GAINS = (0, 1, 2, 3, 255)               # 1x, 3.7x, 16x, 64x, AGC


def _check_speccfg(v: Mapping[str, int]) -> str | None:
    if v["gain"] not in SPECCFG_GAINS:
        return f"gain {v['gain']}: 0..3 (1x, 3.7x, 16x, 64x) or 255 (AGC)"
    if not led_cur_allowed(v["led_cur"]):
        return (f"led_cur 0x{v['led_cur']:02X} drives a lamp above its rating "
                "(white code <= 3, IR <= 2, UV = 0)")
    return None


SPECMODE_N_RANGE = {0: (1, 60), 1: (2, 4), 2: (1, 16), 3: (1, 16), 4: (1, 60)}
SPEC_MODES = {"SURVEY": 0, "HDR": 1, "LOCKIN": 2, "FLUOR": 3, "BURST": 4}


def _check_specmode(v: Mapping[str, int]) -> str | None:
    lo, hi = SPECMODE_N_RANGE[v["mode"]]
    if not lo <= v["n"] <= hi:
        name = next(k for k, m in SPEC_MODES.items() if m == v["mode"])
        return f"{name} needs n = {lo}..{hi}, got {v['n']}"
    return None


SEQCTRL_DELETE = 3


def _check_seqctrl(v: Mapping[str, int]) -> str | None:
    if v["action"] != SEQCTRL_DELETE and v["slot"] != 0:
        return "slot must be 0 unless action is DELETE (3)"
    return None


def magcal_positive_definite(m11: int, m22: int, m33: int, m12: int, m13: int, m23: int) -> bool:
    """Sylvester's criterion on M = [[m11 m12 m13] [m12 m22 m23] [m13 m23 m33]] in exact
    integers, identical to the flight magcal_validate() (autonomy.cpp)."""
    a, b, c, d, e, f = m11, m22, m33, m12, m13, m23
    minor2 = a * b - d * d
    det = a * (b * c - f * f) - d * (d * c - f * e) + e * (d * f - b * e)
    return a > 0 and minor2 > 0 and det > 0


def _check_magcal(v: Mapping[str, int]) -> str | None:
    if not magcal_positive_definite(v["m11"], v["m22"], v["m33"], v["m12"], v["m13"], v["m23"]):
        return "soft-iron matrix M is not positive definite (not a calibration)"
    return None


_MODE_ENUM = {"SAFE": 1, "CRUISE": 2, "ENCOUNTER": 3, "ENC": 3, "TEST": 4}
_UNIT2 = {"OB": 0, "OUTBOARD": 0, "IB": 1, "INBOARD": 1}
_PKT_ENUM = {"HK": APID_HK, "RF": APID_RF, "TIMECORR": APID_TIMECORR, "MAG": APID_MAG,
             "ATT": APID_ATT, "SPEC": APID_SPEC, "ENV": APID_ENV}
_OFF = (-30000, 30000)              # MAGCAL offsets, 0.01 uT (|v| <= 300 uT)
_DIAG = (8192, 32767)               # MAGCAL diagonal, Q2.14 (0.5 .. 1.99994)
_ODIAG = (-8192, 8192)              # MAGCAL off-diagonal, Q2.14 (|v| <= 0.5)

COMMANDS: dict[str, CommandDef] = {c.mnemonic: c for c in [
    CommandDef("NOOP", 0x01, [], False, "No operation (link check, advances counters)"),
    CommandDef("MODE", 0x02, [("mode", "B", 1, 4, _MODE_ENUM)], False,
               "Set flight-software mode: SAFE | CRUISE | ENCOUNTER | TEST (leaves hibernation)"),
    CommandDef("TXPWR", 0x03, [("code", "B", 0, 3, None)], False,
               "TX power code 0..3: E22-400T33D 33/30/27/24 dBm (VERIFY T-RF-07), E22-400T37S "
               "37 dBm for every code, SX126x drive +22/+17/+14/+10 dBm + module PA gain"),
    CommandDef("AIRRATE", 0x04, [("code", "B", 0, 7, None), ("revert_s", "H", 60, 3600, None)], True,
               "Air-rate code (E22: 0-2 2.4k, 3 4.8k, 4 9.6k, 5 19.2k, 6 38.4k, 7 62.5k; SX126x: "
               "LoRa profile 0..7) after 2 more frames; reverts after revert_s without uplink"),
    CommandDef("FPERIOD", 0x05, [("ms", "H", 500, 60000, None)], False,
               "Minimum frame period (the flight also enforces its duty-cycle limit)"),
    CommandDef("PKTRATE", 0x06, [("apid", "H", APID_HK, APID_ENV, _PKT_ENUM),
                                 ("period_s", "H", 0, 3600, None)], False,
               "Generation period of a periodic packet, s (0 = off; stretched to the link "
               "capacity on board)",
               _check_pktrate, "apid must be HK 0x010, RF 0x011, TIMECORR 0x014, MAG 0x020, "
                               "ATT 0x021, SPEC 0x022 or ENV 0x023"),
    CommandDef("PLAYBACK", 0x07, [("start", "B", 0, 1, {"START": 1, "STOP": 0})], False,
               "Start/stop solid-state-recorder playback on VC2 (BUSY during an SSR export)"),
    CommandDef("SSRCLEAR", 0x08, [], True, "Erase the solid-state recorder and VC2"),
    CommandDef("PING", 0x09, [("tag", "I", 0, 0xFFFFFFFF, None)], False,
               "Echo tag in CMDVER (round-trip time measurement)"),
    CommandDef("SENSORS", 0x0A, [("mask", "H", 0, SENSOR_INSTRUMENT_MASK, None)], False,
               "Instrument enable mask: b0 Nicla Sense ME, b7 AS7265X, b8 RM3100 outboard, "
               "b9 RM3100 inboard",
               _check_sensors, "only the instrument bits 0x0381 (b0, b7, b8, b9) may be set"),
    CommandDef("RSTCNT", 0x0B, [], False, "Reset command/TC/CLTU counters and loop statistics"),
    CommandDef("REBOOT", 0x0C, [("magic", "H", 0xB007, 0xB007, None)], True,
               "Reboot the flight computer (magic 0xB007)"),
    CommandDef("MAGCC", 0x0D, [("cycle_count", "H", 50, 400, None)], False,
               "RM3100 cycle count, both units (sensitivity/noise vs. rate)"),
    CommandDef("CMDLOSS", 0x0E, [("seconds", "I", 0, 604800, None)], True,
               "Command-loss timer (0 disables) - SAFE and default radio on expiry"),
    CommandDef("FDIRMASK", 0x0F, [("mask", "H", 0, 0xFFFF, None)], True, "FDIR response enable mask"),
    CommandDef("TXINHIBIT", 0x10, [("seconds", "H", 10, 3600, None)], True,
               "Silence the transmitter for N seconds (auto re-enable)"),
    CommandDef("EVRLEVEL", 0x11, [("level", "B", 0, 4, {"DIAG": 0, "INFO": 1, "WARN": 2,
                                                         "ERROR": 3, "FATAL": 4})], False,
               "Minimum EVR severity downlinked"),
    CommandDef("TIMECORR", 0x12, [], False,
               "TIMECORR after the next reference frame (USLP: the next VC0 frame)"),
    CommandDef("RFSCAN", 0x13, [("first_ch", "B", 0, 83, None), ("last_ch", "B", 0, 83, None)], False,
               "Spacecraft RF survey: ambient noise per channel (downlink paused; BUSY if running)",
               _check_rfscan, "first_ch <= last_ch"),
    CommandDef("HIBERNATE", 0x14, [("beacon_s", "H", 60, 3600, None)], True,
               "Hibernation: receiver in low-power listen, one HK beacon every beacon_s (WAKE)"),
    CommandDef("CHANNEL", 0x15, [("ch", "B", 0, 83, None), ("revert_s", "H", 60, 3600, None)], True,
               "Change RF channel after 2 more frames; reverts after revert_s without uplink"),
    CommandDef("SPECCFG", 0x16, [("gain", "B", 0, 255, {"AUTO": 255, "AGC": 255}),
                                 ("cycles", "B", 1, 255, None), ("lamps", "B", 0, 7, None),
                                 ("led_cur", "B", 0, 11, None), ("ind", "B", 0, 4, None)], False,
               "AS7265X gain (0 1x, 1 3.7x, 2 16x, 3 64x, 255 AGC), integration (N + 1) x 2.78 ms, "
               "lamps b0 white b1 IR b2 UV, LED codes b1:0 white b3:2 IR b5:4 UV (12.5/25/50/"
               "100 mA), indicator LED 0 off 1..4 = 1/2/4/8 mA",
               _check_speccfg, "gain 0..3 or 255 (AGC); led_cur white code 0..3, IR 0..2, UV 0 "
                               "(= 0..11: lamp ratings, contract 26)"),
    CommandDef("CFDPPUT", 0x17, [("source", "B", 0, 2, {"SSR": 0, "EVRLOG": 1, "CADU": 2}),
                                 ("max_kib", "H", 0, 0xFFFF, None)], False,
               "CFDP class-1 file to the ground on VC3: 0 SSR contents, 1 EGSE event log, "
               "2 latest EGSE CADU archive; max_kib 0 = whole file (BUSY without SCLK partition)"),
    CommandDef("CFDPCANCEL", 0x18, [], False, "Cancel the CFDP transfer in progress (idempotent)"),
    CommandDef("RADIOOPT", 0x19, [("flags", "B", 0, 3, None)], False,
               "Radio options: b0 LBT/CAD before transmit, b1 Rx boosted gain (SX126x only)"),
    CommandDef("SPECMODE", 0x1A, [("mode", "B", 0, 4, SPEC_MODES), ("n", "B", 1, 60, None)], False,
               "Spectrometer mode: SURVEY, HDR (n exposures), LOCKIN / FLUOR (n on-off pairs), "
               "BURST (n seconds); takes effect at the next cycle",
               _check_specmode, "n: SURVEY 1..60 (unused), HDR 2..4, LOCKIN 1..16, FLUOR 1..16, "
                                "BURST 1..60"),
    CommandDef("SPECBASE", 0x1B, [("threshold_cdeg", "H", 0, 9000, None)], False,
               "Spectral-event threshold, 0.01 deg (0 = only reset the change-detection baseline)"),
    CommandDef("SPECDIAG", 0x1C, [("device", "B", 0, 2, {"AS72651": 0, "AS72652": 1, "AS72653": 2}),
                                  ("n_sub", "B", 0, 32, None)], False,
               "AS7265X diagnostic dump (versions, slaves, temperature, n_sub COEF words) as EVR "
               "0x0703 lines"),
    CommandDef("SEQLOAD", 0x1D, [("slot", "B", 0, 31, None), ("flags", "B", 0, 1, None),
                                 ("t_s", "I", 0, 0xFFFFFFFF, None), ("len", "B", 1, 12, None),
                                 *[(f"cmd_{i:02d}", "B", 0, 255, None) for i in range(12)]],
               False, "Store one command in the on-board sequence table (slot, b0 relative time, "
                      "time s, command length, opcode + arguments zero padded)", builder_only=True),
    CommandDef("SEQCTRL", 0x1E, [("action", "B", 0, 3, {"START": 0, "STOP": 1, "CLEAR": 2,
                                                         "CLEAR_ALL": 2, "DELETE": 3}),
                                 ("slot", "B", 0, 31, None)], False,
               "Stored sequence control: START (arm), STOP, CLEAR (all), DELETE slot",
               _check_seqctrl, "slot must be 0 unless action is DELETE (3)"),
    CommandDef("MAGBIST", 0x1F, [("unit", "B", 0, 2, {**_UNIT2, "BOTH": 2})], False,
               "RM3100 built-in self-test in the next PA-off window (result in HK mag_bist)"),
    CommandDef("MAGCAL", 0x20, [("unit", "B", 0, 1, _UNIT2),
                                ("off_x", "h", *_OFF, None), ("off_y", "h", *_OFF, None),
                                ("off_z", "h", *_OFF, None),
                                ("m11", "h", *_DIAG, None), ("m22", "h", *_DIAG, None),
                                ("m33", "h", *_DIAG, None),
                                ("m12", "h", *_ODIAG, None), ("m13", "h", *_ODIAG, None),
                                ("m23", "h", *_ODIAG, None)], False,
               "Onboard magnetometer calibration B = M (B_raw - off): offsets 0.01 uT, M in Q2.14; "
               "identity + zero offsets clears it (attitude path only, MAG stays raw)",
               _check_magcal, "M positive definite (exact integer Sylvester criterion, as the "
                              "flight magcal_validate)"),
]}

# COP-1 control commands handled by the ground FOP (Type-BC frames), not opcodes.
DIRECTIVES = {
    "UNLOCK": "COP-1 Unlock (clears FARM Lockout)",
    "SETVR": "COP-1 Set V(R) <n> (resynchronise sequence numbers)",
}

# Ground-station directives (executed by the GDS, never uplinked as such).
GROUND_DIRECTIVES = {
    "GSCAN": "GSCAN [first last] - ground RF survey with the station radio",
    "WAKE": "WAKE - wake a hibernating spacecraft (wake-up preamble + MODE SAFE)",
    "LINKRATE": "LINKRATE <code> - coordinated air-rate change, both ends",
    "LINKCHAN": "LINKCHAN <ch> - coordinated channel change, both ends ([radio] allowed_channels)",
    "LINKMGR": "LINKMGR OFF|ADVISE|AUTO - adaptive link manager",
    "RADIO": "RADIO - read back and show the ground radio configuration",
    **_SPECTRAL_DIRECTIVES,
    "TDM": "TDM WRITE - write the CCSDS Tracking Data Message of the current pass now",
    "AFC": "AFC RESET - reset the automatic frequency correction (SX126x)",
    "SDLS": "SDLS RESETREPLAY - forget the TM (epoch, sn) replay window (two-step confirmation)",
}


def checked_limits(name: str, value: Any, limits: Any) -> str:
    """'RED', 'YELLOW' or 'OK' for a value against (red_low, yellow_low, yellow_high,
    red_high); None limits or value -> 'OK'. `name` is kept for the call signature."""
    if limits is None or value is None:
        return "OK"
    rl, yl, yh, rh = limits
    if (rl is not None and value <= rl) or (rh is not None and value >= rh):
        return "RED"
    if (yl is not None and value <= yl) or (yh is not None and value >= yh):
        return "YELLOW"
    return "OK"
