"""VGQ-1 Telemetry & Command Dictionary (ground half).

Mirrors spacecraft/vgq1_flight/sketch/src/fsw/tlm_packets.h field-for-field and
spacecraft/.../fsw/commands.h opcode-for-opcode. ground/tests/test_cross_check.py
fails if they diverge.

Field tuple: (name, struct-code, scale, unit, description, limits)
  * struct-code  : Python struct format character (big-endian applied globally)
  * scale        : engineering value = raw * scale (None -> raw integer kept)
  * limits       : (red_low, yellow_low, yellow_high, red_high) or None
"""
from __future__ import annotations

from dataclasses import dataclass, field

NA = {"h": 0x7FFF, "H": 0xFFFF, "i": 0x7FFFFFFF}

MODES = {0: "BOOT", 1: "SAFE", 2: "CRUISE", 3: "ENCOUNTER", 4: "TEST"}
RESET_CAUSES = {0: "POWER-ON", 1: "PIN", 2: "SOFTWARE", 3: "WATCHDOG", 4: "BROWNOUT",
                255: "UNKNOWN"}
FARM_STATES = {1: "OPEN", 2: "WAIT", 3: "LOCKOUT"}
EVR_SEVERITY = {0: "DIAG", 1: "INFO", 2: "WARN", 3: "ERROR", 4: "FATAL"}
CMD_STAGES = {1: "ACCEPTED", 2: "EXECUTED", 3: "FAILED"}
CMD_ERRORS = {0: "OK", 1: "UNKNOWN_OPCODE", 2: "BAD_LENGTH", 3: "BAD_ARGUMENT",
              4: "NOT_ALLOWED_IN_MODE", 5: "HARDWARE_FAULT", 6: "BAD_MAGIC"}
AIR_RATES_BPS = {0: 2400, 1: 2400, 2: 2400, 3: 4800, 4: 9600, 5: 19200, 6: 38400, 7: 62500}  # E22-400T37S

FDIR_BITS = ["RADIO_CFG", "AUX_TIMEOUT", "PA_OVERTEMP", "AVI_OVERTEMP", "CMD_LOSS", "I2C_BUS",
             "SENSOR_LOST", "LOOP_OVERRUN", "VC_CONGEST", "LOW_BUS_V", "FARM_LOCKOUT",
             "TX_INHIBIT", "RATE_REVERT", "SDLS_AUTH", "HIBERNATE", "RADIO_FAULT"]
SENSOR_BITS = ["MPU9250", "AK8963", "RM3100_OB", "RM3100_IB", "MMC5603", "VEML7700", "BME690",
               "AS7265X", "AS7343", "NICLA_ENV", "NICLA_ME", "CSS", "TCA9548A", "PA_NTC",
               "BUS_MON"]

AS7265X_NM = [410, 435, 460, 485, 510, 535, 560, 585, 610, 645, 680, 705, 730, 760, 810, 860,
              900, 940]
AS7343_CH = ["F_450_FZ", "F_555_FY", "F_600_FXL", "NIR_855", "VIS_1", "FD_1", "F_425_F2",
             "F_475_F3", "F_515_F4", "F_640_F6", "VIS_2", "FD_2", "F_405_F1", "F_690_F7",
             "F_745_F8", "F_550_F5", "VIS_3", "FD_3"]


def _vec(prefix, code, scale, unit, desc, n=3, axes="xyz", limits=None):
    return [(f"{prefix}_{axes[i]}", code, scale, unit, f"{desc} {axes[i].upper()}", limits)
            for i in range(n)]


def _mag(prefix, desc):
    return (_vec(prefix + "_b", "i", 1e-3, "uT", desc + " field") +
            [(prefix + "_rms", "H", 1e-3, "uT", desc + " RMS fluctuation in interval", None)])


