// =============================================================================
//  VGQ-1 radio abstraction: one interface (vgq::Radio) for every supported
//  transceiver, plus the pure tables and link mathematics behind it.
//
//    VGQ_RADIO_E22_T33D (1)  EBYTE E22-400T33D: SX1262 + PA/LNA, 2 W, UART   (default)
//    VGQ_RADIO_E22_T37S (2)  EBYTE E22-400T37S on E22-400TBH-02, 5 W, UART   (preserved)
//    VGQ_RADIO_SX126X   (3)  SX1268 / SX1262 over SPI through RadioLib 7.8.1 (long range)
//
//  This header and radio.cpp are pure C++ (no Arduino.h). They are compiled into
//  tools/host_test and verified by tools/host_test/test_radio.cpp. The hardware
//  drivers e22.* and sx126x.* implement vgq::Radio on top of them, and the
//  flight executive sees only this interface: it starts transmissions and
//  consumes one event per poll() call.
//
//  Units: frequency in Hz (uint32_t) unless a name says MHz; power in dBm;
//  sensitivity tables in deci-dBm (int16_t, 0.1 dB steps), the API in dBm
//  (float); SNR in centi-dB (0.01 dB); time in ms of the millis() time base, or
//  in microseconds where a name ends in _us.
//
//  Sources (contract v3 sections 2 and 14):
//    E22-400T33D user manual v1.05: 2.2 working parameters, 7.1 commands,
//      7.2 registers, 7.3 factory defaults.
//    E22-xxxT37S user manual v1.5: 2.2, 5.7 abnormal-status log, 7.2.
//    Semtech DS SX1261/2 rev 2.2: Table 3-8 (LoRa sensitivity with Rx boosted
//      gain, FERR_L), 6.1.4 (time on air -> Semtech LoRa formula, AN1200.13),
//      13.1.4 SetTx (timeout in 15.625 us steps).
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

// ---- Radio model codes (RF packet field radio_model; contract 3.2) ---------
// Guarded so that config.h may define them first; the values are fixed by the ICD.
#ifndef VGQ_RADIO_E22_T33D
#define VGQ_RADIO_E22_T33D 1
#endif
#ifndef VGQ_RADIO_E22_T37S
#define VGQ_RADIO_E22_T37S 2
#endif
#ifndef VGQ_RADIO_SX126X
#define VGQ_RADIO_SX126X 3
#endif
// Radio fitted to this build (config.h selects it; the E22-400T33D is the default).
#ifndef VGQ_RADIO
#define VGQ_RADIO VGQ_RADIO_E22_T33D
#endif

