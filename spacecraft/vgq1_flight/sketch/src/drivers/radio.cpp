// =============================================================================
//  VGQ-1 radio abstraction: tables, LoRa link maths, E22 UART framing and the
//  model-independent part of vgq::Radio. Pure C++ (host-tested by
//  tools/host_test/test_radio.cpp). See radio.h for units and sources.
// =============================================================================
#include "radio.h"

#include <math.h>
#include <string.h>

namespace vgq {

namespace {

// E22 air-rate codes REG0[2:0] (T33D manual v1.05 7.2; T37S v1.5 7.2): codes
// 0, 1 and 2 are all 2.4 kbit/s - neither module has a slower setting.
const uint32_t kE22RateBps[kRadioMaxRateCode + 1] = {2400, 2400, 2400, 4800, 9600, 19200, 38400, 62500};

// E22-400T33D power codes REG1[1:0]. Factory default code 0 is the specified
// maximum of 33 dBm (7.3, 2.2); 30 / 27 / 24 dBm for codes 1..3 assume 3 dB
// steps: the v1.05 REG1 table was copied from the T30D (30/27/24/21 dBm).
// VERIFY with docs/08 T-RF-07 (power-meter measurement per code).
const int8_t kT33dPowerDbm[kRadioMaxPowerCode + 1] = {33, 30, 27, 24};
const int8_t kT37sPowerDbm = 37;   // "This module has no power levels" (T37S v1.5 7.2)

// Typical sensitivity at 2.4 kbit/s: T33D -128 dBm (manual 2.2: -126 / -128 / -130),
// T37S -126 dBm (v1.5 2.2: -125 / -126 / -127).
const int16_t kT33dSens2k4Ddbm = -1280;
const int16_t kT37sSens2k4Ddbm = -1260;

// SX126x drive steps (SetTxParams power, dBm) for power codes 0..3 (contract 2.2).
const int8_t kSxDriveDbm[kRadioMaxPowerCode + 1] = {22, 17, 14, 10};

// SX126x LoRa profiles (contract 2.2). Sensitivities with Rx boosted gain:
//   DATASHEET - Semtech DS SX1261/2 Table 3-8 lists this (SF, BW):
//               BW 125 kHz SF12 -137 dBm, BW 125 kHz SF7 -124 dBm;
//   COMPUTED  - S = -174 + 10 log10(BW) + 6 dB NF + SNRmin(SF), rounded to 0.1 dB
//               (test_radio.cpp recomputes every one; contract 2.2's -143.0 for
//               code 0 rounds to -143.1: -143.05 dB).
const LoraProfile kProfiles[kSxNumProfiles] = {
    {12, 31250, 5, -1431, SensSource::COMPUTED},
    {12, 62500, 5, -1400, SensSource::COMPUTED},
    {12, 125000, 5, -1370, SensSource::DATASHEET},
    {11, 125000, 5, -1345, SensSource::COMPUTED},
    {10, 125000, 5, -1320, SensSource::COMPUTED},
    {9, 125000, 5, -1295, SensSource::COMPUTED},
    {8, 125000, 5, -1270, SensSource::COMPUTED},
    {7, 125000, 5, -1240, SensSource::DATASHEET},
};

const uint32_t kChannel0Hz = 410125000UL;   // E22 manuals 7.2 REG2: 410.125 + CH x 1 MHz
const uint32_t kChannelStepHz = 1000000UL;

bool is_e22(uint8_t model) { return model == VGQ_RADIO_E22_T33D || model == VGQ_RADIO_E22_T37S; }

}  // namespace

// =============================================================================
// Settings, channel plan, names
// =============================================================================
const char* sens_source_name(SensSource s) {
  switch (s) {
    case SensSource::DATASHEET: return "datasheet";
    case SensSource::ESTIMATED: return "estimated";
    case SensSource::COMPUTED: return "computed";
  }
  return "?";
}

bool radio_settings_valid(const RadioSettings& s) {
  return s.channel <= kRadioMaxChannel && s.rate_code <= kRadioMaxRateCode &&
         s.power_code <= kRadioMaxPowerCode && s.wor_cycle <= kRadioMaxWorCycle;
}

uint32_t radio_channel_freq_hz(uint8_t channel) {
  if (channel > kRadioMaxChannel) return 0;
  return kChannel0Hz + (uint32_t)channel * kChannelStepHz;
}

float radio_channel_freq_mhz(uint8_t channel) {
  if (channel > kRadioMaxChannel) return 0.0f;
  return 410.125f + (float)channel;
}

const char* radio_model_name(uint8_t model) {
  switch (model) {
    case VGQ_RADIO_E22_T33D: return "E22-400T33D";
    case VGQ_RADIO_E22_T37S: return "E22-400T37S";
    case VGQ_RADIO_SX126X: return "SX126x";
    default: return "?";
  }
}

// =============================================================================
// E22 tables and registers
// =============================================================================
uint32_t e22_air_rate_bps(uint8_t rate_code) {
  return rate_code <= kRadioMaxRateCode ? kE22RateBps[rate_code] : 0;
}

int8_t e22_tx_power_dbm(uint8_t model, uint8_t power_code) {
  if (power_code > kRadioMaxPowerCode) return kRadioNaPower;
  if (model == VGQ_RADIO_E22_T33D) return kT33dPowerDbm[power_code];
  if (model == VGQ_RADIO_E22_T37S) return kT37sPowerDbm;
  return kRadioNaPower;
}

int16_t e22_sensitivity_ddbm(uint8_t model, uint8_t rate_code, SensSource* src) {
  if (!is_e22(model) || rate_code > kRadioMaxRateCode) return 0;
  const int16_t s2k4 = model == VGQ_RADIO_E22_T37S ? kT37sSens2k4Ddbm : kT33dSens2k4Ddbm;
  const uint32_t bps = kE22RateBps[rate_code];
  if (bps == 2400) {
    if (src) *src = SensSource::DATASHEET;
    return s2k4;
  }
  // Same spreading-gain argument as the ground's link.py: +10 log10(rate / 2.4k) dB.
  if (src) *src = SensSource::ESTIMATED;
  const float s_dbm = (float)s2k4 / 10.0f + 10.0f * log10f((float)bps / 2400.0f);
  return (int16_t)lroundf(s_dbm * 10.0f);
}

uint32_t e22_airtime_ms(uint8_t rate_code, size_t n) {
  const uint32_t bps = e22_air_rate_bps(rate_code);
  if (bps == 0) return 0;
  // UART 8N1 = 10 bit per octet; LoRa air time at the nominal rate + 30 % for the
  // preamble, header and the module's sub-packet handling (v2 estimate, unchanged).
  return (uint32_t)(n * 10UL * 1000UL / kE22UartBaud) + (uint32_t)(n * 8UL * 1300UL / bps);
}

void e22_build_regs(uint8_t model, const RadioSettings& s, uint8_t regs[9]) {
  regs[0] = (uint8_t)(s.address >> 8);                       // 00H ADDH
  regs[1] = (uint8_t)(s.address & 0xFF);                     // 01H ADDL
  regs[2] = s.netid;                                         // 02H NETID
  // 03H REG0: UART 9600 (011) | 8N1 (00) | air-rate code
  regs[3] = (uint8_t)((0x3u << 5) | (0x0u << 3) | (s.rate_code & 0x7u));
  // 04H REG1: sub-packet 240 (00) | ambient-noise RSSI on (b5) | abnormal-status log
  // (b2, E22-400T37S only - reserved on the T33D) | power code. The T37S "has no power
  // levels" (v1.5 7.2): its power field is written 00, so REG1 = 0x24 (contract 33).
  const bool t37s = model == VGQ_RADIO_E22_T37S;
  const uint8_t fault_log = t37s ? (uint8_t)(1u << 2) : (uint8_t)0;
  const uint8_t power = t37s ? (uint8_t)0 : (uint8_t)(s.power_code & 0x3u);
  regs[4] = (uint8_t)((0x0u << 6) | (1u << 5) | fault_log | power);
  regs[5] = s.channel;                                       // 05H REG2 channel
  // 06H REG3: RSSI byte appended (b7) | transparent (b6 = 0) | no relay (b5 = 0) |
  // LBT (b4) | WOR receiver role (b3 = 0) | WOR cycle
  regs[6] = (uint8_t)((1u << 7) | (s.lbt ? (1u << 4) : 0u) | (s.wor_cycle & 0x7u));
  regs[7] = 0;                                               // 07H CRYPT_H: never used
  regs[8] = 0;                                               // 08H CRYPT_L
}

bool e22_regs_match(uint8_t model, const uint8_t written[7], const uint8_t read_back[7]) {
  for (size_t i = 0; i < 7; ++i) {
    // T33D REG1 bits 4..2 are reserved: whatever the module returns there is ignored.
    const uint8_t mask = (i == 4 && model == VGQ_RADIO_E22_T33D) ? 0xE3 : 0xFF;
    if ((uint8_t)((written[i] ^ read_back[i]) & mask) != 0) return false;
  }
  return true;
}

uint8_t e22_strap_model(uint8_t build_model, uint8_t low, uint8_t n) {
  if (!is_e22(build_model)) return build_model;            // SX126x: a build switch, not a strap
  const bool jumpered = n > 0 && low <= n && 2u * low > (unsigned)n;   // strict majority LOW
  return jumpered ? (uint8_t)VGQ_RADIO_E22_T37S : build_model;
}

// =============================================================================
// SX126x LoRa maths
// =============================================================================
const LoraProfile* sx_profile(uint8_t code) { return code < kSxNumProfiles ? &kProfiles[code] : nullptr; }

uint32_t lora_symbol_us(uint8_t sf, uint32_t bw_hz) {
  if (bw_hz == 0 || sf < 5 || sf > 12) return 0;
  return (uint32_t)(((uint64_t)1000000u << sf) / bw_hz);
}

bool lora_ldro_required(uint8_t sf, uint32_t bw_hz) { return lora_symbol_us(sf, bw_hz) >= 16380u; }

float lora_sensitivity_dbm(uint8_t sf, uint32_t bw_hz, float nf_db) {
  if (sf < 7 || sf > 12 || bw_hz == 0) return NAN;
  // LoRa demodulator SNR limits: -7.5 dB at SF7, 2.5 dB lower per SF step (SF12: -20 dB).
  const float snr_min_db = -7.5f - 2.5f * (float)(sf - 7);
  return -174.0f + 10.0f * log10f((float)bw_hz) + nf_db + snr_min_db;
}

uint32_t lora_airtime_us(uint8_t sf, uint32_t bw_hz, uint8_t cr, bool ldro, uint16_t n_preamble,
                         size_t payload, bool crc_on, bool implicit_header) {
  // SF5/SF6 use different header constants; the mission never selects them.
  if (sf < 7 || sf > 12 || bw_hz == 0 || cr < 5 || cr > 8 || payload > kSxMaxPayload) return 0;
  const int32_t num = 8 * (int32_t)payload - 4 * (int32_t)sf + 28 + (crc_on ? 16 : 0) -
                      (implicit_header ? 20 : 0);
  const int32_t den = 4 * ((int32_t)sf - (ldro ? 2 : 0));
  const int32_t blocks = num > 0 ? (num + den - 1) / den : 0;
  const uint64_t n_payload = 8u + (uint64_t)blocks * cr;
  // T = (n_pre + 4.25 + n_payload) 2^SF / BW, computed in quarter symbols to stay exact.
  const uint64_t quarter_symbols = 4u * (uint64_t)n_preamble + 17u + 4u * n_payload;
  const uint64_t den_q = 4u * (uint64_t)bw_hz;
  const uint64_t t_us = (quarter_symbols * ((uint64_t)1000000u << sf) + den_q - 1u) / den_q;
  return t_us > UINT32_MAX ? UINT32_MAX : (uint32_t)t_us;
}

float lora_bitrate_bps(uint8_t sf, uint32_t bw_hz, uint8_t cr) {
  if (sf < 5 || sf > 12 || cr < 5 || cr > 8) return 0.0f;
  return (float)sf * 4.0f / (float)cr * (float)bw_hz / (float)(1u << sf);
}

float lora_max_freq_offset_hz(uint8_t sf, uint32_t bw_hz, uint32_t freq_hz) {
  float limit = 0.25f * (float)bw_hz;
  const uint32_t ppm = sf == 12 ? 50u : sf == 11 ? 100u : sf == 10 ? 200u : 0u;
  if (ppm != 0) {
    const float ppm_limit = (float)freq_hz * 1.0e-6f * (float)ppm;
    if (ppm_limit < limit) limit = ppm_limit;
  }
  return limit;
}

int8_t sx_drive_dbm(uint8_t power_code) {
  return power_code <= kRadioMaxPowerCode ? kSxDriveDbm[power_code] : kRadioNaPower;
}

int8_t sx_tx_power_dbm(uint8_t power_code, int8_t pa_gain_db) {
  const int8_t drive = sx_drive_dbm(power_code);
  if (drive == kRadioNaPower) return kRadioNaPower;
  const int16_t p = (int16_t)drive + (int16_t)pa_gain_db;
  return (int8_t)(p > kSxModuleMaxDbm ? kSxModuleMaxDbm : p);
}

int16_t sx_sensitivity_ddbm(uint8_t code, bool rx_boost, SensSource* src) {
  const LoraProfile* p = sx_profile(code);
  if (p == nullptr) return 0;
  if (rx_boost) {
    if (src) *src = p->src;
    return p->sens_ddbm;
  }
  if (p->sf == 12 && p->bw_hz == 125000u) {
    if (src) *src = SensSource::DATASHEET;
    return kSxPowerSavingSf12Bw125Ddbm;
  }
  if (src) *src = SensSource::ESTIMATED;
  const float s_dbm = lora_sensitivity_dbm(p->sf, p->bw_hz, kSxNoiseFigureDb + kSxPowerSavingPenaltyDb);
  return (int16_t)lroundf(s_dbm * 10.0f);
}

uint32_t sx_tx_timeout_raw(uint32_t airtime_us, uint32_t margin_us) {
  const uint64_t t_us = (uint64_t)airtime_us * 3u / 2u + margin_us;
  uint64_t raw = (t_us * 64u + 999u) / 1000u;                 // 15.625 us = 1/64 ms, round up
  if (raw < 1u) raw = 1u;
  if (raw > 0xFFFFFEu) raw = 0xFFFFFEu;
  return (uint32_t)raw;
}

// =============================================================================
// Model-generic figures
// =============================================================================
uint32_t radio_airtime_ms(uint8_t model, uint8_t rate_code, size_t n) {
  if (is_e22(model)) return e22_airtime_ms(rate_code, n);
  const LoraProfile* p = sx_profile(rate_code);
  if (model != VGQ_RADIO_SX126X || p == nullptr) return 0;
  const uint32_t us = lora_airtime_us(p->sf, p->bw_hz, p->cr, lora_ldro_required(p->sf, p->bw_hz),
                                      kSxPreambleSym, n, true, false);
  return (uint32_t)(((uint64_t)us + 999u) / 1000u);
}

uint32_t radio_bitrate_bps(uint8_t model, uint8_t rate_code) {
  if (is_e22(model)) return e22_air_rate_bps(rate_code);
  const LoraProfile* p = sx_profile(rate_code);
  if (model != VGQ_RADIO_SX126X || p == nullptr) return 0;
  return (uint32_t)lroundf(lora_bitrate_bps(p->sf, p->bw_hz, p->cr));
}

int8_t radio_tx_power_dbm(uint8_t model, uint8_t power_code, int8_t pa_gain_db) {
  if (is_e22(model)) return e22_tx_power_dbm(model, power_code);
  if (model == VGQ_RADIO_SX126X) return sx_tx_power_dbm(power_code, pa_gain_db);
  return kRadioNaPower;
}

int16_t radio_sensitivity_ddbm(uint8_t model, uint8_t rate_code, bool rx_boost, SensSource* src) {
  if (is_e22(model)) return e22_sensitivity_ddbm(model, rate_code, src);
  if (model == VGQ_RADIO_SX126X) return sx_sensitivity_ddbm(rate_code, rx_boost, src);
  return 0;
}

uint8_t radio_boot_power_code(bool brownout_reset, uint8_t nominal_code, uint8_t brownout_code) {
  const uint8_t nominal = nominal_code <= kRadioMaxPowerCode ? nominal_code : kRadioMaxPowerCode;
  if (!brownout_reset) return nominal;
  const uint8_t derated = brownout_code <= kRadioMaxPowerCode ? brownout_code : kRadioMaxPowerCode;
  return derated > nominal ? derated : nominal;   // higher code = lower power on every model
}

// =============================================================================
// E22StreamParser (behaviour moved from the v2 Flight::service_rx /
// process_rx_packet; see radio.h for the precedence rules and the changes).
// =============================================================================
void E22StreamParser::on_byte(uint8_t b, uint32_t now) {
  if (len_ < kE22RxBufLen) buf_[len_++] = b;      // an over-long burst is truncated
  last_byte_ms_ = now;
  ++rx_bytes_;
}

void E22StreamParser::noise_requested(uint32_t now) {
  awaiting_noise_ = true;
  noise_req_ms_ = now;
}

bool E22StreamParser::receiving(uint32_t now) const {
  return len_ > 0 || (uint32_t)(now - last_byte_ms_) < kE22RxQuietMs;
}

RadioEvent E22StreamParser::poll(uint32_t now, RxPacket& rx, int32_t& value) {
  if (len_ > 0 && (uint32_t)(now - last_byte_ms_) > kE22RxGapMs) {
    const RadioEvent ev = classify(rx, value);
    len_ = 0;
    if (ev != RadioEvent::NONE) return ev;
  }
  if (awaiting_noise_ && (uint32_t)(now - noise_req_ms_) > kE22NoiseTimeoutMs) {
    awaiting_noise_ = false;
    value = kRadioNaI32;
    return RadioEvent::NOISE;
  }
  return RadioEvent::NONE;
}

RadioEvent E22StreamParser::classify(RxPacket& rx, int32_t& value) {
  // Ambient-noise reply: C1 00 02 <current noise> <RSSI of the last packet>.
  if (awaiting_noise_ && len_ >= 5 && buf_[0] == 0xC1 && buf_[1] == 0x00 && buf_[2] == 0x02) {
    awaiting_noise_ = false;
    value = (int32_t)buf_[3] - 256;
    return RadioEvent::NOISE;
  }
  const bool ff3 = len_ >= 3 && buf_[0] == 0xFF && buf_[1] == 0xFF && buf_[2] == 0xFF;
  // E22-400T37S abnormal-status log, FF FF FF 01..04 (a CLTU always starts EB 90).
  if (model_ == VGQ_RADIO_E22_T37S && ff3 && len_ >= 4 && len_ <= 5 && buf_[3] >= 1 && buf_[3] <= 4) {
    value = buf_[3];
    return RadioEvent::FAULT;
  }
  // "Wrong format" reply to a command.
  if (ff3 && len_ == 3) {
    if (cmd_rejects_ < UINT16_MAX) ++cmd_rejects_;
    if (awaiting_noise_) {
      awaiting_noise_ = false;
      value = kRadioNaI32;
      return RadioEvent::NOISE;
    }
    return RadioEvent::NONE;
  }
  // Uplink packet followed by the RSSI octet (REG3 bit 7).
  size_t n = len_ - 1;
  if (n > RxPacket::kMaxLen) n = RxPacket::kMaxLen;
  memcpy(rx.data, buf_, n);
  rx.len = (uint16_t)n;
  rx.rssi_dbm = (int16_t)((int16_t)buf_[len_ - 1] - 256);
  rx.snr_cdb = kRadioNaI16;
  rx.freq_err_hz = kRadioNaI32;
  rx.t_ms = last_byte_ms_;
  return RadioEvent::PACKET;
}

// =============================================================================
// E22TxTracker (v2 Flight TX_WAIT_BUSY / TX_WAIT_DONE, unchanged)
// =============================================================================
void E22TxTracker::start(uint32_t now) {
  phase_ = WAIT_BUSY;
  t0_ = now;
}

RadioEvent E22TxTracker::poll(uint32_t now, bool aux_high, int32_t& value) {
  const uint32_t dt = now - t0_;
  switch (phase_) {
    case WAIT_BUSY:
      if (!aux_high || dt > kE22AuxEdgeMs) phase_ = WAIT_DONE;
      return RadioEvent::NONE;
    case WAIT_DONE: {
      const bool timeout = dt > kE22AuxTimeoutMs;
      if (!aux_high && !timeout) return RadioEvent::NONE;
      phase_ = IDLE;
      value = dt > (uint32_t)INT32_MAX ? INT32_MAX : (int32_t)dt;
      return timeout ? RadioEvent::TX_TIMEOUT : RadioEvent::TX_DONE;
    }
    case IDLE:
    default:
      return RadioEvent::NONE;
  }
}

// =============================================================================
// vgq::Radio - model-independent figures for the current settings
// =============================================================================
uint32_t Radio::airtime_ms(size_t n) const { return radio_airtime_ms(model_, settings_.rate_code, n); }

uint32_t Radio::bitrate_bps() const { return radio_bitrate_bps(model_, settings_.rate_code); }

int8_t Radio::tx_power_dbm() const { return radio_tx_power_dbm(model_, settings_.power_code, pa_gain_db_); }

float Radio::sensitivity_dbm() const {
  SensSource src = SensSource::COMPUTED;
  return (float)radio_sensitivity_ddbm(model_, settings_.rate_code, rx_boost_ok_, &src) / 10.0f;
}

SensSource Radio::sensitivity_source() const {
  SensSource src = SensSource::COMPUTED;
  (void)radio_sensitivity_ddbm(model_, settings_.rate_code, rx_boost_ok_, &src);
  return src;
}

float Radio::max_freq_offset_hz() const {
  const LoraProfile* p = sx_profile(settings_.rate_code);
  if (model_ != VGQ_RADIO_SX126X || p == nullptr) return 0.0f;
  return lora_max_freq_offset_hz(p->sf, p->bw_hz, radio_channel_freq_hz(settings_.channel));
}

}  // namespace vgq