PACKETS: dict[int, dict] = {
    0x010: {"name": "HK", "desc": "Engineering housekeeping", "fields": [
        ("fsw_mode", "B", None, "", "Flight software mode", None),
        ("sclk_partition", "H", None, "", "SCLK partition (boot count)", None),
        ("uptime_s", "I", None, "s", "Time since MCU boot", None),
        ("reset_cause", "B", None, "", "Cause of last reset", None),
        ("fdir_flags", "H", None, "", "Fault protection flags", None),
        ("sensor_health", "H", None, "", "Sensor health bitmask", None),
        ("cmd_accepted", "H", None, "", "Commands accepted", None),
        ("cmd_rejected", "H", None, "", "Commands rejected", None),
        ("cmd_last_opcode", "B", None, "", "Last opcode executed", None),
        ("cmd_last_status", "B", None, "", "Last command error code", None),
        ("tc_frames_ok", "H", None, "", "TC frames passing FECF", None),
        ("tc_frames_bad", "H", None, "", "TC frames rejected", None),
        ("farm_state", "B", None, "", "FARM-1 state", None),
        ("farm_vr", "B", None, "", "FARM-1 V(R)", None),
        ("cmd_loss_timer_s", "I", None, "s", "Time since last valid TC", None),
        ("avionics_temp", "h", 0.01, "degC", "Avionics (MPU-9250 die) temperature", (-20, 0, 60, 75)),
        ("pa_temp", "h", 0.01, "degC", "PA heatsink temperature (NTC)", (-20, 0, 65, 80)),
        ("bus_voltage", "H", 0.001, "V", "Radio/PA supply voltage (INA226 or divider)", (10.5, 11.0, 13.5, 14.8)),
        ("bus_current", "h", 0.001, "A", "Radio/PA supply current (INA226)", (None, None, 2.0, 2.5)),
        ("loop_max_ms", "H", None, "ms", "Worst-case loop time this interval", (None, None, 250, 1000)),
        ("loop_overruns", "H", None, "", "Scheduler overruns", None),
        ("i2c_errors", "H", None, "", "I2C transaction errors", None),
        ("ssr_fill", "H", 0.1, "%", "Solid-state recorder fill", (None, None, 80, 95)),
        ("ssr_dropped", "H", None, "", "Packets dropped by SSR", None),
        ("vc0_backlog", "H", None, "B", "VC0 queued octets", (None, None, 600, 900)),
        ("vc1_backlog", "H", None, "B", "VC1 queued octets", (None, None, 600, 900)),
        ("vc2_backlog", "H", None, "B", "VC2 queued octets", None),
        ("sdls_enabled", "B", None, "", "Uplink authentication active (SDLS)", None),
        ("sdls_auth_fail", "H", None, "", "TC frames rejected by SDLS", (None, None, 1, 10)),
        ("sdls_last_sn", "I", None, "", "Last authenticated SDLS sequence number", None),
    ]},
    0x011: {"name": "RF", "desc": "Telecom subsystem (E22-400T37S)", "fields": [
        ("air_rate_code", "B", None, "", "E22 air data rate code", None),
        ("tx_power_code", "B", None, "", "E22 TX power code (T37S: all codes = 37 dBm)", None),
        ("channel", "B", None, "", "E22 channel (f = 410.125 + CH MHz)", None),
        ("e22_cfg_ok", "B", None, "", "Radio configuration verified", None),
        ("frames_sent", "I", None, "", "CADUs transmitted", None),
        ("oid_frames_sent", "I", None, "", "Idle (OID) frames transmitted", None),
        ("tx_airtime_ms_total", "I", None, "ms", "Cumulative air time", None),
        ("tx_duty", "H", 0.1, "%", "Transmit duty cycle (60 s)", (None, None, 55, 70)),
        ("frame_period_ms", "H", None, "ms", "Frame cadence", None),
        ("aux_timeouts", "H", None, "", "Radio AUX timeouts", None),
        ("uplink_rssi", "h", 1.0, "dBm", "Uplink RSSI (last TC packet)", None),
        ("sc_noise_floor", "h", 1.0, "dBm", "Spacecraft receiver noise floor", None),
        ("cltu_ok", "H", None, "", "CLTUs decoded", None),
        ("cltu_bad", "H", None, "", "CLTUs rejected", None),
        ("bch_corrected", "H", None, "", "BCH codeblocks corrected", None),
        ("rx_bytes", "I", None, "B", "Octets received from radio", None),
        ("mcfc", "B", None, "", "Master channel frame count", None),
        ("last_airtime", "B", 0.1, "s", "Air time of last frame", None),
    ]},
    0x013: {"name": "CMDVER", "desc": "Command verification", "fields": [
        ("tc_seq", "B", None, "", "N(S) of TC frame (255 = bypass)", None),
        ("opcode", "B", None, "", "Opcode", None),
        ("stage", "B", None, "", "Verification stage", None),
        ("error_code", "B", None, "", "Error code", None),
        ("ground_tag", "I", None, "", "Echoed ground tag (PING)", None),
    ]},
    0x014: {"name": "TIMECORR", "desc": "SCLK correlation sample", "fields": [
        ("ref_mcfc", "B", None, "", "Reference frame MCFC", None),
        ("sclk_coarse", "I", None, "s", "SCLK coarse at reference frame", None),
        ("sclk_fine", "H", None, "", "SCLK fine (1/65536 s)", None),
        ("air_rate_code", "B", None, "", "Air rate code", None),
        ("ref_airtime_ms", "H", None, "ms", "Reference frame air time", None),
    ]},
    0x020: {"name": "MAG", "desc": "Magnetometer science", "fields": [
        ("nsamples", "B", None, "", "Samples averaged", None),
        ("flags", "B", None, "", "b0 sample taken with PA keyed, b1 OB ok, b2 IB ok, b3 body ok", None),
        *_mag("ob", "Outboard RM3100"),
        *_mag("ib", "Inboard RM3100"),
        *_mag("body", "Body MMC5603"),
        ("body_temp", "h", 0.01, "degC", "MMC5603 temperature", None),
    ]},
    0x021: {"name": "ATT", "desc": "Attitude determination", "fields": [
        ("flags", "B", None, "", "b0 IMU b1 ARU b2 CSS b3 TRIAD b4 AK8963", None),
        *_vec("acc", "h", 1e-3, "g", "Acceleration"),
        *_vec("gyro", "h", 0.01, "deg/s", "Angular rate"),
        *_vec("ak", "h", 0.1, "uT", "AK8963 field"),
        *_vec("q_aru", "h", 1 / 16384, "", "ARU quaternion", n=4, axes="wxyz"),
        ("aru_accuracy", "B", None, "", "BHI260AP accuracy (0-3)", None),
        *_vec("css", "H", None, "cts", "Coarse sun sensor", n=4, axes="0123"),
        *_vec("sun", "h", 1 / 32000, "", "Sun vector (body)"),
        *_vec("q_triad", "h", 1 / 16384, "", "TRIAD quaternion", n=4, axes="wxyz"),
    ]},
    0x022: {"name": "SPEC", "desc": "Spectrometers", "fields": [
        ("flags", "B", None, "", "b0 AS7265x b1 AS7343", None),
        ("as7265x_gain", "B", None, "", "AS7265x gain code", None),
        ("as7265x_int_cycles", "B", None, "", "AS7265x integration cycles (2.8 ms)", None),
        *[(f"as7265x_{nm}nm", "f", 1.0, "uW/cm2", f"AS7265x {nm} nm", None) for nm in AS7265X_NM],
        ("as7343_gain", "B", None, "", "AS7343 AGAIN code", None),
        *[(f"as7343_{c}", "H", None, "cts", f"AS7343 {c}", None) for c in AS7343_CH],
        ("as7265x_temp", "b", 1.0, "degC", "AS7265x temperature", None),
    ]},
    0x023: {"name": "ENV", "desc": "Environment", "fields": [
        ("flags", "H", None, "", "b0 BME690 b1 NiclaEnv b2 NiclaME b3 VEML7700", None),
        ("bme_t", "h", 0.01, "degC", "BME690 temperature", None),
        ("bme_p", "I", 0.01, "hPa", "BME690 pressure", None),
        ("bme_rh", "H", 0.01, "%", "BME690 humidity", None),
        ("bme_gas", "I", 1.0, "ohm", "BME690 gas resistance", None),
        ("nenv_t", "h", 0.01, "degC", "Nicla Sense Env temperature (HS4001)", None),
        ("nenv_rh", "H", 0.01, "%", "Nicla Sense Env humidity", None),
        ("nenv_iaq", "f", 1.0, "", "ZMOD4410 IAQ", None),
        ("nenv_tvoc", "f", 1.0, "mg/m3", "ZMOD4410 TVOC", None),
        ("nenv_eco2", "f", 1.0, "ppm", "ZMOD4410 eCO2", None),
        ("nenv_aqi", "H", None, "", "ZMOD4510 outdoor AQI", None),
        ("nenv_no2", "f", 1.0, "ppb", "ZMOD4510 NO2", None),
        ("nenv_o3", "f", 1.0, "ppb", "ZMOD4510 O3", None),
        ("nme_p", "f", 1.0, "hPa", "Nicla Sense ME pressure (BMP390)", None),
        ("nme_t", "h", 0.01, "degC", "Nicla Sense ME temperature", None),
        ("nme_rh", "H", 0.01, "%", "Nicla Sense ME humidity", None),
        ("nme_iaq", "H", None, "", "BSEC IAQ", None),
        ("nme_co2eq", "I", None, "ppm", "BSEC CO2 equivalent", None),
        ("nme_bvoc", "f", 1.0, "ppm", "BSEC breath-VOC equivalent", None),
        ("nme_accuracy", "B", None, "", "BSEC accuracy", None),
        ("veml_lux", "f", 1.0, "lx", "VEML7700 illuminance", None),
    ]},
}