namespace vgq {

// ---- Shared constants ---------------------------------------------------------
static const int16_t kRadioNaI16 = INT16_MIN;     // "not available" in RxPacket fields
static const int32_t kRadioNaI32 = INT32_MIN;     // "not available" in RxPacket / event values
static const int8_t  kRadioNaPower = INT8_MIN;    // invalid model or power code
static const uint8_t kRadioMaxChannel = 83;       // f = 410.125 MHz + CH x 1 MHz, CH 0..83 (all models)
static const uint8_t kRadioMaxRateCode = 7;       // E22 REG0[2:0] air-rate code / SX126x profile
static const uint8_t kRadioMaxPowerCode = 3;      // E22 REG1[1:0] / SX126x drive step
static const uint8_t kRadioMaxWorCycle = 7;       // E22 REG3[2:0]: period = (1 + code) x 500 ms

// ---- E22 UART models (register protocol shared by T33D and T37S) --------------
static const uint32_t kE22UartBaud = 9600;        // configuration mode accepts only 9600 8N1 (7.1)
static const size_t   kE22MaxPacket = 240;        // REG1[7:6] = 00: one LoRa packet per <= 240 octets
static const size_t   kE22RxBufLen = 300;         // receive accumulation buffer (as flight v2)
static const uint32_t kE22RxGapMs = 40;           // UART silence that ends a received burst
static const uint32_t kE22NoiseTimeoutMs = 300;   // C0 C1 C2 C3 noise query: reply deadline
static const uint32_t kE22RxQuietMs = 100;        // no transmission within 100 ms of a received octet
static const uint32_t kE22AuxEdgeMs = 200;        // AUX must fall within 200 ms of the UART write
static const uint32_t kE22AuxTimeoutMs = 30000;   // AUX must rise again within 30 s (TX timeout)

// ---- SX126x LoRa (contract 2.2) -------------------------------------------------
static const uint8_t  kSxNumProfiles = 8;         // profile codes 0..7
static const uint8_t  kSxDefaultProfile = 1;      // SF12 / 62.5 kHz / 4/5
static const uint8_t  kSxSyncWord = 0x12;         // private-network LoRa sync word
static const uint16_t kSxPreambleSym = 12;        // normal preamble length, symbols
// Hibernation (duty-cycled RX): the spacecraft listens for a preamble of this many
// symbols, so a ground WAKE transmission must use the same preamble length (it plays
// the role of the E22 WOR wake-up code). 32 symbols = 2.1 s at SF12 / 62.5 kHz.
static const uint16_t kSxWakePreambleSym = 32;
static const float    kSxNoiseFigureDb = 6.0f;    // assumed receiver NF with Rx boosted gain (contract 2.2)
// Rx power-saving gain costs 4 dB: Table 3-8, BW 125 kHz SF12, -137 (boosted) vs -133 dBm.
// Elsewhere it is modelled as a 4 dB higher noise figure (same rule as ground dssq/link.py).
static const float    kSxPowerSavingPenaltyDb = 4.0f;
static const int16_t  kSxPowerSavingSf12Bw125Ddbm = -1330;   // Table 3-8 value itself
static const uint32_t kSxTxTimeoutMarginUs = 50000;          // added to 1.5 x air time (contract 14)
static const uint32_t kSxLbtMaxWaitMs = 2000;     // CAD deferral limit, then transmit anyway (as E22 LBT)
static const uint8_t  kSxMaxPayload = 255;        // SX126x FIFO packet limit
// Default module PA gain of the E22-400M33S reference module: +22 dBm drive -> 33 dBm.
// VERIFY against the module datasheet and by measurement (docs/08, T-RF-07).
static const int8_t   kSxPaGainDbDefault = 11;
static const int8_t   kSxModuleMaxDbm = 33;       // E22-400M33S maximum output (contract 2.2)

// ---- Events returned by Radio::poll() -------------------------------------------
enum class RadioEvent : uint8_t {
  NONE = 0,      // nothing happened
  PACKET,        // rx holds a received packet (CRC-checked by the modem where it has one)
  NOISE,         // value = ambient noise, dBm (kRadioNaI32: the radio did not answer)
  TX_DONE,       // the transmission started by transmit() ended; value = busy time, ms
  TX_TIMEOUT,    // it ended by timeout (E22: AUX; SX126x: TX timeout / watchdog); value = ms
  FAULT,         // value = RadioFault code (below)
};

// FAULT event codes. 1..4 are the E22-400T37S abnormal-status log (FF FF FF 0x,
// manual v1.5 5.7) and map to HK radio_fault / FDIR RADIO_FAULT; codes >= 16 are
// SX126x driver faults (FDIR RADIO_CFG, EVR); kRadioFaultCleared reports that an
// SX126x fault has been recovered (reset, re-initialised, configuration re-applied).
enum RadioFault : uint8_t {
  kRadioFaultCleared = 0,
  kRadioFaultUnderVoltage = 1,   // < 4.5 V
  kRadioFaultOverVoltage = 2,    // > 15 V
  kRadioFaultOverTemp = 3,       // > 120 C
  kRadioFaultOvAndOt = 4,        // over-voltage and over-temperature
  kRadioFaultBusyStuck = 16,     // SX126x BUSY held high past the bound
  kRadioFaultSpi = 17,           // SPI command error / bad status
  kRadioFaultReinit = 18,        // reset or re-initialisation failed (retried with back-off)
  kRadioFaultNoIrq = 19,         // TX or CAD watchdog expired without a DIO1 interrupt
};

// Provenance of a sensitivity figure (ground adds "measured", contract 2.3).
enum class SensSource : uint8_t { DATASHEET = 0, ESTIMATED = 1, COMPUTED = 2 };
const char* sens_source_name(SensSource s);    // "datasheet" / "estimated" / "computed"

// Link settings common to every model. The "rate code" is the E22 air-rate code or
// the SX126x LoRa profile; the "power code" is the E22 power code or the SX126x
// drive step (contract 2). configure() rejects out-of-range values.
struct RadioSettings {
  uint8_t channel = 23;          // 0..83 -> 433.125 MHz by default
  uint8_t rate_code = 2;         // E22: 2 = 2.4 kbit/s; SX126x: use kSxDefaultProfile
  uint8_t power_code = 0;        // 0 = highest power on every model
  bool lbt = false;              // E22 REG3 b4 listen-before-talk / SX126x CAD before TX
  bool rx_boost = true;          // SX126x Rx boosted gain (register 0x08AC = 0x96)
  uint16_t address = 0x0000;     // E22 ADDH:ADDL (transparent mode: same on both ends)
  uint8_t netid = 0x00;          // E22 NETID
  uint8_t wor_cycle = 3;         // E22 WOR cycle code for hibernation: (1 + 3) x 500 ms = 2 s
};
bool radio_settings_valid(const RadioSettings& s);

// A received packet, copied into caller-owned storage by Radio::poll().
struct RxPacket {
  static const size_t kMaxLen = 256;
  uint8_t data[kMaxLen];
  uint16_t len = 0;              // octets in data
  int16_t rssi_dbm = kRadioNaI16;      // packet RSSI
  int16_t snr_cdb = kRadioNaI16;       // packet SNR, 0.01 dB (SX126x only)
  int32_t freq_err_hz = kRadioNaI32;   // carrier offset estimate, Hz (SX126x only)
  uint32_t t_ms = 0;             // millis() when the packet ended (last octet / RxDone)
  // false: the modem's LoRa payload CRC failed but the payload was delivered anyway
  // (contract 20, "RS rescue"): the flight still runs BCH + FECF + SDLS on it, which
  // decide alone whether the uplink is accepted. Default true (the E22 modules drop
  // CRC failures internally; a driver sets false only where it delivers them).
  bool crc_ok = true;
};

// ---- Channel plan -------------------------------------------------------------------
uint32_t radio_channel_freq_hz(uint8_t channel);   // 0 for channel > 83
float radio_channel_freq_mhz(uint8_t channel);     // 0.0f for channel > 83

// ---- E22 tables (contract 2.1) --------------------------------------------------------
uint32_t e22_air_rate_bps(uint8_t rate_code);      // 0 for an invalid code
int8_t e22_tx_power_dbm(uint8_t model, uint8_t power_code);   // kRadioNaPower if invalid
// Receiver sensitivity in 0.1 dBm: datasheet value at 2.4 kbit/s (codes 0..2),
// S(2.4k) + 10 log10(rate / 2400) "estimated" above. 0 for invalid input.
int16_t e22_sensitivity_ddbm(uint8_t model, uint8_t rate_code, SensSource* src);
// Radio busy time for an n-octet UART write, ms: UART transfer at 9600 8N1 plus the
// LoRa air time at the nominal rate with 30 % margin (the v2 flight estimate, kept so
// frame periods are unchanged before the first AUX measurement). 0 if the code is invalid.
uint32_t e22_airtime_ms(uint8_t rate_code, size_t n);
// Configuration registers 00H..08H for one model (CRYPT left 0). REG1 bit 2
// (abnormal-status log) is set for the E22-400T37S only: the T33D reserves it.
void e22_build_regs(uint8_t model, const RadioSettings& s, uint8_t regs[9]);
// Compares the 7 read-back registers 00H..06H with what was written. The T33D's
// reserved REG1 bits 4..2 are ignored.
bool e22_regs_match(uint8_t model, const uint8_t written[7], const uint8_t read_back[7]);

// ---- SX126x LoRa profiles and maths (contract 2.2, 14) ---------------------------
struct LoraProfile {
  uint8_t sf;            // spreading factor
  uint32_t bw_hz;        // LoRa bandwidth
  uint8_t cr;            // coding-rate denominator: 5 = 4/5
  int16_t sens_ddbm;     // sensitivity with Rx boosted gain, 0.1 dBm
  SensSource src;        // DATASHEET (Table 3-8 has this SF/BW) or COMPUTED
};
const LoraProfile* sx_profile(uint8_t code);       // nullptr if code > 7
uint32_t lora_symbol_us(uint8_t sf, uint32_t bw_hz);
bool lora_ldro_required(uint8_t sf, uint32_t bw_hz);   // symbol time >= 16.38 ms (DS 6.1.1)
// S = -174 + 10 log10(BW) + NF + SNRmin(SF), dBm (contract 2.2); NAN if SF is not 7..12.
float lora_sensitivity_dbm(uint8_t sf, uint32_t bw_hz, float nf_db);
// Semtech LoRa time on air, microseconds, rounded up; 0 for invalid parameters:
//   Tsym = 2^SF / BW
//   n_payload = 8 + max(ceil((8 PL - 4 SF + 28 + 16 CRC - 20 IH) / (4 (SF - 2 DE))) (CR + 4), 0)
//   T = (n_preamble + 4.25 + n_payload) Tsym
uint32_t lora_airtime_us(uint8_t sf, uint32_t bw_hz, uint8_t cr, bool ldro, uint16_t n_preamble,
                         size_t payload, bool crc_on, bool implicit_header);
float lora_bitrate_bps(uint8_t sf, uint32_t bw_hz, uint8_t cr);   // SF (4 / CR) BW / 2^SF
// FERR_L (Table 3-8): the tighter of +-25 % of BW and SF12 50 / SF11 100 / SF10 200 ppm.
float lora_max_freq_offset_hz(uint8_t sf, uint32_t bw_hz, uint32_t freq_hz);
int8_t sx_drive_dbm(uint8_t power_code);           // 22 / 17 / 14 / 10, kRadioNaPower if invalid
// Radiated power: drive + module PA gain, capped at the module maximum (33 dBm).
int8_t sx_tx_power_dbm(uint8_t power_code, int8_t pa_gain_db);
// Sensitivity of a profile in 0.1 dBm. Rx power-saving gain: Table 3-8 value for BW 125
// kHz SF12 (-133 dBm, "datasheet"), elsewhere the model with NF + 4 dB ("estimated").
// 0 if invalid.
int16_t sx_sensitivity_ddbm(uint8_t code, bool rx_boost, SensSource* src);
// SetTx timeout in 15.625 us steps for 1.5 x air time + margin, rounded up and
// clamped to 1..0xFFFFFE (24 bits; 0 would disable the timeout).
uint32_t sx_tx_timeout_raw(uint32_t airtime_us, uint32_t margin_us);

// ---- Model-generic figures (used by Radio and by the tests) ------------------------
uint32_t radio_airtime_ms(uint8_t model, uint8_t rate_code, size_t n);
uint32_t radio_bitrate_bps(uint8_t model, uint8_t rate_code);
int8_t radio_tx_power_dbm(uint8_t model, uint8_t power_code, int8_t pa_gain_db);
int16_t radio_sensitivity_ddbm(uint8_t model, uint8_t rate_code, bool rx_boost, SensSource* src);
const char* radio_model_name(uint8_t model);       // "E22-400T33D" / "E22-400T37S" / "SX126x" / "?"

// Brown-out derating (contract 15): after a BROWNOUT reset the radio starts at
// `brownout_code` instead of `nominal_code` - whichever is the LOWER power (higher
// code) - so a marginal supply is not hit by full-power current peaks again. The
// operator restores power with TXPWR. On the E22-400T37S every code is 37 dBm, so
// derating changes nothing there (check with radio_tx_power_dbm()).
uint8_t radio_boot_power_code(bool brownout_reset, uint8_t nominal_code, uint8_t brownout_code);

// =============================================================================
//  E22 UART receive framing (pure; owned by E22Radio).
//
//  The E22 writes each received LoRa packet to its TXD as one burst (about
//  1 ms per octet at 9600 bit/s); 40 ms of silence ends the burst. A burst is,
//  in this order of precedence:
//    * the reply to a pending ambient-noise query: C1 00 02 <noise> <last RSSI>
//      -> NOISE, dBm = -(256 - noise);
//    * E22-400T37S only: an abnormal-status report FF FF FF <1..4> (4..5 octets,
//      repeated every 500 ms while the module has disabled its RF output) -> FAULT;
//    * exactly FF FF FF: the module's "wrong format" reply to a command (7.1);
//      counted, never delivered (a pending noise query then ends as n/a);
//    * otherwise an uplink packet: with REG3 bit 7 set the module appends one RSSI
//      octet -> PACKET with rssi_dbm = -(256 - last octet), data = the rest.
//  A noise query unanswered after 300 ms ends as NOISE with kRadioNaI32.
//  Changes from the v2 flight code are limited to: the explicit n/a NOISE event,
//  the FF FF FF reply no longer reaching the CLTU decoder, fault parsing restricted
//  to the T37S, and packets longer than RxPacket::kMaxLen (256) being truncated
//  (the longest valid CLTU is 90 octets).
// =============================================================================
class E22StreamParser {
 public:
  explicit E22StreamParser(uint8_t model) : model_(model) {}
  void on_byte(uint8_t b, uint32_t now);
  void noise_requested(uint32_t now);
  bool noise_pending() const { return awaiting_noise_; }
  // True while a burst is being received or within kE22RxQuietMs of its last octet.
  bool receiving(uint32_t now) const;
  // At most one event per call; packets before noise timeouts.
  RadioEvent poll(uint32_t now, RxPacket& rx, int32_t& value);
  uint32_t rx_bytes() const { return rx_bytes_; }
  uint16_t command_rejects() const { return cmd_rejects_; }

