"""Ground radio model registry, LoRa link mathematics and link-quality helpers.

Single ground-side source of truth for everything that depends on WHICH radio is
fitted (contract v3 sections 2, 9, 10 and 14):

  Model         Interface (both ends)              "Rate code" means        Ground backend
  E22-400T33D   UART transparent, SX1262, 2 W      REG0 air-rate code 0..7  radio/e22.py (DEFAULT)
  E22-400T37S   UART transparent, 5 W (preserved)  REG0 air-rate code 0..7  radio/e22.py
  SX126x        SPI via RadioLib (E22-400M33S ref) LoRa profile 0..7        radio/sx126x.py

Channel grid of every model: f = 410.125 MHz + CH x 1 MHz, CH 0..83 (E22 manuals,
REG2). The SX126x is programmed onto the same grid.

Receiver sensitivity per (model, rate code); every value carries its SOURCE:
  "measured"   station.toml [radio.sensitivity_measured] "<MODEL>:<code>" = dBm, produced
               by test T-RF-03 (tools/link_budget.py sensitivity); overrides the rest
  "datasheet"  EBYTE: T33D -128 dBm typ (manual v1.05 2.2: -126 / -128 / -130),
               T37S -126 dBm typ (manual v1.5 2.2), both at 2.4 kbit/s;
               Semtech SX1261/2 DS rev 2.2 Table 3-8 (LoRa, Rx boosted gain)
  "estimated"  E22 rates above 2.4k: S(2.4k) + 10 log10(Rb / 2400 bit/s) - EBYTE
               publishes neither the sensitivity nor the LoRa parameters behind them
  "computed"   LoRa profiles absent from Table 3-8:
               S = -174 dBm/Hz + 10 log10(BW) + NF + SNRmin(SF)

E22 link-quality quantities (rxstats.py): RSSI and the ambient-noise register are
MEASURED; SNR_est = RSSI - noise, C/N0, Eb/N0 and margin are ESTIMATES and are
labelled "est." everywhere. The SX126x reports a true per-packet SNR (SnrPkt / 4)
and a LoRa frequency-error estimate, from which a Doppler / range-rate estimate
is derived after removing the calibrated static crystal offset.

Pure Python, standard library only: imported by the GDS and tools/link_budget.py.
"""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

# ----------------------------------------------------------------- constants
C_M_S = 299_792_458.0                  # speed of light in vacuum, m/s
CADU_OCTETS = 236                      # one CADU = one radio packet (ASM 4 + RS codeblock 232)

CHANNEL_BASE_MHZ = 410.125             # E22 manuals 7.2, REG2: f = 410.125 + CH x 1 MHz
CHANNEL_STEP_MHZ = 1.0
CHANNEL_MAX = 83
RATE_CODES = range(8)                  # E22 air-rate codes / SX126x LoRa profiles
POWER_CODES = range(4)

E22_T33D, E22_T37S, SX126X = "E22-400T33D", "E22-400T37S", "SX126x"
DEFAULT_MODEL = E22_T33D
MODEL_IDS = {1: E22_T33D, 2: E22_T37S, 3: SX126X}      # RF packet radio_model (contract 3.2)

SOURCE_MEASURED, SOURCE_DATASHEET = "measured", "datasheet"
SOURCE_ESTIMATED, SOURCE_COMPUTED = "estimated", "computed"
MEASURED_RANGE_DBM = (-160.0, -60.0)   # plausibility window for T-RF-03 results

# ------------------------------------------------- E22 UART models (contract 2.1)
# REG0[2:0]: codes 0, 1 and 2 are ALL 2.4 kbit/s on both modules (T33D manual v1.05
# 7.2, T37S manual v1.5 7.2) - there is no 0.3k / 1.2k setting.
E22_AIR_RATE_BPS = {0: 2400, 1: 2400, 2: 2400, 3: 4800, 4: 9600, 5: 19200, 6: 38400, 7: 62500}
E22_RATE_LABELS = {0: "2.4k", 1: "2.4k", 2: "2.4k", 3: "4.8k", 4: "9.6k", 5: "19.2k",
                   6: "38.4k", 7: "62.5k"}
E22_MIN_EFFECTIVE_RATE_CODE = 2        # 0 and 1 are aliases of 2
E22_SENS_2K4_DBM = {E22_T33D: -128.0, E22_T37S: -126.0}
# T33D REG1[1:0]. Code 0 = 33 dBm is consistent with the factory default
# (C0 00 00 62 00 17, "33dbm") and "max transmit power 33 dBm"; the v1.05 manual's
# REG1 table itself was copied from the T30D (30/27/24/21 dBm), so codes 1..3 are the
# 3 dB ladder below 33 dBm - VERIFY by measurement (docs/08 T-RF-07).
E22_T33D_POWER_DBM = {0: 33.0, 1: 30.0, 2: 27.0, 3: 24.0}
E22_T37S_POWER_DBM = {0: 37.0, 1: 37.0, 2: 37.0, 3: 37.0}   # "no power levels" (manual v1.5)
# EBYTE does not publish the LoRa parameters behind each air-rate code, so E22 air
# time is an estimate: payload bits / air rate x 1.3 (the flight software's model).
E22_AIRTIME_FACTOR = 1.3
E22_UART_BAUD = 9600                   # the flight drives the module UART at 9600 8N1 (10 bit/octet)