PACKETS_BY_NAME = {v["name"]: k for k, v in PACKETS.items()}
APID_EVR = 0x012
APID_RFSCAN = 0x015          # variable length: first_ch u8, count u8, duration_ms u16, noise i8[count]


# --------------------------------------------------------------------------------
# Command dictionary (opcodes MUST match spacecraft/.../fsw/commands.h)
# --------------------------------------------------------------------------------
@dataclass
class CommandDef:
    mnemonic: str
    opcode: int
    args: list = field(default_factory=list)   # [(name, struct_code, min, max, enum|None)]
    hazardous: bool = False
    desc: str = ""


_MODE_ENUM = {"SAFE": 1, "CRUISE": 2, "ENCOUNTER": 3, "ENC": 3, "TEST": 4}

COMMANDS: dict[str, CommandDef] = {c.mnemonic: c for c in [
    CommandDef("NOOP", 0x01, [], False, "No operation (link check, advances counters)"),
    CommandDef("MODE", 0x02, [("mode", "B", 1, 4, _MODE_ENUM)], False,
               "Set flight-software mode: SAFE | CRUISE | ENCOUNTER | TEST"),
    CommandDef("TXPWR", 0x03, [("code", "B", 0, 3, None)], False,
               "Set E22 TX power code (NO effect on the E22-400T37S: every code is 37 dBm)"),
    CommandDef("AIRRATE", 0x04, [("code", "B", 0, 7, None), ("revert_s", "H", 60, 3600, None)], True,
               "Change air data rate (T37S: 0-2 = 2.4k, 3 = 4.8k, 4 = 9.6k ... 7 = 62.5k); "
               "spacecraft reverts after revert_s unless a TC is received"),
    CommandDef("FPERIOD", 0x05, [("ms", "H", 500, 60000, None)], False,
               "Requested frame period (flight enforces the duty-cycle limit)"),
    CommandDef("PKTRATE", 0x06, [("apid", "H", 0x010, 0x023, None), ("period_s", "H", 0, 3600, None)],
               False, "Set generation period of a telemetry packet (0 = off)"),
    CommandDef("PLAYBACK", 0x07, [("start", "B", 0, 1, {"START": 1, "STOP": 0})], False,
               "Start/stop solid-state-recorder playback on VC2"),
    CommandDef("SSRCLEAR", 0x08, [], True, "Erase the solid-state recorder"),
    CommandDef("PING", 0x09, [("tag", "I", 0, 0xFFFFFFFF, None)], False,
               "Echo tag in CMDVER (round-trip time measurement)"),
    CommandDef("SENSORS", 0x0A, [("mask", "H", 0, 0xFFFF, None)], False, "Sensor enable mask"),
    CommandDef("RSTCNT", 0x0B, [], False, "Reset HK/RF counters"),
    CommandDef("REBOOT", 0x0C, [("magic", "H", 0xB007, 0xB007, None)], True,
               "Reboot the flight computer (magic 0xB007)"),
    CommandDef("MAGCC", 0x0D, [("cycle_count", "H", 50, 400, None)], False,
               "RM3100 cycle count (sensitivity/noise vs. rate)"),
    CommandDef("CMDLOSS", 0x0E, [("seconds", "I", 0, 604800, None)], True,
               "Command-loss timer (0 disables) - enters SAFE on expiry"),
    CommandDef("FDIRMASK", 0x0F, [("mask", "H", 0, 0xFFFF, None)], True, "FDIR response enable mask"),
    CommandDef("TXINHIBIT", 0x10, [("seconds", "H", 10, 3600, None)], True,
               "Silence the transmitter for N seconds (auto re-enable)"),
    CommandDef("EVRLEVEL", 0x11, [("level", "B", 0, 4, None)], False, "Minimum EVR severity downlinked"),
    CommandDef("TIMECORR", 0x12, [], False, "Generate a TIMECORR packet now"),
    CommandDef("RFSCAN", 0x13, [("first_ch", "B", 0, 83, None), ("last_ch", "B", 0, 83, None)], False,
               "Spacecraft RF survey: ambient noise on each E22 channel (downlink paused)"),
    CommandDef("HIBERNATE", 0x14, [("beacon_s", "H", 60, 3600, None)], True,
               "Wake-on-Radio hibernation, one HK beacon every beacon_s (wake with WAKE)"),
    CommandDef("CHANNEL", 0x15, [("ch", "B", 0, 83, None), ("revert_s", "H", 60, 3600, None)], True,
               "Change RF channel; reverts after revert_s unless a TC arrives on the new channel"),
]}