 private:
  RadioEvent classify(RxPacket& rx, int32_t& value);
  uint8_t model_;
  uint8_t buf_[kE22RxBufLen] = {0};
  size_t len_ = 0;
  uint32_t last_byte_ms_ = 0;
  uint32_t rx_bytes_ = 0;
  bool awaiting_noise_ = false;
  uint32_t noise_req_ms_ = 0;
  uint16_t cmd_rejects_ = 0;
};

// E22 transmission tracking from the AUX line (pure; owned by E22Radio):
// AUX falls once the module has data to send; if that edge is not seen within
// kE22AuxEdgeMs it is assumed missed. The transmission ends when AUX rises again
// (TX_DONE) or after kE22AuxTimeoutMs (TX_TIMEOUT). value = elapsed ms.
class E22TxTracker {
 public:
  void start(uint32_t now);
  bool active() const { return phase_ != IDLE; }
  RadioEvent poll(uint32_t now, bool aux_high, int32_t& value);

 private:
  enum Phase : uint8_t { IDLE, WAIT_BUSY, WAIT_DONE };
  Phase phase_ = IDLE;
  uint32_t t0_ = 0;
};

// =============================================================================
//  vgq::Radio - the interface the flight executive uses.
//
//  Call order: begin() once at boot -> configure() -> then every loop pass
//  poll() until it returns NONE (or once per pass; events are never lost, they
//  wait for the next call). transmit() only when busy() is false; exactly one
//  TX_DONE or TX_TIMEOUT follows every accepted transmit(). Nothing blocks for
//  long except configure() / set_hibernation() on the E22 models (UART register
//  access in configuration mode, typically 60-80 ms, bounded by ~4 s on a dead
//  module): call those only when !busy().
//
//  Buffers: transmit() copies or writes out the frame before it returns (SX126x
//  copies into its own 255-octet buffer, E22 writes it to the UART ring), so the
//  caller may reuse its buffer at once. poll() copies a received packet into the
//  caller's RxPacket.
// =============================================================================
class Radio {
 public:
  Radio(const Radio&) = delete;
  Radio& operator=(const Radio&) = delete;