# -------------------------------------------------- SX126x LoRa (contract 2.2, 14)
LORA_PREAMBLE_SYMBOLS = 12             # SX1261/2 DS 6.1.3: 12-symbol default; both ends
LORA_SYNC_WORD = 0x12                  # private network (register value 0x1424)
LDRO_SYMBOL_S = 16.38e-3               # DS 6.1.1.4: LowDataRateOptimize when Tsym >= 16.38 ms
THERMAL_NOISE_DBM_HZ = -174.0          # kT at 290 K
SX126X_NF_DB = 6.0                     # ASSUMED receiver NF with Rx boosted gain (VERIFY: T-RF-03)
# DS Table 3-8, BW 125 kHz SF12: -137 dBm (boosted) vs -133 dBm (power-saving gain).
SX126X_POWER_SAVING_PENALTY_DB = 4.0
# LoRa demodulator SNR limits per spreading factor (Semtech).
LORA_SNR_MIN_DB = {7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
# SX1261/2 DS rev 2.2 Table 3-8, LoRa sensitivity with Rx boosted gain, split RF
# paths, RF-switch insertion loss excluded. Key: (SF, BW in kHz rounded to 0.1).
SX1262_SENSITIVITY_DBM = {(7, 10.4): -134.0, (12, 10.4): -148.0, (7, 125.0): -124.0,
                          (12, 125.0): -137.0, (7, 250.0): -121.0, (12, 250.0): -134.0,
                          (7, 500.0): -117.0, (12, 500.0): -129.0}
SX1262_SENSITIVITY_POWER_SAVING_DBM = {(12, 125.0): -133.0}    # same table, power-saving gain
# Chip drive per power code; radiated = drive + module PA gain, capped at the
# module maximum. PA gain 11 dB for the reference E22-400M33S - VERIFY against the
# module datasheet (the module's maximum is 33 dBm).
SX126X_DRIVE_DBM = {0: 22.0, 1: 17.0, 2: 14.0, 3: 10.0}
SX126X_PA_GAIN_DB = 11.0
SX126X_MODULE_MAX_DBM = 33.0
SX126X_DEFAULT_RATE_CODE = 1           # SF12 / 62.5 kHz / 4/5
# FERR_L (DS Table 3-8): +-25 % of BW for all bandwidths and, the tighter applying,
# +-50 ppm at SF12, +-100 ppm at SF11, +-200 ppm at SF10.
LORA_FERR_BW_FRACTION = 0.25
LORA_FERR_PPM = {12: 50.0, 11: 100.0, 10: 200.0}
FREQ_ERR_WARN_FRACTION = 0.5           # contract 14: WARN above 50 %, ALARM above 80 %
FREQ_ERR_ALARM_FRACTION = 0.8


@dataclass(frozen=True)
class LoRaProfile:
    """One LoRa modulation setting. cr = 1..4 selects coding rate 4/(4 + cr)."""
    sf: int
    bw_hz: float
    cr: int = 1

    @property
    def symbol_s(self) -> float:
        return (1 << self.sf) / self.bw_hz

    @property
    def ldro(self) -> bool:
        return self.symbol_s >= LDRO_SYMBOL_S

    @property
    def bit_rate_bps(self) -> float:
        """Nominal LoRa bit rate SF x BW / 2^SF x 4 / (4 + CR)."""
        return self.sf * self.bw_hz / (1 << self.sf) * 4.0 / (4 + self.cr)

    @property
    def label(self) -> str:
        return f"SF{self.sf} BW{self.bw_hz / 1000:g} CR4/{4 + self.cr}"


SX126X_PROFILES = {0: LoRaProfile(12, 31_250.0), 1: LoRaProfile(12, 62_500.0),
                   2: LoRaProfile(12, 125_000.0), 3: LoRaProfile(11, 125_000.0),
                   4: LoRaProfile(10, 125_000.0), 5: LoRaProfile(9, 125_000.0),
                   6: LoRaProfile(8, 125_000.0), 7: LoRaProfile(7, 125_000.0)}


@dataclass(frozen=True)
class RadioModel:
    """Static capabilities of one radio model."""
    name: str
    model_id: int                       # RF packet radio_model
    interface: str                      # "UART" (EBYTE firmware) or "SPI" (RadioLib)
    packet_mode: bool                   # True: whole packets with per-packet RSSI/SNR/freq error
    has_fault_log: bool                 # E22 REG1 b2 abnormal-status reports FF FF FF 0x
    has_noise_register: bool            # E22 C0 C1 C2 C3 ambient-noise query
    min_effective_rate_code: int        # lowest code that is not an alias of another
    max_tx_dbm: float
    description: str


MODELS = {
    E22_T33D: RadioModel(E22_T33D, 1, "UART", False, False, True, E22_MIN_EFFECTIVE_RATE_CODE, 33.0,
                         "EBYTE E22-400T33D, SX1262 + PA/LNA, 2 W, 3.3-5.5 V (>= 4.7 V full power)"),
    E22_T37S: RadioModel(E22_T37S, 2, "UART", False, True, True, E22_MIN_EFFECTIVE_RATE_CODE, 37.0,
                         "EBYTE E22-400T37S on the E22-400TBH-02 board, 5 W (preserved option)"),
    SX126X: RadioModel(SX126X, 3, "SPI", True, False, False, 0, SX126X_MODULE_MAX_DBM,
                       "SX1268/SX1262 over SPI via RadioLib (reference module EBYTE E22-400M33S)"),
}


# ------------------------------------------------------------------ helpers
def _int_in(value: object, lo: int, hi: int, what: str) -> int:
    """Range-checked integer (bool is rejected: True is not a channel number)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be an integer, got {value!r}")
    if not lo <= value <= hi:
        raise ValueError(f"{what} {value} outside {lo}..{hi}")
    return value


def _float(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def dbm_to_mw(d: float) -> float:
    return 10 ** (d / 10.0)


def channel_freq_mhz(channel: int) -> float:
    return CHANNEL_BASE_MHZ + CHANNEL_STEP_MHZ * _int_in(channel, 0, CHANNEL_MAX, "channel")


def get_model(name: str | RadioModel | None = None) -> RadioModel:
    """Model by name (case-insensitive); None or "" selects DEFAULT_MODEL."""
    if isinstance(name, RadioModel):
        return name
    if name is None or name == "":
        return MODELS[DEFAULT_MODEL]
    if not isinstance(name, str):
        raise ValueError(f"radio model must be a string, got {name!r}")
    for key, model in MODELS.items():
        if key.lower() == name.strip().lower():
            return model
    raise ValueError(f"unknown radio model {name!r}; valid: {', '.join(MODELS)}")


def model_from_id(model_id: int) -> RadioModel:
    """Model from the RF packet radio_model field (1, 2, 3)."""
    name = MODEL_IDS.get(model_id)
    if name is None:
        raise ValueError(f"unknown radio_model id {model_id!r}")
    return MODELS[name]


# --------------------------------------------------------------- LoRa maths
def lora_airtime_s(payload_octets: int, sf: int, bw_hz: float, cr: int = 1,
                   preamble: int = LORA_PREAMBLE_SYMBOLS, explicit_header: bool = True,
                   crc: bool = True, ldro: bool | None = None) -> float:
    """LoRa time on air (Semtech; SX1261/2 DS 6.1.4, valid for SF7..SF12):
        Tsym = 2^SF / BW
        n_payload = 8 + max(ceil((8 PL - 4 SF + 28 + 16 CRC - 20 IH) / (4 (SF - 2 DE))) (CR + 4), 0)
        T = (n_preamble + 4.25 + n_payload) Tsym
    IH = 1 for implicit header, DE = 1 with LowDataRateOptimize (default: Tsym >= 16.38 ms).
    Integer arithmetic for the symbol count, so the result is exact to float precision."""
    pl = _int_in(payload_octets, 0, 255, "payload length")
    _int_in(sf, 7, 12, "spreading factor")
    _int_in(cr, 1, 4, "coding rate index")
    _int_in(preamble, 6, 65535, "preamble length")
    bw = _float(bw_hz, "bandwidth")
    if bw <= 0:
        raise ValueError("bandwidth must be positive")
    tsym = (1 << sf) / bw
    de = int(tsym >= LDRO_SYMBOL_S if ldro is None else bool(ldro))
    ih = 0 if explicit_header else 1
    num = 8 * pl - 4 * sf + 28 + (16 if crc else 0) - 20 * ih
    den = 4 * (sf - 2 * de)
    blocks = -(-num // den) if num > 0 else 0              # ceil for positive, 0 otherwise
    n_payload = 8 + blocks * (cr + 4)
    return (preamble + 4.25 + n_payload) * tsym


def profile_airtime_s(code: int, payload_octets: int = CADU_OCTETS) -> float:
    p = SX126X_PROFILES[_int_in(code, 0, 7, "SX126x profile")]
    return lora_airtime_s(payload_octets, p.sf, p.bw_hz, p.cr)


def lora_sensitivity_computed_dbm(sf: int, bw_hz: float, nf_db: float = SX126X_NF_DB) -> float:
    """-174 + 10 log10(BW) + NF + SNRmin(SF), rounded to 0.1 dB."""
    _int_in(sf, 7, 12, "spreading factor")
    return round(THERMAL_NOISE_DBM_HZ + 10 * math.log10(_float(bw_hz, "bandwidth")) + nf_db
                 + LORA_SNR_MIN_DB[sf], 1)


def lora_sensitivity_dbm(sf: int, bw_hz: float, rx_boost: bool = True) -> tuple[float, str]:
    """Datasheet value where DS Table 3-8 lists (SF, BW), else the computed one. With
    power-saving gain the 4 dB penalty is extrapolated from the single Table 3-8 pair
    (BW 125 kHz SF12), so those values are labelled "estimated" (as the flight does)."""
    key = (sf, round(bw_hz / 1000.0, 1))
    table = SX1262_SENSITIVITY_DBM if rx_boost else SX1262_SENSITIVITY_POWER_SAVING_DBM
    if key in table:
        return table[key], SOURCE_DATASHEET
    if rx_boost:
        return lora_sensitivity_computed_dbm(sf, bw_hz), SOURCE_COMPUTED
    return lora_sensitivity_computed_dbm(sf, bw_hz, SX126X_NF_DB + SX126X_POWER_SAVING_PENALTY_DB), SOURCE_ESTIMATED


def lora_max_freq_offset_hz(sf: int, bw_hz: float, freq_hz: float) -> float:
    """Largest TX-RX offset tolerated without sensitivity loss (DS Table 3-8 FERR_L):
    +-25 % of BW, and for SF10..12 also the ppm limit - the tighter one applies."""
    _int_in(sf, 5, 12, "spreading factor")
    limit = LORA_FERR_BW_FRACTION * _float(bw_hz, "bandwidth")
    ppm = LORA_FERR_PPM.get(sf)
    if ppm is not None:
        limit = min(limit, ppm * 1e-6 * _float(freq_hz, "frequency"))
    return limit


def freq_offset_state(freq_err_hz: float, limit_hz: float) -> str:
    """'OK', 'WARN' (> 50 % of the tolerated offset) or 'ALARM' (> 80 %)."""
    if limit_hz <= 0:
        raise ValueError("limit must be positive")
    r = abs(freq_err_hz) / limit_hz
    return "ALARM" if r > FREQ_ERR_ALARM_FRACTION else "WARN" if r > FREQ_ERR_WARN_FRACTION else "OK"


def doppler(freq_err_hz: float, offset_cal_hz: float, freq_hz: float) -> dict:
    """Doppler estimate = raw frequency error - calibrated static crystal offset;
    range rate = -c x Doppler / f (positive = receding). The sign convention of the
    SX126x estimator is VERIFY (docs/08: offset a signal generator by +1 kHz)."""
    f = _float(freq_hz, "frequency")
    if f <= 0:
        raise ValueError("frequency must be positive")
    d = _float(freq_err_hz, "frequency error") - _float(offset_cal_hz, "offset calibration")
    return {"offset_cal_hz": float(offset_cal_hz), "doppler_hz": d, "range_rate_m_s": -C_M_S * d / f}


# --------------------------------------------------------- per-model tables
def rate_bps(model: str | RadioModel, code: int) -> float:
    m = get_model(model)
    _int_in(code, 0, 7, "rate code")
    return SX126X_PROFILES[code].bit_rate_bps if m.packet_mode else float(E22_AIR_RATE_BPS[code])


def rate_label(model: str | RadioModel, code: int) -> str:
    m = get_model(model)
    _int_in(code, 0, 7, "rate code")
    return SX126X_PROFILES[code].label if m.packet_mode else E22_RATE_LABELS[code]


def power_dbm(model: str | RadioModel, code: int, pa_gain_db: float = SX126X_PA_GAIN_DB) -> float:
    """Nominal radiated power (module output) for a power code."""
    m = get_model(model)
    _int_in(code, 0, 3, "power code")
    if m.name == E22_T33D:
        return E22_T33D_POWER_DBM[code]
    if m.name == E22_T37S:
        return E22_T37S_POWER_DBM[code]
    return min(SX126X_DRIVE_DBM[code] + _float(pa_gain_db, "PA gain"), SX126X_MODULE_MAX_DBM)


def airtime_s(model: str | RadioModel, code: int, payload_octets: int = CADU_OCTETS) -> tuple[float, str]:
    """(air time of one packet in s, source): exact LoRa formula for the SX126x,
    the flight software's estimate for the E22 UART models."""
    m = get_model(model)
    if m.packet_mode:
        return profile_airtime_s(code, payload_octets), SOURCE_COMPUTED
    _int_in(payload_octets, 0, 1000, "payload length")
    return payload_octets * 8 * E22_AIRTIME_FACTOR / rate_bps(m, code), SOURCE_ESTIMATED


def lora_airtime_us(payload_octets: int, sf: int, bw_hz: float, cr: int = 1,
                    preamble: int = LORA_PREAMBLE_SYMBOLS) -> int:
    """LoRa time on air in whole microseconds, rounded UP, computed in integers exactly as
    the flight lora_airtime_us() (radio.cpp: quarter symbols, explicit header, CRC on, LDRO
    when Tsym >= 16.38 ms). The bandwidth must be a whole number of hertz."""
    pl = _int_in(payload_octets, 0, 255, "payload length")
    _int_in(sf, 7, 12, "spreading factor")
    _int_in(cr, 1, 4, "coding rate index")
    _int_in(preamble, 6, 65535, "preamble length")
    bw = _float(bw_hz, "bandwidth")
    if bw <= 0 or not bw.is_integer():
        raise ValueError(f"bandwidth {bw_hz!r} must be a positive whole number of Hz")
    bw_i = int(bw)
    de = 2 if (1 << sf) / bw >= LDRO_SYMBOL_S else 0
    num = 8 * pl - 4 * sf + 28 + 16
    den = 4 * (sf - de)
    blocks = -(-num // den) if num > 0 else 0
    n_payload = 8 + blocks * (cr + 4)
    quarter_symbols = 4 * preamble + 17 + 4 * n_payload
    return -(-(quarter_symbols * (1_000_000 << sf)) // (4 * bw_i))


def busy_s(model: str | RadioModel, code: int, payload_octets: int = CADU_OCTETS) -> float:
    """Transmitter busy time of one packet as the FLIGHT computes it (radio_airtime_ms(),
    whole milliseconds), in seconds - the time base of the flight frame period and of RF
    last_airtime:
      E22 models  UART transfer at 9600 8N1 + estimated air time, both in integer ms:
                  n x 10 x 1000 // 9600 + n x 8 x 1300 // rate  (236 octets at 2.4k: 245 + 1022 = 1267 ms)
      SX126x      LoRa time on air, microseconds rounded up, then milliseconds rounded up
                  (profile 0: 34636 ms, profile 1: 17318 ms)."""
    m = get_model(model)
    _int_in(code, 0, 7, "rate code")
    n = _int_in(payload_octets, 0, 255, "payload length")
    if m.packet_mode:
        p = SX126X_PROFILES[code]
        ms = -(-lora_airtime_us(n, p.sf, p.bw_hz, p.cr) // 1000)
    else:
        bps = E22_AIR_RATE_BPS[code]
        ms = n * 10 * 1000 // E22_UART_BAUD + n * 8 * round(E22_AIRTIME_FACTOR * 1000) // bps
    return ms / 1000.0


def parse_measured(table: Mapping[str, object] | None) -> dict[tuple[str, int], float]:
    """Validates station.toml [radio.sensitivity_measured]: "<MODEL>:<code>" = dBm.
    Raises ValueError naming the offending entry (a typo must not be silently ignored)."""
    out: dict[tuple[str, int], float] = {}
    for key, value in (table or {}).items():
        model_txt, sep, code_txt = str(key).rpartition(":")
        if not sep or not code_txt.strip().isdigit():
            raise ValueError(f"[radio.sensitivity_measured] key {key!r}: expected \"<MODEL>:<code>\"")
        model = get_model(model_txt)
        code = _int_in(int(code_txt), 0, 7, f"[radio.sensitivity_measured] {key!r} rate code")
        dbm = _float(value, f"[radio.sensitivity_measured] {key!r}")
        if not MEASURED_RANGE_DBM[0] <= dbm <= MEASURED_RANGE_DBM[1]:
            raise ValueError(f"[radio.sensitivity_measured] {key!r} = {dbm} dBm outside "
                             f"{MEASURED_RANGE_DBM[0]:g}..{MEASURED_RANGE_DBM[1]:g} dBm")
        out[(model.name, code)] = dbm
    return out


def sensitivity(model: str | RadioModel, code: int,
                measured: Mapping[str, object] | Mapping[tuple[str, int], float] | None = None,
                rx_boost: bool = True) -> tuple[float, str]:
    """(sensitivity in dBm, source) for a model and rate code; a T-RF-03 measured
    value (raw station.toml table or the output of parse_measured) takes precedence."""
    m = get_model(model)
    _int_in(code, 0, 7, "rate code")
    if measured:
        parsed = all(isinstance(k, tuple) for k in measured)       # already parse_measured() output
        table: Mapping[tuple[str, int], float] = (
            measured if parsed else parse_measured(measured))      # type: ignore[assignment,arg-type]
        if (m.name, code) in table:
            return float(table[(m.name, code)]), SOURCE_MEASURED
    if m.packet_mode:
        p = SX126X_PROFILES[code]
        return lora_sensitivity_dbm(p.sf, p.bw_hz, rx_boost)
    s = E22_SENS_2K4_DBM[m.name]
    bps = E22_AIR_RATE_BPS[code]
    if bps == 2400:
        return s, SOURCE_DATASHEET
    return round(s + 10 * math.log10(bps / 2400.0), 1), SOURCE_ESTIMATED


# --------------------------------------------------------- station settings
@dataclass(frozen=True)
class RadioSettings:
    """The station's radio configuration (station.toml [radio]) in validated form."""
    model: RadioModel
    rate_code: int
    power_code: int
    channel: int
    rx_boost: bool = True
    cad: bool = True
    lbt: bool = False
    pa_gain_db: float = SX126X_PA_GAIN_DB
    freq_offset_cal_hz: float = 0.0
    measured: Mapping[tuple[str, int], float] = field(default_factory=dict)

    @classmethod
    def from_config(cls, radio_cfg: Mapping[str, object]) -> RadioSettings:
        model = get_model(radio_cfg.get("model"))          # type: ignore[arg-type]
        sx = radio_cfg.get("sx126x") or {}
        if not isinstance(sx, Mapping):
            raise ValueError("[radio.sx126x] must be a table")
        default_rate = SX126X_DEFAULT_RATE_CODE if model.packet_mode else E22_MIN_EFFECTIVE_RATE_CODE
        return cls(model=model,
                   rate_code=_int_in(radio_cfg.get("air_rate", default_rate), 0, 7, "[radio] air_rate"),
                   power_code=_int_in(radio_cfg.get("power", 0), 0, 3, "[radio] power"),
                   channel=_int_in(radio_cfg.get("channel", 23), 0, CHANNEL_MAX, "[radio] channel"),
                   rx_boost=bool(sx.get("rx_boosted_gain", True)),
                   cad=bool(sx.get("cad_before_tx", True)),
                   lbt=bool(radio_cfg.get("lbt", False)),
                   pa_gain_db=_float(sx.get("pa_gain_db", SX126X_PA_GAIN_DB), "[radio.sx126x] pa_gain_db"),
                   freq_offset_cal_hz=_float(radio_cfg.get("freq_offset_cal_hz", 0.0),
                                             "[radio] freq_offset_cal_hz"),
                   measured=parse_measured(radio_cfg.get("sensitivity_measured")))  # type: ignore[arg-type]

    def with_(self, **changes: object) -> RadioSettings:
        return replace(self, **changes)                     # type: ignore[arg-type]

    @property
    def freq_hz(self) -> float:
        return channel_freq_mhz(self.channel) * 1e6

    @property
    def sensitivity(self) -> tuple[float, str]:
        return sensitivity(self.model, self.rate_code, self.measured, self.rx_boost)

    @property
    def freq_offset_limit_hz(self) -> float | None:
        if not self.model.packet_mode:
            return None
        p = SX126X_PROFILES[self.rate_code]
        return lora_max_freq_offset_hz(p.sf, p.bw_hz, self.freq_hz)


def describe(radio: Mapping[str, object] | RadioSettings) -> dict:
    """Snapshot 'radio' block (contract 10) for a [radio] table or RadioSettings."""
    s = radio if isinstance(radio, RadioSettings) else RadioSettings.from_config(radio)
    m = s.model
    prof = SX126X_PROFILES[s.rate_code] if m.packet_mode else None
    sens, src = s.sensitivity
    air, air_src = airtime_s(m, s.rate_code)
    return {
        "model": m.name, "model_id": m.model_id, "interface": m.interface, "packet_mode": m.packet_mode,
        "rate_code": s.rate_code, "rate_label": rate_label(m, s.rate_code),
        "bps": round(rate_bps(m, s.rate_code), 3),
        "sf": prof.sf if prof else None, "bw_khz": prof.bw_hz / 1000.0 if prof else None,
        "cr": f"4/{4 + prof.cr}" if prof else None, "ldro": prof.ldro if prof else None,
        "power_code": s.power_code, "power_dbm": power_dbm(m, s.power_code, s.pa_gain_db),
        "power_verified": m.name == E22_T37S,      # T33D ladder and SX126x PA gain are VERIFY items
        "channel": s.channel, "freq_mhz": channel_freq_mhz(s.channel),
        "rx_boost": s.rx_boost if m.packet_mode else None,
        "cad": s.cad if m.packet_mode else None,
        "lbt": None if m.packet_mode else s.lbt,
        "sensitivity_dbm": sens, "sensitivity_source": src,
        "airtime_s": round(air, 6), "airtime_source": air_src,
        "freq_offset_limit_hz": s.freq_offset_limit_hz, "freq_offset_cal_hz": s.freq_offset_cal_hz,
        "has_fault_log": m.has_fault_log, "has_noise_register": m.has_noise_register,
    }


# ------------------------------------------------------------ radio backends
@dataclass(frozen=True)
class RxPacket:
    """One packet from a packet-mode ground radio (SX126x backend).
    data is empty when the radio's payload CRC failed (the payload is discarded)."""
    data: bytes
    ert: float                          # UNIX time the packet reached the GDS
    rssi_dbm: float | None              # packet RSSI (SX126x RssiPkt)
    snr_db: float | None                # per-packet SNR (SnrPkt / 4)
    freq_err_hz: float | None           # raw LoRa frequency-error estimate, no calibration
    crc_ok: bool


class RadioBackend:
    """Interface between the GDS main loop and a ground radio.
    Byte-stream radios (E22 UART) deliver octets through read(); packet radios
    (SX126x) deliver RxPacket objects through packets() and return b"" from read().
    write() takes one CLTU (or, for the E22, a module command) and never raises on
    link errors - backends count and log them."""
    packet_mode: bool = False

    def read(self) -> bytes:
        return b""

    def packets(self) -> list[RxPacket]:
        return []

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


# ------------------------------------------- automatic frequency control (24.8)
# SX126x option only. The station receiver is tuned to f_channel + c (c = correction, set
# through the station MCU RPC rf_foff). For every packet the chip reports a frequency error
# e = s (f_rx - f_LO), where the sign convention s of the LoRa frequency-error register is a
# VERIFY item (docs/08: offset a signal generator by +1 kHz). With the believed sign b the
# total received offset is estimated as c + b e for each packet; the median of the last
# AFC_WINDOW such estimates (robust to an outlier packet) is the loop estimate. The loop
# steps the correction toward it, limited in slew and magnitude by FERR_L (DS rev 2.2
# Table 3-8: +-25 % of BW, and +-50/100/200 ppm at SF12/11/10, the tighter applying).
# Sign self-check: after a correct step the residual shrinks by the step; with the wrong
# belief it grows by the step. Growth by more than half the step flips b once (event); a
# second divergence puts the loop in FAULT and returns to the static offset.
AFC_SNR_MARGIN_DB = 3.0                # accept SNR >= typical demodulator SNR (DS Table 6-1) + 3 dB
AFC_WINDOW = 8                         # median of the last 8 accepted packets
AFC_MIN_SAMPLES = 3                    # first step needs a median of >= 3 packets (engineering choice)
AFC_DEADBAND_MIN_HZ = 50.0             # step only when |estimate - correction| > max(50 Hz, 2 % BW)
AFC_DEADBAND_BW_FRACTION = 0.02
AFC_SLEW_FERR_FRACTION = 0.25          # one step <= 25 % of FERR_L
AFC_BOUND_FERR_FRACTION = 0.8          # |correction| <= 80 % of FERR_L
AFC_LOS_HOLD_S = 600.0                 # hold the correction through LOS, then back to the static offset
AFC_CHECK_SAMPLES = 3                  # post-step packets for the sign self-check (engineering choice)
AFC_SETTLE_MARGIN_S = 0.5              # a packet counts after a change only if it ended >= one air
                                       # time + this margin after the change was confirmed
AFC_CONFIRM_TIMEOUT_S = 10.0           # rf_foff result expected within this (engineering choice)
AFC_RETRY_S = 5.0                      # pause after a failed rf_foff (engineering choice)
AFC_RPC_MAX_HZ = 50_000                # station rf_foff(int32 hz) accepts |hz| <= 50000
AFC_EVENTS_MAX = 64
# rf_foff results: 1 / -1 / -2 come from the station MCU (contract 24.8); -3 is the ground's
# own code for "no answer": the Router Bridge call failed or timed out (the backend returns
# None), so whether the station applied the offset is unknown - treated as not applied.
AFC_RPC_NO_ANSWER = -3
AFC_RPC_RESULTS = {1: "applied", -1: "out of range", -2: "radio down", AFC_RPC_NO_ANSWER: "no answer (bridge)"}


class AfcLoop:
    """Ground automatic frequency control for the SX126x packet radio (contract 24.8).

    Call order (GDS radio thread, one clock for every t):
        corr = afc.on_packet(t, freq_err_hz, snr_db, crc_ok)   # per received packet
        corr = afc.tick(t, los)                                 # periodically (>= 1 Hz)
        if corr is not None:                                    # int Hz for the station
            r = station.rf_foff(corr)                           # 1 / -1 / -2, None = no answer
            afc.confirm(corr, AFC_RPC_NO_ANSWER if r is None else r, t_now)
    While a command awaits confirm() no other command is issued and packets are not used
    (their local-oscillator frequency is uncertain). pop_events() returns
    (t, level, text) tuples for the GDS event log. Pure: no I/O, no threads."""

    def __init__(self, sf: int, bw_hz: float, freq_hz: float, static_offset_hz: float = 0.0,
                 enabled: bool = True, sign: int = 1, airtime_s: float | None = None):
        if sign not in (1, -1) or isinstance(sign, bool):
            raise ValueError("sign must be +1 or -1")
        self.enabled = bool(enabled)
        self.sign = sign
        self.events: deque[tuple[float, str, str]] = deque(maxlen=AFC_EVENTS_MAX)
        self.static_hz = 0
        self.correction_hz = 0
        self.counters = {"accepted": 0, "rejected_snr": 0, "rejected_invalid": 0, "skipped_settling": 0,
                         "steps": 0, "sign_flips": 0, "rpc_failures": 0}
        self._configure(sf, bw_hz, freq_hz, airtime_s)
        cal = _float(static_offset_hz, "static offset")
        self.static_hz = round(max(-self.bound_hz, min(self.bound_hz, cal)))
        if abs(cal) > self.bound_hz:
            self._event(0.0, "WARN", f"AFC: static offset {cal:.0f} Hz clipped to +-{self.bound_hz:.0f} Hz "
                                     "(80 % of FERR_L)")
        self.correction_hz = self.static_hz
        self.state = "STATIC" if self.enabled else "DISABLED"
        self._flipped = False
        self._hist: deque[tuple[int, float]] = deque(maxlen=AFC_WINDOW)     # (correction, raw error)
        self._check: list[float] = []
        self._step: tuple[float, float] | None = None                       # (residual before, step)
        self._pending: tuple[int, float, bool, float] | None = None          # (hz, t, is_step, residual)
        self._goal_static = False
        self._t_applied = -math.inf
        self._t_retry = -math.inf
        self._t_los: float | None = None
        self._limited = False

    @classmethod
    def from_settings(cls, s: RadioSettings, enabled: bool = True, sign: int = 1) -> AfcLoop:
        """Loop for the configured SX126x profile and channel; disabled for the E22 models."""
        p = SX126X_PROFILES[s.rate_code]
        return cls(p.sf, p.bw_hz, s.freq_hz, s.freq_offset_cal_hz, enabled and s.model.packet_mode, sign,
                   profile_airtime_s(s.rate_code))

    def _configure(self, sf: int, bw_hz: float, freq_hz: float, airtime_s: float | None) -> None:
        if isinstance(sf, bool) or sf not in LORA_SNR_MIN_DB:
            raise ValueError(f"spreading factor must be one of {sorted(LORA_SNR_MIN_DB)}")
        bw = _float(bw_hz, "bandwidth")
        f = _float(freq_hz, "frequency")
        if bw <= 0 or f <= 0:
            raise ValueError("bandwidth and frequency must be positive")
        self.sf, self.bw_hz, self.freq_hz = sf, bw, f
        self.limit_hz = lora_max_freq_offset_hz(sf, bw, f)
        self.snr_min_db = LORA_SNR_MIN_DB[sf] + AFC_SNR_MARGIN_DB
        self.deadband_hz = max(AFC_DEADBAND_MIN_HZ, AFC_DEADBAND_BW_FRACTION * bw)
        self.slew_hz = AFC_SLEW_FERR_FRACTION * self.limit_hz
        self.bound_hz = min(AFC_BOUND_FERR_FRACTION * self.limit_hz, float(AFC_RPC_MAX_HZ))
        air = lora_airtime_s(CADU_OCTETS, sf, bw) if airtime_s is None else _float(airtime_s, "air time")
        if not 0.0 <= air <= 60.0:
            raise ValueError("air time must be 0..60 s")
        self.settle_s = air + AFC_SETTLE_MARGIN_S

    # ------------------------------------------------------------- helpers
    def _event(self, t: float, level: str, text: str) -> None:
        self.events.append((t, level, text))

    def pop_events(self) -> list[tuple[float, str, str]]:
        out = list(self.events)
        self.events.clear()
        return out

    def residual_hz(self, freq_err_hz: float) -> float:
        """Received minus tuned frequency (Hz, positive = above) for a raw chip value, with the
        loop's current sign belief (TdmRecord.freq_offset_hz)."""
        return self.sign * _float(freq_err_hz, "frequency error")

    @property
    def estimate_hz(self) -> float | None:
        """Median total received offset (Hz) relative to the nominal channel frequency."""
        if not self._hist:
            return None
        v = sorted(c + self.sign * e for c, e in self._hist)
        n = len(v)
        return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])

    def _clamp(self, hz: float) -> int:
        b = math.floor(self.bound_hz)
        return max(-b, min(b, round(hz)))

    def _command(self, hz: int, t: float, is_step: bool, residual: float) -> int:
        self._pending = (hz, t, is_step, residual)
        return hz

    def _expire_pending(self, t: float) -> None:
        if self._pending is not None and t - self._pending[1] > AFC_CONFIRM_TIMEOUT_S:
            self._event(t, "WARN", f"AFC: no rf_foff confirmation for {self._pending[0]} Hz within "
                                   f"{AFC_CONFIRM_TIMEOUT_S:g} s - correction unchanged")
            self.counters["rpc_failures"] += 1
            self._pending = None
            self._t_retry = t + AFC_RETRY_S

    # ------------------------------------------------------------- inputs
    def on_packet(self, t: float, freq_err_hz: float | None, snr_db: float | None,
                  crc_ok: bool = True) -> int | None:
        """One received packet (raw chip frequency error, Hz; SNR, dB). Returns a new
        correction (int Hz) to send with rf_foff, else None."""
        t = _float(t, "time")
        self._expire_pending(t)
        if self.state in ("DISABLED", "FAULT"):
            return None
        valid = crc_ok is True and isinstance(freq_err_hz, (int, float)) and not isinstance(freq_err_hz, bool) \
            and math.isfinite(freq_err_hz) and abs(freq_err_hz) <= 0.5 * self.bw_hz
        if not valid:
            self.counters["rejected_invalid"] += 1
            return None
        if isinstance(snr_db, bool) or not isinstance(snr_db, (int, float)) or not math.isfinite(snr_db) \
                or snr_db < self.snr_min_db:
            self.counters["rejected_snr"] += 1
            return None
        if self._pending is not None or t < self._t_applied + self.settle_s:
            self.counters["skipped_settling"] += 1
            return None
        e = float(freq_err_hz)                                        # type: ignore[arg-type]
        self._hist.append((self.correction_hz, e))
        self.counters["accepted"] += 1
        if self.state == "HOLD":
            self._resume(t)
        if self.state == "CHECK" and self._step is not None:
            self._check.append(self.sign * e)
            if len(self._check) < AFC_CHECK_SAMPLES:
                return None
            pre, step = self._step
            post = sorted(self._check)[len(self._check) // 2]
            self._check, self._step = [], None
            if abs(post) - abs(pre) > 0.5 * abs(step):
                if self._flipped:
                    self.state = "FAULT"
                    self._hist.clear()
                    self._event(t, "ERROR", f"AFC: residual grew again after the sign inversion ({pre:.0f} -> "
                                            f"{post:.0f} Hz) - AFC stopped, back to the static offset; check the "
                                            "station frequency reference and reset the AFC")
                    return self._return_static(t)
                self.sign = -self.sign
                self._flipped = True
                self.counters["sign_flips"] += 1
                self._event(t, "WARN", f"AFC: residual grew after a {step:.0f} Hz step ({pre:.0f} -> {post:.0f} "
                                       f"Hz) - frequency-error sign convention inverted to {self.sign:+d} (VERIFY)")
            self.state = "TRACK"
        return self._evaluate(t)

    def _evaluate(self, t: float) -> int | None:
        est = self.estimate_hz
        if est is None or len(self._hist) < AFC_MIN_SAMPLES:
            self.state = "STATIC"
            return None
        self.state = "TRACK"
        if self._pending is not None or t < self._t_retry:
            return None
        diff = est - self.correction_hz
        if abs(diff) <= self.deadband_hz:
            self._limited = False
            return None
        step = max(-self.slew_hz, min(self.slew_hz, diff))
        target = self._clamp(self.correction_hz + step)
        if target == self.correction_hz:
            if not self._limited:
                self._limited = True
                self._event(t, "WARN", f"AFC: estimate {est:.0f} Hz beyond the correction bound "
                                       f"+-{self.bound_hz:.0f} Hz (80 % of FERR_L) - correction held at the bound")
            return None
        return self._command(target, t, True, diff)

    def _return_static(self, t: float) -> int | None:
        self._goal_static = self.correction_hz != self.static_hz
        if not self._goal_static or self._pending is not None or t < self._t_retry:
            return None
        return self._command(self.static_hz, t, False, 0.0)

    def _resume(self, t: float) -> None:
        self._t_los = None
        self.state = "TRACK" if len(self._hist) >= AFC_MIN_SAMPLES else "STATIC"
        self._event(t, "INFO", f"AFC: signal back, tracking resumes from {self.correction_hz} Hz")

    def tick(self, t: float, los: bool) -> int | None:
        """Periodic service: LOS hold (AFC_LOS_HOLD_S, then the static offset), confirmation
        time-outs and retries. Returns a correction to send, else None."""
        t = _float(t, "time")
        self._expire_pending(t)
        if self.state == "DISABLED":
            return None
        if self._goal_static:
            return self._return_static(t)
        if self.state == "FAULT":
            return None
        if los and self.state in ("TRACK", "CHECK", "STATIC") and (self._hist or self.correction_hz != self.static_hz):
            self.state, self._t_los = "HOLD", t
            self._check, self._step = [], None
            self._event(t, "INFO", f"AFC: LOS - holding {self.correction_hz} Hz for {AFC_LOS_HOLD_S:g} s")
        elif los and self.state == "HOLD" and self._t_los is not None and t - self._t_los >= AFC_LOS_HOLD_S:
            self._hist.clear()
            self._t_los = None
            self.state = "STATIC"
            self._event(t, "INFO", f"AFC: no signal for {AFC_LOS_HOLD_S:g} s - back to the static offset "
                                   f"{self.static_hz} Hz")
            return self._return_static(t)
        elif not los and self.state == "HOLD":
            self._resume(t)
        return None

    def confirm(self, hz: int, result: int, t: float) -> None:
        """Result of the station RPC rf_foff(hz): 1 applied, -1 out of range, -2 radio down,
        AFC_RPC_NO_ANSWER (-3) no answer from the bridge. Anything but 1 is a failure."""
        t = _float(t, "time")
        p = self._pending
        if p is None or p[0] != hz:
            self._event(t, "WARN", f"AFC: unexpected rf_foff confirmation for {hz} Hz ignored")
            return
        self._pending = None
        if result != 1:
            self.counters["rpc_failures"] += 1
            self._t_retry = t + AFC_RETRY_S
            self._event(t, "WARN", f"AFC: rf_foff({hz}) failed: {AFC_RPC_RESULTS.get(result, f'result {result}')}")
            return
        step = hz - self.correction_hz
        self.correction_hz = hz
        self._t_applied = t
        if hz == self.static_hz:
            self._goal_static = False
        if p[2]:
            self.counters["steps"] += 1
            self._step = (p[3], float(step))
            self._check = []
            self.state = "CHECK"

    def reconfigure(self, t: float, sf: int, bw_hz: float, freq_hz: float,
                    airtime_s: float | None = None) -> int | None:
        """New LoRa profile or channel at time t: limits recomputed, history cleared; returns
        a correction when the current one exceeds the new bound."""
        t = _float(t, "time")
        self._configure(sf, bw_hz, freq_hz, airtime_s)
        self._hist.clear()
        self._check, self._step = [], None
        self.static_hz = self._clamp(self.static_hz)
        if self.state not in ("DISABLED", "FAULT"):
            self.state = "STATIC"
        target = self._clamp(self.correction_hz)
        if target != self.correction_hz and self.state != "DISABLED" and self._pending is None:
            return self._command(target, t, False, 0.0)
        return None

    def reset(self) -> None:
        """Operator reset (e.g. after FAULT): history cleared, sign check re-armed; the
        correction returns to the static offset at the next tick()."""
        if self.state == "DISABLED":
            return
        self._hist.clear()
        self._check, self._step = [], None
        self._flipped = False
        self.state = "STATIC"
        self._goal_static = self.correction_hz != self.static_hz

    def snapshot(self) -> dict:
        """Snapshot block rf.afc (contract 24.8) plus diagnostics."""
        est = self.estimate_hz
        return {"enabled": self.enabled, "state": self.state, "correction_hz": self.correction_hz,
                "estimate_hz": est, "residual_hz": None if est is None else est - self.correction_hz,
                "sign": self.sign, "static_hz": self.static_hz,
                "doppler_hz": None if est is None else est - self.static_hz,
                "pending_hz": None if self._pending is None else self._pending[0],
                "samples": len(self._hist), "limit_hz": self.limit_hz, "bound_hz": self.bound_hz,
                "slew_hz": self.slew_hz, "deadband_hz": self.deadband_hz, "snr_min_db": self.snr_min_db,
                **self.counters}