# COP-1 control commands handled by the ground FOP (Type-BC frames), not opcodes.
DIRECTIVES = {
    "UNLOCK": "COP-1 Unlock (clears FARM Lockout)",
    "SETVR": "COP-1 Set V(R) <n> (resynchronise sequence numbers)",
}

# Ground-station directives (executed by the GDS / Radio Control Unit, not uplinked
# as such). Those touching the ground radio need the RCU (VENTUNO Q MCU driving M0/M1).
GROUND_DIRECTIVES = {
    "GSCAN": "GSCAN [first last] - ground RF survey with the station E22 (needs RCU)",
    "WAKE": "WAKE - wake a hibernating spacecraft (WOR-transmitter MODE SAFE, needs RCU)",
    "LINKRATE": "LINKRATE <code> - coordinated air-rate change, both ends (needs RCU)",
    "LINKCHAN": "LINKCHAN <ch> - coordinated channel change, both ends (needs RCU)",
    "LINKMGR": "LINKMGR OFF|ADVISE|AUTO - adaptive link manager",
    "RADIO": "RADIO - read back and show the ground E22 configuration (needs RCU)",
}


def checked_limits(name: str, value, limits):
    """Returns 'RED', 'YELLOW' or 'OK' for a value against (rl, yl, yh, rh)."""
    if limits is None or value is None:
        return "OK"
    rl, yl, yh, rh = limits
    if (rl is not None and value <= rl) or (rh is not None and value >= rh):
        return "RED"
    if (yl is not None and value <= yl) or (yh is not None and value >= yh):
        return "YELLOW"
    return "OK"