  uint8_t model() const { return model_; }           // VGQ_RADIO_* code
  virtual const char* name() const = 0;              // e.g. "E22-400T33D", "SX1268"

  // Hardware bring-up (pins, bus, module self-test). false: module absent/unresponsive.
  virtual bool begin() = 0;
  // Applies `s` and verifies it by read-back where the hardware allows. persist:
  // E22 writes the module's flash (C0) instead of volatile registers (C2); the
  // SX126x has no non-volatile memory and ignores it. Out-of-range settings are
  // rejected (false) without touching the radio.
  virtual bool configure(const RadioSettings& s, bool persist) = 0;
  // Starts sending n octets (1..240 on E22, 1..255 on SX126x); non-blocking.
  virtual bool transmit(const uint8_t* data, size_t n, uint32_t now) = 0;
  // True while the radio cannot accept transmit(): transmission (or SX126x CAD /
  // deferral) in progress, reception in progress, noise query pending, recovery.
  virtual bool busy(uint32_t now) const = 0;
  virtual RadioEvent poll(uint32_t now, RxPacket& rx, int32_t& value) = 0;
  // Asks for one ambient-noise reading (NOISE event). false if it cannot now.
  virtual bool request_noise(uint32_t now) = 0;
  // Result of the last SX126x channel-activity detection (true = LoRa activity
  // found). Always false on the E22 models: their LBT runs inside the module.
  virtual bool channel_busy() const = 0;
  // Hibernation: E22 Wake-on-Radio receiver (2 s cycle by default), SX126x
  // duty-cycled RX listening for a kSxWakePreambleSym preamble. Transmissions
  // remain possible (beacons); the driver returns to the low-power receiver after.
  virtual bool set_hibernation(bool on, uint32_t now) = 0;

  // ---- state ----
  bool hibernating() const { return hibernating_; }
  const RadioSettings& settings() const { return settings_; }
  bool config_ok() const { return cfg_ok_; }         // last configure() verified
  bool rx_boost_active() const { return rx_boost_ok_; }   // read back 0x96 from 0x08AC
  // RF packet flags (contract 3.2): b0 Rx boosted gain on, b1 LBT/CAD on.
  uint8_t rf_flags() const { return (uint8_t)((rx_boost_ok_ ? 1 : 0) | (settings_.lbt ? 2 : 0)); }
  uint32_t last_tx_launch_ms() const { return tx_launch_ms_; }   // when the radio was keyed

  // ---- counters (16-bit ones saturate; rx_bytes counts modulo 2^32 as in v2) ----
  // E22: every UART octet received; SX126x: payload octets of good packets.
  virtual uint32_t rx_bytes() const { return rx_payload_bytes_; }
  uint16_t crc_errors() const { return crc_errors_; }        // SX126x payload CRC failures
  uint16_t header_errors() const { return header_errors_; }  // SX126x LoRa header errors
  uint16_t lbt_deferrals() const { return lbt_deferrals_; }  // transmissions deferred by CAD
  uint16_t recoveries() const { return recoveries_; }        // SX126x reset + re-init successes

  // ---- link figures for the current settings ----
  uint32_t airtime_ms(size_t n) const;               // busy time of an n-octet transmission
  uint32_t bitrate_bps() const;                      // nominal air bit rate
  int8_t tx_power_dbm() const;                       // radiated power
  float sensitivity_dbm() const;                     // receiver sensitivity (this end)
  SensSource sensitivity_source() const;
  float max_freq_offset_hz() const;                  // SX126x FERR_L; 0 on E22 models

 protected:
  Radio(uint8_t model, int8_t pa_gain_db) : model_(model), pa_gain_db_(pa_gain_db) {}
  ~Radio() = default;                                // never deleted through the interface
  static void inc16(uint16_t& c) { if (c < UINT16_MAX) ++c; }

  const uint8_t model_;
  const int8_t pa_gain_db_;
  RadioSettings settings_;
  bool cfg_ok_ = false;
  bool hibernating_ = false;
  bool rx_boost_ok_ = false;
  uint32_t tx_launch_ms_ = 0;
  uint32_t rx_payload_bytes_ = 0;
  uint16_t crc_errors_ = 0, header_errors_ = 0, lbt_deferrals_ = 0, recoveries_ = 0;
};

// The radio instance selected by VGQ_RADIO, with the pins and bus from config.h.
// Defined in e22.cpp (E22 models) or sx126x.cpp (SX126x); static storage, no heap.
Radio& flight_radio();

}  // namespace vgq
